import asyncio
import logging
import secrets
import socket
import time
from typing import Dict, Tuple

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

try:
    import camera_worker_optimized as camera_worker
except ImportError:
    import camera_worker

from auth import get_current_user

router = APIRouter(tags=["stream"])
logger = logging.getLogger("anpr.stream")

_tickets: Dict[str, Tuple[str, float]] = {}
_TICKET_TTL = 30.0


def _purge_expired_tickets() -> None:
    now = time.monotonic()
    for k in [k for k, (_, exp) in _tickets.items() if now > exp]:
        _tickets.pop(k, None)


@router.post("/api/stream/ticket")
async def issue_stream_ticket(current_user: dict = Depends(get_current_user)):
    _purge_expired_tickets()
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = (current_user["username"], time.monotonic() + _TICKET_TTL)
    return {"ticket": ticket}


@router.websocket("/api/stream")
async def ws_stream(
    websocket: WebSocket,
    ticket: str = Query(default=""),
) -> None:
    authorized = False

    if ticket:
        _purge_expired_tickets()
        entry = _tickets.pop(ticket, None)
        if entry is not None:
            _, expiry = entry
            if time.monotonic() <= expiry:
                authorized = True
            else:
                logger.warning("WebSocket ticket expired")
        else:
            logger.warning("WebSocket ticket not found or already used")

    if not authorized:
        await websocket.close(code=4001, reason="Unauthorized")
        logger.warning("WebSocket rejected — missing or invalid ticket")
        return

    await websocket.accept()
    camera_worker.register(websocket)
    logger.info("WebSocket client connected")

    try:
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive(), timeout=30.0)
                if msg.get("type") == "websocket.disconnect":
                    break
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
    except asyncio.CancelledError:
        pass
    except (socket.gaierror, OSError) as exc:
        logger.warning(f"WebSocket session error: {exc}")
    finally:
        camera_worker.unregister(websocket)
        logger.info("WebSocket client disconnected")
