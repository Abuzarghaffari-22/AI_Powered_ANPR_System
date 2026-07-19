# ANPR pipeline — YOLO detection, Tesseract OCR, MySQL lookup

import logging
import math
import os
import re
import threading
import time
import warnings
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import mysql.connector

os.environ.update({
    "OMP_NUM_THREADS":     "2",
    "OPENBLAS_NUM_THREADS": "2",
    "MKL_NUM_THREADS":     "2",
    "TORCH_CPP_LOG_LEVEL": "ERROR",
    "CYSIGNALS_CRASH_LOGS": "",
})
warnings.filterwarnings("ignore")

import cv2
import numpy as np
from dotenv import load_dotenv
from PIL import Image as PILImage

try:
    from tesserocr import PSM as _TessPSM      # type: ignore[import-untyped]
    from tesserocr import OEM as _TessOEM      # type: ignore[import-untyped]
    from tesserocr import PyTessBaseAPI as _PyTessBaseAPI  # type: ignore[import-untyped]
    _TESSEROCR_AVAILABLE = True
except ImportError:
    _TessPSM = _TessOEM = _PyTessBaseAPI = None  # type: ignore[assignment]
    _TESSEROCR_AVAILABLE = False

load_dotenv(override=True)
logger = logging.getLogger("anpr.pipeline")

BACKEND_DIR = Path(__file__).resolve().parent
MODEL_PATH  = BACKEND_DIR / "models" / "yolov11_plate_detection.pt"



def _load_validated_env() -> dict:
    def _f(k, d, lo, hi):
        v = float(os.getenv(k, d))
        if not (lo <= v <= hi):
            raise RuntimeError(f"[ENV] {k}={v} out of range [{lo},{hi}]")
        return v

    def _i(k, d, lo, hi):
        v = int(os.getenv(k, d))
        if not (lo <= v <= hi):
            raise RuntimeError(f"[ENV] {k}={v} out of range [{lo},{hi}]")
        return v

    cfg = {
        "CONF_THRESHOLD":         _f("CONF_THRESHOLD",        "0.30",  0.01, 0.99),
        "STABLE_FRAMES":          _i("STABLE_FRAMES",         "3",     1,    10),
        "SAME_PLATE_COOLDOWN":    _f("SAME_PLATE_COOLDOWN",   "8.0",   1.0,  300.0),
        "MIN_PLATE_W":            _i("MIN_PLATE_W",           "40",    10,   400),
        "MAX_PLATE_W":            _i("MAX_PLATE_W",           "650",   50,   2000),
        "MIN_ASPECT":             _f("MIN_ASPECT",            "1.1",   0.5,  20.0),
        "MAX_ASPECT":             _f("MAX_ASPECT",            "7.0",   1.0,  30.0),
        "MAX_FRAME_FRAC":         _f("MAX_FRAME_FRAC",        "0.45",  0.01, 1.0),
        "SKY_SKIP_FRAC":          _f("SKY_SKIP_FRAC",         "0.04",  0.0,  0.5),
        "YOLO_INPUT_W":           _i("YOLO_INPUT_W",          "640",   128,  1920),
        "BLUR_THRESHOLD":         _f("BLUR_THRESHOLD",        "18.0",  0.1,  500.0),
        "LOW_LIGHT_THRESHOLD":    _f("LOW_LIGHT_THRESHOLD",   "50.0",  1.0,  200.0),
        "ENABLE_LOW_LIGHT_BOOST": bool(int(os.getenv("ENABLE_LOW_LIGHT_BOOST", "1"))),
        "MIN_OCR_CONF":           _f("MIN_OCR_CONF",          "0.15",  0.0,  1.0),
    }
    logger.info(
        f"[ENV] CONF={cfg['CONF_THRESHOLD']} STABLE={cfg['STABLE_FRAMES']} "
        f"MIN_OCR_CONF={cfg['MIN_OCR_CONF']} BLUR={cfg['BLUR_THRESHOLD']}"
    )
    return cfg


_CFG = _load_validated_env()

CONF_THRESHOLD         = _CFG["CONF_THRESHOLD"]
STABLE_FRAMES          = _CFG["STABLE_FRAMES"]
MIN_PLATE_W            = _CFG["MIN_PLATE_W"]
MAX_PLATE_W            = _CFG["MAX_PLATE_W"]
MIN_ASPECT             = _CFG["MIN_ASPECT"]
MAX_ASPECT             = _CFG["MAX_ASPECT"]
MAX_FRAME_FRAC         = _CFG["MAX_FRAME_FRAC"]
SKY_SKIP_FRAC          = _CFG["SKY_SKIP_FRAC"]
YOLO_INPUT_W           = _CFG["YOLO_INPUT_W"]
BLUR_THRESHOLD         = _CFG["BLUR_THRESHOLD"]
LOW_LIGHT_THRESHOLD    = _CFG["LOW_LIGHT_THRESHOLD"]
ENABLE_LOW_LIGHT_BOOST = _CFG["ENABLE_LOW_LIGHT_BOOST"]
MIN_OCR_CONF           = _CFG["MIN_OCR_CONF"]
SAME_PLATE_COOLDOWN    = _CFG["SAME_PLATE_COOLDOWN"]



_model        = None
_pytesseract  = None
_model_lock   = threading.Lock()

_TESSDATA = ""
for _p in [
    "/usr/share/tesseract-ocr/5/tessdata/",
    "/usr/share/tesseract-ocr/4.00/tessdata/",
    "/usr/share/tessdata/",
    "/usr/local/share/tessdata/",
]:
    if Path(_p).is_dir():
        _TESSDATA = _p
        break

_tess_line = None
_tess_raw  = None
_tess_lock = threading.Lock()


_clahe_ocr   = cv2.createCLAHE(clipLimit=3.5, tileGridSize=(4, 4))
_clahe_dark  = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(4, 4))
_clahe_glare = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
_clahe_plate = cv2.createCLAHE(clipLimit=3.5, tileGridSize=(3, 3))
_clahe_v6    = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(4, 4))
_clahe_sharp = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))

_USM_KERNEL = None  # built lazily

_sr_model      = None
_sr_model_lock = threading.Lock()
_SR_AVAILABLE  = False

def _load_sr_model():
    global _sr_model, _SR_AVAILABLE
    if _sr_model is not None:
        return _sr_model
    with _sr_model_lock:
        if _sr_model is not None:
            return _sr_model
        try:
            sr = cv2.dnn_superres.DnnSuperResImpl_create()
            model_path = BACKEND_DIR / "models" / "EDSR_x4.pb"
            if model_path.exists():
                sr.readModel(str(model_path))
                sr.setModel("edsr", 4)
                _sr_model = sr
                _SR_AVAILABLE = True
                logger.info("Super-resolution EDSR x4 loaded")
            else:
                logger.debug("EDSR_x4.pb not found — SR disabled (using LANCZOS fallback)")
        except Exception as exc:
            logger.debug(f"SR model load failed ({exc}) — using LANCZOS fallback")
    return _sr_model


_GAMMA_LUT: np.ndarray = np.array(
    [((i / 255.0) ** (1.0 / 1.8)) * 255 for i in range(256)], dtype="uint8"
)

_RE_STRIP_SPACE = re.compile(r"\s+")
_RE_STRIP_CHARS = re.compile(r"[^A-Z0-9\-]")
_RE_DASH_SPACE  = re.compile(r"[\-\s]")
_RE_ALL_DIGITS  = re.compile(r"^\d+$")
_RE_ALL_ALPHA   = re.compile(r"^[A-Z]+$")


_RE_YEAR_PURE  = re.compile(r"^([A-Z]{1,5})(\d{4})(\d{2})$")
_RE_YEAR_MIXED = re.compile(r"^([A-Z]{1,5}\d{1,6}[A-Z]?)(\d{2})$")
_RE_SIMPLE     = re.compile(r"^([A-Z]{1,5})(\d{1,6})$")
_RE_OCR_SPLIT  = re.compile(r"^([A-Z]{1,5})([0-9][A-Z0-9]*)$")

_RE_SCORE_PK_MIXED  = re.compile(r"^[A-Z]{1,5}\d{1,6}[A-Z]?-\d{2,4}$")
_RE_SCORE_YEAR      = re.compile(r"^[A-Z]{2,5}[0-9]+-[0-9]{2,4}$")
_RE_SCORE_DIPL      = re.compile(r"^[A-Z]{2,3}-[A-Z0-9]{2,4}$")

# PSM 7 = single line, PSM 8 = single word, PSM 11 = sparse, PSM 6 = block
_WL      = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
_TESS_7  = f"--oem 3 --psm 7  -c tessedit_char_whitelist={_WL}"
_TESS_8  = f"--oem 3 --psm 8  -c tessedit_char_whitelist={_WL}"
_TESS_11 = f"--oem 3 --psm 11 -c tessedit_char_whitelist={_WL}"
_TESS_6  = f"--oem 3 --psm 6  -c tessedit_char_whitelist={_WL}"
# OEM 1 = LSTM only — better on two-row plates
_TESS_11L = f"--oem 1 --psm 11 -c tessedit_char_whitelist={_WL}"
_TESS_6L  = f"--oem 1 --psm 6  -c tessedit_char_whitelist={_WL}"
_TESS_7L  = f"--oem 1 --psm 7  -c tessedit_char_whitelist={_WL}"
_TESS_13L = f"--oem 1 --psm 13 -c tessedit_char_whitelist={_WL}"

# letter<->digit fixmaps — conservative to avoid corrupting mixed prefixes
_DIGIT_FIXMAP: dict[str, str] = {
    "O": "0", "o": "0",
    "I": "1", "l": "1", "L": "1",
    "D": "0",
    "G": "6",
    "Q": "0",
    "Z": "2",
    "B": "8",
    "S": "5",
    "U": "0",
    "J": "1",
}
_ALPHA_FIXMAP_PURE: dict[str, str] = {
    "0": "O", "1": "I", "6": "G", "5": "S", "8": "B",
}
# mixed prefix: only 0->O, anything more corrupts real digits
_ALPHA_FIXMAP_MIXED: dict[str, str] = {"0": "O"}

_FIXABLE_ALPHA_DIGITS: frozenset[str] = frozenset(_ALPHA_FIXMAP_PURE.keys())



_BUFFER_SIZE       = max(STABLE_FRAMES * 3, 6)
_plate_buffer      : deque = deque(maxlen=_BUFFER_SIZE)
_plate_scores      : deque = deque(maxlen=_BUFFER_SIZE)
_last_stable       : str   = ""
_last_stable_time  : float = 0.0
_buffer_first_seen : float = 0.0
# 20s gives enough OCR reads before forcing a commit
_BUFFER_FLUSH_SECS : float = 20.0
# tolerate up to 6 consecutive missed frames before clearing (handles motion blur)
_NO_DET_CLEAR_FRAMES : int  = 6
_no_det_streak       : int  = 0
_pipeline_lock               = threading.Lock()
_last_lookup_cache : dict    = {}
_lookup_cache_lock           = threading.Lock()

_log_pool      = None
_log_pool_lock = threading.Lock()

_CROPS_DIR = BACKEND_DIR / "crops"
_CROPS_DIR.mkdir(parents=True, exist_ok=True)


def _save_crop(crop: np.ndarray, plate: str) -> str:
    try:
        safe = re.sub(r"[^A-Z0-9\-]", "", plate.upper())[:20]
        ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:20]
        name = f"{ts}_{safe}.jpg"
        path = _CROPS_DIR / name
        cv2.imwrite(str(path), crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return f"crops/{name}"
    except Exception as exc:
        logger.debug(f"[CROP] save failed: {exc}")
        return ""


def _get_log_pool():
    global _log_pool
    if _log_pool is not None:
        return _log_pool
    with _log_pool_lock:
        if _log_pool is None:
            from concurrent.futures import ThreadPoolExecutor
            _log_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="LogWriter")
    return _log_pool


def reset_pipeline_state() -> None:
    """clears buffer + caches, call after DB reconnect"""
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
    try:
        import plate_store as _ps
        _ps.clear_cache()
    except ImportError:
        pass



def get_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"YOLO model not found: {MODEL_PATH}")
        

        import torch as _torch
        import random
        import numpy as np
        
        _torch.manual_seed(42)
        np.random.seed(42)
        random.seed(42)
        
        _orig = _torch.load
        def _patched(f, *a, **kw):
            kw.setdefault("weights_only", False)
            return _orig(f, *a, **kw)
        _torch.load = _patched
        try:
            from ultralytics import YOLO
            m = YOLO(str(MODEL_PATH))
        finally:
            _torch.load = _orig
        m(np.zeros((YOLO_INPUT_W, YOLO_INPUT_W, 3), dtype=np.uint8), verbose=False)
        _model = m
        logger.info(f"YOLO loaded + warmed: {MODEL_PATH.name}")
    return _model


def _get_tess_apis():
    global _tess_line, _tess_raw
    if _tess_line is not None:
        return _tess_line, _tess_raw
    with _tess_lock:
        if _tess_line is not None:
            return _tess_line, _tess_raw
        if not _TESSEROCR_AVAILABLE:
            return None, None
        try:
            api_line = _PyTessBaseAPI(path=_TESSDATA, lang="eng",
                                       psm=_TessPSM.SINGLE_LINE, oem=_TessOEM.LSTM_ONLY)
            api_line.SetVariable("tessedit_char_whitelist", _WL)
            api_word = _PyTessBaseAPI(path=_TESSDATA, lang="eng",
                                       psm=_TessPSM.SINGLE_WORD, oem=_TessOEM.LSTM_ONLY)
            api_word.SetVariable("tessedit_char_whitelist", _WL)
            _tess_line = api_line
            _tess_raw  = api_word
            logger.info("tesserocr C++ APIs ready")
            return _tess_line, _tess_raw
        except Exception as exc:
            logger.warning(f"tesserocr init failed ({exc}) — using pytesseract")
            return None, None


def get_tesseract():
    global _pytesseract
    if _pytesseract is not None:
        return _pytesseract
    import pytesseract as _pt
    if os.name == "nt":
        for p in [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]:
            if Path(p).exists():
                _pt.pytesseract.tesseract_cmd = p
                break
    _pt.get_tesseract_version()
    _pytesseract = _pt
    return _pytesseract



def _is_valid_plate_region(x1: int, y1: int, x2: int, y2: int,
                             frame_w: int, frame_h: int) -> bool:
    w, h = x2 - x1, y2 - y1
    if h <= 0 or w <= 0:
        return False
    # sub-18px boxes are almost always road marking noise
    if h < 18:
        return False
    if w * h < 720:
        return False
    if not (MIN_PLATE_W <= w <= MAX_PLATE_W):
        return False
    aspect = w / h
    if not (MIN_ASPECT <= aspect <= MAX_ASPECT):
        return False
    if (w * h) / (frame_w * frame_h) > MAX_FRAME_FRAC:
        return False
    if y1 < int(frame_h * SKY_SKIP_FRAC):
        return False
    if x1 < 0 or x2 > frame_w:
        return False
    return True


def _iou(a: tuple, b: tuple) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter)


def _nms_boxes(boxes: list[tuple], iou_thresh: float = 0.45) -> list[tuple]:
    """standard NMS — returns boxes sorted by conf, overlaps removed"""
    if not boxes:
        return []
    boxes = sorted(boxes, key=lambda b: b[4], reverse=True)
    kept: list[tuple] = []
    suppressed = [False] * len(boxes)
    for i, box in enumerate(boxes):
        if suppressed[i]:
            continue
        kept.append(box)
        for j in range(i + 1, len(boxes)):
            if not suppressed[j] and _iou(box[:4], boxes[j][:4]) > iou_thresh:
                suppressed[j] = True
    return kept



def _fix_pure_alpha_zone(s: str) -> str:
    return "".join(_ALPHA_FIXMAP_PURE.get(c, c) for c in s.upper())


def _fix_mixed_alpha_zone(s: str) -> str:
    # only fixes 0->O in the leading alpha run, leaves digits alone
    s = s.upper()
    alpha_end = next((i for i, c in enumerate(s) if c.isdigit()), len(s))
    return "".join(_ALPHA_FIXMAP_MIXED.get(c, c) for c in s[:alpha_end]) + s[alpha_end:]


def _fix_digit_zone(s: str) -> str:
    return "".join(_DIGIT_FIXMAP.get(c, c) for c in s.upper())



def normalize_plate(text: str) -> str:
    """canonical form for DB/user input strings"""
    if not text:
        return ""
    text = _RE_STRIP_SPACE.sub("", text.upper().strip())
    text = _RE_STRIP_CHARS.sub("", text)
    if len(text) < 3:
        return ""
    if "-" not in text:
        m = _RE_YEAR_PURE.match(text)
        if m:
            return m.group(1) + m.group(2) + "-" + m.group(3)
        ms = _RE_SIMPLE.match(text)
        if ms and ms.group(1).isalpha():
            pre, suf = ms.group(1), ms.group(2)
            if len(suf) == 5:
                return pre + suf[:3] + "-" + suf[3:]
            if len(suf) == 6:
                return pre + suf[:4] + "-" + suf[4:]
            return pre + "-" + suf
        mk = _RE_YEAR_MIXED.match(text)
        if mk and len(mk.group(2)) == 2:
            return mk.group(1) + "-" + mk.group(2)
    return text


def _score_plate(norm: str) -> float:
    if not norm:
        return 0.0
    if "-" not in norm:
        if re.match(r"^[A-Z]{2,5}\d{3,6}$", norm):
            return 0.90
        if re.match(r"^[A-Z]{1}\d{3,6}$", norm):
            return 0.50
        return 0.30

    prefix, _, suffix = norm.partition("-")
    p_len, s_len = len(prefix), len(suffix)

    # try alpha-fix on digit-only prefix before scoring
    if not prefix.isalpha():
        fixed = _fix_pure_alpha_zone(prefix)
        if fixed.isalpha():
            prefix = fixed
            norm   = prefix + "-" + suffix

    if _RE_SCORE_PK_MIXED.match(norm) or _RE_SCORE_YEAR.match(norm):
        return 0.90

    if prefix.isalpha() and suffix.isdigit():
        if p_len == 1 and 3 <= s_len <= 4:
            return 0.50
        if p_len == 1 and 2 <= s_len <= 6:
            return 0.40
        if p_len == 1 and s_len == 1:
            return 0.10
        if 2 <= p_len <= 5 and 3 <= s_len <= 4:
            return 0.90
        if p_len >= 2 and s_len < 2:
            return 0.15
        if p_len <= 2 and s_len == 2:
            return 0.55
        if p_len <= 2 and s_len == 3:
            return 0.76
        if p_len <= 2 and s_len >= 4:
            return 0.76
        if 2 <= p_len <= 5 and s_len <= 6:
            return 0.80

    if _RE_SCORE_DIPL.match(norm):
        return 0.70

    return 0.42


def normalize_plate_ocr(text: str) -> str:
    """normalize raw tesseract output to a clean plate string"""
    if not text:
        return ""

    if "\n" in text:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        joined = "".join(lines)

        def _clean(s):
            return _RE_STRIP_CHARS.sub("", _RE_STRIP_SPACE.sub("", s.upper()))

        cands_ml = [joined]

        for i in range(len(lines)):
            for j in range(i + 2, min(i + 4, len(lines) + 1)):
                cands_ml.append("".join(lines[i:j]))


        for l in lines:
            cands_ml.append(l)

        if len(lines) >= 2:
            last_digits = re.sub(r"[^0-9]", "", lines[-1])
            if len(last_digits) >= 3:
                for header in lines[:-1]:
                    hc = re.sub(r"[^A-Z0-9]", "", header.upper())
                    am = re.match(r"^([A-Z]{2,5})", hc)
                    if not am:
                        continue
                    alpha = am.group(1)
                    ym = re.search(r"(\d{2})$", hc)
                    if ym:
                        cands_ml.append(alpha + last_digits + "-" + ym.group(1))
                    cands_ml.append(alpha + last_digits)
                    for trim in range(1, min(4, len(hc))):
                        tail = hc[trim:]
                        am2 = re.match(r"^([A-Z]{2,5})", tail)
                        if am2:
                            a2 = am2.group(1)
                            ym2 = re.search(r"(\d{2})$", tail)
                            if ym2:
                                cands_ml.append(a2 + last_digits + "-" + ym2.group(1))
                            cands_ml.append(a2 + last_digits)

        # Strip 1-3 leading garbage chars from the joined string
        joined_clean = _clean(joined)
        for trim in range(1, min(4, len(joined_clean))):
            cands_ml.append(joined_clean[trim:])

        # Strip trailing single alpha from any line before joining with next
        for i in range(len(lines) - 1):
            l1 = lines[i].rstrip()
            if l1 and l1[-1].isalpha() and len(l1) > 2:
                stripped = l1[:-1] + lines[i + 1]
                cands_ml.append(stripped)


        best_text, best_sc = "", 0.0
        for c in cands_ml:
            cc = _clean(c)
            if len(cc) < 5:
                continue
            sc_raw = _score_plate(cc)
            if sc_raw > best_sc:
                best_sc, best_text = sc_raw, cc
            n = normalize_plate(cc)
            sc_n = _score_plate(n) if n else 0.0
            if sc_n > best_sc:
                best_sc, best_text = sc_n, cc

        text = best_text if best_text else joined

    text = _RE_STRIP_SPACE.sub("", text.upper().strip())
    text = _RE_STRIP_CHARS.sub("", text)
    if len(text) < 5:
        return ""

    if not re.search(r"[A-Z]", text):
        for b in (2, 3):
            if b >= len(text):
                break
            lead = text[:b]
            if all(c in _FIXABLE_ALPHA_DIGITS for c in lead):
                fixed_lead = _fix_pure_alpha_zone(lead)
                if fixed_lead.isalpha():
                    text = fixed_lead + text[b:]
                    break
        if not re.search(r"[A-Z]", text):
            return ""

    # PSM11 sometimes picks up the line above the plate number
    if text[0].isdigit():
        first_alpha = next((i for i, c in enumerate(text) if c.isalpha()), -1)
        if first_alpha > 0:
            tail = text[first_alpha:]
            if re.search(r"[A-Z]", tail) and re.search(r"\d", tail):
                n_tail = normalize_plate_ocr(tail)
                if n_tail and _score_plate(n_tail) >= 0.72:
                    return n_tail

    # Must have both alpha and digit
    if not re.search(r"[A-Z]", text) or not re.search(r"\d", text):
        return ""
    if _RE_ALL_DIGITS.match(text):
        return ""

    if "-" in text:
        prefix, _, suffix = text.partition("-")
        if not suffix:
            return ""

        suffix = _fix_digit_zone(suffix)

        # Oversized suffix: drop leading digit if it improves score
        # e.g. 'LEC-11729' (OCR joins top border '1' with suffix '1729') -> 'LEC-1729'
        if len(suffix) > 4 and suffix[0].isdigit():
            alt_suffix = suffix[1:]
            alt_cand   = prefix + "-" + alt_suffix
            if _score_plate(alt_cand) > _score_plate(prefix + "-" + suffix):
                suffix = alt_suffix
        # try stripping 1-2 leading garbage chars off prefix
        prefix_candidates: list[str] = [prefix]
        if len(prefix) >= 3:
            prefix_candidates.append(prefix[1:])
        if len(prefix) >= 4:
            prefix_candidates.append(prefix[2:])

        best_result, best_sc, best_canonical = "", 0.0, 0
        for pfx in prefix_candidates:
            if not pfx:
                continue

            pure_alpha = _RE_ALL_ALPHA.match(pfx) is not None
            if not pure_alpha:
                bad = [c for c in pfx if c.isdigit() and c not in _FIXABLE_ALPHA_DIGITS]
                if not bad:
                        cand = _fix_pure_alpha_zone(pfx)
                        if cand.isalpha():
                            # only accept if prefix had >=2 real alpha chars
                            # prevents '10B'->IOB winning over correct 'MN10B'->MN108
                            real_alpha_in_orig = sum(1 for c in pfx if c.isalpha())
                            if real_alpha_in_orig >= 2:
                                pfx        = cand
                                pure_alpha = True
            fixed_pfx = _fix_pure_alpha_zone(pfx) if pure_alpha else _fix_mixed_alpha_zone(pfx)
            candidate = fixed_pfx + "-" + suffix
            if len(candidate) < 4:
                continue
            sc = _score_plate(candidate)
            # pure-alpha 2-3 char prefix is canonical; mixed is never canonical
            pfx_canonical = 1 if (fixed_pfx.isalpha() and 2 <= len(fixed_pfx) <= 3) else 0
            # tiebreak: prefer longer (fewer stripped chars = more trustworthy)
            if (sc > best_sc) or (sc == best_sc and pfx_canonical > best_canonical) or \
               (sc == best_sc and pfx_canonical == best_canonical and len(candidate) > len(best_result)):
                best_sc, best_result, best_canonical = sc, candidate, pfx_canonical

            if not pure_alpha:
                alpha_end = next((i for i, c in enumerate(fixed_pfx) if c.isdigit()), len(fixed_pfx))
                if alpha_end > 0 and alpha_end < len(fixed_pfx):
                    digit_fixed = fixed_pfx[:alpha_end] + _fix_digit_zone(fixed_pfx[alpha_end:])
                    if digit_fixed != fixed_pfx:
                        cand2 = digit_fixed + "-" + suffix
                        sc2   = _score_plate(cand2)
                        pfx2_canonical = 1 if (digit_fixed.isalpha() and 2 <= len(digit_fixed) <= 3) else 0
                        if (sc2 > best_sc) or (sc2 == best_sc and pfx2_canonical > best_canonical) or \
                           (sc2 == best_sc and pfx2_canonical == best_canonical and len(cand2) > len(best_result)):
                            best_sc, best_result, best_canonical = sc2, cand2, pfx2_canonical


                _DIGIT_LOOKALIKE_CHARS = set(_DIGIT_FIXMAP.keys()) | set('0123456789')
                strict_alpha_end = next(
                    (i for i, c in enumerate(pfx) if c in _DIGIT_LOOKALIKE_CHARS), len(pfx)
                )
                if strict_alpha_end >= 2 and strict_alpha_end < len(pfx):
                    strict_fixed = pfx[:strict_alpha_end] + _fix_digit_zone(pfx[strict_alpha_end:])
                    has_digit_fixed = alpha_end > 0 and alpha_end < len(fixed_pfx)
                    if strict_fixed != fixed_pfx and (not has_digit_fixed or strict_fixed != digit_fixed):
                        cand3 = strict_fixed + "-" + suffix
                        sc3   = _score_plate(cand3)
                        pfx3_canonical = 1 if (strict_fixed.isalpha() and 2 <= len(strict_fixed) <= 3) else 0
                        if (sc3 > best_sc) or (sc3 == best_sc and pfx3_canonical > best_canonical) or \
                           (sc3 == best_sc and pfx3_canonical == best_canonical and len(cand3) > len(best_result)):
                            best_sc, best_result, best_canonical = sc3, cand3, pfx3_canonical

        # If the best candidate scores poorly (<0.85) or is a mixed-prefix candidate
        # that lost alpha chars through stripping, fall back to the raw cleaned string.
        # Scores of 0.80 from a truncated/garbled prefix (e.g. 'SISOO-12','MNIOB-16')
        # look "good" but produce strings plate_store edit1 cannot recover.
        # The raw string (e.g. 'BRS1S00-12') feeds directly into edit1 and succeeds.
        raw_fallback = prefix + "-" + suffix
        if best_sc < 0.90 and len(raw_fallback) >= 5:

            best_alpha = sum(1 for c in best_result if c.isalpha() and c != '-')
            raw_alpha  = sum(1 for c in raw_fallback if c.isalpha() and c != '-')
            raw_pfx, _, raw_sfx = raw_fallback.partition("-")
            fixed_raw_pfx = _fix_pure_alpha_zone(raw_pfx) if _RE_ALL_ALPHA.match(
                _fix_pure_alpha_zone(raw_pfx)) else _fix_mixed_alpha_zone(raw_pfx)
            fixed_raw_sfx = _fix_digit_zone(raw_sfx)
            fixed_raw_fallback = fixed_raw_pfx + "-" + fixed_raw_sfx
            if raw_alpha > best_alpha:
                return fixed_raw_fallback
        return best_result if len(best_result) >= 5 else (raw_fallback if len(raw_fallback) >= 5 else "")

    n = len(text)
    candidates: list[str] = []

    for b in (2, 3, 4):
        if b <= 0 or b >= n:
            continue
        raw_pre = text[:b]
        raw_suf = text[b:]

        if any(c.isdigit() and c not in _FIXABLE_ALPHA_DIGITS for c in raw_pre):
            continue
        if raw_suf and raw_suf[0].isalpha():
            continue

        if b == 2 and any(c.isalpha() for c in raw_pre) and \
                any(c in _FIXABLE_ALPHA_DIGITS for c in raw_pre):
            continue

        fixed_pre = _fix_pure_alpha_zone(raw_pre)
        if not fixed_pre.isalpha():
            continue
        fixed_suf = _fix_digit_zone(raw_suf)
        if not fixed_suf.isdigit():
            continue

        suf_len = len(fixed_suf)
        if suf_len == 6:
            candidates.append(fixed_pre + fixed_suf[:4] + "-" + fixed_suf[4:])
        elif suf_len == 5:
            candidates.append(fixed_pre + fixed_suf[:3] + "-" + fixed_suf[3:])
        elif suf_len >= 3:
            candidates.append(fixed_pre + "-" + fixed_suf)
        elif suf_len == 2 and len(fixed_pre) <= 2:
            candidates.append(fixed_pre + "-" + fixed_suf)

    if n >= 6:
        yr   = text[n - 2:]
        body = text[:n - 2]
        if yr.isdigit() and body and body[0].isalpha():
            fixed_body = _fix_mixed_alpha_zone(body)
            alpha_cnt  = sum(1 for c in fixed_body if c.isalpha())
            if alpha_cnt >= 2 and re.search(r"\d", fixed_body):
                candidates.append(fixed_body + "-" + yr)

    if len(text) >= 6 and text[0].isalpha():
        tail = text[1:]
        tn   = len(tail)

        for b in (2, 3, 4):
            if b <= 0 or b >= tn: continue
            raw_pre = tail[:b]
            raw_suf = tail[b:]
            if any(c.isdigit() and c not in _FIXABLE_ALPHA_DIGITS for c in raw_pre): continue
            if raw_suf and raw_suf[0].isalpha(): continue
            fixed_pre = _fix_pure_alpha_zone(raw_pre)
            if not fixed_pre.isalpha(): continue
            fixed_suf = _fix_digit_zone(raw_suf)
            if not fixed_suf.isdigit(): continue
            suf_len = len(fixed_suf)
            if suf_len == 6:
                candidates.append(fixed_pre + fixed_suf[:4] + "-" + fixed_suf[4:])
            elif suf_len == 5:
                candidates.append(fixed_pre + fixed_suf[:3] + "-" + fixed_suf[3:])
            elif suf_len >= 3:
                candidates.append(fixed_pre + "-" + fixed_suf)
        if tn >= 6:
            yr_t   = tail[tn - 2:]
            body_t = tail[:tn - 2]
            if yr_t.isdigit() and body_t and body_t[0].isalpha():
                fixed_body_t = _fix_mixed_alpha_zone(body_t)
                alpha_cnt_t  = sum(1 for c in fixed_body_t if c.isalpha())
                if alpha_cnt_t >= 2 and re.search(r"\d", fixed_body_t):
                    candidates.append(fixed_body_t + "-" + yr_t)

    m_pure = _RE_YEAR_PURE.match(text)
    if m_pure:
        candidates.append(
            _fix_pure_alpha_zone(m_pure.group(1))
            + _fix_digit_zone(m_pure.group(2))
            + "-" + _fix_digit_zone(m_pure.group(3))
        )

    m_mixed = _RE_YEAR_MIXED.match(text)
    if m_mixed and len(m_mixed.group(2)) == 2:
        pp = m_mixed.group(1)
        if sum(1 for c in pp if c.isalpha()) >= 2 and re.search(r"\d", pp):
            candidates.append(_fix_mixed_alpha_zone(pp) + "-" + m_mixed.group(2))

    m_ocr = _RE_OCR_SPLIT.match(text)
    if m_ocr:
        candidates.append(
            _fix_pure_alpha_zone(m_ocr.group(1)) + "-" + _fix_digit_zone(m_ocr.group(2))
        )

    if candidates:
        def _key(c: str):
            sc = _score_plate(c)
            pre, _, suf = c.partition("-")
            pure_alpha_pre = pre.isalpha()
            if pure_alpha_pre:
                canonical = 1 if 2 <= len(pre) <= 4 else 0
            else:
                alpha_run = len(pre) - len(pre.lstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZ'))
                canonical = 1 if 2 <= alpha_run <= 4 else 0
            short_pref = -len(pre) if not canonical else 0
            return (sc, canonical, short_pref)
        best = max(candidates, key=_key)
        return best if len(best) >= 5 else ""

    return normalize_plate(text)



def _unsharp_mask(gray: np.ndarray, amount: float = 1.5, radius: int = 1) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (2 * radius + 1, 2 * radius + 1), 0)
    sharpened = cv2.addWeighted(gray, 1.0 + amount, blurred, -amount, 0)
    return sharpened


def _remove_plate_border(gray: np.ndarray) -> np.ndarray:
    """strip the plate border frame — tesseract reads it as I/1/- otherwise"""
    h, w = gray.shape[:2]
    if h < 20 or w < 60:
        return gray

    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    row_sum = bw.sum(axis=1) / 255.0
    col_sum = bw.sum(axis=0) / 255.0

    # 45% threshold catches thin borders without catching char strokes
    row_dense = row_sum > (w * 0.45)
    col_dense = col_sum > (h * 0.45)

    top_crop = bot_crop = 0
    for i in range(min(6, h // 4)):
        if row_dense[i]:
            top_crop = i + 1
    for i in range(h - 1, max(h - 7, h // 2), -1):
        if row_dense[i]:
            bot_crop = h - i

    left_crop = right_crop = 0
    for i in range(min(6, w // 4)):
        if col_dense[i]:
            left_crop = i + 1
    for i in range(w - 1, max(w - 7, w // 2), -1):
        if col_dense[i]:
            right_crop = w - i

    y1 = top_crop
    y2 = h - bot_crop
    x1 = left_crop
    x2 = w - right_crop
    if y2 - y1 < h * 0.5 or x2 - x1 < w * 0.5:
        return gray  # border detection went wrong — safe fallback
    if y1 >= y2 or x1 >= x2:
        return gray
    return gray[y1:y2, x1:x2]


def _angle_aware_pad(crop: np.ndarray, yolo_conf: float = 1.0) -> np.ndarray:
    # extra padding for low-confidence boxes that tend to clip plate edges
    if crop is None or crop.size == 0:
        return crop
    h, w = crop.shape[:2]
    extra = max(0.0, (1.0 - yolo_conf) * 0.15)
    px = int(w * extra)
    py = int(h * extra)
    if px == 0 and py == 0:
        return crop
    return cv2.copyMakeBorder(crop, py, py, px, px,
                              cv2.BORDER_REPLICATE)


def _is_image_blurry(image: np.ndarray, threshold: float = 40.0) -> tuple[bool, float]:
    if image is None or image.size == 0:
        return True, 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    var  = cv2.Laplacian(gray, cv2.CV_64F).var()
    # large plates have lower laplacian variance per-pixel, scale threshold
    h, w = gray.shape[:2]
    area = w * h
    if area > 100_000:
        adjusted = threshold * (100_000 / area) ** 0.5
    else:
        adjusted = threshold
    return bool(var < adjusted), float(var)


def _detect_low_light(image: np.ndarray) -> tuple[bool, float]:
    if image is None or image.size == 0:
        return False, 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    br   = float(np.mean(gray))
    return br < LOW_LIGHT_THRESHOLD, br


def _enhance_low_light(image: np.ndarray) -> np.ndarray:
    if image is None or image.size == 0:
        return image
    gray      = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    brightened = cv2.LUT(gray, _GAMMA_LUT)
    enhanced   = _clahe_dark.apply(brightened)
    return cv2.bilateralFilter(enhanced, 5, 55, 55)


def _suppress_glare(image: np.ndarray) -> np.ndarray:
    if image is None or image.size == 0 or image.ndim != 3:
        return image
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    if float(v.std()) > 60.0:
        v   = _clahe_glare.apply(v)
        hsv = cv2.merge([h, s, v])
        return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return image


def _deskew_plate(gray: np.ndarray) -> np.ndarray:
    """deskew up to 35deg using minAreaRect, cross-checked with Hough"""
    _, t_dark  = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    _, t_light = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY     + cv2.THRESH_OTSU)
    binary = t_dark if cv2.countNonZero(t_dark) >= cv2.countNonZero(t_light) else t_light

    coords = np.column_stack(np.where(binary > 0))
    if len(coords) < 20:
        return gray
    angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 0.5 or abs(angle) > 35:
        return gray
    # cross-check with Hough for borderline angles; use whichever agrees with text rows
    if abs(angle) > 15:
        try:
            edges_h = cv2.Canny(binary, 50, 150)
            lines = cv2.HoughLinesP(edges_h, 1, math.pi / 180, threshold=40,
                                    minLineLength=int(gray.shape[1] * 0.25),
                                    maxLineGap=int(gray.shape[1] * 0.1))
            if lines is not None and len(lines) >= 3:
                angles_h = []
                for ln in lines:
                    x1h, y1h, x2h, y2h = ln[0]
                    if x2h != x1h:
                        a = math.degrees(math.atan2(y2h - y1h, x2h - x1h))
                        if abs(a) <= 35:
                            angles_h.append(a)
                if len(angles_h) >= 3:
                    angles_h.sort()
                    hough_angle = angles_h[len(angles_h) // 2]  # median
                    if abs(hough_angle - angle) > 8:
                        angle = hough_angle
        except Exception:
            pass
    h, w = gray.shape

    rad = math.radians(abs(angle))
    new_w = int(w * math.cos(rad) + h * math.sin(rad)) + 2
    new_h = int(w * math.sin(rad) + h * math.cos(rad)) + 2
    cx, cy = new_w / 2, new_h / 2
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)

    M[0, 2] += cx - w / 2
    M[1, 2] += cy - h / 2
    return cv2.warpAffine(gray, M, (new_w, new_h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def _dewarp_plate(crop: np.ndarray) -> np.ndarray:
    """perspective correction — finds largest 4-pt contour and warps flat"""
    h, w = crop.shape[:2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop.copy()
    enhanced = _clahe_plate.apply(gray)

    med = float(np.median(enhanced))
    lo  = max(10, int(0.5 * med))
    hi  = min(250, int(1.5 * med))
    edges = cv2.Canny(enhanced, lo, hi)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, kernel, iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return crop

    plate_area = w * h
    best_quad = None
    best_area = 0

    for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.04 * peri, True)
        if len(approx) != 4:
            continue
        area = cv2.contourArea(approx)
        if area < 0.15 * plate_area or area > 0.98 * plate_area:
            continue
        if area > best_area:
            best_area = area
            best_quad = approx

    if best_quad is None:
        return crop

    pts = best_quad.reshape(4, 2).astype(np.float32)
    s   = pts.sum(axis=1)
    d   = np.diff(pts, axis=1).flatten()
    ordered = np.array([
        pts[np.argmin(s)],
        pts[np.argmin(d)],
        pts[np.argmax(s)],
        pts[np.argmax(d)],
    ], dtype=np.float32)

    tl, tr, br, bl = ordered
    top_w    = float(np.linalg.norm(tr - tl))
    bot_w    = float(np.linalg.norm(br - bl))
    left_h   = float(np.linalg.norm(bl - tl))
    right_h  = float(np.linalg.norm(br - tr))
    dst_w    = max(int((top_w + bot_w) / 2), w, 300)
    dst_h    = max(int((left_h + right_h) / 2), h, 60)
    # reject if aspect is outside plate range — quad is probably noise
    quad_aspect = dst_w / max(dst_h, 1)
    if not (2.0 <= quad_aspect <= 7.0):
        return crop

    dst = np.array([[0, 0], [dst_w - 1, 0],
                    [dst_w - 1, dst_h - 1], [0, dst_h - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(ordered, dst)
    warped = cv2.warpPerspective(crop, M, (dst_w, dst_h),
                                 flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)

    # only reject on large brightness shift; std check removed (valid warps increase contrast)
    orig_mean = float(gray.mean())
    warp_gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY) if warped.ndim == 3 else warped
    warp_mean = float(warp_gray.mean())
    if orig_mean > 0 and abs(warp_mean / orig_mean - 1.0) > 0.40:
        return crop

    return warped



def _prepare_crop(crop: np.ndarray, skip_dewarp: bool = False) -> tuple[PILImage.Image, ...]:
    """preprocess crop and return 7 PIL variants for OCR voting"""
    if not skip_dewarp:
        crop = _dewarp_plate(crop)
    h, w = crop.shape[:2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop.copy()

    TARGET = 600
    is_dark = float(gray.mean()) < 60.0

    sr = _load_sr_model()
    if _SR_AVAILABLE and sr is not None and w < 300:
        bgr_in = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        try:
            bgr_up = sr.upsample(bgr_in)
            gray   = cv2.cvtColor(bgr_up, cv2.COLOR_BGR2GRAY)
        except Exception:
            gray = cv2.resize(gray, (w * 4, h * 4), interpolation=cv2.INTER_LANCZOS4)
        apply_bilateral = False
    elif w < 400 or is_dark:
        mult = max(2, -(-TARGET // w))
        mult = min(mult, 4)
        gray = cv2.resize(gray, (w * mult, h * mult), interpolation=cv2.INTER_LANCZOS4)
        apply_bilateral = False
    elif w < TARGET:
        gray = cv2.resize(gray, (TARGET, int(h * TARGET / w)), interpolation=cv2.INTER_LANCZOS4)
        apply_bilateral = True
    elif w > TARGET:
        gray = cv2.resize(gray, (TARGET, int(h * TARGET / w)), interpolation=cv2.INTER_AREA)
        apply_bilateral = True
    else:
        apply_bilateral = True

    if apply_bilateral and float(gray.mean()) >= 80.0:
        gray = cv2.bilateralFilter(gray, 5, 45, 45)

    # skip USM on very dark images — amplifies noise
    if float(gray.mean()) >= 50.0:
        gray = _unsharp_mask(gray, amount=1.2, radius=1)

    gray = _remove_plate_border(gray)
    gray = _deskew_plate(gray)

    eq = _clahe_ocr.apply(gray)
    _, bin_otsu = cv2.threshold(eq, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    adaptive = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 9
    )
    morph_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    morph = cv2.morphologyEx(bin_otsu, cv2.MORPH_OPEN, morph_kernel)
    morph = cv2.morphologyEx(morph, cv2.MORPH_CLOSE, morph_kernel)
    eq4 = _clahe_v6.apply(gray)

    return (
        PILImage.fromarray(gray),           # v1 — raw gray
        PILImage.fromarray(cv2.bitwise_not(gray)),  # v2 — inverted
        PILImage.fromarray(eq),             # v3 — CLAHE
        PILImage.fromarray(bin_otsu),       # v4 — Otsu binary
        PILImage.fromarray(adaptive),       # v5 — adaptive threshold
        PILImage.fromarray(eq4),            # v6 — strong CLAHE
        PILImage.fromarray(morph),          # v7 — morph-cleaned Otsu (noise dots removed)
    )



def _ocr_via_tesserocr(v1, v2, v3, v4, v5=None, v6=None, v7=None) -> str:
    """Run tesserocr C++ API — early exit at 0.80+ to cut latency by ~60%."""
    api_line, api_word = _get_tess_apis()
    if api_line is None:
        return ""
    # Priority order: best variants first so we hit 0.80 fast and skip the rest
    priority = [pil for pil in (v5, v1, v3, v7, v4, v2, v6) if pil is not None]
    best_raw, best_sc = "", 0.0
    with _tess_lock:
        for pil in priority:
            api_line.SetImage(pil)
            r = api_line.GetUTF8Text().strip()
            if r and len(r) >= 3:
                n = normalize_plate_ocr(r)
                s = _score_plate(n) if n else 0.0
                if s > best_sc:
                    best_sc, best_raw = s, r
                    if best_sc >= 0.80:   # exit early — good enough
                        return best_raw

        if best_sc < 0.80:
            for pil in (v5, v1, v2, v6, v7):
                if pil is None:
                    continue
                api_word.SetImage(pil)
                r = api_word.GetUTF8Text().strip()
                if r and len(r) >= 3:
                    n = normalize_plate_ocr(r)
                    s = _score_plate(n) if n else 0.0
                    if s > best_sc:
                        best_sc, best_raw = s, r
                        if best_sc >= 0.80:
                            return best_raw
    return best_raw


def _ocr_via_pytesseract(v1, v2, v3, v4, v5=None, v6=None, v7=None) -> str:
    # PSM6 first (block mode), exit at 0.90+ so PSM11 can't block a better PSM6 result
    try:
        tess = get_tesseract()
    except Exception:
        return ""
    best_raw, best_sc = "", 0.0
    combos = [
        (v1, _TESS_6L),
        (v3, _TESS_6L),
        (v5, _TESS_6L),
        (v6, _TESS_6L),
        (v1, _TESS_11L),
        (v3, _TESS_11L),
        (v5, _TESS_11L),
        (v4, _TESS_11L),
        (v1, _TESS_7L),
        (v3, _TESS_7L),
        (v5, _TESS_7),
        (v1, _TESS_7),
        (v2, _TESS_7),
        (v3, _TESS_11),
        (v1, _TESS_6),
        (v3, _TESS_7),
        (v3, _TESS_6),
        (v6, _TESS_6),
        (v6, _TESS_7),
        (v5, _TESS_8),
        (v4, _TESS_7),
        (v4, _TESS_11),
        (v2, _TESS_6),
        (v7, _TESS_7L),
        (v7, _TESS_6L),
    ]
    for pil, cfg in combos:
        if pil is None:
            continue
        try:
            r = str(tess.image_to_string(pil, config=cfg)).strip()
            if r and len(r) >= 3:
                n = normalize_plate_ocr(r)
                s = _score_plate(n) if n else 0.0
                if s > best_sc:
                    best_sc, best_raw = s, r
                    if best_sc >= 0.90:   # only exit at 0.90+ — don't short-circuit on 0.80
                        return best_raw
        except Exception:
            continue
    return best_raw


def read_plate_ocr(crop: np.ndarray) -> tuple[str, float, str]:
    """run full OCR on a crop; returns (norm_plate, conf, raw) or empty triple"""
    if crop is None or crop.size == 0:
        return "", 0.0, ""

    is_blurry, blur_var = _is_image_blurry(crop, threshold=BLUR_THRESHOLD)
    if is_blurry:
        logger.debug(f"[OCR SKIP] blur={blur_var:.1f}")
        return "", 0.0, ""

    crops_to_try = []
    glare_crop = _suppress_glare(crop)
    crops_to_try.append(glare_crop)
    # also try raw crop — glare suppression can hurt borderline-contrast plates
    if not (glare_crop is crop or (hasattr(glare_crop, 'shape') and
            glare_crop.shape == crop.shape and
            (glare_crop == crop).all())):
        crops_to_try.append(crop)
    if ENABLE_LOW_LIGHT_BOOST:
        is_dark, _ = _detect_low_light(crop)
        if is_dark:
            enhanced = _enhance_low_light(crop)
            if enhanced.ndim == 2:
                enhanced = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
            crops_to_try.append(_suppress_glare(enhanced))

    best_raw, best_sc = "", 0.0
    for crop_v in crops_to_try:
        v1, v2, v3, v4, v5, v6, v7 = _prepare_crop(crop_v)
        if _TESSEROCR_AVAILABLE:
            r = _ocr_via_tesserocr(v1, v2, v3, v4, v5, v6, v7)
            if not r:
                r = _ocr_via_pytesseract(v1, v2, v3, v4, v5, v6, v7)
        else:
            r = _ocr_via_pytesseract(v1, v2, v3, v4, v5, v6, v7)
        if r:
            n  = normalize_plate_ocr(r)
            sc = _score_plate(n) if n else 0.0
            if sc > best_sc:
                best_sc, best_raw = sc, r
        if best_sc >= 0.90:
            break

    # retry without dewarp — covers false-quad distortion and wrong-but-passing dewarped results
    if best_sc < 0.90:
        v1, v2, v3, v4, v5, v6, v7 = _prepare_crop(crop, skip_dewarp=True)
        if _TESSEROCR_AVAILABLE:
            r = _ocr_via_tesserocr(v1, v2, v3, v4, v5, v6, v7)
            if not r:
                r = _ocr_via_pytesseract(v1, v2, v3, v4, v5, v6, v7)
        else:
            r = _ocr_via_pytesseract(v1, v2, v3, v4, v5, v6, v7)
        if r:
            n  = normalize_plate_ocr(r)
            sc = _score_plate(n) if n else 0.0
            if sc > best_sc:
                best_sc, best_raw = sc, r

    # narrow crop retry — 6x upscale for small/distant plates
    if best_sc < 0.90:
        g_check = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        if g_check.shape[1] < 150:
            h0, w0 = g_check.shape[:2]
            up6 = cv2.resize(g_check, (w0 * 6, h0 * 6), interpolation=cv2.INTER_LANCZOS4)
            up6 = _deskew_plate(up6)
            eq6  = _clahe_ocr.apply(up6)
            adapt6 = cv2.adaptiveThreshold(
                up6, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 9
            )
            _, ot6 = cv2.threshold(eq6, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            pil_g6  = PILImage.fromarray(up6)
            pil_a6  = PILImage.fromarray(adapt6)
            pil_e6  = PILImage.fromarray(eq6)
            pil_o6  = PILImage.fromarray(ot6)
            r6 = _ocr_via_pytesseract(pil_g6, PILImage.fromarray(cv2.bitwise_not(up6)),
                                       pil_e6, pil_o6, v5=pil_a6, v6=pil_e6)
            if r6:
                n6  = normalize_plate_ocr(r6)
                sc6 = _score_plate(n6) if n6 else 0.0
                if sc6 > best_sc:
                    best_sc, best_raw = sc6, r6

    # medium-crop retry at 900px (tesseract sweet spot)
    if best_sc < 0.90:
        g_med = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        w_med = g_med.shape[1]
        if 100 <= w_med <= 260:
            TARGET_W = 900
            mult_m = TARGET_W // w_med
            hm, wm = g_med.shape[:2]
            up_m = cv2.resize(g_med, (wm * mult_m, hm * mult_m), interpolation=cv2.INTER_LANCZOS4)
            up_m = _deskew_plate(up_m)
            eq_m   = _clahe_ocr.apply(up_m)
            eq4_m  = _clahe_v6.apply(up_m)
            _, ot_m = cv2.threshold(eq_m, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            adapt_m = cv2.adaptiveThreshold(
                up_m, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 9
            )
            pm1 = PILImage.fromarray(up_m)
            pm2 = PILImage.fromarray(cv2.bitwise_not(up_m))
            pm3 = PILImage.fromarray(eq_m)
            pm4 = PILImage.fromarray(ot_m)
            pm5 = PILImage.fromarray(adapt_m)
            pm6 = PILImage.fromarray(eq4_m)
            rm = _ocr_via_pytesseract(pm1, pm2, pm3, pm4, v5=pm5, v6=pm6)
            if rm:
                nm  = normalize_plate_ocr(rm)
                scm = _score_plate(nm) if nm else 0.0
                if scm > best_sc:
                    best_sc, best_raw = scm, rm

    # Dark plate retry: if score < 0.90 and image is dark, try again at exactly 4x upscale
    # bypassing _prepare_crop's own upscale logic (to avoid double-scaling).
    # Handles plates like Car_022 (408px dark) where 2x=816px misses suffix digits.
    if best_sc < 0.90:
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        if float(g.mean()) < 60.0:
            h0, w0 = g.shape[:2]
            up3 = cv2.resize(g, (w0 * 4, h0 * 4), interpolation=cv2.INTER_LANCZOS4)
            up3 = _deskew_plate(up3)
            eq3  = _clahe_ocr.apply(up3)
            eq4  = _clahe_dark.apply(up3)
            _, ot3 = cv2.threshold(eq3, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            pv1 = PILImage.fromarray(up3)
            pv2 = PILImage.fromarray(cv2.bitwise_not(up3))
            pv3 = PILImage.fromarray(eq3)
            pv4 = PILImage.fromarray(ot3)
            pv6 = PILImage.fromarray(eq4)
            r = _ocr_via_pytesseract(pv1, pv2, pv3, pv4, v5=None, v6=pv6)
            if r and _score_plate(normalize_plate_ocr(r)) <= best_sc:

                try:
                    tess = get_tesseract()
                    r11 = str(tess.image_to_string(pv6, config=_TESS_11)).strip()
                    if r11:
                        n11 = normalize_plate_ocr(r11)
                        s11 = _score_plate(n11) if n11 else 0.0
                        if s11 > _score_plate(normalize_plate_ocr(r) if r else ""):
                            r = r11
                except Exception:
                    pass
            if r:
                n  = normalize_plate_ocr(r)
                sc = _score_plate(n) if n else 0.0
                if sc > best_sc:
                    best_sc, best_raw = sc, r

    # rotation sweep — for plates where deskew angle detection failed
    if best_sc < 0.90:
        g_rot = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        h_r, w_r = g_rot.shape[:2]
        for sweep_deg in (-15, -10, -5, 5, 10, 15):
            rad_s = math.radians(sweep_deg)
            nw = int(w_r * math.cos(abs(rad_s)) + h_r * math.sin(abs(rad_s))) + 2
            nh = int(w_r * math.sin(abs(rad_s)) + h_r * math.cos(abs(rad_s))) + 2
            M_s = cv2.getRotationMatrix2D((w_r / 2, h_r / 2), sweep_deg, 1.0)
            M_s[0, 2] += nw / 2 - w_r / 2
            M_s[1, 2] += nh / 2 - h_r / 2
            rotated = cv2.warpAffine(g_rot, M_s, (nw, nh),
                                     flags=cv2.INTER_CUBIC,
                                     borderMode=cv2.BORDER_REPLICATE)
            rot_bgr = cv2.cvtColor(rotated, cv2.COLOR_GRAY2BGR)
            v1r, v2r, v3r, v4r, v5r, v6r, v7r = _prepare_crop(rot_bgr, skip_dewarp=True)
            r_rot = _ocr_via_pytesseract(v1r, v2r, v3r, v4r, v5r, v6r, v7r)
            if r_rot:
                n_rot = normalize_plate_ocr(r_rot)
                sc_rot = _score_plate(n_rot) if n_rot else 0.0
                if sc_rot > best_sc:
                    best_sc, best_raw = sc_rot, r_rot
            if best_sc >= 0.90:
                break

    if not best_raw:
        return "", 0.0, ""

    norm = normalize_plate_ocr(best_raw)
    if not norm:
        return "", 0.0, ""

    norm = re.sub(r'^[^A-Z0-9]+', '', norm)
    norm = re.sub(r'[^A-Z0-9\-]+$', '', norm)
    if not norm or len(norm) < 5:
        return "", 0.0, ""

    conf = _score_plate(norm)
    if conf < MIN_OCR_CONF:
        try:
            import plate_store as _ps
            if _ps.is_loaded():
                _veh, _, _mt = _ps.lookup(norm)
                if _veh is not None and _mt == "exact":
                    with _pipeline_lock:
                        _vote_count = sum(1 for t, _ in _plate_scores if t == norm)
                    if _vote_count >= 2:
                        conf = max(conf, MIN_OCR_CONF)  # needs 2 votes to promote
                    else:
                        logger.debug(
                            f"[OCR PROMOTE BLOCKED] norm='{norm}' votes={_vote_count} < 2 "
                            f"— single-frame noise suppressed"
                        )
        except Exception:
            pass
    if conf < MIN_OCR_CONF:
        logger.debug(f"[OCR REJECT] norm='{norm}' conf={conf:.2f} < MIN={MIN_OCR_CONF}")
        return "", 0.0, ""

    logger.debug(f"[OCR] raw='{best_raw}' -> '{norm}' conf={conf:.2f}")
    return norm, conf, best_raw



def _to_str(val: object) -> str:
    return "" if val is None else str(val)


def _compute_auth_status(vehicle: dict) -> str:
    raw = vehicle.get("is_authorized", 0)
    if isinstance(raw, (bytes, bytearray)):
        raw = int.from_bytes(raw, "little")
    if bool(int(raw) if not isinstance(raw, bool) else raw):
        return "authorized"
    dues   = _to_str(vehicle.get("dues")).strip().lower()
    status = _to_str(vehicle.get("status")).strip().lower()  # 'Authorized'/'Unauthorized' in DB
    if dues in ("clear", "paid", "", "none") and status in ("authorized", "active"):
        return "authorized"
    return "unauthorized"


def lookup_plate(conn, plate: str, raw_ocr: str = "") -> tuple[dict | None, str, str]:
    """plate_store lookup first, falls back to direct DB with 4-step cascade"""
    if not plate or len(plate) < 5:
        return None, "unauthorized", "no_text"

    try:
        import plate_store as _ps
        if _ps.is_loaded():
            vehicle, _, match_type = _ps.lookup(plate)
            if vehicle is not None:
                status = _compute_auth_status(vehicle)
                logger.info(f"[LOOKUP] '{plate}' -> {status.upper()} ({match_type}) [store]")
                return vehicle, status, match_type
            # plate_store's edit1 can recover double-confusion cases
            # that normalize_plate_ocr mangled into garbage
            if raw_ocr and raw_ocr != plate and len(raw_ocr) >= 4:
                clean_raw = _RE_STRIP_CHARS.sub("", _RE_STRIP_SPACE.sub("", raw_ocr.upper()))
                if "-" not in clean_raw and "-" in plate:
                    dash_pos = len(plate.split("-")[0])
                    if dash_pos < len(clean_raw):
                        clean_raw = clean_raw[:dash_pos] + "-" + clean_raw[dash_pos:]
                if clean_raw != plate and len(clean_raw) >= 4:
                    vehicle2, _, match_type2 = _ps.lookup(clean_raw)
                    if vehicle2 is not None:
                        status2 = _compute_auth_status(vehicle2)
                        logger.info(f"[LOOKUP] '{plate}' raw_fallback='{clean_raw}' -> {status2.upper()} ({match_type2}) [store]")
                        return vehicle2, status2, match_type2
            logger.info(f"[LOOKUP] '{plate}' -> NOT FOUND [store size={_ps.size()}]")
            return None, "unauthorized", "not_found"
    except Exception as exc:
        logger.warning(f"[LOOKUP] plate_store error: {exc} — DB fallback")

    if conn is None:
        return None, "unauthorized", "no_db"

    cur   = conn.cursor(dictionary=True)
    clean = _RE_DASH_SPACE.sub("", plate).upper()
    vehicle, match_type = None, "not_found"

    try:

        cur.execute("SELECT * FROM vehicles WHERE license_normalized=%s LIMIT 1", (plate,))
        row = cur.fetchone()
        if row:
            vehicle, match_type = row, "exact"


        if vehicle is None:
            try:
                cur.execute("SELECT * FROM vehicles WHERE license_stripped=%s LIMIT 1", (clean,))
                row = cur.fetchone()
                if row:
                    vehicle, match_type = row, "fuzzy"
            except mysql.connector.Error:
                pass


        if vehicle is None:
            cur.execute(
                "SELECT * FROM vehicles WHERE REPLACE(license_normalized,'-','')=%s LIMIT 1",
                (clean,))
            row = cur.fetchone()
            if row:
                vehicle, match_type = row, "fuzzy"

        # 4. edit-distance-1 (bucket, not full-table scan)
        if vehicle is None and len(clean) >= 4:
            cur.execute(
                "SELECT license_normalized, license_stripped FROM vehicles "
                "WHERE LENGTH(license_stripped) = %s",
                (len(clean),))
            for candidate in cur.fetchall():
                cs = (candidate.get("license_stripped") or "").upper()
                if len(cs) == len(clean) and sum(a != b for a, b in zip(cs, clean)) == 1:
                    cur.execute(
                        "SELECT * FROM vehicles WHERE license_normalized=%s LIMIT 1",
                        (candidate["license_normalized"],))
                    vehicle = cur.fetchone()
                    if vehicle:
                        match_type = "edit1"
                    break
    finally:
        cur.close()

    if vehicle is None:
        logger.info(f"[LOOKUP] '{plate}' -> NOT FOUND [DB]")
        return None, "unauthorized", "not_found"


    try:
        cur2 = conn.cursor(dictionary=True)
        cur2.execute("SELECT * FROM vehicles WHERE id=%s LIMIT 1", (vehicle["id"],))
        full_row = cur2.fetchone()
        cur2.close()
        if full_row:
            vehicle = full_row
    except mysql.connector.Error:
        pass

    status = _compute_auth_status(vehicle)
    logger.info(f"[LOOKUP] '{plate}' -> {status.upper()} ({match_type}) [DB]")
    return vehicle, status, match_type


def save_log(conn, plate: str, vehicle: dict | None,
             status: str, confidence: float, image_path: str = "") -> None:
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO detection_logs
                (detected_plate, matched_plate, vehicle_id, owner_name,
                 status, confidence, image_path, detected_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
        """, (
            plate,
            vehicle["license_normalized"] if vehicle else None,
            vehicle["id"]                 if vehicle else None,
            vehicle["owner_name"]         if vehicle else "Unknown",
            status,
            round(min(max(confidence, 0.0), 1.0), 4),
            image_path,
        ))
        conn.commit()
        logger.info(f"LOG SAVED | plate='{plate}' status={status}")
    except mysql.connector.Error as exc:
        logger.error(f"Log write FAILED: {exc}")
        try:
            conn.rollback()
        except mysql.connector.Error:
            pass
    finally:
        try:
            cur.close()
        except mysql.connector.Error:
            pass



GREEN  = (0, 210,  80)
RED    = (60,  40, 230)
ORANGE = (0, 165, 255)
WHITE  = (255, 255, 255)
GRAY_D = (20,  20,  20)

_FONT      = cv2.FONT_HERSHEY_SIMPLEX
_FONT_BOLD = cv2.FONT_HERSHEY_DUPLEX


def _draw_rounded_rect(img, pt1, pt2, color, radius=8, thickness=-1):
    x1, y1 = pt1
    x2, y2 = pt2
    r = min(radius, (x2 - x1) // 2, (y2 - y1) // 2)
    if thickness == -1:
        cv2.rectangle(img, (x1 + r, y1), (x2 - r, y2), color, -1)
        cv2.rectangle(img, (x1, y1 + r), (x2, y2 - r), color, -1)
        for cx, cy in [(x1 + r, y1 + r), (x2 - r, y1 + r),
                       (x1 + r, y2 - r), (x2 - r, y2 - r)]:
            cv2.circle(img, (cx, cy), r, color, -1)
    else:
        cv2.rectangle(img, (x1 + r, y1), (x2 - r, y2), color, thickness)
        cv2.rectangle(img, (x1, y1 + r), (x2, y2 - r), color, thickness)
        for cx, cy in [(x1 + r, y1 + r), (x2 - r, y1 + r),
                       (x1 + r, y2 - r), (x2 - r, y2 - r)]:
            cv2.circle(img, (cx, cy), r, color, thickness)


def draw_result(frame: np.ndarray, bbox: tuple, plate: str,
                vehicle: dict | None, status: str,
                yolo_conf: float = 0.0, ocr_conf: float = 0.0) -> np.ndarray:
    x1, y1, x2, y2 = bbox
    fh, fw   = frame.shape[:2]
    plate_w  = x2 - x1
    color    = GREEN if status == "authorized" else RED
    corner   = 16

    for pt1, pt2, pt3 in [
        ((x1, y1), (x1 + corner, y1), (x1, y1 + corner)),
        ((x2, y1), (x2 - corner, y1), (x2, y1 + corner)),
        ((x1, y2), (x1 + corner, y2), (x1, y2 - corner)),
        ((x2, y2), (x2 - corner, y2), (x2, y2 - corner)),
    ]:
        cv2.line(frame, pt1, pt2, color, 3, cv2.LINE_AA)
        cv2.line(frame, pt1, pt3, color, 3, cv2.LINE_AA)

    bar_y  = y2 + 4
    cv2.rectangle(frame, (x1, bar_y), (x1 + plate_w, bar_y + 6), (50, 50, 50), -1)
    fill_w = max(6, int(plate_w * min(ocr_conf, 1.0)))
    cv2.rectangle(frame, (x1, bar_y), (x1 + fill_w, bar_y + 6), color, -1)

    lines_main = [
        (plate or "UNREADABLE", _FONT_BOLD, 0.72, color, 2),
        (status.upper(),        _FONT,      0.48, color, 1),
    ]
    lines_info: list = []
    if vehicle:
        lines_info += [
            (f"Owner : {vehicle.get('owner_name', '—')}",              _FONT, 0.42, WHITE, 1),
            (f"Car   : {vehicle.get('make','—')} {vehicle.get('model','—')}", _FONT, 0.42, WHITE, 1),
            (f"Color : {vehicle.get('color','—')}",                    _FONT, 0.42, WHITE, 1),
            (f"Dues  : {vehicle.get('dues','—')}",                     _FONT, 0.42,
             (80, 220, 80) if str(vehicle.get("dues", "")).lower() in ("clear", "paid")
             else (80, 80, 240), 1),
        ]
    else:
        lines_info.append(("NOT IN REGISTRY", _FONT, 0.42, (80, 80, 240), 1))
    lines_info.append((f"YOLO {yolo_conf:.0%}   OCR {ocr_conf:.0%}", _FONT, 0.40, (180, 180, 180), 1))

    lh_main, lh_info = 26, 20
    panel_h = len(lines_main) * lh_main + len(lines_info) * lh_info + 18
    panel_w = max(plate_w, 280)
    panel_y = max(0, y1 - panel_h - 6)
    px1     = max(0, min(x1, fw - panel_w - 2))
    px2     = min(px1 + panel_w, fw - 2)

    overlay = frame.copy()
    _draw_rounded_rect(overlay, (px1, panel_y), (px2, y1 - 2), GRAY_D, radius=6)
    cv2.addWeighted(overlay, 0.78, frame, 0.22, 0, frame)
    cv2.rectangle(frame, (px1, panel_y), (px1 + 4, y1 - 2), color, -1)
    cv2.line(frame, (px1, panel_y), (px2, panel_y), color, 1, cv2.LINE_AA)

    ty = panel_y + 20
    for text, font, scale, col, thk in lines_main:
        cv2.putText(frame, f" {text}", (px1 + 8, ty), font, scale, col, thk, cv2.LINE_AA)
        ty += lh_main
    cv2.line(frame, (px1 + 8, ty - 4), (px2 - 4, ty - 4), (60, 60, 60), 1)
    ty += 4
    for text, font, scale, col, thk in lines_info:
        cv2.putText(frame, f" {text}", (px1 + 8, ty), font, scale, col, thk, cv2.LINE_AA)
        ty += lh_info
    return frame


def draw_reading(frame: np.ndarray, bbox: tuple, partial_text: str = "") -> np.ndarray:
    x1, y1, x2, y2 = bbox
    corner = 16
    for pt1, pt2, pt3 in [
        ((x1, y1), (x1 + corner, y1), (x1, y1 + corner)),
        ((x2, y1), (x2 - corner, y1), (x2, y1 + corner)),
        ((x1, y2), (x1 + corner, y2), (x1, y2 - corner)),
        ((x2, y2), (x2 - corner, y2), (x2, y2 - corner)),
    ]:
        cv2.line(frame, pt1, pt2, ORANGE, 2, cv2.LINE_AA)
        cv2.line(frame, pt1, pt3, ORANGE, 2, cv2.LINE_AA)
    label = f"READING: {partial_text}" if partial_text else "SCANNING..."
    (tw, th), _ = cv2.getTextSize(label, _FONT_BOLD, 0.52, 1)
    lx, ly = x1, max(0, y1 - 10)
    ov = frame.copy()
    cv2.rectangle(ov, (lx - 2, ly - th - 6), (lx + tw + 6, ly + 2), (0, 80, 140), -1)
    cv2.addWeighted(ov, 0.75, frame, 0.25, 0, frame)
    cv2.putText(frame, label, (lx + 2, ly - 2), _FONT_BOLD, 0.52, ORANGE, 1, cv2.LINE_AA)
    return frame


def process_frame(frame: np.ndarray, conn) -> tuple[np.ndarray, dict | None]:
    """YOLO -> crop -> OCR -> buffer vote -> DB lookup -> draw overlay"""
    global _last_stable, _last_stable_time, _buffer_first_seen, _no_det_streak

    model  = get_model()
    fh, fw = frame.shape[:2]

    t_yolo = time.perf_counter()
    if fw > YOLO_INPUT_W:
        scale  = YOLO_INPUT_W / fw
        yf     = cv2.resize(frame, (YOLO_INPUT_W, int(fh * scale)), interpolation=cv2.INTER_LINEAR)
    else:
        yf, scale = frame.copy(), 1.0
    yolo_out = model(yf, verbose=False, imgsz=YOLO_INPUT_W)
    yolo_ms  = (time.perf_counter() - t_yolo) * 1000

    raw_boxes: list[tuple] = []
    for result in yolo_out:
        if result.boxes is None:
            continue
        for box in result.boxes:
            c = float(box.conf[0])
            if c < CONF_THRESHOLD:
                continue
            bx1, by1, bx2, by2 = map(int, box.xyxy[0])
            if scale != 1.0:
                bx1, by1 = int(bx1 / scale), int(by1 / scale)
                bx2, by2 = int(bx2 / scale), int(by2 / scale)
            if _is_valid_plate_region(bx1, by1, bx2, by2, fw, fh):
                raw_boxes.append((bx1, by1, bx2, by2, c))

    clean_boxes = _nms_boxes(raw_boxes)  # sorted by conf descending, overlaps removed
    best_coords = tuple(clean_boxes[0][:4]) if clean_boxes else None
    best_conf   = clean_boxes[0][4]         if clean_boxes else 0.0

    # native-res fallback — slight resize can kill YOLO confidence on ~720p frames
    if best_coords is None and scale != 1.0:
        yolo_out_nat = model(frame, verbose=False)
        nat_boxes: list[tuple] = []
        for result in yolo_out_nat:
            if result.boxes is None:
                continue
            for box in result.boxes:
                c = float(box.conf[0])
                if c < CONF_THRESHOLD:
                    continue
                bx1, by1, bx2, by2 = map(int, box.xyxy[0])
                if _is_valid_plate_region(bx1, by1, bx2, by2, fw, fh):
                    nat_boxes.append((bx1, by1, bx2, by2, c))
        clean_nat = _nms_boxes(nat_boxes)
        if clean_nat:
            best_coords = tuple(clean_nat[0][:4])
            best_conf   = clean_nat[0][4]
            logger.debug(f"[YOLO] native-res fallback recovered box conf={best_conf:.3f}")

    # 1280px fallback for small/distant plates invisible at 640px
    if best_coords is None:
        yolo_out_lg = model(yf, verbose=False, imgsz=1280)
        lg_boxes: list[tuple] = []
        for result in yolo_out_lg:
            if result.boxes is None:
                continue
            for box in result.boxes:
                c = float(box.conf[0])
                if c < CONF_THRESHOLD:
                    continue
                bx1, by1, bx2, by2 = map(int, box.xyxy[0])
                if scale != 1.0:
                    bx1, by1 = int(bx1 / scale), int(by1 / scale)
                    bx2, by2 = int(bx2 / scale), int(by2 / scale)
                if _is_valid_plate_region(bx1, by1, bx2, by2, fw, fh):
                    lg_boxes.append((bx1, by1, bx2, by2, c))
        clean_lg = _nms_boxes(lg_boxes)
        if clean_lg:
            best_coords = tuple(clean_lg[0][:4])
            best_conf   = clean_lg[0][4]
            logger.debug(f"[YOLO] 1280px multi-scale recovered box conf={best_conf:.3f}")


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
    px = max(20, int((x2 - x1) * 0.12))
    py = max(14, int((y2 - y1) * 0.18))
    crop = frame[max(0, y1 - py): min(fh, y2 + py),
                 max(0, x1 - px): min(fw, x2 + px)]
    crop = _angle_aware_pad(crop, best_conf)

    if crop.size == 0:
        with _pipeline_lock:
            _plate_buffer.clear()
            _plate_scores.clear()
            _buffer_first_seen = 0.0
        return frame, None

    t_ocr = time.perf_counter()
    plate_text, ocr_conf, raw_ocr = read_plate_ocr(crop)
    ocr_ms = (time.perf_counter() - t_ocr) * 1000

    with _pipeline_lock:
        if plate_text:
            if not _plate_buffer and _buffer_first_seen == 0.0:
                _buffer_first_seen = time.time()
            _plate_buffer.append(plate_text)
            _plate_scores.append((plate_text, ocr_conf))
        else:
            return draw_reading(frame.copy(), (x1, y1, x2, y2)), None

        buf_len  = len(_plate_buffer)
        snapshot = list(_plate_scores)
        elapsed  = time.time() - _buffer_first_seen if _buffer_first_seen > 0 else 0.0

    require = max(STABLE_FRAMES, 1)
    force   = elapsed >= _BUFFER_FLUSH_SECS and buf_len >= require
    stable  = buf_len >= require or force

    if stable:
        counts: dict[str, int]   = {}
        maxsc:  dict[str, float] = {}
        wt_sum: dict[str, float] = {}  # weight = ocr_conf
        for t, s in snapshot:
            counts[t] = counts.get(t, 0) + 1
            if s > maxsc.get(t, 0.0):
                maxsc[t] = s
            wt_sum[t] = wt_sum.get(t, 0.0) + s
        confirmed = max(counts, key=lambda k: (counts[k], wt_sum.get(k, 0.0), maxsc.get(k, 0.0)))
        winner_score = maxsc.get(confirmed, 0.0)
        stable = (counts[confirmed] >= require and winner_score >= MIN_OCR_CONF) or force
        # forced flush still requires 95% of MIN_OCR_CONF (blocks noise reads)
        if force and winner_score < MIN_OCR_CONF * 0.95:
            with _pipeline_lock:
                _plate_buffer.clear()
                _plate_scores.clear()
                _buffer_first_seen = 0.0
            return draw_reading(frame.copy(), (x1, y1, x2, y2)), None
    else:
        confirmed = plate_text

    if not stable:
        return draw_reading(frame.copy(), (x1, y1, x2, y2), plate_text), None

    with _pipeline_lock:
        _plate_buffer.clear()
        _plate_scores.clear()
        _buffer_first_seen = 0.0

    now = time.time()
    with _pipeline_lock:
        already = (confirmed == _last_stable and (now - _last_stable_time) < SAME_PLATE_COOLDOWN)
        if not already:
            _last_stable      = confirmed
            _last_stable_time = now

    t_db = time.perf_counter()
    with _lookup_cache_lock:
        cached = _last_lookup_cache.get(confirmed)

    if already and cached is not None:
        vehicle, status, match_type = cached
        db_ms = 0.0
    else:
        vehicle, status, match_type = lookup_plate(conn, confirmed, raw_ocr)
        with _lookup_cache_lock:
            _last_lookup_cache[confirmed] = (vehicle, status, match_type)
        db_ms = (time.perf_counter() - t_db) * 1000

    frame = draw_result(frame, (x1, y1, x2, y2), confirmed, vehicle, status,
                        best_conf, ocr_conf)

    if not already:
        import database as _db
        crop_path = _save_crop(crop, confirmed)
        def _log_worker(pl, veh, st, cf, cp):
            lc = None
            try:
                lc = _db.get_connection()
                lc.autocommit = False
                save_log(lc, pl, veh, st, cf, cp)
            except mysql.connector.Error as e:
                logger.error(f"[LogWriter] DB error: {e}")
            except OSError as e:
                logger.error(f"[LogWriter] OS error: {e}")
            finally:
                if lc:
                    try:
                        lc.close()
                    except Exception:
                        pass
        _get_log_pool().submit(_log_worker, confirmed, vehicle, status, ocr_conf, crop_path)
        logger.info(
            f"DETECTION | '{confirmed}' | {status.upper()} | match={match_type} | "
            f"yolo={yolo_ms:.0f}ms ocr={ocr_ms:.0f}ms db={db_ms:.1f}ms | "
            f"yolo_conf={best_conf:.2f} ocr_conf={ocr_conf:.2f}"
        )

    return frame, {
        "plate":      confirmed,
        "status":     status,
        "match_type": match_type,
        "yolo_conf":  round(best_conf, 3),
        "ocr_conf":   round(ocr_conf, 3),
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
