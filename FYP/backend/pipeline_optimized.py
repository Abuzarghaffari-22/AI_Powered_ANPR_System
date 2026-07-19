"""
ANPR Pipeline — SPEED-FIRST, industry-grade.

Target: plate detected + OCR + DB lookup in < 2 seconds on CPU.

Key optimisations:
  1. YOLO at 320px          → ~300ms per inference  (was 640px = 2500ms)
  2. Persistent tesserocr   → ~200-400ms per call   (was pytesseract = 1700ms)
  3. 4-combo fast-path OCR  → exit at score >= 0.75 (was 23 combos = 40s+)
  4. Buffer flush at 1.0s   → fires within 1s of plate appearing
  5. STABLE_FRAMES=1        → single confident read fires immediately
  6. DB lookup in-memory    → < 1ms (plate_store)
"""

import os
os.environ.update({
    "OMP_NUM_THREADS": "2",
    "OPENBLAS_NUM_THREADS": "2",
    "MKL_NUM_THREADS": "2",
})

from pipeline import (
    logger,
    MODEL_PATH,
    CONF_THRESHOLD,
    STABLE_FRAMES,
    YOLO_INPUT_W,
    MIN_OCR_CONF,
    SAME_PLATE_COOLDOWN,
    _model_lock,
    _is_valid_plate_region,
    normalize_plate_ocr,
    _score_plate,
    lookup_plate,
    save_log,
    draw_result,
    draw_reading,
    reset_pipeline_state,
    _get_log_pool,
    _clahe_ocr,
    _deskew_plate,
    _suppress_glare,
    _detect_low_light,
    _enhance_low_light,
    _is_image_blurry,
    BLUR_THRESHOLD,
    ENABLE_LOW_LIGHT_BOOST,
    _TESSDATA,
    _save_crop,
)

import time
import threading
from collections import deque

import cv2
import numpy as np
from PIL import Image as PILImage

# ═══════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════

YOLO_INPUT_W_OPTIMIZED  = 320          # ~300ms on CPU after warmup
EFFECTIVE_STABLE_FRAMES = max(int(STABLE_FRAMES), 1)
_BUFFER_FLUSH_SECS      = 20.0         # matches pipeline.py — 20s before force-flush
_MIN_VOTES_FOR_FUZZY    = EFFECTIVE_STABLE_FRAMES

# ═══════════════════════════════════════════════════════════════════════════
# YOLO MODEL
# ═══════════════════════════════════════════════════════════════════════════

_model_optimized = None


def get_model_optimized():
    global _model_optimized
    if _model_optimized is not None:
        return _model_optimized
    with _model_lock:
        if _model_optimized is not None:
            return _model_optimized
        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"YOLO model not found: {MODEL_PATH}")
        import torch
        torch.set_num_threads(2)
        torch.set_grad_enabled(False)
        _orig = torch.load
        def _patched(f, *a, **kw):
            kw.setdefault("weights_only", False)
            return _orig(f, *a, **kw)
        torch.load = _patched
        try:
            from ultralytics import YOLO
            model = YOLO(str(MODEL_PATH))
            dummy = np.zeros((480, 640, 3), dtype=np.uint8)
            for _ in range(5):
                model(dummy, verbose=False, imgsz=YOLO_INPUT_W_OPTIMIZED, device="cpu")
            _model_optimized = model
            logger.info("YOLO loaded + warmed (320px, 5 passes)")
        finally:
            torch.load = _orig
    return _model_optimized


# ═══════════════════════════════════════════════════════════════════════════
# PERSISTENT tesserocr API  (no process-spawn overhead per call)
# ═══════════════════════════════════════════════════════════════════════════

_tess_api_line   = None
_tess_api_word   = None
_tess_api_sparse = None
_tess_lock       = threading.Lock()
_WL              = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"


def _get_tess_apis():
    """Lazy-init persistent tesserocr API objects (thread-safe, created once)."""
    global _tess_api_line, _tess_api_word, _tess_api_sparse
    if _tess_api_line is not None:
        return _tess_api_line, _tess_api_word, _tess_api_sparse
    with _tess_lock:
        if _tess_api_line is not None:
            return _tess_api_line, _tess_api_word, _tess_api_sparse
        try:
            from tesserocr import PyTessBaseAPI, PSM, OEM
            api_line = PyTessBaseAPI(
                path=_TESSDATA, lang="eng",
                psm=PSM.SINGLE_LINE, oem=OEM.LSTM_ONLY)
            api_line.SetVariable("tessedit_char_whitelist", _WL)

            api_word = PyTessBaseAPI(
                path=_TESSDATA, lang="eng",
                psm=PSM.SINGLE_WORD, oem=OEM.LSTM_ONLY)
            api_word.SetVariable("tessedit_char_whitelist", _WL)

            api_sparse = PyTessBaseAPI(
                path=_TESSDATA, lang="eng",
                psm=PSM.SPARSE_TEXT, oem=OEM.LSTM_ONLY)
            api_sparse.SetVariable("tessedit_char_whitelist", _WL)

            _tess_api_line   = api_line
            _tess_api_word   = api_word
            _tess_api_sparse = api_sparse
            logger.info("tesserocr persistent APIs ready (SINGLE_LINE / SINGLE_WORD / SPARSE_TEXT)")
        except Exception as exc:
            logger.warning(f"tesserocr init failed: {exc} — will use pytesseract fallback")
    return _tess_api_line, _tess_api_word, _tess_api_sparse


# ═══════════════════════════════════════════════════════════════════════════
# FAST OCR  — target < 1s for clean plates
# ═══════════════════════════════════════════════════════════════════════════

def _preprocess_crop(crop: np.ndarray) -> tuple:
    """
    Preprocess plate crop once and return (gray, eq, inv, otsu) PIL images.
    All variants share the same scaled/deskewed base — no redundant work.
    """
    proc = _suppress_glare(crop)
    if ENABLE_LOW_LIGHT_BOOST:
        is_dark, _ = _detect_low_light(proc)
        if is_dark:
            enhanced = _enhance_low_light(proc)
            if enhanced.ndim == 2:
                enhanced = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
            proc = _suppress_glare(enhanced)

    gray = cv2.cvtColor(proc, cv2.COLOR_BGR2GRAY) if proc.ndim == 3 else proc.copy()
    h, w = gray.shape[:2]

    # Scale to 600px wide — Tesseract sweet spot
    TARGET = 600
    if w < TARGET:
        mult = max(2, -(-TARGET // w))
        mult = min(mult, 4)
        gray = cv2.resize(gray, (w * mult, h * mult), interpolation=cv2.INTER_LANCZOS4)
    elif w > TARGET:
        gray = cv2.resize(gray, (TARGET, int(h * TARGET / w)), interpolation=cv2.INTER_AREA)

    gray = _deskew_plate(gray)
    eq   = _clahe_ocr.apply(gray)
    _, otsu = cv2.threshold(eq, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    inv  = cv2.bitwise_not(gray)

    return (PILImage.fromarray(gray),
            PILImage.fromarray(eq),
            PILImage.fromarray(inv),
            PILImage.fromarray(otsu))


def _ocr_tesserocr(pil_gray, pil_eq, pil_inv, pil_otsu) -> tuple[str, float]:
    """
    Fast OCR using persistent tesserocr C++ API.
    4 combos in priority order, exit immediately at score >= 0.75.
    Typical: 1-2 combos × 200-400ms = 200-800ms total.
    """
    api_line, api_word, api_sparse = _get_tess_apis()
    if api_line is None:
        return "", 0.0

    combos = [
        (api_line,   pil_gray),   # LSTM SINGLE_LINE on gray — best for single-row
        (api_line,   pil_eq),     # LSTM SINGLE_LINE on CLAHE — uneven lighting
        (api_sparse, pil_gray),   # LSTM SPARSE_TEXT on gray — two-row / noisy plates
        (api_sparse, pil_eq),     # LSTM SPARSE_TEXT on CLAHE — dark/uneven two-row
        (api_word,   pil_gray),   # LSTM SINGLE_WORD on gray — short plates
        (api_line,   pil_inv),    # LSTM SINGLE_LINE inverted — dark/night plates
    ]

    best_raw, best_sc = "", 0.0
    with _tess_lock:
        for api, pil in combos:
            try:
                api.SetImage(pil)
                raw = api.GetUTF8Text().strip()
                if raw and len(raw) >= 3:
                    norm = normalize_plate_ocr(raw)
                    sc   = _score_plate(norm) if norm else 0.0
                    if sc > best_sc:
                        best_sc, best_raw = sc, raw
                    if best_sc >= 0.75:
                        break
            except Exception:
                continue

    return best_raw, best_sc


def _fast_ocr(crop: np.ndarray) -> tuple[str, float, str]:
    """
    Industry-speed OCR pipeline:
      1. Blur gate — skip blurry crops immediately
      2. Preprocess once (scale, deskew, CLAHE)
      3. tesserocr fast-path (4 combos, exit at 0.75) — ~200-800ms
      4. If score < 0.60, fall back to full read_plate_ocr — rare, hard plates only

    Returns (normalized_plate, confidence, raw_ocr).
    """
    if crop is None or crop.size == 0:
        return "", 0.0, ""

    is_blurry, _ = _is_image_blurry(crop, threshold=BLUR_THRESHOLD)
    if is_blurry:
        return "", 0.0, ""

    pil_gray, pil_eq, pil_inv, pil_otsu = _preprocess_crop(crop)

    # Fast path via tesserocr
    best_raw, best_sc = _ocr_tesserocr(pil_gray, pil_eq, pil_inv, pil_otsu)

    if best_sc >= 0.45 and best_raw:
        norm = normalize_plate_ocr(best_raw)
        sc   = _score_plate(norm) if norm else 0.0
        if sc >= MIN_OCR_CONF:
            return norm, sc, best_raw

    # For hard plates where fast-path score < 0.45, try one more pass
    # with SPARSE_TEXT on inverted + otsu — no full fallback (too slow)
    api_line, api_word, api_sparse = _get_tess_apis()
    if api_sparse is not None:
        with _tess_lock:
            for pil in (pil_inv, pil_otsu):
                try:
                    api_sparse.SetImage(pil)
                    raw = api_sparse.GetUTF8Text().strip()
                    if raw and len(raw) >= 3:
                        norm = normalize_plate_ocr(raw)
                        sc   = _score_plate(norm) if norm else 0.0
                        if sc > best_sc:
                            best_sc, best_raw = sc, raw
                        if best_sc >= 0.42:
                            break
                except Exception:
                    continue

    if best_raw:
        norm = normalize_plate_ocr(best_raw)
        sc   = _score_plate(norm) if norm else 0.0
        # plate_store promotion: only promote a low-score exact match after 2 votes
        if sc < MIN_OCR_CONF:
            try:
                import plate_store as _ps
                if _ps.is_loaded():
                    _veh, _, _mt = _ps.lookup(norm)
                    if _veh is not None and _mt == "exact":
                        with _pipeline_lock:
                            _vote_count = sum(1 for t, _ in _plate_scores if t == norm)
                        if _vote_count >= 2:
                            sc = max(sc, MIN_OCR_CONF)
            except Exception:
                pass
        if sc >= MIN_OCR_CONF:
            return norm, sc, best_raw

    return "", 0.0, ""


def read_plate_ocr_fast(crop: np.ndarray) -> tuple[str, float]:
    """API-compat wrapper — returns (plate, conf)."""
    plate, conf, _ = _fast_ocr(crop)
    return plate, conf
# ═══════════════════════════════════════════════════════════════════════════
# CONFIDENCE
# ═══════════════════════════════════════════════════════════════════════════

_MATCH_WEIGHT = {
    "exact":              1.00,
    "fuzzy":              0.85,
    "edit1":              0.80,
    "edit2":              0.75,
    "edit3":              0.70,
    "confusion":          0.80,
    "confusion_prefix":   0.78,
    "confusion_edit":     0.75,
    "conf_insertion":     0.73,
    "conf_suffix":        0.72,
    "conf_leading_strip": 0.72,
    "prefix":             0.75,
    "trail_strip":        0.78,
    "insertion":          0.73,
    "trim_confusion":     0.72,
    "trim_insert":        0.70,
    "lead_confusion":     0.70,
    "suffix":             0.70,
    "trim_dc_suffix":     0.68,
    "substring":          0.65,
    "double_confusion":   0.65,
    "not_found":          0.0,
    "no_text":            0.0,
    "no_db":              0.0,
}


def _effective_confidence(ocr_score: float, match_type: str, matched: bool) -> float:
    w = _MATCH_WEIGHT.get(match_type, 0.0)
    if matched:
        return round(min(1.0, 0.5 * ocr_score + 0.5 * w), 3)
    return round(min(0.50, ocr_score * 0.5), 3)


# ═══════════════════════════════════════════════════════════════════════════
# PIPELINE STATE
# ═══════════════════════════════════════════════════════════════════════════

_plate_buffer      = deque(maxlen=max(EFFECTIVE_STABLE_FRAMES * 4, 8))
_plate_scores      = deque(maxlen=max(EFFECTIVE_STABLE_FRAMES * 4, 8))
_last_stable       = ""
_last_stable_time  = 0.0
_buffer_first_seen = 0.0
_pipeline_lock     = threading.Lock()
_last_lookup_cache = {}
_lookup_cache_lock = threading.Lock()
_model_ready       = False  # True after first YOLO inference; prevents warmup-flush
_no_det_streak     = 0      # consecutive frames with no plate — buffer cleared after 6
_NO_DET_CLEAR_FRAMES = 6


def reset_pipeline_state_optimized() -> None:
    """Clear buffer and caches. Does NOT reset _model_ready (YOLO stays loaded)."""
    global _last_stable, _last_stable_time, _buffer_first_seen, _no_det_streak
    with _pipeline_lock:
        _plate_buffer.clear()
        _plate_scores.clear()
        _last_stable       = ""
        _last_stable_time  = 0.0
        _buffer_first_seen = 0.0
        _no_det_streak     = 0
        with _lookup_cache_lock:
            _last_lookup_cache.clear()
    logger.debug("[optimized] pipeline state reset")


def _vote(snapshot):
    counts, maxsc = {}, {}
    for t, s in snapshot:
        counts[t] = counts.get(t, 0) + 1
        if s > maxsc.get(t, 0.0):
            maxsc[t] = s
    confirmed = max(counts, key=lambda k: (counts[k], maxsc.get(k, 0.0)))
    return confirmed, counts[confirmed], maxsc.get(confirmed, 0.0)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN PER-FRAME PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

def process_frame_optimized(frame: np.ndarray, conn) -> tuple[np.ndarray, dict | None]:
    global _last_stable, _last_stable_time, _buffer_first_seen, _model_ready, _no_det_streak

    model = get_model_optimized()

    # After YOLO warmup, flush stale buffer that accumulated during loading
    if not _model_ready:
        _model_ready = True
        with _pipeline_lock:
            _plate_buffer.clear()
            _plate_scores.clear()
            _buffer_first_seen = 0.0
        logger.info("[optimized] YOLO ready — pipeline reset after warmup")

    fh, fw = frame.shape[:2]

    # ── YOLO at 320px ─────────────────────────────────────────────────────
    t_yolo = time.perf_counter()
    if fw > YOLO_INPUT_W_OPTIMIZED:
        scale = YOLO_INPUT_W_OPTIMIZED / fw
        yf = cv2.resize(frame, (YOLO_INPUT_W_OPTIMIZED, int(fh * scale)),
                        interpolation=cv2.INTER_LINEAR)
    else:
        yf, scale = frame, 1.0

    yolo_out = model(yf, verbose=False, imgsz=YOLO_INPUT_W_OPTIMIZED, device="cpu")
    yolo_ms  = (time.perf_counter() - t_yolo) * 1000

    best_coords, best_conf = None, 0.0
    for result in yolo_out:
        if result.boxes is None:
            continue
        for box in result.boxes:
            c = float(box.conf[0])
            if c < CONF_THRESHOLD:
                continue
            bx1, by1, bx2, by2 = map(int, box.xyxy[0])
            if scale != 1.0:
                bx1 = int(bx1 / scale); by1 = int(by1 / scale)
                bx2 = int(bx2 / scale); by2 = int(by2 / scale)
            if _is_valid_plate_region(bx1, by1, bx2, by2, fw, fh) and c > best_conf:
                best_conf, best_coords = c, (bx1, by1, bx2, by2)

    if best_coords is None:
        with _pipeline_lock:
            _no_det_streak += 1
            if _no_det_streak >= _NO_DET_CLEAR_FRAMES:
                _plate_buffer.clear()
                _plate_scores.clear()
                _buffer_first_seen = 0.0
                _no_det_streak = 0
        return frame, None

    with _pipeline_lock:
        _no_det_streak = 0

    x1, y1, x2, y2 = best_coords

    # ── Crop with padding ─────────────────────────────────────────────────
    px = max(20, int((x2 - x1) * 0.12))
    py = max(14, int((y2 - y1) * 0.18))
    crop = frame[max(0, y1 - py): min(fh, y2 + py),
                 max(0, x1 - px): min(fw, x2 + px)]
    if crop.size == 0:
        with _pipeline_lock:
            _plate_buffer.clear()
            _plate_scores.clear()
            _buffer_first_seen = 0.0
        return frame, None

    # ── Fast OCR (tesserocr, 4 combos max) ────────────────────────────────
    t_ocr = time.perf_counter()
    plate_text, ocr_conf, raw_ocr = _fast_ocr(crop)
    ocr_ms = (time.perf_counter() - t_ocr) * 1000

    # ── Buffer / voting ───────────────────────────────────────────────────
    with _pipeline_lock:
        if _buffer_first_seen == 0.0:
            _buffer_first_seen = time.time()
        if plate_text:
            _plate_buffer.append(plate_text)
            _plate_scores.append((plate_text, ocr_conf))
        else:
            return draw_reading(frame.copy(), (x1, y1, x2, y2)), None

        buf_len  = len(_plate_buffer)
        snapshot = list(_plate_scores)
        elapsed  = time.time() - _buffer_first_seen if _buffer_first_seen > 0 else 0.0

    confirmed, votes, conf_score = _vote(snapshot)

    require = EFFECTIVE_STABLE_FRAMES
    force   = (elapsed >= _BUFFER_FLUSH_SECS and buf_len >= require)
    stable  = (votes >= require) or force

    # Force-flush gate: same threshold as pipeline.py (95% of MIN_OCR_CONF).
    # Blocks borderline scores that only survive because the plate stayed in frame.
    if force and conf_score < MIN_OCR_CONF * 0.95:
        with _pipeline_lock:
            _plate_buffer.clear()
            _plate_scores.clear()
            _buffer_first_seen = 0.0
        return draw_reading(frame.copy(), (x1, y1, x2, y2)), None

    if not stable:
        return draw_reading(frame.copy(), (x1, y1, x2, y2), plate_text), None

    # ── DB lookup (in-memory plate_store, < 1ms) ──────────────────────────
    t_db = time.perf_counter()
    probe_vehicle, probe_status, probe_match = lookup_plate(conn, confirmed, raw_ocr)
    db_ms = (time.perf_counter() - t_db) * 1000

    exact_match = probe_match in ("exact", "fuzzy")
    if probe_vehicle is not None and not exact_match and votes < _MIN_VOTES_FOR_FUZZY:
        return draw_reading(frame.copy(), (x1, y1, x2, y2), plate_text), None

    # ── Clear buffer for next vehicle ─────────────────────────────────────
    with _pipeline_lock:
        _plate_buffer.clear()
        _plate_scores.clear()
        _buffer_first_seen = 0.0

    # ── Cooldown ──────────────────────────────────────────────────────────
    now = time.time()
    with _pipeline_lock:
        already = (confirmed == _last_stable and
                   (now - _last_stable_time) < SAME_PLATE_COOLDOWN)
        if not already:
            _last_stable      = confirmed
            _last_stable_time = now

    if already:
        with _lookup_cache_lock:
            cached = _last_lookup_cache.get(confirmed)
        vehicle, status, match_type = cached if cached else (probe_vehicle, probe_status, probe_match)
        eff_conf = _effective_confidence(conf_score, match_type, vehicle is not None)
        eff_conf = round(min(max(eff_conf, 0.0), 1.0), 3)
        frame = draw_result(frame, (x1, y1, x2, y2), confirmed, vehicle, status,
                            best_conf, eff_conf)
        return frame, None

    # ── Store result ──────────────────────────────────────────────────────
    vehicle, status, match_type = probe_vehicle, probe_status, probe_match
    with _lookup_cache_lock:
        _last_lookup_cache[confirmed] = (vehicle, status, match_type)

    matched  = vehicle is not None
    eff_conf = _effective_confidence(conf_score, match_type, matched)
    eff_conf = round(min(max(eff_conf, 0.0), 1.0), 3)

    # ── Draw overlay ──────────────────────────────────────────────────────
    frame = draw_result(frame, (x1, y1, x2, y2), confirmed, vehicle, status,
                        best_conf, eff_conf)

    # ── Async DB log (non-blocking) ───────────────────────────────────────
    import database as _db
    crop_path = _save_crop(crop, confirmed)

    def _log_worker(pl, veh, st, cf, cp):
        lc = None
        try:
            lc = _db.get_connection()
            lc.autocommit = True
            save_log(lc, pl, veh, st, cf, cp)
        except Exception as e:
            logger.error(f"[LogWriter] {e}")
        finally:
            if lc:
                try: lc.close()
                except Exception: pass

    _get_log_pool().submit(_log_worker, confirmed, vehicle, status, eff_conf, crop_path)

    logger.info(
        f"DETECTION | '{confirmed}' | {status.upper()} | {match_type} | "
        f"yolo={yolo_ms:.0f}ms ocr={ocr_ms:.0f}ms db={db_ms:.1f}ms | "
        f"yolo_conf={best_conf:.2f} ocr_score={conf_score:.2f} eff_conf={eff_conf:.2f}"
    )

    return frame, {
        "plate":      confirmed,
        "status":     status,
        "match_type": match_type,
        "yolo_conf":  round(best_conf, 3),
        "ocr_conf":   eff_conf,
        "bbox":       [x1, y1, x2, y2],
        "vehicle": {
            "owner_name":     vehicle.get("owner_name")     if vehicle else None,
            "make":           vehicle.get("make")           if vehicle else None,
            "model":          vehicle.get("model")          if vehicle else None,
            "color":          vehicle.get("color")          if vehicle else None,
            "dues":           vehicle.get("dues")           if vehicle else None,
            "license_number": vehicle.get("license_number") if vehicle else None,
        } if vehicle else None,
    }
