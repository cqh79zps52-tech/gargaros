"""F5 — smooth mouse motion (absolute or relative mode) + held buttons.

`mode="relative"` sends N MOUSEEVENTF_MOVE events that pointer-locked apps
(Chrome cloud gaming, fullscreen FPS) consume as raw WM_INPUT. `mode="absolute"`
interpolates via SetCursorPos and updates the visible cursor — useful outside of
pointer-locked contexts.

`/mouse/button` mirrors /key/down /key/up for mouse buttons (right-click hold
for ADS, etc.) with state tracking and release-on-shutdown.
"""

from __future__ import annotations

import asyncio
import time

from litestar import Request, post
from msgspec import Struct


class MouseMoveSmoothBody(Struct):
    dx: int
    dy: int
    duration_ms: int = 200
    steps: int = 20
    mode: str = "absolute"  # backward-compat default; "relative" for pointer-locked apps


class MouseButtonBody(Struct):
    button: str
    action: str


def _split_steps(total: int, steps: int) -> list[int]:
    """Evenly split `total` into `steps` integer pieces summing exactly to `total`."""
    sign = 1 if total >= 0 else -1
    mag = abs(total)
    base, rem = divmod(mag, steps)
    return [sign * (base + (1 if i < rem else 0)) for i in range(steps)]


@post("/mouse/move_smooth")
async def mouse_move_smooth(request: Request, data: MouseMoveSmoothBody) -> dict:
    driver = request.app.state.input_driver
    steps = max(1, data.steps)
    mode = data.mode.lower()
    if mode not in ("relative", "absolute"):
        raise ValueError(f"unknown mode {data.mode!r}; expected 'relative' or 'absolute'")

    xs = _split_steps(int(data.dx), steps)
    ys = _split_steps(int(data.dy), steps)
    per_step_ms = max(0, data.duration_ms) / steps
    start = time.monotonic()
    done = 0

    if mode == "relative":
        for i in range(steps):
            driver.mouse_move_relative(xs[i], ys[i])
            done += 1
            if i < steps - 1 and per_step_ms > 0:
                await asyncio.sleep(per_step_ms / 1000)
    else:  # absolute
        cur_x, cur_y = driver.mouse_get_position()
        for i in range(steps):
            cur_x += xs[i]
            cur_y += ys[i]
            driver.mouse_set_position(cur_x, cur_y)
            done += 1
            if i < steps - 1 and per_step_ms > 0:
                await asyncio.sleep(per_step_ms / 1000)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    return {"elapsed_ms": elapsed_ms, "steps_done": done, "mode": mode}


@post("/mouse/button")
async def mouse_button(request: Request, data: MouseButtonBody) -> dict:
    state = request.app.state.input_state
    action = data.action.lower()
    if action == "down":
        was_new = await state.press_button(data.button)
        return {"button": data.button, "state": "down", "newly_pressed": was_new}
    if action == "up":
        held_s = await state.release_button(data.button)
        if held_s is None:
            return {"button": data.button, "state": "up", "warning": "button was not down"}
        return {"button": data.button, "state": "up", "was_held_ms": int(held_s * 1000)}
    return {"error": f"unknown action {data.action!r}; expected 'down' or 'up'"}


@post("/mouse/release_all")
async def mouse_release_all(request: Request) -> dict:
    state = request.app.state.input_state
    released = await state.release_all_buttons()
    return {"buttons": released}
