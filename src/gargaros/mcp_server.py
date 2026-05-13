"""Expose Gargaros as an MCP server (stdio transport).

This is a thin adapter: each MCP tool here just forwards to a running Gargaros
HTTP server on `localhost:7331`. The auth/Host/ETag/batch logic stays in the
HTTP layer; we don't duplicate it here.

Usage::

    # In one terminal
    python -m gargaros

    # In another terminal (or as configured in Claude Desktop's mcp.json)
    gargaros-mcp
"""

from __future__ import annotations

import base64
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from gargaros.client import Client


def _make_client() -> Client:
    base_url = os.environ.get("GARGAROS_URL", "http://127.0.0.1:7331")
    return Client(base_url=base_url)


mcp = FastMCP("gargaros")
_client: Client | None = None


def _c() -> Client:
    global _client
    if _client is None:
        _client = _make_client()
    return _client


@mcp.tool()
def health() -> dict:
    """Check whether the Gargaros HTTP server is up and the Windows-MCP backend is alive."""
    return _c().health()


@mcp.tool()
def screenshot(
    monitor: int = 0,
    raw: bool = False,
    bbox: list[int] | None = None,
) -> str:
    """Capture a screenshot. Default returns JPEG (smaller); set raw=True for PNG without Pillow
    re-encoding (~40% faster, larger output). bbox=[x1,y1,x2,y2] crops to a region — useful to
    isolate the cloud-gaming video stream from the browser chrome. Returns base64-encoded bytes."""
    rect = tuple(bbox) if bbox else None  # type: ignore[arg-type]
    return base64.b64encode(_c().screenshot(monitor=monitor, raw=raw, bbox=rect)).decode("ascii")


@mcp.tool()
def click(x: int, y: int, button: str = "left", double: bool = False) -> str:
    """Click at absolute screen coordinates. button: left/right/middle. double=True for double-click."""
    _c().click(x=x, y=y, button=button, double=double)
    return f"clicked at ({x},{y})"


@mcp.tool()
def move(x: int, y: int, relative: bool = False) -> str:
    """Move the mouse cursor to (x, y). relative=True interprets as offsets from current position."""
    _c().move(x=x, y=y, relative=relative)
    return f"moved to ({x},{y})"


@mcp.tool()
def type_text(text: str, press_enter: bool = False) -> str:
    """Type text at the current cursor position. press_enter=True submits a newline."""
    _c().type(text, press_enter=press_enter)
    return f"typed {len(text)} chars"


@mcp.tool()
def key(name: str, modifiers: list[str] | None = None) -> str:
    """Press a key combo. modifiers can include any of ctrl, alt, shift, win."""
    _c().key(name, modifiers=modifiers or [])
    return f"pressed {'+'.join((modifiers or []) + [name])}"


@mcp.tool()
def scroll(dx: int = 0, dy: int = 0) -> str:
    """Scroll. dy>0 = up, dy<0 = down. dx>0 = right, dx<0 = left."""
    _c().scroll(dx=dx, dy=dy)
    return f"scrolled dx={dx} dy={dy}"


@mcp.tool()
def find(text: str, monitor: int = 0, fuzzy: bool = True, min_confidence: float = 0.5) -> list[dict]:
    """Find text on the current screen via OCR. Returns matches with bounding boxes and centers."""
    return _c().find(text, monitor=monitor, fuzzy=fuzzy, min_confidence=min_confidence)


@mcp.tool()
def ui_snapshot(use_vision: bool = False, use_ui_tree: bool = True, use_annotation: bool = True) -> dict:
    """Capture a Windows UI Automation snapshot of the desktop. Returns labeled clickable elements with coords."""
    return _c().ui_snapshot(use_vision=use_vision, use_ui_tree=use_ui_tree, use_annotation=use_annotation)


@mcp.tool()
def ui_click_label(label: int, button: str = "left", double: bool = False) -> str:
    """Click a labeled UI element by its label id from a recent ui_snapshot."""
    _c().ui_click_label(label=label, button=button, double=double)
    return f"clicked label {label}"


@mcp.tool()
def ui_type_label(label: int, text: str, clear: bool = False, press_enter: bool = False) -> str:
    """Type into a labeled UI element by its label id from a recent ui_snapshot."""
    _c().ui_type_label(label=label, text=text, clear=clear, press_enter=press_enter)
    return f"typed into label {label}"


@mcp.tool()
def ui_launch_app(name: str, mode: str = "launch") -> Any:
    """Launch, switch to, or resize a Windows app by name."""
    return _c().ui_launch_app(name=name, mode=mode)


@mcp.tool()
def browser_snapshot(tab_id: int | None = None, include_screenshot: bool = True) -> dict:
    """Capture a structured snapshot of the active browser tab (text, clickable selectors, inputs, screenshot)."""
    return _c().browser_snapshot(tab_id=tab_id, include_screenshot=include_screenshot)


@mcp.tool()
def browser_click(selector: str, tab_id: int | None = None) -> dict:
    """Click an element in the active browser tab by CSS selector."""
    return _c().browser_click(selector=selector, tab_id=tab_id)


@mcp.tool()
def browser_type(
    selector: str,
    text: str,
    clear: bool = False,
    press_enter: bool = False,
    tab_id: int | None = None,
) -> dict:
    """Type text into a form input in the active browser tab by CSS selector."""
    return _c().browser_type(
        selector=selector, text=text, clear=clear, press_enter=press_enter, tab_id=tab_id
    )


@mcp.tool()
def browser_navigate(url: str, tab_id: int | None = None, wait_for_load: bool = True) -> dict:
    """Navigate the active browser tab to a URL."""
    return _c().browser_navigate(url=url, tab_id=tab_id, wait_for_load=wait_for_load)


@mcp.tool()
def browser_tabs() -> list[dict]:
    """List currently open browser tabs."""
    return _c().browser_tabs()


# ---- Gaming primitives (F3-F5) ----


@mcp.tool()
def key_hold(name: str, duration_ms: int, modifiers: list[str] | None = None) -> dict:
    """Press and hold a key for `duration_ms`, then release. Use this when you need a held
    input (walking forward in a game with W, sprinting with Shift, charging an attack).
    Don't use for single taps — use `key` instead. Don't use for sequences — use `key_sequence`."""
    return _c().key_hold(name, duration_ms, modifiers=modifiers)


@mcp.tool()
def key_down(name: str) -> dict:
    """Press a key down without releasing it. You MUST pair this with a later `key_up` or
    `input_release_all`, otherwise the key stays stuck. Prefer `key_hold` for time-bounded holds."""
    return _c().key_down(name)


@mcp.tool()
def key_up(name: str) -> dict:
    """Release a previously-held key. Idempotent — calling on a key that isn't down returns 200
    with a warning, not an error."""
    return _c().key_up(name)


@mcp.tool()
def key_sequence(keys: list[dict], inter_key_delay_ms: int = 30) -> dict:
    """Execute a sequence of timed key presses, e.g. avance+saute+avance.
    `keys` is a list of {"name": "w", "hold_ms": 800}. Use this for combos that are tighter
    than what a batch of key_hold calls would give. Always cleans up — no stuck keys on error."""
    return _c().key_sequence(keys, inter_key_delay_ms=inter_key_delay_ms)


@mcp.tool()
def key_release_all() -> dict:
    """Release every currently-held key AND mouse button (alias of input_release_all).
    Call this as a safety net if you ever doubt the input state."""
    return _c().key_release_all()


@mcp.tool()
def mouse_move_smooth(
    dx: int,
    dy: int,
    duration_ms: int = 200,
    steps: int = 20,
    mode: str = "absolute",
) -> dict:
    """Move the mouse smoothly over time. Use mode='relative' when the application has captured
    the pointer (fullscreen games, including cloud gaming streams with pointer lock — Chrome on
    xbox.com/play). Use mode='absolute' (default) for normal desktop interactions where the
    visible cursor should move to a new position. dx/dy are pixels in absolute mode, mickeys
    (raw motion deltas) in relative mode. The duration is split into 'steps' smaller movements
    to avoid teleporting the camera."""
    return _c().mouse_move_smooth(dx, dy, duration_ms=duration_ms, steps=steps, mode=mode)


@mcp.tool()
def mouse_button(button: str, action: str) -> dict:
    """Press or release a mouse button. button: left/right/middle. action: down/up.
    Use for held interactions like right-click to ADS in shooters. State is tracked so the
    button is released on server shutdown."""
    return _c().mouse_button(button, action)


@mcp.tool()
def mouse_release_all() -> dict:
    """Release every currently-held mouse button."""
    return _c().mouse_release_all()


@mcp.tool()
def input_release_all() -> dict:
    """Release every held key and mouse button. The big red button for cleanup —
    call when something feels stuck."""
    return _c().input_release_all()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
