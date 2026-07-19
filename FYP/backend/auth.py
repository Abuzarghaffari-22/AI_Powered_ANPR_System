import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import mysql.connector  # type: ignore
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt  # type: ignore

load_dotenv(override=True)
logger = logging.getLogger("anpr.auth")

SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
if not SECRET_KEY or SECRET_KEY == "changeme":
    import secrets as _sec
    SECRET_KEY = _sec.token_hex(32)
    logger.critical(
        "[Auth] SECRET_KEY is missing or is the insecure default. "
        "A random key is being used for this session — tokens will be invalidated on restart. "
        "Set a strong SECRET_KEY in backend/.env"
    )

ALGORITHM   = os.getenv("ALGORITHM",   "HS256")
EXPIRE_MINS = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

_FAILED_ATTEMPTS: dict[str, list[float]] = {}
_FAILED_LOCK    = threading.Lock()
_MAX_ATTEMPTS   = 5
_WINDOW_SECS    = 300
_LOCKOUT_SECS   = 300

_PWD_RE = re.compile(
    r'^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[!@#$%^&*()\-_=+\[\]{}|;:,.<>?]).{12,}$'
)


def validate_password_strength(password: str) -> None:
    if not _PWD_RE.match(password):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Password must be 12+ characters and include uppercase, lowercase, digit and symbol.",
        )


def _check_rate_limit(username: str) -> None:
    now = time.monotonic()
    with _FAILED_LOCK:
        attempts = [t for t in _FAILED_ATTEMPTS.get(username, []) if now - t < _WINDOW_SECS]
        _FAILED_ATTEMPTS[username] = attempts
    if len(attempts) >= _MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Try again in {_LOCKOUT_SECS // 60} minutes.",
        )


def _record_failure(username: str) -> None:
    now = time.monotonic()
    with _FAILED_LOCK:
        lst = _FAILED_ATTEMPTS.setdefault(username, [])
        lst.append(now)
        _FAILED_ATTEMPTS[username] = [t for t in lst if now - t < _WINDOW_SECS]


def _clear_failures(username: str) -> None:
    with _FAILED_LOCK:
        _FAILED_ATTEMPTS.pop(username, None)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _get_user_from_db(username: str) -> Optional[dict]:
    conn = None
    try:
        from database import get_connection
        conn = get_connection()
        conn.autocommit = True
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username=%s LIMIT 1",
            (username,),
        )
        row = cur.fetchone()
        cur.close()
        return row
    except (mysql.connector.Error, OSError) as exc:
        logger.error(f"[Auth] DB lookup failed for '{username}': {exc}")
        return None
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _update_last_login(username: str) -> None:
    conn = None
    try:
        from database import get_connection
        conn = get_connection()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET last_login=%s WHERE username=%s",
            (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), username),
        )
        cur.close()
    except (mysql.connector.Error, OSError) as exc:
        logger.warning(f"[Auth] last_login update failed for '{username}': {exc}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def ensure_admin() -> None:
    username = os.getenv("ADMIN_USERNAME", "admin")
    password = os.getenv("ADMIN_PASSWORD", "")

    _WEAK = {"admin123", "admin", "password", "123456", "changeme", ""}
    if password in _WEAK:
        logger.critical(
            "[Auth] ADMIN_PASSWORD is weak or default. "
            "Set a strong password in backend/.env — system will start but login is insecure."
        )

    conn = None
    try:
        from database import get_connection
        conn = get_connection()
        conn.autocommit = True
        cur = conn.cursor(dictionary=True)

        cur.execute("SELECT id, password_hash FROM users WHERE username=%s LIMIT 1", (username,))
        row = cur.fetchone()

        if row is None:
            new_hash = hash_password(password)
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, 'admin')",
                (username, new_hash),
            )
            logger.info(f"[Auth] Admin user '{username}' created in DB.")
        else:
            if not _verify_password(password, row["password_hash"]):
                new_hash = hash_password(password)
                cur.execute(
                    "UPDATE users SET password_hash=%s WHERE username=%s",
                    (new_hash, username),
                )
                logger.info(f"[Auth] Admin user '{username}' password synced from .env to DB.")
            else:
                logger.info(f"[Auth] Admin user '{username}' DB hash matches .env — no update needed.")

        cur.close()
    except (mysql.connector.Error, OSError) as exc:
        logger.error(f"[Auth] ensure_admin failed: {exc}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def authenticate_user(username: str, password: str) -> Optional[dict]:
    _check_rate_limit(username)
    user = _get_user_from_db(username)
    if not user or not _verify_password(password, user["password_hash"]):
        _record_failure(username)
        return None
    _clear_failures(username)
    _update_last_login(username)
    return user


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=EXPIRE_MINS))
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload  = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username or not isinstance(username, str):
            raise exc
    except JWTError:
        raise exc

    user = _get_user_from_db(username)
    if not user:
        raise exc
    return user


async def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return current_user
