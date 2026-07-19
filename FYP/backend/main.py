import asyncio
import logging
import logging.handlers
import os
import warnings
from contextlib import asynccontextmanager
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning, module="jose")

from dotenv import load_dotenv
load_dotenv(override=True)

_LOG_DIR  = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "backend.log"

_fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

_fh = logging.handlers.RotatingFileHandler(
    _LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_fh.setFormatter(_fmt)
_fh.setLevel(logging.INFO)

_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
_sh.setLevel(logging.INFO)

root = logging.getLogger()
if not root.handlers:
    root.setLevel(logging.INFO)
    root.addHandler(_fh)
    root.addHandler(_sh)
else:
    root.setLevel(logging.INFO)
    has_file   = any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)
    has_stream = any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
        for h in root.handlers
    )
    if not has_file:
        root.addHandler(_fh)
    if not has_stream:
        root.addHandler(_sh)

logger = logging.getLogger("anpr.main")

import mysql.connector  # type: ignore
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

try:
    import camera_worker_optimized as camera_worker
    logger.info("Using OPTIMIZED camera worker (high-performance mode)")
except ImportError:
    import camera_worker
    logger.info("Using standard camera worker (fallback mode)")

from auth import ensure_admin
from database import init_pool, get_connection
import plate_store
from routes.auth_routes      import router as auth_router
from routes.detection_routes import router as det_router
from routes.register_routes  import router as reg_router
from routes.stats_routes     import router as stats_router
from routes.stream_routes    import router as stream_router
from routes.vehicle_routes   import router as veh_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ANPR backend starting…")

    pool_size = int(os.getenv("DB_POOL_SIZE", "10"))
    init_pool(size=pool_size)

    try:
        ensure_admin()
    except Exception as exc:
        logger.error(f"ensure_admin failed: {exc}", exc_info=True)

    try:
        _ic = get_connection()
        _ic.autocommit = True
        _cur = _ic.cursor(dictionary=True)
        _cur.execute(
            "SELECT INDEX_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='vehicles' "
            "AND INDEX_NAME IN ('uq_plate_norm','idx_license_stripped','idx_auth_covering')",
            (os.getenv("DB_NAME", "anpr_db"),)
        )
        found = {r["INDEX_NAME"] for r in _cur.fetchall()}
        required = {"uq_plate_norm", "idx_license_stripped", "idx_auth_covering"}
        missing = required - found
        if missing:
            logger.critical(f"[DB] MISSING INDEXES on vehicles table: {missing} — run database_schema.sql to recreate")
        else:
            logger.info("[DB] Index verification passed")
        _cur.close()
        _ic.close()
    except Exception as exc:
        logger.warning(f"[DB] Index verification skipped: {exc}")

    try:
        _conn = get_connection()
        _conn.autocommit = True
        n = plate_store.load(_conn)
        _conn.close()
        logger.info(f"PlateStore ready — {n} vehicles in RAM")
    except Exception as exc:
        logger.error(f"PlateStore load failed (will fall back to DB): {exc}")

    loop  = asyncio.get_running_loop()
    cam   = int(os.getenv("CAMERA_INDEX",           "0"))
    every = int(os.getenv("PROCESS_EVERY_N_FRAMES", "1"))
    try:
        camera_worker.start(loop, cam_idx=cam, every=every)
        logger.info(f"Camera worker started  cam={cam}  every={every}")
    except Exception as exc:
        logger.error(f"Camera worker failed to start: {exc}", exc_info=True)

    yield

    logger.info("Shutting down…")
    camera_worker.stop()


app = FastAPI(
    title       = "ANPR System API",
    description = "Automatic Number Plate Recognition — FastAPI backend",
    version     = "1.0.0",
    lifespan    = lifespan,
    docs_url    = "/api/docs",
    redoc_url   = "/api/redoc",
    openapi_url = "/api/openapi.json",
)

_MAX_BODY = 2 * 1024 * 1024

@app.middleware("http")
async def _limit_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_BODY:
        return JSONResponse(status_code=413, content={"detail": "Request body too large"})
    return await call_next(request)


def _get_local_ips() -> list[str]:
    ips = ["localhost", "127.0.0.1"]
    import socket
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = str(info[4][0])
            if ip not in ips and not ip.startswith("fe80") and ":" not in ip:
                ips.append(ip)
    except (socket.gaierror, OSError) as exc:
        logger.debug(f"getaddrinfo failed: {exc}")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2.0)
        s.connect(("10.254.254.254", 1))
        ip = s.getsockname()[0]
        if ip not in ips:
            ips.append(ip)
        s.shutdown(socket.SHUT_RDWR)
        s.close()
    except (socket.error, socket.timeout) as e:
        logger.debug(f"Socket connection test failed: {e}")
    return ips


_raw_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
)
ALLOWED_ORIGINS: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

try:
    for ip in _get_local_ips():
        for port in ("3000", "8000"):
            origin = f"http://{ip}:{port}"
            if origin not in ALLOWED_ORIGINS:
                ALLOWED_ORIGINS.append(origin)
except Exception as exc:
    logger.warning(f"Failed to auto-detect local network IPs for CORS: {exc}")

logger.info(f"CORS allowed origins: {ALLOWED_ORIGINS}")

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ALLOWED_ORIGINS,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

app.include_router(auth_router)
app.include_router(det_router)
app.include_router(veh_router)
app.include_router(stats_router)
app.include_router(stream_router)
app.include_router(reg_router)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "system":        "ANPR",
        "version":       "1.0",
        "camera_active": camera_worker.is_alive(),
        "docs":          "/api/docs",
    }


@app.get("/api/health", tags=["health"])
async def health():
    db_ok = False
    try:
        conn = get_connection()
        conn.ping(reconnect=False)
        conn.close()
        db_ok = True
    except mysql.connector.Error as exc:
        logger.debug(f"DB health check failed: {exc}")

    cam_ok  = camera_worker.is_alive()
    overall = "ok" if (db_ok and cam_ok) else "degraded"
    return {
        "status":        overall,
        "camera_worker": cam_ok,
        "database":      db_ok,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host    = "0.0.0.0",
        port    = 8000,
        reload  = False,
        workers = 1,
    )
