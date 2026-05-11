from __future__ import annotations

from typing import Annotated, Any

from litestar import Request, post
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
    args = translate.click_args(x=data.x, y=data.y, button=data.button, double=data.double)
    await request.app.state.backend.call_tool("Click", args)
    return {"ok": True}


@post("/move")
async def move(request: Request, data: MoveBody) -> dict:
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
    shortcut = translate.shortcut_string(name=data.name, modifiers=data.modifiers)
    await request.app.state.backend.call_tool("Shortcut", {"shortcut": shortcut})
    return {"ok": True}


@post("/scroll")
async def scroll(request: Request, data: ScrollBody) -> dict:
    args = translate.scroll_args(dx=data.dx, dy=data.dy, x=data.x, y=data.y)
    await request.app.state.backend.call_tool("Scroll", args)
    return {"ok": True}


@post("/drag")
async def drag(request: Request, data: DragBody) -> dict:
    backend = request.app.state.backend
    await backend.call_tool("Move", translate.move_args(x=data.src.x, y=data.src.y, drag=False))
    await backend.call_tool("Move", translate.move_args(x=data.to.x, y=data.to.y, drag=True))
    return {"ok": True}
