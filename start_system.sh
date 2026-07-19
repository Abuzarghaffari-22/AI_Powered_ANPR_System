#!/usr/bin/env bash
# =============================================================================
#  ANPR System — Master Startup Script
#  Usage:  ./start_system.sh [--no-frontend] [--port-backend N] [--port-frontend N]
#  Stop :  Ctrl+C  (kills both backend and frontend gracefully)
# =============================================================================
set -uo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/FYP/backend"
FRONTEND_DIR="$SCRIPT_DIR/FYP/frontend"
LOG_DIR="$SCRIPT_DIR/FYP/logs"
VENV_PYTHON="$BACKEND_DIR/venv/bin/python"
VENV_UVICORN="$BACKEND_DIR/venv/bin/uvicorn"

# ── Defaults ──────────────────────────────────────────────────────────────────
PORT_BACKEND=8000
PORT_FRONTEND=3000
START_FRONTEND=true

# ── Arg parsing ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --no-frontend)   START_FRONTEND=false; shift ;;
    --port-backend)  PORT_BACKEND="$2";   shift 2 ;;
    --port-frontend) PORT_FRONTEND="$2";  shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*" >&2; }
step()  { echo -e "${CYAN}[»]${NC} $*"; }

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  echo ""
  warn "Shutting down ANPR System..."
  [[ -n "$BACKEND_PID"  ]] && kill "$BACKEND_PID"  2>/dev/null || true
  [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  info "All services stopped."
  exit 0
}
trap cleanup INT TERM

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        AI POWERED ANPR SYSTEM — STARTUP          ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""

# ── Pre-flight checks ─────────────────────────────────────────────────────────
step "Running pre-flight checks..."

# Python venv
if [[ ! -f "$VENV_PYTHON" ]]; then
  error "Python venv not found at: $VENV_PYTHON"
  error "Run: python3 -m venv $BACKEND_DIR/venv && $BACKEND_DIR/venv/bin/pip install -r $BACKEND_DIR/requirements.txt"
  exit 1
fi
info "Python venv found"

# YOLO model
if [[ ! -f "$BACKEND_DIR/models/yolov11_plate_detection.pt" ]]; then
  error "YOLO model not found: $BACKEND_DIR/models/yolov11_plate_detection.pt"
  exit 1
fi
info "YOLO model found"

# .env file
if [[ ! -f "$BACKEND_DIR/.env" ]]; then
  error ".env file missing. Copy the example and fill in your values:"
  error "  cp $BACKEND_DIR/.env.example $BACKEND_DIR/.env"
  exit 1
fi
info ".env file found"

# Read DB credentials from .env
DB_HOST=$(grep "^DB_HOST"     "$BACKEND_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d ' ' || echo "localhost")
DB_USER=$(grep "^DB_USER"     "$BACKEND_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d ' ' || echo "")
DB_PASS=$(grep "^DB_PASSWORD" "$BACKEND_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d ' ' || echo "")
DB_NAME=$(grep "^DB_NAME"     "$BACKEND_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d ' ' || echo "anpr_db")

# MySQL check
if mysql -u "$DB_USER" -p"$DB_PASS" -h "$DB_HOST" "$DB_NAME" -e "SELECT 1;" >/dev/null 2>&1; then
  info "MySQL database '$DB_NAME' reachable"
else
  error "Cannot connect to MySQL database '$DB_NAME'"
  error "Check DB_HOST / DB_USER / DB_PASSWORD / DB_NAME in $BACKEND_DIR/.env"
  exit 1
fi

# Tesseract
if command -v tesseract >/dev/null 2>&1; then
  TESS_VER=$(tesseract --version 2>&1 | head -1)
  info "Tesseract found: $TESS_VER"
else
  warn "Tesseract not found — install with: sudo apt install tesseract-ocr"
  warn "OCR will attempt pytesseract fallback but accuracy will be reduced"
fi

# Node modules
if [[ "$START_FRONTEND" == "true" ]] && [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  step "node_modules not found — running npm install..."
  cd "$FRONTEND_DIR"
  npm install
  if [[ $? -ne 0 ]]; then
    error "npm install failed. Fix the errors above then re-run this script."
    exit 1
  fi
  info "npm install complete"
fi

# Free up ports — kill any leftover process from a previous run
for PORT_CHK in $PORT_BACKEND $PORT_FRONTEND; do
  ORPHAN=$(lsof -Pi ":$PORT_CHK" -sTCP:LISTEN -t 2>/dev/null || true)
  if [[ -n "$ORPHAN" ]]; then
    warn "Port $PORT_CHK in use (PID $ORPHAN) — killing leftover process..."
    kill "$ORPHAN" 2>/dev/null || true
    sleep 1
  fi
done
info "Ports $PORT_BACKEND and $PORT_FRONTEND are free"

mkdir -p "$LOG_DIR"
info "Log directory ready"

# ── DroidCam check (optional) ─────────────────────────────────────────────────
CAMERA_IP=$(grep "^CAMERA_IP" "$BACKEND_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d ' ' || echo "")
if [[ -n "$CAMERA_IP" ]]; then
  echo ""
  step "Checking DroidCam at $CAMERA_IP..."
  if curl -s --connect-timeout 2 --max-time 3 "http://$CAMERA_IP:4747/video" -o /dev/null 2>/dev/null; then
    info "DroidCam reachable at http://$CAMERA_IP:4747"
  elif nc -z -w2 "$CAMERA_IP" 4747 2>/dev/null; then
    info "DroidCam port 4747 open at $CAMERA_IP"
  else
    warn "DroidCam not reachable at $CAMERA_IP — falling back to local webcam"
  fi
fi

# ── Start Backend ─────────────────────────────────────────────────────────────
echo ""
step "Starting FastAPI backend on port $PORT_BACKEND..."
cd "$BACKEND_DIR"

export CYSIGNALS_CRASH_LOGS=""
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1

"$VENV_UVICORN" main:app \
  --host 0.0.0.0 \
  --port "$PORT_BACKEND" \
  --workers 1 \
  --log-level error \
  --no-access-log \
  --timeout-keep-alive 30 \
  >> "$LOG_DIR/uvicorn_errors.log" 2>&1 &
BACKEND_PID=$!

# Wait up to 60s for backend — YOLO model warmup takes 12-20s
step "Waiting for backend to be ready (YOLO warmup takes ~15s)..."
READY=false
for i in $(seq 1 120); do
  sleep 0.5
  if curl -sf "http://localhost:$PORT_BACKEND/api/health" > /dev/null 2>&1; then
    READY=true
    break
  fi
  # Check the process is still alive
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    error "Backend process crashed. Check log: $LOG_DIR/uvicorn_errors.log"
    tail -20 "$LOG_DIR/uvicorn_errors.log" 2>/dev/null || true
    exit 1
  fi
done

if [[ "$READY" == "true" ]]; then
  HEALTH=$(curl -s "http://localhost:$PORT_BACKEND/api/health" 2>/dev/null)
  info "Backend running — PID: $BACKEND_PID"
  info "Health: $HEALTH"
else
  error "Backend did not respond within 60s."
  error "Check log: $LOG_DIR/uvicorn_errors.log"
  tail -20 "$LOG_DIR/uvicorn_errors.log" 2>/dev/null || true
  kill "$BACKEND_PID" 2>/dev/null || true
  exit 1
fi

# ── Start Frontend ────────────────────────────────────────────────────────────
if [[ "$START_FRONTEND" == "true" ]]; then
  echo ""
  step "Starting Next.js frontend on port $PORT_FRONTEND..."
  cd "$FRONTEND_DIR"
  PORT="$PORT_FRONTEND" npm run dev >> "$LOG_DIR/frontend.log" 2>&1 &
  FRONTEND_PID=$!

  # Wait up to 60s — first compile can take 30-40s
  FRONT_READY=false
  for i in $(seq 1 120); do
    sleep 0.5
    if curl -sf "http://localhost:$PORT_FRONTEND" >/dev/null 2>&1; then
      FRONT_READY=true
      break
    fi
    if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
      error "Frontend process crashed. Check log: $LOG_DIR/frontend.log"
      tail -20 "$LOG_DIR/frontend.log" 2>/dev/null || true
      exit 1
    fi
  done

  if [[ "$FRONT_READY" == "true" ]]; then
    info "Frontend running — PID: $FRONTEND_PID"
  else
    warn "Frontend is still compiling — it will be ready in a few seconds"
    warn "Check: $LOG_DIR/frontend.log"
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           ANPR SYSTEM IS RUNNING                 ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC}  Dashboard  : ${CYAN}http://localhost:$PORT_FRONTEND${NC}"
echo -e "${GREEN}║${NC}  Backend    : ${CYAN}http://localhost:$PORT_BACKEND${NC}"
echo -e "${GREEN}║${NC}  API Docs   : ${CYAN}http://localhost:$PORT_BACKEND/api/docs${NC}"
echo -e "${GREEN}║${NC}  WebSocket  : ${CYAN}ws://localhost:$PORT_BACKEND/api/stream${NC}"
[[ -n "$CAMERA_IP" ]] && \
echo -e "${GREEN}║${NC}  DroidCam   : ${CYAN}http://$CAMERA_IP:4747${NC}"
echo -e "${GREEN}║${NC}  Logs       : ${CYAN}$LOG_DIR/${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC}  Login      : admin / (see .env ADMIN_PASSWORD)"
echo -e "${GREEN}║${NC}  Press      : Ctrl+C to stop all services"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""

wait
