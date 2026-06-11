from __future__ import annotations

from typing import Any

import anyio
from litestar import Request, post
from litestar.exceptions import ClientException, HTTPException
from msgspec import Struct

from gargaros import translate


class SnapshotBody(Struct):
    use_vision: bool = False
    use_dom: bool = False
    use_annotation: bool = True
    use_ui_tree: bool = True
    width_reference_line: int | None = None
    height_reference_line: int | None = None
    display: list[int] | None = None


class ClickLabelBody(Struct):
    label: int
    button: str = "left"
    double: bool = False


class TypeLabelBody(Struct):
    label: int
    text: str
    clear: bool = False
    press_enter: bool = False


class ScrapeBody(Struct):
    url: str
    query: str | None = None
    use_dom: bool = False
    use_sampling: bool = True


class LaunchAppBody(Struct):
    name: str
    mode: str = "launch"
    window_loc: list[int] | None = None
    window_size: list[int] | None = None


class AgentLaunchBody(Struct):
    cmdline: str


def _serialize_content(result: Any) -> dict:
    """Flatten an mcp.types.CallToolResult into a JSON-serializable dict."""
    out: dict[str, Any] = {"text": [], "images": [], "isError": bool(getattr(result, "isError", False))}
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text is not None:
            out["text"].append(text)
            continue
        data = getattr(item, "data", None)
        mime = getattr(item, "mimeType", None) or getattr(item, "mime_type", None)
        if data and mime:
            out["images"].append({"mime": mime, "b64": data})
    return out


def _surface_label_error(result: Any) -> None:
    """If Windows-MCP complains the desktop state is empty, raise 409."""
    if not getattr(result, "isError", False):
        return
    text_parts = [getattr(item, "text", "") for item in getattr(result, "content", []) or []]
    msg = " ".join(t for t in text_parts if t)
    if "Desktop state is empty" in msg or "call Snapshot first" in msg.lower():
        raise HTTPException(status_code=409, detail="call /ui/snapshot first to populate desktop state")
    if msg:
        raise ClientException(detail=msg)


def _uia_active(request: Request):
    """Return the HiddenDesktop if its UIA layer is usable, else None."""
    hd = getattr(request.app.state, "hidden_desktop", None)
    if hd is not None and getattr(hd, "uia_ready", False):
        return hd
    return None


@post("/ui/snapshot")
async def ui_snapshot(request: Request, data: SnapshotBody) -> dict:
    hd = _uia_active(request)
    if hd is not None:
        elements = await anyio.to_thread.run_sync(hd.uia_snapshot)
        return {"elements": elements, "count": len(elements), "source": "hidden_desktop"}
    args = translate.snapshot_args(
        use_vision=data.use_vision,
        use_dom=data.use_dom,
        use_annotation=data.use_annotation,
        use_ui_tree=data.use_ui_tree,
        width_reference_line=data.width_reference_line,
        height_reference_line=data.height_reference_line,
        display=data.display,
    )
    result = await request.app.state.backend.call_tool("Snapshot", args)
    return _serialize_content(result)


@post("/ui/click_label")
async def ui_click_label(request: Request, data: ClickLabelBody) -> dict:
    hd = _uia_active(request)
    if hd is not None:
        try:
            method = await anyio.to_thread.run_sync(hd.uia_click_label, data.label)
        except KeyError:
            raise HTTPException(
                status_code=409, detail="unknown label; call /ui/snapshot first"
            ) from None
        return {"ok": True, "method": method}
    args = translate.click_label_args(label=data.label, button=data.button, double=data.double)
    result = await request.app.state.backend.call_tool("Click", args)
    _surface_label_error(result)
    return {"ok": True}


@post("/ui/type_label")
async def ui_type_label(request: Request, data: TypeLabelBody) -> dict:
    hd = _uia_active(request)
    if hd is not None:
        try:
            method = await anyio.to_thread.run_sync(
                hd.uia_type_label, data.label, data.text, data.clear, data.press_enter
            )
        except KeyError:
            raise HTTPException(
                status_code=409, detail="unknown label; call /ui/snapshot first"
            ) from None
        return {"ok": True, "method": method}
    args = translate.type_label_args(
        label=data.label,
        text=data.text,
        clear=data.clear,
        press_enter=data.press_enter,
    )
    result = await request.app.state.backend.call_tool("Type", args)
    _surface_label_error(result)
    return {"ok": True}


@post("/ui/scrape")
async def ui_scrape(request: Request, data: ScrapeBody) -> dict:
    args = translate.scrape_args(
        url=data.url, query=data.query, use_dom=data.use_dom, use_sampling=data.use_sampling
    )
    result = await request.app.state.backend.call_tool("Scrape", args)
    return _serialize_content(result)


@post("/ui/launch_app")
async def ui_launch_app(request: Request, data: LaunchAppBody) -> dict:
    # Note: this routes through Windows-MCP and launches on the VISIBLE desktop. To launch
    # on the hidden desktop (agent_dsk), use POST /agent/launch_app instead.
    args = translate.app_args(
        name=data.name, mode=data.mode, window_loc=data.window_loc, window_size=data.window_size
    )
    result = await request.app.state.backend.call_tool("App", args)
    return _serialize_content(result)


@post("/agent/launch_app")
async def agent_launch_app(request: Request, data: AgentLaunchBody) -> dict:
    """Launch a process on the hidden desktop (agent_dsk) via CreateProcess.

    ``cmdline`` is passed straight to CreateProcess, so use a real command line such as
    "notepad.exe" or "msedge.exe https://example.com" (no Start-menu name resolution).
    """
    hd = getattr(request.app.state, "hidden_desktop", None)
    if hd is None:
        raise HTTPException(
            status_code=409,
            detail="hidden desktop is not active (GARGAROS_HIDDEN_DESKTOP_ENABLED=0?)",
        )
    if not data.cmdline.strip():
        raise ClientException(detail="cmdline is required")
    pid = await anyio.to_thread.run_sync(hd.launch, data.cmdline)
    return {"ok": True, "pid": pid, "desktop": hd.name}
