"""F3 + F4 — held-key endpoints (hold, down, up, sequence, release_all).

These bypass Windows-MCP and drive SendInput via input_driver. State is
tracked in app.state.input_state so we can release everything on shutdown.
"""

from __future__ import annotations

import asyncio
import time

from litestar import Request, post
from msgspec import Struct


class KeyHoldBody(Struct):
    name: str
    duration_ms: int
    modifiers: list[str] = []


class KeyNameBody(Struct):
    name: str


class KeySequenceItem(Struct):
    name: str
    hold_ms: int


class KeySequenceBody(Struct):
    keys: list[KeySequenceItem]
    inter_key_delay_ms: int = 30


@post("/key/hold")
async def key_hold(request: Request, data: KeyHoldBody) -> dict:
    state = request.app.state.input_state
    start = time.monotonic()
    pressed_mods: list[str] = []
    pressed_main = False
    try:
        for mod in data.modifiers:
            await state.press_key(mod)
            pressed_mods.append(mod)
        await state.press_key(data.name)
        pressed_main = True
        await asyncio.sleep(max(0, data.duration_ms) / 1000)
    finally:
        if pressed_main:
            await state.release_key(data.name)
        for mod in reversed(pressed_mods):
            await state.release_key(mod)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return {"elapsed_ms": elapsed_ms}


@post("/key/down")
async def key_down(request: Request, data: KeyNameBody) -> dict:
    state = request.app.state.input_state
    was_new = await state.press_key(data.name)
    return {"state": "down", "newly_pressed": was_new}


@post("/key/up")
async def key_up(request: Request, data: KeyNameBody) -> dict:
    state = request.app.state.input_state
    held_s = await state.release_key(data.name)
    if held_s is None:
        return {"state": "up", "warning": f"key {data.name!r} was not down"}
    return {"state": "up", "was_held_ms": int(held_s * 1000)}


@post("/key/sequence")
async def key_sequence(request: Request, data: KeySequenceBody) -> dict:
    state = request.app.state.input_state
    start = time.monotonic()
    inter = max(0, data.inter_key_delay_ms) / 1000
    pressed_now: list[str] = []
    try:
        for i, item in enumerate(data.keys):
            if i > 0 and inter > 0:
                await asyncio.sleep(inter)
            await state.press_key(item.name)
            pressed_now.append(item.name)
            await asyncio.sleep(max(0, item.hold_ms) / 1000)
            await state.release_key(item.name)
            pressed_now.pop()
    finally:
        # rollback: release any key still down from a failure mid-sequence
        for name in reversed(pressed_now):
            await state.release_key(name)
    total_ms = int((time.monotonic() - start) * 1000)
    return {"total_elapsed_ms": total_ms}


@post("/key/release_all")
async def key_release_all(request: Request) -> dict:
    """Release every held key AND mouse button (alias of /input/release_all per spec section 6)."""
    state = request.app.state.input_state
    released = await state.release_all()
    return released
