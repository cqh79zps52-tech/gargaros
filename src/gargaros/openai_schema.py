"""Generate OpenAI function-calling tool schemas for Gargaros endpoints.

Pass the result of `tools()` directly to `openai.chat.completions.create(tools=...)`.
"""

from __future__ import annotations

_INT = {"type": "integer"}
_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_NUM = {"type": "number"}


def _tool(name: str, description: str, params: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": params,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def tools() -> list[dict]:
    return [
        _tool(
            "screenshot",
            "Capture a JPEG screenshot of the desktop. Returns image bytes.",
            {"monitor": _INT},
            [],
        ),
        _tool(
            "click",
            "Click at absolute screen coordinates (x, y). button: left/right/middle. double=True for double-click.",
            {"x": _INT, "y": _INT, "button": _STR, "double": _BOOL},
            ["x", "y"],
        ),
        _tool(
            "move",
            "Move the mouse cursor to (x, y). relative=True interprets x,y as offsets from the current position.",
            {"x": _INT, "y": _INT, "relative": _BOOL},
            ["x", "y"],
        ),
        _tool(
            "type",
            "Type text at the current cursor position. press_enter=True submits a newline at the end.",
            {"text": _STR, "press_enter": _BOOL},
            ["text"],
        ),
        _tool(
            "key",
            "Press a key combo. modifiers can include any of ctrl, alt, shift, win.",
            {
                "name": _STR,
                "modifiers": {"type": "array", "items": _STR},
            },
            ["name"],
        ),
        _tool(
            "scroll",
            "Scroll. dy>0 = up, dy<0 = down. dx>0 = right, dx<0 = left.",
            {"dx": _INT, "dy": _INT},
            [],
        ),
        _tool(
            "ui_snapshot",
            "Capture a Windows UI Automation snapshot of the desktop. Returns labeled, clickable UI elements with coords. Call before ui_click_label.",
            {
                "use_vision": _BOOL,
                "use_ui_tree": _BOOL,
                "use_annotation": _BOOL,
            },
            [],
        ),
        _tool(
            "ui_click_label",
            "Click a labeled UI element by its label id from a recent ui_snapshot.",
            {"label": _INT, "button": _STR, "double": _BOOL},
            ["label"],
        ),
        _tool(
            "ui_type_label",
            "Type into a labeled UI element by its label id from a recent ui_snapshot.",
            {"label": _INT, "text": _STR, "clear": _BOOL, "press_enter": _BOOL},
            ["label", "text"],
        ),
        _tool(
            "ui_launch_app",
            "Launch, switch to, or resize a Windows app by name.",
            {"name": _STR, "mode": _STR},
            ["name"],
        ),
        _tool(
            "find",
            "Find text on the current screen via OCR. Returns a list of matches with bounding boxes and centers.",
            {"text": _STR, "monitor": _INT, "fuzzy": _BOOL, "min_confidence": _NUM},
            ["text"],
        ),
        _tool(
            "browser_snapshot",
            "Capture a structured snapshot of the active browser tab via the Gargaros extension: URL, title, visible text, clickable elements with CSS selectors, form inputs, and an optional screenshot.",
            {"tab_id": _INT, "include_screenshot": _BOOL},
            [],
        ),
        _tool(
            "browser_click",
            "Click an element in the active browser tab by CSS selector (use selectors from a recent browser_snapshot).",
            {"selector": _STR, "tab_id": _INT},
            ["selector"],
        ),
        _tool(
            "browser_type",
            "Type text into a form input in the active browser tab by CSS selector. clear=True empties first, press_enter=True submits.",
            {
                "selector": _STR,
                "text": _STR,
                "clear": _BOOL,
                "press_enter": _BOOL,
                "tab_id": _INT,
            },
            ["selector", "text"],
        ),
        _tool(
            "browser_navigate",
            "Navigate the active browser tab to a URL. wait_for_load=True blocks until the tab finishes loading.",
            {"url": _STR, "tab_id": _INT, "wait_for_load": _BOOL},
            ["url"],
        ),
        _tool(
            "browser_tabs",
            "List currently open browser tabs (id, url, title, active).",
            {},
            [],
        ),
    ]
