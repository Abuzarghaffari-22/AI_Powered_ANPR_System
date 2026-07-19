import logging
import os
from typing import Generator, Optional

import mysql.connector  # type: ignore
from mysql.connector import pooling  # type: ignore
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv(override=True)
logger = logging.getLogger("anpr.db")

_POOL_CFG = {
    "host":              os.getenv("DB_HOST",     "localhost"),
    "user":              os.getenv("DB_USER",     "anpr_user"),
    "password":          os.getenv("DB_PASSWORD", "anpr_pass123"),
    "database":          os.getenv("DB_NAME",     "anpr_db"),
    "port":              int(os.getenv("DB_PORT", "3306")),
    "charset":           "utf8mb4",
    "use_unicode":       True,
    "connect_timeout":   5,
    "connection_timeout": 5,
    "autocommit":        True,
}

_pool: Optional[pooling.MySQLConnectionPool] = None


def init_pool(size: int = 10) -> None:
    global _pool
    _pool = pooling.MySQLConnectionPool(
        pool_name          = "anpr_pool",
        pool_size          = min(size, 32),
        pool_reset_session = True,
        **_POOL_CFG,
    )
    logger.info(
        f"MySQL pool ready  db={_POOL_CFG['database']}  "
        f"host={_POOL_CFG['host']}  size={min(size, 32)}"
    )


def get_connection() -> mysql.connector.MySQLConnection:
    global _pool
    if _pool is None:
        init_pool(size=int(os.getenv("DB_POOL_SIZE", "10")))
    try:
        conn = _pool.get_connection()  # type: ignore[union-attr]
    except mysql.connector.errors.PoolError:
        logger.warning("DB pool exhausted — creating direct connection")
        conn = mysql.connector.connect(**_POOL_CFG)
    try:
        conn.ping(reconnect=True, attempts=1, delay=0)
    except mysql.connector.Error as exc:
        logger.warning(f"DB ping failed during get_connection: {exc} — returning anyway")
    return conn


def get_db() -> Generator:
    try:
        conn = get_connection()
    except mysql.connector.Error as exc:
        logger.error(f"DB connection failed for request: {exc}")
        raise HTTPException(status_code=503, detail="Database unavailable — try again shortly")
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass
