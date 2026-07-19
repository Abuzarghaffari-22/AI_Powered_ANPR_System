#!/usr/bin/env python3
"""
Download the EDSR x4 super-resolution model for improved OCR on small plates.

Usage:
    cd FYP/backend
    python3 download_sr_model.py

The model (~23 MB) is saved to:
    FYP/backend/models/EDSR_x4.pb

Source: OpenCV DNN Super Resolution model zoo
https://github.com/Saafke/EDSR_Tensorflow/tree/master/models
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

MODEL_URL  = "https://github.com/Saafke/EDSR_Tensorflow/raw/master/models/EDSR_x4.pb"
MODEL_PATH = Path(__file__).resolve().parent / "models" / "EDSR_x4.pb"
# SHA-256 of the official EDSR_x4.pb from the OpenCV model zoo
EXPECTED_SHA256 = "7b6e4d4a4571e4e8e3e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4e4"  # placeholder


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _progress(count, block_size, total):
    pct = min(100, int(count * block_size * 100 / total)) if total > 0 else 0
    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
    print(f"\r  [{bar}] {pct}%", end="", flush=True)


def main():
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

    if MODEL_PATH.exists():
        size_mb = MODEL_PATH.stat().st_size / 1_048_576
        print(f"✅  EDSR_x4.pb already exists ({size_mb:.1f} MB) — skipping download.")
        print(f"    Path: {MODEL_PATH}")
        return 0

    print(f"Downloading EDSR x4 super-resolution model...")
    print(f"  Source : {MODEL_URL}")
    print(f"  Target : {MODEL_PATH}")

    try:
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH, reporthook=_progress)
        print()  # newline after progress bar
    except Exception as exc:
        print(f"\n❌  Download failed: {exc}")
        print("\nManual download:")
        print(f"  wget -O {MODEL_PATH} '{MODEL_URL}'")
        if MODEL_PATH.exists():
            MODEL_PATH.unlink()
        return 1

    size_mb = MODEL_PATH.stat().st_size / 1_048_576
    print(f"✅  Downloaded EDSR_x4.pb ({size_mb:.1f} MB)")
    print(f"    Path: {MODEL_PATH}")
    print("\nThe ANPR system will automatically use super-resolution on the next start.")
    print("Small/distant plates will have improved OCR accuracy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
