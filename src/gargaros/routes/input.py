from __future__ import annotations

from typing import Annotated, Any

import anyio
from litestar import Request, post
from litestar.exceptions import HTTPException
from msgspec import Struct

from gargaros import translate


class ClickBody(Struct):
    x: int
    y: int
    button: str = "left"
    double: bool = False


class MoveBody(Struct):
    x: int
    y: int
    relative: bool = False


class TypeBody(Struct):
    text: str
    press_enter: bool = False


class KeyBody(Struct):
    name: str
    modifiers: list[str] = []


class ScrollBody(Struct):
    dx: int = 0
    dy: int = 0
    x: int | None = None
    y: int | None = None


class Point(Struct):
    x: int
    y: int


class DragBody(Struct):
    src: Annotated[Point, "from"]  # 'from' is a reserved keyword in Python
    to: Point
    button: str = "left"


@post("/click")
async def click(request: Request, data: ClickBody) -> dict:
    hd = getattr(request.app.state, "hidden_desktop", None)
    if hd is not None:
        def _do() -> None:
            # Coordinates are absolute virtual-screen pixels == composite pixel space.
            res = hd.resolve_hwnd(data.x, data.y)
            if res is not None:
                hwnd, lx, ly = res
                hd.click_background(hwnd, lx, ly, data.button, data.double)  # PostMessage, no cursor
            else:
                hd.send_input_click(data.x, data.y, data.button, data.double)  # fallback on agent_dsk
        await anyio.to_thread.run_sync(_do)
        return {"ok": True}
    args = translate.click_args(x=data.x, y=data.y, button=data.button, double=data.double)
    await request.app.state.backend.call_tool("Click", args)
    return {"ok": True}


@post("/move")
async def move(request: Request, data: MoveBody) -> dict:
    hd = getattr(request.app.state, "hidden_desktop", None)
    if hd is not None:
        if data.relative:
            # No user cursor exists on the hidden desktop to anchor a relative move.
            raise HTTPException(
                status_code=409,
                detail="relative move is not supported on the hidden desktop; use absolute x,y",
            )

        def _do() -> None:
            res = hd.resolve_hwnd(data.x, data.y)
            if res is not None:
                hwnd, lx, ly = res
                hd.move_background(hwnd, lx, ly)
            else:
                hd.send_input_move(data.x, data.y)
        await anyio.to_thread.run_sync(_do)
        return {"ok": True}

    if data.relative:
        # Windows-MCP only supports absolute coords; resolve via current cursor pos
        import ctypes

        class _PT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = _PT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        target_x, target_y = pt.x + data.x, pt.y + data.y
    else:
        target_x, target_y = data.x, data.y
    args = translate.move_args(x=target_x, y=target_y, drag=False)
    await request.app.state.backend.call_tool("Move", args)
    return {"ok": True}


@post("/type")
async def type_text(request: Request, data: TypeBody) -> dict:
    hd = getattr(request.app.state, "hidden_desktop", None)
    if hd is not None:
        def _do() -> None:
            hwnd = hd.foreground_hidden_hwnd()
            if hwnd is not None:
                hd.type_background(hwnd, data.text, data.press_enter)  # WM_CHAR, no cursor
            else:
                hd.send_input_type(data.text, data.press_enter)  # fallback on agent_dsk
        await anyio.to_thread.run_sync(_do)
        return {"ok": True}

    args: dict[str, Any] = translate.type_args(text=data.text, press_enter=data.press_enter)
    if "loc" not in args:
        # Type tool requires loc; default to current cursor position so we don't have to click first
        import ctypes

        class _PT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = _PT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        args["loc"] = [pt.x, pt.y]
    await request.app.state.backend.call_tool("Type", args)
    return {"ok": True}


@post("/key")
async def key(request: Request, data: KeyBody) -> dict:
    # Pass 1: shortcuts still route through Windows-MCP (visible desktop). Hidden-desktop
    # keyboard shortcuts will arrive with the dedicated UIA/SendInput pass.
    shortcut = translate.shortcut_string(name=data.name, modifiers=data.modifiers)
    await request.app.state.backend.call_tool("Shortcut", {"shortcut": shortcut})
    return {"ok": True}


@post("/scroll")
async def scroll(request: Request, data: ScrollBody) -> dict:
    hd = getattr(request.app.state, "hidden_desktop", None)
    if hd is not None:
        x = data.x if data.x is not None else 0
        y = data.y if data.y is not None else 0

        def _do() -> None:
            res = hd.resolve_hwnd(x, y) if (data.x is not None and data.y is not None) else None
            if res is not None:
                hwnd, _lx, _ly = res
                hd.scroll_background(hwnd, x, y, data.dx, data.dy)
            else:
                hd.send_input_scroll(x, y, data.dx, data.dy)
        await anyio.to_thread.run_sync(_do)
        return {"ok": True}

    args = translate.scroll_args(dx=data.dx, dy=data.dy, x=data.x, y=data.y)
    await request.app.state.backend.call_tool("Scroll", args)
    return {"ok": True}


@post("/drag")
async def drag(request: Request, data: DragBody) -> dict:
    hd = getattr(request.app.state, "hidden_desktop", None)
    if hd is not None:
        def _do() -> None:
            res = hd.resolve_hwnd(data.src.x, data.src.y)
            if res is not None:
                hwnd, lx1, ly1 = res
                # Convert the destination into the same window's local coords (window
                # left/top = src.x - lx1, src.y - ly1).
                lx2 = data.to.x - (data.src.x - lx1)
                ly2 = data.to.y - (data.src.y - ly1)
                hd.drag_background(hwnd, lx1, ly1, lx2, ly2, data.button)
            else:
                hd.send_input_drag(data.src.x, data.src.y, data.to.x, data.to.y, data.button)
        await anyio.to_thread.run_sync(_do)
        return {"ok": True}

    backend = request.app.state.backend
    await backend.call_tool("Move", translate.move_args(x=data.src.x, y=data.src.y, drag=False))
    await backend.call_tool("Move", translate.move_args(x=data.to.x, y=data.to.y, drag=True))
    return {"ok": True}
