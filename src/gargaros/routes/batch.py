from __future__ import annotations

from typing import Any

from litestar import Request, post
from litestar.exceptions import ClientException
from msgspec import Struct

from gargaros import translate


class Action(Struct):
    op: str
    args: dict[str, Any] = {}


class BatchBody(Struct):
    actions: list[Action]
    continue_on_error: bool = False


_OP_TO_TOOL: dict[str, tuple[str, callable]] = {
    "click": ("Click", lambda a: translate.click_args(**a)),
    "move": ("Move", lambda a: translate.move_args(**a)),
    "type": ("Type", lambda a: translate.type_args(**a)),
    "scroll": ("Scroll", lambda a: translate.scroll_args(**a)),
    "key": ("Shortcut", lambda a: {"shortcut": translate.shortcut_string(**a)}),
}


@post("/batch")
async def batch(request: Request, data: BatchBody) -> dict:
    settings = request.app.state.settings
    if len(data.actions) > settings.batch_max:
        raise ClientException(detail=f"too many actions; max {settings.batch_max}")

    backend = request.app.state.backend
    results: list[dict[str, Any]] = []
    for i, action in enumerate(data.actions):
        op = action.op.lower()
        if op not in _OP_TO_TOOL:
            results.append({"index": i, "ok": False, "error": f"unknown op: {action.op}"})
            if not data.continue_on_error:
                break
            continue
        tool_name, builder = _OP_TO_TOOL[op]
        try:
            tool_args = builder(action.args)
            await backend.call_tool(tool_name, tool_args)
            results.append({"index": i, "ok": True})
        except Exception as e:  # noqa: BLE001
            results.append({"index": i, "ok": False, "error": str(e)})
            if not data.continue_on_error:
                break
    return {"results": results}
