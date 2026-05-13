"""Extended /batch — supports legacy {op,args} actions and CDC F1 {type,...flat} actions.

Action categories:
  - Windows-MCP backed: click, move, type, scroll, key (translate.* + backend.call_tool)
  - Local input driver (F3-F5): key_hold, key_down, key_up, key_sequence,
    mouse_move_smooth, mouse_button (state-tracked via input_state)
  - Internal: sleep (max 5000 ms — capped to avoid zombie batches)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from litestar import Request, post
from litestar.exceptions import ClientException
from msgspec import Struct

from gargaros import translate

_RESERVED = {"type", "op", "args"}


class BatchBody(Struct):
    actions: list[dict[str, Any]]
    continue_on_error: bool = False


def _extract_op_and_args(action: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    op = action.get("type") or action.get("op")
    if not isinstance(op, str):
        raise ValueError("action missing 'type' (or legacy 'op') field")
    if "args" in action and isinstance(action["args"], dict):
        return op.lower(), action["args"]
    args = {k: v for k, v in action.items() if k not in _RESERVED}
    return op.lower(), args


# ---- handlers ----

async def _do_click(request: Request, args: dict[str, Any]) -> None:
    await request.app.state.backend.call_tool("Click", translate.click_args(**args))


async def _do_move(request: Request, args: dict[str, Any]) -> None:
    await request.app.state.backend.call_tool("Move", translate.move_args(**args))


async def _do_type(request: Request, args: dict[str, Any]) -> None:
    await request.app.state.backend.call_tool("Type", translate.type_args(**args))


async def _do_scroll(request: Request, args: dict[str, Any]) -> None:
    await request.app.state.backend.call_tool("Scroll", translate.scroll_args(**args))


async def _do_key(request: Request, args: dict[str, Any]) -> None:
    await request.app.state.backend.call_tool("Shortcut", {"shortcut": translate.shortcut_string(**args)})


async def _do_key_hold(request: Request, args: dict[str, Any]) -> None:
    state = request.app.state.input_state
    name = args["name"]
    duration_ms = int(args.get("duration_ms", 0))
    mods = args.get("modifiers", []) or []
    pressed_mods: list[str] = []
    try:
        for m in mods:
            await state.press_key(m)
            pressed_mods.append(m)
        await state.press_key(name)
        await asyncio.sleep(max(0, duration_ms) / 1000)
    finally:
        await state.release_key(name)
        for m in reversed(pressed_mods):
            await state.release_key(m)


async def _do_key_down(request: Request, args: dict[str, Any]) -> None:
    await request.app.state.input_state.press_key(args["name"])


async def _do_key_up(request: Request, args: dict[str, Any]) -> None:
    await request.app.state.input_state.release_key(args["name"])


async def _do_key_sequence(request: Request, args: dict[str, Any]) -> None:
    state = request.app.state.input_state
    inter = max(0, int(args.get("inter_key_delay_ms", 30))) / 1000
    keys = args["keys"]
    pressed_now: list[str] = []
    try:
        for i, item in enumerate(keys):
            if i > 0 and inter > 0:
                await asyncio.sleep(inter)
            name = item["name"]
            await state.press_key(name)
            pressed_now.append(name)
            await asyncio.sleep(max(0, int(item.get("hold_ms", 0))) / 1000)
            await state.release_key(name)
            pressed_now.pop()
    finally:
        for name in reversed(pressed_now):
            await state.release_key(name)


async def _do_mouse_move_smooth(request: Request, args: dict[str, Any]) -> None:
    from gargaros.routes.mouse import _split_steps  # avoid circular at import time
    driver = request.app.state.input_driver
    steps = max(1, int(args.get("steps", 20)))
    total_dx = int(args.get("dx", 0))
    total_dy = int(args.get("dy", 0))
    duration_ms = int(args.get("duration_ms", 200))
    mode = str(args.get("mode", "absolute")).lower()
    if mode not in ("relative", "absolute"):
        raise ValueError(f"unknown mode {mode!r}; expected 'relative' or 'absolute'")
    xs = _split_steps(total_dx, steps)
    ys = _split_steps(total_dy, steps)
    per_step_ms = max(0, duration_ms) / steps
    if mode == "relative":
        for i in range(steps):
            driver.mouse_move_relative(xs[i], ys[i])
            if i < steps - 1 and per_step_ms > 0:
                await asyncio.sleep(per_step_ms / 1000)
    else:
        cur_x, cur_y = driver.mouse_get_position()
        for i in range(steps):
            cur_x += xs[i]
            cur_y += ys[i]
            driver.mouse_set_position(cur_x, cur_y)
            if i < steps - 1 and per_step_ms > 0:
                await asyncio.sleep(per_step_ms / 1000)


async def _do_mouse_button(request: Request, args: dict[str, Any]) -> None:
    state = request.app.state.input_state
    button = args["button"]
    action = args["action"].lower()
    if action == "down":
        await state.press_button(button)
    elif action == "up":
        await state.release_button(button)
    else:
        raise ValueError(f"unknown mouse action {action!r}; expected 'down' or 'up'")


async def _do_sleep(request: Request, args: dict[str, Any]) -> None:
    ms = int(args.get("ms", 0))
    if ms > 5000:
        raise ValueError(f"sleep max 5000ms, got {ms}")
    await asyncio.sleep(max(0, ms) / 1000)


_OP_HANDLERS: dict[str, Callable[[Request, dict[str, Any]], Awaitable[None]]] = {
    "click": _do_click,
    "move": _do_move,
    "type": _do_type,
    "scroll": _do_scroll,
    "key": _do_key,
    "key_hold": _do_key_hold,
    "key_down": _do_key_down,
    "key_up": _do_key_up,
    "key_sequence": _do_key_sequence,
    "mouse_move_smooth": _do_mouse_move_smooth,
    "mouse_button": _do_mouse_button,
    "sleep": _do_sleep,
}


@post("/batch")
async def batch(request: Request, data: BatchBody) -> dict:
    settings = request.app.state.settings
    if len(data.actions) > settings.batch_max:
        raise ClientException(detail=f"too many actions; max {settings.batch_max}")

    batch_start = time.monotonic()
    results: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0
    for i, action in enumerate(data.actions):
        action_start = time.monotonic()
        try:
            op, args = _extract_op_and_args(action)
        except Exception as e:
            results.append({
                "index": i,
                "type": None,
                "status": "error",
                "elapsed_ms": 0,
                "error": str(e),
            })
            failed += 1
            if not data.continue_on_error:
                break
            continue
        handler = _OP_HANDLERS.get(op)
        if handler is None:
            results.append({
                "index": i,
                "type": op,
                "status": "error",
                "elapsed_ms": int((time.monotonic() - action_start) * 1000),
                "error": f"unknown op: {op}",
            })
            failed += 1
            if not data.continue_on_error:
                break
            continue
        try:
            await handler(request, args)
            results.append({
                "index": i,
                "type": op,
                "status": "ok",
                "elapsed_ms": int((time.monotonic() - action_start) * 1000),
            })
            succeeded += 1
        except Exception as e:  # noqa: BLE001
            results.append({
                "index": i,
                "type": op,
                "status": "error",
                "elapsed_ms": int((time.monotonic() - action_start) * 1000),
                "error": str(e),
            })
            failed += 1
            if not data.continue_on_error:
                break
    total_ms = int((time.monotonic() - batch_start) * 1000)
    return {
        "total_elapsed_ms": total_ms,
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }
