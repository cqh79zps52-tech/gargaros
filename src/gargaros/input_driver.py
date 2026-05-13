"""Low-level SendInput wrappers used by the gaming endpoints (F3-F5).

These primitives bypass Windows-MCP and call ctypes.windll.user32.SendInput
directly, because Windows-MCP's Shortcut tool fires press+release as a unit
and has no separate KeyDown/KeyUp or MOUSEEVENTF_MOVE-relative path.

The module imports cleanly on non-Windows platforms; calls raise at runtime
if user32 is not available. Tests patch the module-level `_send_input` hook
to capture INPUT payloads without touching real OS input.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable

_PLATFORM_WIN = sys.platform == "win32"

if _PLATFORM_WIN:
    _user32 = ctypes.windll.user32
else:
    _user32 = None


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_KEYUP = 0x0002

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040

_MOUSE_BUTTON_FLAGS: dict[tuple[str, str], int] = {
    ("left", "down"): MOUSEEVENTF_LEFTDOWN,
    ("left", "up"): MOUSEEVENTF_LEFTUP,
    ("right", "down"): MOUSEEVENTF_RIGHTDOWN,
    ("right", "up"): MOUSEEVENTF_RIGHTUP,
    ("middle", "down"): MOUSEEVENTF_MIDDLEDOWN,
    ("middle", "up"): MOUSEEVENTF_MIDDLEUP,
}


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


_VK_MAP: dict[str, int] = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "escape": 0x1B, "esc": 0x1B, "backspace": 0x08,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "win": 0x5B,
    "lshift": 0xA0, "rshift": 0xA1, "lctrl": 0xA2, "rctrl": 0xA3,
    "lalt": 0xA4, "ralt": 0xA5,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "insert": 0x2D, "delete": 0x2E,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91,
}


def virtual_key_code(name: str) -> int:
    """Resolve a human-readable key name to its Windows virtual-key code."""
    lower = name.lower()
    if lower in _VK_MAP:
        return _VK_MAP[lower]
    if len(lower) == 1:
        c = lower.upper()
        if "A" <= c <= "Z":
            return ord(c)
        if "0" <= c <= "9":
            return ord(c)
    if lower.startswith("f") and lower[1:].isdigit():
        n = int(lower[1:])
        if 1 <= n <= 24:
            return 0x70 + n - 1
    raise ValueError(f"unsupported key name: {name!r}")


def _real_send_input(inputs: list[INPUT]) -> int:
    if _user32 is None:
        raise RuntimeError("SendInput unavailable: not running on Windows")
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    return int(_user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT)))


_send_input: Callable[[list[INPUT]], int] = _real_send_input
"""Module-level hook. Tests reassign this to capture INPUT payloads."""


def key_down(name: str) -> None:
    """Press a key without releasing. State tracking lives in InputState."""
    vk = virtual_key_code(name)
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = vk
    inp.ki.wScan = 0
    inp.ki.dwFlags = 0
    inp.ki.time = 0
    _send_input([inp])


def key_up(name: str) -> None:
    """Release a previously-pressed key. Safe to call even if not pressed."""
    vk = virtual_key_code(name)
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = vk
    inp.ki.wScan = 0
    inp.ki.dwFlags = KEYEVENTF_KEYUP
    inp.ki.time = 0
    _send_input([inp])


def mouse_move_relative(dx: int, dy: int) -> None:
    """One relative MOUSEEVENTF_MOVE event (raw input). Use multiples for smooth motion.
    Generates WM_INPUT — required for pointer-locked apps (Chrome cloud gaming, fullscreen FPS)."""
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi.dx = int(dx)
    inp.mi.dy = int(dy)
    inp.mi.mouseData = 0
    inp.mi.dwFlags = MOUSEEVENTF_MOVE
    inp.mi.time = 0
    _send_input([inp])


def _real_get_cursor_pos() -> tuple[int, int]:
    if _user32 is None:
        raise RuntimeError("GetCursorPos unavailable: not running on Windows")

    class _PT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = _PT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def _real_set_cursor_pos(x: int, y: int) -> None:
    if _user32 is None:
        raise RuntimeError("SetCursorPos unavailable: not running on Windows")
    _user32.SetCursorPos(int(x), int(y))


_get_cursor_pos: Callable[[], tuple[int, int]] = _real_get_cursor_pos
_set_cursor_pos: Callable[[int, int], None] = _real_set_cursor_pos


def mouse_set_position(x: int, y: int) -> None:
    """Move the visible cursor to absolute screen coords (SetCursorPos).
    Does NOT generate WM_INPUT — pointer-locked apps won't see this."""
    _set_cursor_pos(x, y)


def mouse_get_position() -> tuple[int, int]:
    """Current cursor screen coords."""
    return _get_cursor_pos()


def mouse_button(button: str, action: str) -> None:
    """Send a single mouse button event. button: left/right/middle, action: down/up."""
    key = (button.lower(), action.lower())
    if key not in _MOUSE_BUTTON_FLAGS:
        raise ValueError(f"unsupported mouse button/action: {button!r}/{action!r}")
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi.dx = 0
    inp.mi.dy = 0
    inp.mi.mouseData = 0
    inp.mi.dwFlags = _MOUSE_BUTTON_FLAGS[key]
    inp.mi.time = 0
    _send_input([inp])
