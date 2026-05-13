"""F8 — window-management endpoints.

Required because SendInput targets the foreground window. Agents need to explicitly
focus Chrome (xCloud) before driving keys/mouse, and want to verify focus before
each batch via the expect_focus mechanism in input endpoints.
"""

from __future__ import annotations

import asyncio
import time

from litestar import Request, get, post
from litestar.exceptions import HTTPException
from msgspec import Struct

from gargaros import window_driver


class Selector(Struct, omit_defaults=True):
    title_contains: str | None = None
    title_regex: str | None = None
    process_name: str | None = None
    hwnd: int | None = None


class FocusBody(Struct):
    selector: dict
    restore_if_minimized: bool = True


class WaitForFocusBody(Struct):
    selector: dict
    timeout_ms: int = 5000
    poll_interval_ms: int = 50


def _selector_is_empty(selector: dict | None) -> bool:
    if not selector:
        return True
    return not any(selector.get(k) for k in ("title_contains", "title_regex", "process_name", "hwnd"))


def check_expect_focus(selector: dict | None) -> None:
    """Raise 412 if a non-empty selector is given and no foreground window matches.
    Called by input endpoints with `expect_focus=`.
    """
    if _selector_is_empty(selector):
        return
    active = window_driver.get_active_window()
    if active is None:
        raise HTTPException(
            status_code=412,
            detail={"error": "focus_mismatch", "expected": str(selector), "actual": None},
        )
    matches = window_driver.find_windows(selector)
    if not any(m.hwnd == active.hwnd for m in matches):
        raise HTTPException(
            status_code=412,
            detail={
                "error": "focus_mismatch",
                "expected": str(selector),
                "actual": active.title or active.process_name,
            },
        )


@get("/window/list")
async def window_list(request: Request, visible_only: bool = True) -> list[dict]:
    return [w.to_dict() for w in window_driver.list_windows(visible_only=visible_only)]


@get("/window/active")
async def window_active(request: Request) -> dict:
    info = window_driver.get_active_window()
    if info is None:
        raise HTTPException(status_code=404, detail="no foreground window")
    return info.to_dict()


@post("/window/focus")
async def window_focus(request: Request, data: FocusBody) -> dict:
    if _selector_is_empty(data.selector):
        raise HTTPException(status_code=400, detail="selector must be non-empty")
    start = time.monotonic()
    matches = window_driver.find_windows(data.selector)
    if not matches:
        raise HTTPException(status_code=404, detail=f"no window matches {data.selector}")
    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "ambiguous_selector",
                "matches": [{"hwnd": m.hwnd, "title": m.title, "pid": m.pid} for m in matches],
            },
        )
    target = matches[0]
    ok = window_driver.focus_window(target.hwnd, restore_if_minimized=data.restore_if_minimized)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return {"hwnd": target.hwnd, "title": target.title, "elapsed_ms": elapsed_ms, "ok": ok}


@post("/window/wait_for_focus")
async def window_wait_for_focus(request: Request, data: WaitForFocusBody) -> dict:
    if _selector_is_empty(data.selector):
        raise HTTPException(status_code=400, detail="selector must be non-empty")
    start = time.monotonic()
    interval = max(10, int(data.poll_interval_ms)) / 1000
    deadline = start + max(0, int(data.timeout_ms)) / 1000
    while True:
        active = window_driver.get_active_window()
        if active is not None:
            matches = window_driver.find_windows(data.selector)
            if any(m.hwnd == active.hwnd for m in matches):
                return {"matched": True, "elapsed_ms": int((time.monotonic() - start) * 1000)}
        if time.monotonic() >= deadline:
            raise HTTPException(
                status_code=408,
                detail={
                    "matched": False,
                    "elapsed_ms": int((time.monotonic() - start) * 1000),
                },
            )
        await asyncio.sleep(interval)
