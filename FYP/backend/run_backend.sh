#!/usr/bin/env bash
# ANPR Backend — production launcher
# Fixes: double module-init (--workers=1), cysignals abort (CYSIGNALS_CRASH_LOGS unset),
#        excessive log noise (--log-level info), no reload in production.

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$BACKEND_DIR/../logs"
mkdir -p "$LOG_DIR"
cd "$BACKEND_DIR"

# Suppress cysignals crash handler that causes "FATAL: exception not rethrown" on SIGINT
export CYSIGNALS_CRASH_LOGS=""

# Prevent OpenCV/cysignals SIGINT conflict
export PYTHONFAULTHANDLER=1

# Single worker = no duplicate module-level imports, no duplicate log lines
exec venv/bin/uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level warning \
    --no-access-log \
    --timeout-keep-alive 30
