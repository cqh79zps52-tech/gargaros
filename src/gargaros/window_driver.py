"""F8 — Windows window-management primitives (ctypes wrappers).

EnumWindows + GetWindowText / GetWindowThreadProcessId / GetWindowRect for listing,
GetForegroundWindow for active-window lookup, SetForegroundWindow + ShowWindow for
focus changes. SetForegroundWindow has a well-known reliability issue when the caller
isn't the current foreground window — we use the AttachThreadInput workaround.

Module imports cleanly on non-Windows so tests can monkey-patch the OS hooks.
"""

from __future__ import annotations

import ctypes
import re
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

_PLATFORM_WIN = sys.platform == "win32"

if _PLATFORM_WIN:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _psapi = ctypes.windll.psapi
else:
    _user32 = None
    _kernel32 = None
    _psapi = None


SW_RESTORE = 9
SW_SHOWNORMAL = 1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process_name: str
    pid: int
    is_foreground: bool
    is_visible: bool
    is_minimized: bool
    bounds: dict  # {x, y, width, height}

    def to_dict(self) -> dict:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "process_name": self.process_name,
            "pid": self.pid,
            "is_foreground": self.is_foreground,
            "is_visible": self.is_visible,
            "is_minimized": self.is_minimized,
            "bounds": self.bounds,
        }


# ---- low-level Win32 wrappers (monkey-patchable for tests) ----


def _real_enum_windows() -> list[int]:
    if _user32 is None:
        raise RuntimeError("EnumWindows unavailable: not running on Windows")
    hwnds: list[int] = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd: int, _lparam: int) -> bool:
        hwnds.append(int(hwnd))
        return True

    _user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return hwnds


def _real_get_window_text(hwnd: int) -> str:
    if _user32 is None:
        return ""
    length = _user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _real_get_window_thread_process_id(hwnd: int) -> tuple[int, int]:
    """Returns (thread_id, process_id)."""
    if _user32 is None:
        return (0, 0)
    pid = wintypes.DWORD(0)
    tid = _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(tid), int(pid.value)


def _real_get_process_name(pid: int) -> str:
    if _kernel32 is None or _psapi is None or pid == 0:
        return ""
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        # QueryFullProcessImageNameW returns the full path; we want just the basename
        size = wintypes.DWORD(260)
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            full = buf.value
            return full.rsplit("\\", 1)[-1] if "\\" in full else full
        return ""
    finally:
        _kernel32.CloseHandle(h)


def _real_is_window_visible(hwnd: int) -> bool:
    if _user32 is None:
        return False
    return bool(_user32.IsWindowVisible(hwnd))


def _real_is_iconic(hwnd: int) -> bool:
    """IsIconic returns true if the window is minimized."""
    if _user32 is None:
        return False
    return bool(_user32.IsIconic(hwnd))


def _real_get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    if _user32 is None:
        return (0, 0, 0, 0)
    rect = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def _real_get_foreground_window() -> int:
    if _user32 is None:
        return 0
    return int(_user32.GetForegroundWindow())


def _real_set_foreground_window(hwnd: int) -> bool:
    """Bring window to foreground. Windows fights us here — there are three layers
    of defense Microsoft added against focus-stealing apps. The robust workaround:

      1. Send a benign Alt keystroke to clear the "no foreground change" lock that
         Windows sets when no input has reached our process recently.
      2. AttachThreadInput so SetForegroundWindow sees us as part of the FG thread.
      3. Call AllowSetForegroundWindow(ASFW_ANY).
      4. Then SetForegroundWindow + BringWindowToTop.
    """
    if _user32 is None or _kernel32 is None:
        return False
    fg = _user32.GetForegroundWindow()
    if fg == hwnd:
        return True

    # (1) Synthetic Alt press resets Windows' focus-stealing prevention timer.
    # Without this, SetForegroundWindow silently fails when our thread hasn't
    # received input recently.
    KEYEVENTF_KEYUP = 0x0002
    VK_MENU = 0x12
    _user32.keybd_event(VK_MENU, 0, 0, 0)
    _user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

    fg_tid = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
    our_tid = _kernel32.GetCurrentThreadId()
    attached = False
    if fg_tid and fg_tid != our_tid:
        attached = bool(_user32.AttachThreadInput(our_tid, fg_tid, True))
    try:
        _user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        _user32.BringWindowToTop(hwnd)
        ok = bool(_user32.SetForegroundWindow(hwnd))
        return ok
    finally:
        if attached:
            _user32.AttachThreadInput(our_tid, fg_tid, False)


def _real_show_window(hwnd: int, cmd: int) -> bool:
    if _user32 is None:
        return False
    return bool(_user32.ShowWindow(hwnd, cmd))


# Monkey-patchable hooks for tests
_enum_windows: Callable[[], list[int]] = _real_enum_windows
_get_window_text: Callable[[int], str] = _real_get_window_text
_get_window_thread_process_id: Callable[[int], tuple[int, int]] = _real_get_window_thread_process_id
_get_process_name: Callable[[int], str] = _real_get_process_name
_is_window_visible: Callable[[int], bool] = _real_is_window_visible
_is_iconic: Callable[[int], bool] = _real_is_iconic
_get_window_rect: Callable[[int], tuple[int, int, int, int]] = _real_get_window_rect
_get_foreground_window: Callable[[], int] = _real_get_foreground_window
_set_foreground_window: Callable[[int], bool] = _real_set_foreground_window
_show_window: Callable[[int, int], bool] = _real_show_window


# ---- public API ----


def _window_info(hwnd: int, foreground_hwnd: int) -> WindowInfo:
    title = _get_window_text(hwnd)
    _tid, pid = _get_window_thread_process_id(hwnd)
    process_name = _get_process_name(pid)
    is_visible = _is_window_visible(hwnd)
    is_minimized = _is_iconic(hwnd)
    left, top, right, bottom = _get_window_rect(hwnd)
    bounds = {"x": left, "y": top, "width": right - left, "height": bottom - top}
    return WindowInfo(
        hwnd=hwnd,
        title=title,
        process_name=process_name,
        pid=pid,
        is_foreground=(hwnd == foreground_hwnd),
        is_visible=is_visible,
        is_minimized=is_minimized,
        bounds=bounds,
    )


def list_windows(visible_only: bool = True) -> list[WindowInfo]:
    """Enumerate top-level windows. visible_only filters out hidden system windows."""
    fg = _get_foreground_window()
    out: list[WindowInfo] = []
    for hwnd in _enum_windows():
        info = _window_info(hwnd, fg)
        if visible_only and (not info.is_visible or not info.title):
            continue
        out.append(info)
    return out


def get_active_window() -> WindowInfo | None:
    """Return the currently-foreground window."""
    fg = _get_foreground_window()
    if not fg:
        return None
    return _window_info(fg, fg)


def find_windows(selector: dict) -> list[WindowInfo]:
    """Find windows matching the selector. Supports title_contains (case-insensitive
    substring), title_regex, process_name (case-insensitive exact), and hwnd. All keys
    combine with AND."""
    if not selector:
        return []
    title_contains = selector.get("title_contains")
    title_regex = selector.get("title_regex")
    process_name = selector.get("process_name")
    target_hwnd = selector.get("hwnd")

    regex = re.compile(title_regex) if title_regex else None
    pn_lower = process_name.lower() if process_name else None
    fg = _get_foreground_window()

    def _matches(info: WindowInfo) -> bool:
        if title_contains and title_contains.lower() not in info.title.lower():
            return False
        if regex and not regex.search(info.title):
            return False
        if pn_lower and info.process_name.lower() != pn_lower:
            return False
        return True

    # Direct hwnd lookup bypasses EnumWindows — special shell/system windows
    # (the desktop's WorkerW, popups, etc.) are reachable via GetForegroundWindow
    # but skipped by EnumWindows.
    if target_hwnd is not None:
        info = _window_info(target_hwnd, fg)
        if info.pid == 0:  # invalid hwnd
            return []
        return [info] if _matches(info) else []

    matches: list[WindowInfo] = []
    for hwnd in _enum_windows():
        info = _window_info(hwnd, fg)
        if not info.is_visible or not info.title:
            continue
        if _matches(info):
            matches.append(info)
    return matches


def focus_window(hwnd: int, restore_if_minimized: bool = True) -> bool:
    """Bring `hwnd` to foreground. Restores if minimized when requested."""
    if restore_if_minimized and _is_iconic(hwnd):
        _show_window(hwnd, SW_RESTORE)
    return _set_foreground_window(hwnd)
