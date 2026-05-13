"""F9 routes — /input/lock, /input/unlock, /input/lock_status."""

from __future__ import annotations

from litestar import Request, get, post
from litestar.exceptions import HTTPException
from msgspec import Struct


class LockBody(Struct):
    unlock_hotkey: str = "ctrl+shift+f12"
    allow_safe_keys: bool = True
    block_keyboard: bool = True
    block_mouse: bool = True
    watchdog_ms: int = 2000


@post("/input/lock")
async def input_lock(request: Request, data: LockBody) -> dict:
    lock = request.app.state.input_lock
    if lock.locked:
        raise HTTPException(status_code=409, detail="input lock already active")
    try:
        token = request.app.state.token
        port = request.app.state.settings.port
        lock.acquire(
            unlock_hotkey=data.unlock_hotkey,
            allow_safe_keys=data.allow_safe_keys,
            block_keyboard=data.block_keyboard,
            block_mouse=data.block_mouse,
            watchdog_ms=data.watchdog_ms,
            watchdog_args={"port": port, "token": token},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return lock.status_dict()


@post("/input/unlock")
async def input_unlock(request: Request) -> dict:
    lock = request.app.state.input_lock
    return lock.release()


@get("/input/lock_status")
async def input_lock_status(request: Request) -> dict:
    return request.app.state.input_lock.status_dict()
