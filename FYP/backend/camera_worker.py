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
from datetime import datetime, timezone
from typing import Optional, Set, Tuple, Union

import cv2
import numpy as np
import mysql.connector
from dotenv import load_dotenv

from database import get_connection
from pipeline import process_frame, reset_pipeline_state, SAME_PLATE_COOLDOWN, draw_result

load_dotenv(override=True)
logger = logging.getLogger("anpr.worker")

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|stimeout;5000000|timeout;5000000|max_delay;500000"
)

_MAX_READ_FAILS     = 90
_RECONNECT_WAIT_MIN = 1
_RECONNECT_WAIT_MAX = 10
_DB_PING_EVERY      = 30
_TARGET_CAPTURE_FPS = 6

_frame_queue:     queue.Queue = queue.Queue(maxsize=1)
_broadcast_queue: queue.Queue = queue.Queue(maxsize=2)

_websockets: Set = set()
_ws_lock          = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None
_stop             = threading.Event()

_capture_thread:   Optional[threading.Thread] = None
_process_thread:   Optional[threading.Thread] = None
_broadcast_thread: Optional[threading.Thread] = None

_reconnect_event:  threading.Event = threading.Event()
_recovery_thread:  Optional[threading.Thread] = None
_recovery_lock:    threading.Lock = threading.Lock()   # guards _recovery_thread writes
_recovery_stop:    threading.Event = threading.Event()


def register(ws) -> None:
    with _ws_lock:
        _websockets.add(ws)
    logger.info(f"WS connected   active={len(_websockets)}")


def unregister(ws) -> None:
    with _ws_lock:
        _websockets.discard(ws)
    logger.info(f"WS disconnected  active={len(_websockets)}")


async def _broadcast(msg: str) -> None:
    with _ws_lock:
        sockets = list(_websockets)
    dead = []
    for ws in sockets:
        try:
            await ws.send_text(msg)
        except (RuntimeError, OSError):
            dead.append(ws)
    for ws in dead:
        unregister(ws)


def _push(payload: dict) -> None:
    if _loop and not _loop.is_closed():
        asyncio.run_coroutine_threadsafe(_broadcast(json.dumps(payload)), _loop)


class _MJPEGReader:
    """direct MJPEG stream reader — bypasses OpenCV's broken buffering on DroidCam"""
    def __init__(self, url: str, timeout: float = 5.0):
        self._url     = url
        self._timeout = timeout
        self._stream  = None
        self._buf     = b""
        self._open()

    def _open(self):
        try:
            self._stream = urllib.request.urlopen(self._url, timeout=self._timeout)
            self._buf    = b""
        except Exception as exc:
            self._stream = None
            raise OSError(f"Cannot open MJPEG stream {self._url}: {exc}") from exc

    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        if self._stream is None:
            return False, None
        try:
            for _ in range(200):  # max 200 chunks (~200KB) per frame
                self._buf += self._stream.read(4096)
                start = self._buf.find(b"\xff\xd8")
                if start == -1:
                    self._buf = b""
                    continue
                end = self._buf.find(b"\xff\xd9", start + 2)
                if end == -1:
                    continue
                jpeg = self._buf[start:end + 2]
                self._buf = self._buf[end + 2:]  # keep remainder
                arr = np.frombuffer(jpeg, dtype=np.uint8)
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
        """Stub — returns sensible defaults for width/height/fps."""
        if prop == cv2.CAP_PROP_FRAME_WIDTH:  return 1280.0
        if prop == cv2.CAP_PROP_FRAME_HEIGHT: return 720.0
        if prop == cv2.CAP_PROP_FPS:          return 30.0
        return 0.0


def _open_camera(cam_idx: int) -> Tuple[Optional[Union[cv2.VideoCapture, _MJPEGReader]], str]:
    cam_ip = os.getenv("CAMERA_IP", "").strip()
    if cam_ip:
        try:
            with socket.create_connection((cam_ip, 4747), timeout=0.5):
                port_4747 = True
        except OSError:
            port_4747 = False
        try:
            with socket.create_connection((cam_ip, 8080), timeout=0.5):
                port_8080 = True
        except OSError:
            port_8080 = False

        if not port_4747 and not port_8080:
            logger.warning(f"Camera IP {cam_ip} unreachable on ports 4747/8080 — will retry")
            return None, ""

        if port_4747:
            url = f"http://{cam_ip}:4747/video"
            label = f"DroidCam MJPEG {cam_ip}:4747"
            logger.info(f"Trying: {label}")
            try:
                reader = _MJPEGReader(url, timeout=5.0)
                # Read up to 10 frames to confirm live content
                brightness = 0.0
                for _ in range(10):
                    ret, frame = reader.read()
                    if ret and frame is not None:
                        brightness = float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean())
                        if brightness >= 5.0:
                            break
                if brightness >= 5.0:
                    logger.info(f"Connected: {label} [1280x720 @ 30fps] brightness={brightness:.1f}")
                    return reader, label
                else:
                    reader.release()
                    logger.warning(f"Black frames ({brightness:.1f}) from {label} — skipping")
            except OSError as exc:
                logger.warning(f"MJPEG reader failed for {label}: {exc}")

        if port_8080:
            url = f"http://{cam_ip}:8080/video"
            label = f"IP-Webcam {cam_ip}:8080"
            logger.info(f"Trying: {label}")
            try:
                reader = _MJPEGReader(url, timeout=5.0)
                brightness = 0.0
                for _ in range(10):
                    ret, frame = reader.read()
                    if ret and frame is not None:
                        brightness = float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean())
                        if brightness >= 5.0:
                            break
                if brightness >= 5.0:
                    logger.info(f"Connected: {label} brightness={brightness:.1f}")
                    return reader, label
                else:
                    reader.release()
                    logger.warning(f"Black frames ({brightness:.1f}) from {label}")
            except OSError as exc:
                logger.warning(f"MJPEG reader failed for {label}: {exc}")

        return None, ""

    label = f"Local webcam index {cam_idx}"
    logger.info(f"Trying: {label}")
    try:
        cap = cv2.VideoCapture(cam_idx)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            cap.release()
            logger.warning(f"Failed: {label}")
            return None, ""
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT,   720)
        cap.set(cv2.CAP_PROP_FPS,             30)
        cap.set(cv2.CAP_PROP_AUTOFOCUS,        1)
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE,    3)
        cap.set(cv2.CAP_PROP_BUFFERSIZE,       1)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        brightness = 0.0
        for _ in range(5):
            ret, _frame = cap.read()
            if ret and _frame is not None:
                brightness = float(cv2.cvtColor(_frame, cv2.COLOR_BGR2GRAY).mean())
                if brightness >= 5.0:
                    break
        if brightness < 5.0:
            cap.release()
            logger.warning(f"Black frames ({brightness:.1f}) from {label}")
            return None, ""
        w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        logger.info(f"Connected: {label}  [{w}x{h} @ {fps:.0f}fps]")
        return cap, label
    except (cv2.error, OSError, ValueError) as exc:
        logger.warning(f"Error connecting to {label}: {exc}")

    return None, ""


def _is_ip_camera_online(ip: str) -> bool:
    for port in (4747, 8080):
        try:
            with socket.create_connection((ip, port), timeout=0.5):  # Reduced timeout
                return True
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            logger.debug(f"Port {port} test failed for {ip}: {e}")
            continue
    return False


def _ip_camera_recovery_worker(ip: str) -> None:
    logger.info(f"IP camera recovery thread started for {ip}")
    while not _stop.is_set() and not _recovery_stop.is_set():
        for _ in range(150):
            if _stop.is_set() or _recovery_stop.is_set():
                break
            time.sleep(0.1)
        if _stop.is_set() or _recovery_stop.is_set():
            break

        if _is_ip_camera_online(ip):
            logger.info(f"IP camera recovery: {ip} is now reachable! Triggering reconnect.")
            _reconnect_event.set()
            break
    logger.info("IP camera recovery thread stopped")


def _capture_thread_fn(cam_idx: int) -> None:
    reconnect_wait  = _RECONNECT_WAIT_MIN
    _frame_interval = 1.0 / _TARGET_CAPTURE_FPS
    _last_sent      = 0.0

    while not _stop.is_set():
        _reconnect_event.clear()
        cap, label = _open_camera(cam_idx)

        if cap is None:
            cam_ip = os.getenv("CAMERA_IP", "").strip()
            if cam_ip:
                msg = f"Waiting for IP camera at {cam_ip} — retrying in {reconnect_wait}s…"
                logger.warning(msg)
                _push({"type": "camera_status", "connected": False, "message": msg})
                _interruptible_sleep(reconnect_wait)
                reconnect_wait = min(reconnect_wait * 2, _RECONNECT_WAIT_MAX)
            else:
                msg = f"No webcam at index {cam_idx} — retrying in {reconnect_wait}s"
                logger.error(msg)
                _push({"type": "camera_status", "connected": False, "message": msg})
                _interruptible_sleep(reconnect_wait)
                reconnect_wait = min(reconnect_wait * 2, _RECONNECT_WAIT_MAX)
            continue

        _push({"type": "camera_status", "connected": True, "message": label})
        reconnect_wait = _RECONNECT_WAIT_MIN
        fail_count     = 0

        cam_ip      = os.getenv("CAMERA_IP", "").strip()
        is_fallback = bool(cam_ip and label.startswith("Local"))

        _recovery_stop.set()
        with _recovery_lock:
            rt = _recovery_thread
        if rt and rt.is_alive():
            rt.join(timeout=2)
        _recovery_stop.clear()
        _reconnect_event.clear()

        if is_fallback:
            new_rt = threading.Thread(
                target=_ip_camera_recovery_worker, args=(cam_ip,),
                daemon=True, name="IPCamRecovery"
            )
            with _recovery_lock:
                _recovery_thread = new_rt
            new_rt.start()

        for _ in range(2):   # flush stale buffered frames
            cap.read()
        _last_sent = 0.0

        while not _stop.is_set():
            if _reconnect_event.is_set():
                logger.info("IP camera has become available — switching back from fallback")
                _reconnect_event.clear()
                break

            ret, frame = cap.read()
            if not ret:
                fail_count += 1
                if fail_count >= _MAX_READ_FAILS:
                    logger.warning(f"Stream lost after {fail_count} failures — reconnecting")
                    _push({"type": "camera_status", "connected": False,
                           "message": "Stream lost — reconnecting…"})
                    break
                time.sleep(0.01)
                continue

            fail_count = 0

            now = time.monotonic()
            if now - _last_sent < _frame_interval:
                continue
            _last_sent = now

            try:
                _frame_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                _frame_queue.put_nowait(frame)
            except queue.Full:
                pass

        cap.release()

        _recovery_stop.set()
        with _recovery_lock:
            rt = _recovery_thread
        if rt and rt.is_alive():
            rt.join(timeout=2)

    logger.info("Capture thread stopped")


def _process_thread_fn(every: int) -> None:
    conn_holder: list[Optional[mysql.connector.MySQLConnection]] = [None]
    try:
        conn_holder[0] = get_connection()
        conn_holder[0].autocommit = False  # save_log uses explicit commit/rollback
    except Exception as exc:
        logger.error(f"Cannot get initial DB connection: {exc}")

    reset_pipeline_state()

    frame_n          = 0
    processed_n      = 0
    last_ann         = None
    last_ping_n      = 0
    last_result      = None
    last_result_time = 0.0
    last_sent_key    = ""
    last_sent_time   = 0.0
    cooldown = SAME_PLATE_COOLDOWN
    annotated        = None

    fps_frame_count  = 0
    fps_window_start = time.monotonic()

    while not _stop.is_set():
        try:
            frame = _frame_queue.get(timeout=0.1)
        except queue.Empty:
            continue

        frame_n += 1

        fps_frame_count += 1
        now_mono = time.monotonic()
        if now_mono - fps_window_start >= 5.0:
            fps = fps_frame_count / (now_mono - fps_window_start)
            logger.info(f"WORKER FPS={fps:.1f}  frames={frame_n}  processed={processed_n}")
            fps_frame_count  = 0
            fps_window_start = now_mono

        detection = None
        is_new    = False

        if frame_n % every == 0:
            processed_n += 1

            if processed_n - last_ping_n >= _DB_PING_EVERY:
                last_ping_n = processed_n
                conn = conn_holder[0]
                try:
                    if conn is None:
                        raise ConnectionError("No DB connection")
                    conn.ping(reconnect=True, attempts=2, delay=1)
                except (ConnectionError, mysql.connector.Error) as ping_exc:
                    logger.warning(f"DB stale ({ping_exc}) -- reconnecting")
                    try:
                        if conn is not None:
                            conn.close()
                    except OSError as close_exc:
                        logger.debug(f"DB close error (ignored): {close_exc}")
                    conn_holder[0] = None
                    try:
                        conn = get_connection()
                        conn.autocommit = False  # save_log uses explicit commit/rollback
                        conn_holder[0]  = conn
                        reset_pipeline_state()
                    except Exception as reconnect_exc:
                        logger.error(f"DB reconnect failed: {reconnect_exc}")
                        time.sleep(2)
                        continue

            if conn_holder[0] is None:
                try:
                    conn_holder[0] = get_connection()
                    conn_holder[0].autocommit = False  # save_log uses explicit commit/rollback
                    logger.info("DB connection recovered")
                    reset_pipeline_state()
                except Exception as db_exc:
                    logger.warning(f"DB still unavailable: {db_exc} -- showing raw frame")
                    annotated = last_ann if last_ann is not None else frame

            if conn_holder[0] is not None:
                try:
                    annotated, detection = process_frame(frame, conn_holder[0])
                    last_ann = annotated
                    if detection:
                        det_key  = f"{detection.get('plate')}|{detection.get('status')}"
                        now_mono = time.monotonic()
                        last_result      = detection
                        last_result_time = now_mono
                        if last_sent_key and (now_mono - last_sent_time) >= cooldown:
                            last_sent_key  = ""
                            last_sent_time = 0.0
                        if det_key != last_sent_key:
                            last_sent_key  = det_key
                            last_sent_time = now_mono
                            is_new         = True
                            logger.debug(f"[WS] new detection broadcast: {det_key}")
                    else:
                        if last_sent_key and (time.monotonic() - last_sent_time) >= cooldown:
                            last_sent_key  = ""
                            last_sent_time = 0.0
                except Exception as exc:  # noqa: BLE001 — must not crash the worker loop
                    logger.error(f"process_frame error: {exc}", exc_info=True)
                    annotated = last_ann if last_ann is not None else frame
            else:
                annotated = last_ann if last_ann is not None else frame
        else:
            annotated  = frame
            result_age = time.monotonic() - last_result_time
            OVERLAY_SHOW_SECS = min(3.0, cooldown * 0.4)
            if last_result and result_age < OVERLAY_SHOW_SECS:
                detection = last_result
                try:
                    bbox = last_result.get("bbox", [])
                    if len(bbox) == 4:
                        veh = last_result.get("vehicle")
                        annotated = draw_result(
                            frame.copy(),
                            tuple(bbox),
                            last_result.get("plate", ""),
                            veh,
                            last_result.get("status", "unauthorized"),
                            last_result.get("yolo_conf", 0.0),
                            last_result.get("ocr_conf", 0.0),
                        )
                except (cv2.error, ValueError, TypeError) as draw_exc:
                    logger.debug(f"draw_result skipped: {draw_exc}")
            else:
                if last_result is not None:
                    last_result      = None
                    last_result_time = 0.0
                    last_sent_key    = ""
                    last_sent_time   = 0.0
                detection = None

        payload = {
            "annotated": annotated if annotated is not None else frame,
            "frame_n":   frame_n,
            "detection": detection,
            "is_new":    is_new,
            "ts":        datetime.now(timezone.utc).isoformat(),
        }

        try:
            _broadcast_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            _broadcast_queue.put_nowait(payload)
        except queue.Full:
            pass

    conn = conn_holder[0]
    if conn:
        try:
            conn.close()
        except OSError as exc:
            logger.debug(f"DB close on shutdown (ignored): {exc}")
    logger.info("Process thread stopped")


def _broadcast_thread_fn() -> None:
    while not _stop.is_set():
        try:
            payload = _broadcast_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        with _ws_lock:
            has_clients = bool(_websockets)
        if not has_clients:
            continue

        annotated = payload["annotated"]
        try:
            # resize to 960x540 — halves bandwidth vs 1280x720, ~12ms encode vs ~27ms
            h, w = annotated.shape[:2]
            if w > 960:
                annotated = cv2.resize(annotated, (960, 540), interpolation=cv2.INTER_LINEAR)
            _, buf = cv2.imencode(
                ".jpg", annotated,
                [cv2.IMWRITE_JPEG_QUALITY, 65],
            )
            b64 = base64.b64encode(buf).decode()
        except (cv2.error, OSError) as enc_exc:
            logger.error(f"JPEG encode error: {enc_exc}")
            try:
                _, buf = cv2.imencode('.png', annotated)
                b64 = base64.b64encode(buf).decode()
            except (cv2.error, OSError, ValueError) as fallback_exc:
                logger.error(f"Fallback encoding also failed: {fallback_exc}")
                continue

        _push({
            "type":      "frame",
            "frame":     b64,
            "frame_num": payload["frame_n"],
            "ts":        payload["ts"],
            "detection": payload["detection"],
            "is_new":    payload.get("is_new", False),
        })

    logger.info("Broadcast thread stopped")


def _interruptible_sleep(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not _stop.is_set() and time.monotonic() < deadline:
        time.sleep(0.1)


def start(
    loop:    asyncio.AbstractEventLoop,
    cam_idx: int = 0,
    every:   int = 1,
) -> None:
    global _loop, _capture_thread, _process_thread, _broadcast_thread, _recovery_thread

    if cam_idx == -1:
        logger.info("Camera worker disabled (CAMERA_INDEX=-1)")
        return

    _loop = loop
    _stop.clear()

    _capture_thread = threading.Thread(
        target=_capture_thread_fn, args=(cam_idx,),
        daemon=True, name="CameraCapture",
    )
    _process_thread = threading.Thread(
        target=_process_thread_fn, args=(every,),
        daemon=True, name="CameraProcess",
    )
    _broadcast_thread = threading.Thread(
        target=_broadcast_thread_fn,
        daemon=True, name="CameraBroadcast",
    )

    _capture_thread.start()
    _process_thread.start()
    _broadcast_thread.start()
    logger.info(f"Camera worker started (3-thread pipeline)  cam={cam_idx}  every={every}")


def stop() -> None:
    _stop.set()
    _recovery_stop.set()
    with _recovery_lock:
        rt = _recovery_thread
    threads = [_capture_thread, _process_thread, _broadcast_thread, rt]
    for t in threads:
        if t and t.is_alive():
            t.join(timeout=5)


def is_alive() -> bool:
    return any(
        t is not None and t.is_alive()
        for t in (_capture_thread, _process_thread, _broadcast_thread)
    )


def get_stats() -> dict:
    return {
        "dropped":     0,
        "processed":   0,
        "capture_fps": 0.0,
        "process_fps": 0.0,
    }
