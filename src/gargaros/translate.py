"""Pure functions converting Gargaros HTTP payloads into Windows-MCP tool args."""

from __future__ import annotations

from typing import Any


def click_args(*, x: int, y: int, button: str = "left", double: bool = False) -> dict[str, Any]:
    if button not in {"left", "right", "middle"}:
        raise ValueError(f"invalid button: {button}")
    return {"loc": [int(x), int(y)], "button": button, "clicks": 2 if double else 1}


def move_args(*, x: int, y: int, drag: bool = False) -> dict[str, Any]:
    return {"loc": [int(x), int(y)], "drag": bool(drag)}


def type_args(*, text: str, loc: list[int] | None = None, press_enter: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"text": text, "press_enter": bool(press_enter)}
    if loc is not None:
        if len(loc) != 2:
            raise ValueError("loc must be [x,y]")
        out["loc"] = [int(loc[0]), int(loc[1])]
    return out


def scroll_args(*, dx: int = 0, dy: int = 0, x: int | None = None, y: int | None = None) -> dict[str, Any]:
    """eyehands-style {dx,dy} -> Windows-MCP Scroll args.

    Sign convention: dy>0 scrolls up, dy<0 scrolls down. dx>0 scrolls right, dx<0 scrolls left.
    Wheel_times = abs() of the dominant axis (rounded up to 1 if any).
    """
    if dx == 0 and dy == 0:
        raise ValueError("scroll requires at least one of dx, dy")
    if abs(dy) >= abs(dx):
        type_ = "vertical"
        direction = "up" if dy > 0 else "down"
        wheel_times = max(1, abs(dy))
    else:
        type_ = "horizontal"
        direction = "right" if dx > 0 else "left"
        wheel_times = max(1, abs(dx))
    out: dict[str, Any] = {"type": type_, "direction": direction, "wheel_times": int(wheel_times)}
    if x is not None and y is not None:
        out["loc"] = [int(x), int(y)]
    return out


def snapshot_args(
    *,
    use_vision: bool = False,
    use_dom: bool = False,
    use_annotation: bool = True,
    use_ui_tree: bool = True,
    width_reference_line: int | None = None,
    height_reference_line: int | None = None,
    display: list[int] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "use_vision": bool(use_vision),
        "use_dom": bool(use_dom),
        "use_annotation": bool(use_annotation),
        "use_ui_tree": bool(use_ui_tree),
    }
    if width_reference_line is not None:
        out["width_reference_line"] = int(width_reference_line)
    if height_reference_line is not None:
        out["height_reference_line"] = int(height_reference_line)
    if display is not None:
        out["display"] = [int(d) for d in display]
    return out


def click_label_args(*, label: int, button: str = "left", double: bool = False) -> dict[str, Any]:
    if button not in {"left", "right", "middle"}:
        raise ValueError(f"invalid button: {button}")
    return {"label": int(label), "button": button, "clicks": 2 if double else 1}


def type_label_args(*, label: int, text: str, clear: bool = False, press_enter: bool = False) -> dict[str, Any]:
    return {
        "label": int(label),
        "text": text,
        "clear": bool(clear),
        "press_enter": bool(press_enter),
    }


def scrape_args(*, url: str, query: str | None = None, use_dom: bool = False, use_sampling: bool = True) -> dict[str, Any]:
    if not url:
        raise ValueError("url is required")
    out: dict[str, Any] = {"url": url, "use_dom": bool(use_dom), "use_sampling": bool(use_sampling)}
    if query is not None:
        out["query"] = query
    return out


def app_args(
    *,
    name: str | None = None,
    mode: str = "launch",
    window_loc: list[int] | None = None,
    window_size: list[int] | None = None,
) -> dict[str, Any]:
    if mode not in {"launch", "resize", "switch"}:
        raise ValueError(f"invalid app mode: {mode}")
    out: dict[str, Any] = {"mode": mode}
    if name is not None:
        out["name"] = name
    if window_loc is not None:
        if len(window_loc) != 2:
            raise ValueError("window_loc must be [x,y]")
        out["window_loc"] = [int(window_loc[0]), int(window_loc[1])]
    if window_size is not None:
        if len(window_size) != 2:
            raise ValueError("window_size must be [w,h]")
        out["window_size"] = [int(window_size[0]), int(window_size[1])]
    return out


def shortcut_string(*, name: str, modifiers: list[str] | None = None) -> str:
    """Build a Windows-MCP Shortcut tool argument like 'ctrl+shift+a'."""
    parts: list[str] = []
    if modifiers:
        for m in modifiers:
            ml = m.strip().lower()
            if ml not in {"ctrl", "alt", "shift", "win"}:
                raise ValueError(f"invalid modifier: {m}")
            parts.append(ml)
    if not name:
        raise ValueError("key name is required")
    parts.append(name.strip().lower())
    return "+".join(parts)


def extract_image_bytes(call_tool_result: Any) -> bytes | None:
    """Pull the first image payload out of an mcp.types.CallToolResult.

    Returns raw bytes (already decoded from base64) or None if no image content found.
    """
    import base64

    content_iter = getattr(call_tool_result, "content", None) or []
    for item in content_iter:
        data = getattr(item, "data", None)
        mime = getattr(item, "mimeType", None) or getattr(item, "mime_type", None)
        if data and mime and mime.startswith("image/"):
            try:
                return base64.b64decode(data)
            except Exception:  # noqa: BLE001
                continue
    return None
