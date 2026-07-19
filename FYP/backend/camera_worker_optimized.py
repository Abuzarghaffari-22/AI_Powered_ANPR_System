# 4-thread pipeline: capture -> process -> detection -> broadcast

import asyncio
import base64
import json
import logging
import os
import queue
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Set, Tuple, Union

import cv2
import numpy as np
import mysql.connector
from dotenv import load_dotenv

from database import get_connection
from pipeline_optimized import (
    process_frame_optimized,
    SAME_PLATE_COOLDOWN,
    reset_pipeline_state_optimized,
)
from pipeline import reset_pipeline_state, draw_result

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|stimeout;5000000|timeout;5000000|max_delay;500000"
)

load_dotenv(override=True)
logger = logging.getLogger("anpr.worker_optimized")

_MAX_READ_FAILS     = 60
_RECONNECT_WAIT_MIN = 1
_RECONNECT_WAIT_MAX = 8
_DB_PING_EVERY      = 40
_TCP_PROBE_TIMEOUT  = 0.8
_TARGET_CAPTURE_FPS = 10



@dataclass
class _WorkerState:
    loop:             Optional[asyncio.AbstractEventLoop] = None
    stop:             threading.Event = field(default_factory=threading.Event)
    websockets:       Set             = field(default_factory=set)
    ws_lock:          threading.Lock  = field(default_factory=threading.Lock)

    # Single-slot queues — always hold the LATEST item only
    frame_queue:      queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=1))
    broadcast_queue:  queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=2))
    detection_queue:  queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=1))

    detection_result: Optional[dict] = None
    detection_lock:   threading.Lock = field(default_factory=threading.Lock)

    # Last encoded frame for new WS clients
    last_frame_msg:   Optional[str]  = None
    frame_msg_lock:   threading.Lock = field(default_factory=threading.Lock)

    capture_thread:   Optional[threading.Thread] = None
    process_thread:   Optional[threading.Thread] = None
    detection_thread: Optional[threading.Thread] = None
    broadcast_thread: Optional[threading.Thread] = None

    stats_lock:  threading.Lock = field(default_factory=threading.Lock)
    dropped:     int   = 0
    processed:   int   = 0
    capture_fps: float = 0.0
    process_fps: float = 0.0


_state = _WorkerState()


def register(ws) -> None:
    with _state.ws_lock:
        _state.websockets.add(ws)
    logger.info(f"WS connected  active={len(_state.websockets)}")
    # Send last cached frame immediately so the client doesn't see a blank screen
    with _state.frame_msg_lock:
        msg = _state.last_frame_msg
    if msg and _state.loop and not _state.loop.is_closed():
        async def _send():
            try:
                await ws.send_text(msg)
            except Exception:
                pass
        asyncio.run_coroutine_threadsafe(_send(), _state.loop)


def unregister(ws) -> None:
    with _state.ws_lock:
        _state.websockets.discard(ws)
    logger.info(f"WS disconnected  active={len(_state.websockets)}")


async def _broadcast(msg: str) -> None:
    with _state.ws_lock:
        sockets = list(_state.websockets)
    dead = []
    for ws in sockets:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        unregister(ws)


def _push(payload: dict) -> None:
    if _state.loop and not _state.loop.is_closed():
        asyncio.run_coroutine_threadsafe(
            _broadcast(json.dumps(payload)), _state.loop
        )



def _tcp_reachable(ip: str, port: int) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=_TCP_PROBE_TIMEOUT):
            return True
    except OSError:
        return False


class _MJPEGReader:
    def __init__(self, url: str, timeout: float = 5.0):
        self._url     = url
        self._timeout = timeout
        self._stream  = None
        self._buf     = b""
        req = urllib.request.Request(url, headers={"Connection": "keep-alive"})
        self._stream  = urllib.request.urlopen(req, timeout=self._timeout)

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._stream is None:
            return False, None
        try:
            for _ in range(500):
                chunk = self._stream.read(8192)
                if not chunk:
                    return False, None
                self._buf += chunk
                if len(self._buf) > 600_000:
                    s = self._buf.rfind(b"\xff\xd8")
                    self._buf = self._buf[s:] if s != -1 else b""
                    continue
                s = self._buf.find(b"\xff\xd8")
                if s == -1:
                    self._buf = b""
                    continue
                e = self._buf.find(b"\xff\xd9", s + 2)
                if e == -1:
                    continue
                jpeg = self._buf[s:e + 2]
                self._buf = self._buf[e + 2:]
                arr   = np.frombuffer(jpeg, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    return True, frame
        except Exception:
            pass
        return False, None

    def isOpened(self) -> bool:
        return self._stream is not None

    def release(self):
        if self._stream:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._buf = b""

    def get(self, prop):
        if prop == cv2.CAP_PROP_FRAME_WIDTH:  return 1280.0
        if prop == cv2.CAP_PROP_FRAME_HEIGHT: return 720.0
        if prop == cv2.CAP_PROP_FPS:          return 30.0
        return 0.0


def _open_camera(cam_idx: int) -> Tuple[Optional[Union[cv2.VideoCapture, _MJPEGReader]], str]:
    cam_ip = os.getenv("CAMERA_IP", "").strip()

    if cam_ip:
        for port, path, app in [(4747, "/video", "DroidCam"), (8080, "/video", "IP-Webcam")]:
            if not _tcp_reachable(cam_ip, port):
                continue
            url   = f"http://{cam_ip}:{port}{path}"
            label = f"{app} {cam_ip}:{port}"
            logger.info(f"Trying: {label}")
            try:
                reader = _MJPEGReader(url, timeout=5.0)
                brightness = 0.0
                for _ in range(15):
                    ret, frame = reader.read()
                    if ret and frame is not None:
                        brightness = float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean())
                        if brightness >= 5.0:
                            break
                if brightness >= 5.0:
                    logger.info(f"Connected: {label}  brightness={brightness:.1f}")
                    return reader, label
                reader.release()
                logger.warning(f"Black frames from {label} (brightness={brightness:.1f})")
            except Exception as exc:
                logger.warning(f"Failed {label}: {exc}")
        logger.warning(f"IP camera {cam_ip} unreachable — falling back to local webcam {cam_idx}")
        # Fall through to local webcam instead of returning None

    label = f"Local webcam {cam_idx}"
    logger.info(f"Trying: {label}")
    try:
        cap = cv2.VideoCapture(cam_idx)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            cap.release()
            return None, ""
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT,  720)
        cap.set(cv2.CAP_PROP_FPS,           30)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        brightness = 0.0
        for _ in range(10):
            ret, f = cap.read()
            if ret and f is not None:
                brightness = float(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).mean())
                if brightness >= 5.0:
                    break
        if brightness < 5.0:
            cap.release()
            return None, ""
        w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        logger.info(f"Connected: {label} [{w}x{h} @ {fps:.0f}fps]")
        return cap, label
    except Exception as exc:
        logger.warning(f"Local webcam error: {exc}")
    return None, ""


def _capture_thread_fn(cam_idx: int) -> None:
    reconnect_wait = _RECONNECT_WAIT_MIN

    while not _state.stop.is_set():
        cap, label = _open_camera(cam_idx)

        if cap is None:
            msg = f"No camera available (tried IP={os.getenv('CAMERA_IP','').strip() or 'N/A'}, webcam={cam_idx})"
            logger.warning(msg)
            _push({"type": "camera_status", "connected": False, "message": msg})
            _interruptible_sleep(reconnect_wait)
            reconnect_wait = min(reconnect_wait * 2, _RECONNECT_WAIT_MAX)
            continue

        _push({"type": "camera_status", "connected": True, "message": label})
        reconnect_wait = _RECONNECT_WAIT_MIN
        fail_count     = 0
        frame_interval = 1.0 / _TARGET_CAPTURE_FPS
        last_sent      = 0.0
        fps_count      = 0
        fps_start      = time.monotonic()

        for _ in range(3):
            cap.read()

        while not _state.stop.is_set():
            ret, frame = cap.read()
            if not ret or frame is None:
                fail_count += 1
                if fail_count >= _MAX_READ_FAILS:
                    logger.warning("Stream lost — reconnecting")
                    _push({"type": "camera_status", "connected": False,
                           "message": "Stream lost — reconnecting…"})
                    break
                time.sleep(0.02)
                continue

            fail_count = 0
            now = time.monotonic()
            if now - last_sent < frame_interval:
                continue
            last_sent = now

            fps_count += 1
            if now - fps_start >= 5.0:
                with _state.stats_lock:
                    _state.capture_fps = fps_count / (now - fps_start)
                fps_count = 0
                fps_start = now

            try:
                _state.frame_queue.get_nowait()
                with _state.stats_lock:
                    _state.dropped += 1
            except queue.Empty:
                pass
            try:
                _state.frame_queue.put_nowait(frame)
            except queue.Full:
                pass

        cap.release()

    logger.info("Capture thread stopped")


def _process_thread_fn(every: int) -> None:
    frame_n          = 0
    last_result      = None      # last confirmed detection dict
    last_result_time = 0.0       # monotonic time when last_result was set
    last_sent_key    = ""
    last_sent_time   = 0.0
    cooldown         = SAME_PLATE_COOLDOWN
    det_sample_n     = 0
    fps_count        = 0
    fps_start        = time.monotonic()
    OVERLAY_SECS     = min(cooldown, 4.0)

    while not _state.stop.is_set():
        try:
            frame = _state.frame_queue.get(timeout=0.1)
        except queue.Empty:
            continue

        frame_n  += 1
        fps_count += 1
        now = time.monotonic()

        if now - fps_start >= 5.0:
            fps = fps_count / (now - fps_start)
            with _state.stats_lock:
                _state.process_fps = fps
            logger.info(f"PROCESS FPS={fps:.1f}  frames={frame_n}")
            fps_count = 0
            fps_start = now

        if frame_n % every == 0:
            det_sample_n += 1
            with _state.stats_lock:
                _state.processed = det_sample_n
            try:
                _state.detection_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                _state.detection_queue.put_nowait(frame)
            except queue.Full:
                pass

        with _state.detection_lock:
            det_snapshot = _state.detection_result

        detection = None
        is_new    = False
        annotated = frame

        if det_snapshot is not None:
            det_result = det_snapshot.get("detection")
            det_ann    = det_snapshot.get("annotated")
            det_time   = det_snapshot.get("ts_mono", 0.0)
            result_age = now - det_time

            if det_result is not None:
                det_key = f"{det_result.get('plate')}|{det_result.get('status')}"

                if last_sent_key and (now - last_sent_time) >= cooldown:
                    last_sent_key  = ""
                    last_sent_time = 0.0

                if det_key != last_sent_key:
                    last_sent_key  = det_key
                    last_sent_time = now
                    is_new         = True
                    logger.info(f"[WS] NEW detection → {det_key}")

                last_result      = det_result
                last_result_time = det_time

            # Show overlay while result is fresh
            if last_result is not None:
                age = now - last_result_time
                if age < OVERLAY_SECS:
                    detection = last_result
                    if det_ann is not None and result_age < 1.0:
                        annotated = det_ann
                    else:
                        try:
                            bbox = last_result.get("bbox", [])
                            if len(bbox) == 4:
                                annotated = draw_result(
                                    frame.copy(),
                                    tuple(bbox),
                                    last_result.get("plate", ""),
                                    last_result.get("vehicle"),
                                    last_result.get("status", "unauthorized"),
                                    last_result.get("yolo_conf", 0.0),
                                    last_result.get("ocr_conf", 0.0),
                                )
                        except (cv2.error, ValueError, TypeError):
                            pass
                else:
                    last_result    = None
                    last_sent_key  = ""
                    last_sent_time = 0.0
                    detection      = None

        payload = {
            "annotated": annotated,
            "frame_n":   frame_n,
            "detection": detection,
            "is_new":    is_new,
            "ts":        datetime.now(timezone.utc).isoformat(),
        }
        try:
            _state.broadcast_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            _state.broadcast_queue.put_nowait(payload)
        except queue.Full:
            pass

    logger.info("Process thread stopped")


def _detection_thread_fn() -> None:
    conn_holder: list[Optional[mysql.connector.MySQLConnection]] = [None]
    try:
        conn_holder[0] = get_connection()
        conn_holder[0].autocommit = True
    except Exception as exc:
        logger.error(f"[DetectionThread] DB connect failed: {exc}")

    reset_pipeline_state()
    reset_pipeline_state_optimized()
    import pipeline_optimized as _po
    _po._model_ready = False
    det_count = 0

    while not _state.stop.is_set():
        try:
            frame = _state.detection_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        det_count += 1

        if det_count % _DB_PING_EVERY == 0:
            conn = conn_holder[0]
            try:
                if conn is None:
                    raise ConnectionError("no conn")
                conn.ping(reconnect=True, attempts=2, delay=1)
            except Exception as exc:
                logger.warning(f"[DetectionThread] DB ping failed: {exc} — reconnecting")
                try:
                    if conn:
                        conn.close()
                except Exception:
                    pass
                conn_holder[0] = None
                try:
                    conn_holder[0] = get_connection()
                    conn_holder[0].autocommit = True
                    reset_pipeline_state()
                    reset_pipeline_state_optimized()
                except Exception as exc2:
                    logger.error(f"[DetectionThread] DB reconnect failed: {exc2}")
                    continue

        if conn_holder[0] is None:
            try:
                conn_holder[0] = get_connection()
                conn_holder[0].autocommit = True
                reset_pipeline_state()
                reset_pipeline_state_optimized()
            except Exception as exc:
                logger.warning(f"[DetectionThread] DB unavailable: {exc}")
                continue

        try:
            annotated, detection = process_frame_optimized(frame, conn_holder[0])
        except Exception as exc:
            logger.error(f"[DetectionThread] pipeline error: {exc}", exc_info=True)
            continue

        new_result = {
            "detection": detection,
            "annotated": annotated,
            "ts_mono":   time.monotonic(),
        }
        with _state.detection_lock:
            _state.detection_result = new_result

    conn = conn_holder[0]
    if conn:
        try:
            conn.close()
        except Exception:
            pass
    logger.info("Detection thread stopped")


def _broadcast_thread_fn() -> None:
    logger.info("Broadcast thread started")

    while not _state.stop.is_set():
        try:
            payload = _state.broadcast_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        with _state.ws_lock:
            if not _state.websockets:
                continue

        annotated = payload["annotated"]
        try:
            h, w = annotated.shape[:2]
            if w > 960:
                annotated = cv2.resize(annotated, (960, 540),
                                       interpolation=cv2.INTER_LINEAR)
            _, buf = cv2.imencode(".jpg", annotated,
                                  [cv2.IMWRITE_JPEG_QUALITY, 72])
            b64 = base64.b64encode(buf).decode()
        except Exception as exc:
            logger.error(f"JPEG encode error: {exc}")
            continue

        msg = json.dumps({
            "type":      "frame",
            "frame":     b64,
            "frame_num": payload["frame_n"],
            "ts":        payload["ts"],
            "detection": payload["detection"],
            "is_new":    payload.get("is_new", False),
        })

        with _state.frame_msg_lock:
            _state.last_frame_msg = msg

        if _state.loop and not _state.loop.is_closed():
            asyncio.run_coroutine_threadsafe(_broadcast(msg), _state.loop)

    logger.info("Broadcast thread stopped")


def _interruptible_sleep(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not _state.stop.is_set() and time.monotonic() < deadline:
        time.sleep(0.1)


def start(loop: asyncio.AbstractEventLoop, cam_idx: int = 0, every: int = 1) -> None:
    if cam_idx == -1:
        logger.info("Camera worker disabled (CAMERA_INDEX=-1)")
        return

    _state.loop = loop
    _state.stop.clear()
    _state.detection_result = None

    _state.capture_thread = threading.Thread(
        target=_capture_thread_fn, args=(cam_idx,),
        daemon=True, name="CameraCapture")
    _state.process_thread = threading.Thread(
        target=_process_thread_fn, args=(every,),
        daemon=True, name="CameraProcess")
    _state.detection_thread = threading.Thread(
        target=_detection_thread_fn,
        daemon=True, name="CameraDetection")
    _state.broadcast_thread = threading.Thread(
        target=_broadcast_thread_fn,
        daemon=True, name="CameraBroadcast")

    _state.capture_thread.start()
    _state.process_thread.start()
    _state.detection_thread.start()
    _state.broadcast_thread.start()
    logger.info(f"Camera worker started  cam={cam_idx}  every={every}  [4-thread]")


def stop() -> None:
    _state.stop.set()
    for t in (_state.capture_thread, _state.process_thread,
              _state.detection_thread, _state.broadcast_thread):
        if t and t.is_alive():
            t.join(timeout=5)


def is_alive() -> bool:
    return any(t is not None and t.is_alive()
               for t in (_state.capture_thread, _state.process_thread,
                         _state.detection_thread, _state.broadcast_thread))


def get_stats() -> dict:
    with _state.stats_lock:
        return {
            "dropped":     _state.dropped,
            "processed":   _state.processed,
            "capture_fps": _state.capture_fps,
            "process_fps": _state.process_fps,
        }
