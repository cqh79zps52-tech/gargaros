"""Hidden secondary Windows desktop ("agent_dsk") layer.

The agent's applications live on a separate Windows *desktop object* created with
CreateDesktop. We never call SwitchDesktop towards it, so it stays invisible and the
user's cursor/keyboard focus on the visible desktop are never disturbed.

Because DXGI/Desktop-Duplication only captures the displayed desktop, we capture each
hidden window with PrintWindow(PW_RENDERFULLCONTENT) and composite them onto a black
canvas in absolute virtual-screen coordinates. Actions go through PostMessage (no cursor)
or, as a fallback, SendInput executed on a dedicated thread permanently attached to the
hidden desktop via SetThreadDesktop.

This module is pure pywin32/ctypes and fully synchronous; async HTTP handlers bridge to it
through anyio.to_thread.run_sync.
"""

from __future__ import annotations

import concurrent.futures
import ctypes
import logging
import queue
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

# comtypes (imported lazily by the UIA layer) calls CoInitializeEx at import time using
# sys.coinit_flags. Force MTA (0): the UIA worker thread makes blocking out-of-process COM
# calls (SetFocus/SetValue) with no message pump, which would deadlock under STA.
if not hasattr(sys, "coinit_flags"):
    sys.coinit_flags = 0  # COINIT_MULTITHREADED

import pywintypes
import win32api
import win32gui
import win32process
import win32service
from PIL import Image

logger = logging.getLogger(__name__)

# --- Win32 constants not reliably exposed by pywin32 in this build ----------------
PW_RENDERFULLCONTENT = 2
GENERIC_ALL = 0x10000000
WHEEL_DELTA = 120

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WM_CHAR = 0x0102

MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_MBUTTON = 0x0010

# Virtual-screen metrics
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

_BUTTONS = {
    "left": (WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON),
    "right": (WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON),
    "middle": (WM_MBUTTONDOWN, WM_MBUTTONUP, MK_MBUTTON),
}

# --- SendInput (ctypes) structures ------------------------------------------------
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002

_MOUSE_DOWN_UP = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _send_inputs(inputs: list[_INPUT]) -> None:
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    ctypes.windll.user32.SendInput(n, arr, ctypes.sizeof(_INPUT))


def _mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> _INPUT:
    mi = _MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, None)
    return _INPUT(INPUT_MOUSE, _INPUTUNION(mi=mi))


def _key_input(char: str, keyup: bool = False) -> _INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if keyup else 0)
    ki = _KEYBDINPUT(0, ord(char), flags, 0, None)
    return _INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=ki))


def _vk_input(vk: int, keyup: bool = False) -> _INPUT:
    flags = KEYEVENTF_KEYUP if keyup else 0
    ki = _KEYBDINPUT(vk, 0, flags, 0, None)
    return _INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=ki))


VK_CONTROL = 0x11
VK_DELETE = 0x2E
VK_RETURN = 0x0D
VK_A = 0x41


def _lparam(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


# --- process-tree resolution ------------------------------------------------------
# Some launchers (e.g. notepad.exe on Windows 11) are stubs that spawn the real window
# under a different PID, so an exact-PID match misses the window. We therefore match any
# descendant of a launched PID.
TH32CS_SNAPPROCESS = 0x00000002

# Window classes that live on every desktop but are never agent content.
_SYSTEM_WINDOW_CLASSES = frozenset({
    "IME", "MSCTFIME UI", "Default IME", "CicLoaderWndClass",
    "TF_FloatingLangBar_WndTitle", "tooltips_class32",
})


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def _parent_map() -> dict[int, int]:
    """Snapshot {pid: parent_pid} for all processes."""
    out: dict[int, int] = {}
    snap = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1 or snap == 0:
        return out
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        ok = ctypes.windll.kernel32.Process32First(snap, ctypes.byref(entry))
        while ok:
            out[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = ctypes.windll.kernel32.Process32Next(snap, ctypes.byref(entry))
    finally:
        ctypes.windll.kernel32.CloseHandle(snap)
    return out


def descendant_pids(roots: set[int]) -> set[int]:
    """All PIDs whose ancestry chain reaches one of ``roots`` (roots included)."""
    parents = _parent_map()
    result = set(roots)
    for pid in parents:
        seen = set()
        cur = pid
        while cur and cur not in seen:
            seen.add(cur)
            if cur in roots:
                result.update(seen)
                break
            cur = parents.get(cur, 0)
    return result


# UIA control types we treat as interactive / labelable.
INTERACTIVE_CONTROL_TYPES = frozenset({
    "ButtonControl", "EditControl", "CheckBoxControl", "RadioButtonControl",
    "ComboBoxControl", "ListItemControl", "MenuItemControl", "HyperlinkControl",
    "TabItemControl", "SliderControl", "SpinnerControl", "TreeItemControl",
    "DocumentControl", "SplitButtonControl", "ToggleButtonControl", "ThumbControl",
})


def _safe(fn, default=None):
    """Run a UIA accessor, swallowing dead-element / COM errors."""
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def _describe_element(el) -> dict | None:
    """Return label-able info for an interactive element, or None to skip it."""
    ctn = _safe(lambda: el.ControlTypeName)
    if ctn not in INTERACTIVE_CONTROL_TYPES:
        return None
    if not _safe(lambda: el.IsEnabled, False):
        return None
    if _safe(lambda: el.IsOffscreen, True):
        return None
    rect = _safe(lambda: el.BoundingRectangle)
    if rect is None:
        return None
    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
    if right - left <= 0 or bottom - top <= 0:
        return None
    name = (_safe(lambda: el.Name, "") or "").strip()
    ctype = (_safe(lambda: el.LocalizedControlType, "") or ctn).strip().title()
    return {
        "name": name,
        "control_type": ctype,
        "bounds": [left, top, right, bottom],
        "center": [(left + right) // 2, (top + bottom) // 2],
    }


@dataclass
class WindowLayout:
    """Absolute screen rect of a hidden window in the last composite frame."""

    hwnd: int
    left: int
    top: int
    right: int
    bottom: int


def resolve_executable(cmdline: str) -> str:
    """Resolve a bare exe name in a command line to a full path.

    CreateProcess (with lpApplicationName=None) only searches PATH/cwd, so app names that
    live under versioned Program Files dirs (chrome.exe, msedge.exe, ...) fail. We resolve
    the first token via PATH then the Windows "App Paths" registry, leaving the rest of the
    command line untouched. An already-quoted/absolute first token is returned unchanged.
    """
    import shlex
    import shutil
    import subprocess
    import winreg

    cmdline = cmdline.strip()
    if not cmdline:
        return cmdline
    try:
        tokens = shlex.split(cmdline, posix=False)
    except ValueError:
        return cmdline
    if not tokens:
        return cmdline
    exe = tokens[0].strip('"')
    if "\\" in exe or "/" in exe or ":" in exe:
        return cmdline  # already a path

    resolved = shutil.which(exe)
    if resolved is None:
        name = exe if exe.lower().endswith(".exe") else exe + ".exe"
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key = winreg.OpenKey(
                    root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}"
                )
                resolved, _ = winreg.QueryValueEx(key, None)
                break
            except OSError:
                continue
    if not resolved:
        return cmdline
    return subprocess.list2cmdline([resolved, *tokens[1:]])


def ensure_desktop(name: str = "agent_dsk"):
    """Open the named desktop object, creating it if needed. Never SwitchDesktop.

    Returns a pywin32 desktop handle the caller must keep alive (GC closes it otherwise).
    """
    try:
        return win32service.OpenDesktop(name, 0, False, GENERIC_ALL)
    except win32service.error:
        return win32service.CreateDesktop(name, 0, GENERIC_ALL, None)


def capture_window(hwnd: int) -> Image.Image:
    """Capture a single window via PrintWindow(PW_RENDERFULLCONTENT) -> PIL image."""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = max(1, right - left), max(1, bottom - top)
    import win32ui

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        # PW_RENDERFULLCONTENT captures most modern (incl. many DWM) surfaces.
        ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)
        return Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)


class HiddenDesktop:
    """Owns the hidden desktop handle, launched PIDs, and the dedicated input thread."""

    def __init__(self, name: str = "agent_dsk") -> None:
        self.name = name
        self.hdesk = ensure_desktop(name)  # keep the handle alive on the instance
        self._hdesk_int = int(self.hdesk)
        self.pids: set[int] = set()
        self.layout: list[WindowLayout] = []
        self._layout_lock = threading.Lock()
        self.uia_labels: dict[int, object] = {}  # label -> UIA element (touched only on worker)
        self.uia_meta: dict[int, dict] = {}  # label -> {hwnd, bounds}
        self.uia_ready = False
        self._jobs: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._worker_err: BaseException | None = None
        self._worker = threading.Thread(target=self._serve, name="agent_dsk-input", daemon=True)
        self._worker.start()
        self._ready.wait(timeout=5)
        if self._worker_err is not None:
            raise self._worker_err
        logger.info("hidden desktop ready: %s", name)

    # --- dedicated SendInput thread ----------------------------------------------
    def _serve(self) -> None:
        # CRITICAL: SetThreadDesktop must run on a clean thread that owns no windows/hooks,
        # otherwise it fails. This brand-new thread does nothing GUI-ish before the call,
        # then every SendInput fallback is marshalled here so injection targets agent_dsk.
        if not ctypes.windll.user32.SetThreadDesktop(self._hdesk_int):
            self._worker_err = OSError("SetThreadDesktop(agent_dsk) failed")
            self._ready.set()
            return
        # Initialize COM + the UIA automation singleton ON THIS THREAD so every UIA call
        # (which must stay in one apartment) is marshalled here alongside SendInput. UIA is
        # optional: if it fails, coordinate input still works.
        try:
            # MTA (COINIT_MULTITHREADED = 0). An STA thread without a message pump can
            # deadlock on blocking out-of-process COM calls (e.g. SetFocus/SetValue); MTA
            # does not require a pump. The binding's later CoInitialize(None) is then a
            # no-op (RPC_E_CHANGED_MODE), leaving us in MTA.
            ctypes.windll.ole32.CoInitializeEx(None, 0)
            from windows_mcp.uia.core import _AutomationClient

            _AutomationClient.instance()
            self.uia_ready = True
        except Exception:  # noqa: BLE001
            logger.exception("UIA init failed on agent_dsk worker; UIA endpoints disabled")
        self._ready.set()
        while True:
            fn, fut = self._jobs.get()
            if fn is None:  # shutdown sentinel
                return
            try:
                fut.set_result(fn())
            except BaseException as exc:  # noqa: BLE001
                fut.set_exception(exc)

    def _run_on_worker(self, fn: Callable[[], object], timeout: float = 5.0) -> object:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._jobs.put((fn, fut))
        return fut.result(timeout=timeout)

    def shutdown(self) -> None:
        self._jobs.put((None, None))

    # --- app launching ------------------------------------------------------------
    def launch(self, cmdline: str) -> int:
        cmdline = resolve_executable(cmdline)
        si = win32process.STARTUPINFO()
        si.lpDesktop = self.name  # the key: the app is born on agent_dsk
        h_proc, h_thread, pid, _tid = win32process.CreateProcess(
            None, cmdline, None, None, False, 0, None, None, si
        )
        win32api.CloseHandle(h_thread)
        win32api.CloseHandle(h_proc)
        self.pids.add(pid)
        logger.info("launched on %s: %s (pid=%s)", self.name, cmdline, pid)
        return pid

    # --- window enumeration / capture --------------------------------------------
    def enum_windows(self) -> list[tuple[int, tuple[int, int, int, int]]]:
        found: list[int] = []
        owned = descendant_pids(self.pids)  # launched PIDs + their descendants

        def _cb(hwnd: int, _extra) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in owned:
                found.append(hwnd)
                return True
            # agent_dsk is dedicated to the agent, so any titled, reasonably sized,
            # non-system window on it is ours too — even when a launcher re-execs and
            # breaks the PID tree (e.g. Chrome/Edge spawn the real browser detached).
            title = win32gui.GetWindowText(hwnd) or ""
            cls = win32gui.GetClassName(hwnd) or ""
            if title.strip() and cls not in _SYSTEM_WINDOW_CLASSES:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                if right - left >= 100 and bottom - top >= 100:
                    found.append(hwnd)
            return True

        try:
            win32gui.EnumDesktopWindows(self.hdesk, _cb, None)
        except pywintypes.error as exc:
            # EnumDesktopWindows spuriously raises ERROR_NO_MORE_FILES (18) when the
            # desktop has few/no windows or a stale last-error lingers. Anything already
            # collected in `found` is valid; only re-raise unexpected errors.
            if exc.winerror not in (0, 18):
                raise
        out: list[tuple[int, tuple[int, int, int, int]]] = []
        for hwnd in found:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if left <= -30000 or right - left <= 0 or bottom - top <= 0:
                continue  # minimized / zero-size
            out.append((hwnd, (left, top, right, bottom)))
        return out

    def capture_composite(self) -> Image.Image:
        """Composite all hidden windows onto a black canvas in absolute screen coords.

        A composite pixel (x, y) therefore equals an absolute virtual-screen coordinate,
        which is what input resolution and SendInput fallback both expect.
        """
        vx = win32api.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = win32api.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = win32api.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = win32api.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        canvas = Image.new("RGB", (max(1, vw), max(1, vh)), (0, 0, 0))
        layout: list[WindowLayout] = []
        for hwnd, (left, top, right, bottom) in self.enum_windows():
            try:
                sub = capture_window(hwnd)
            except Exception:  # noqa: BLE001
                logger.exception("capture_window failed for hwnd=%s", hwnd)
                continue
            canvas.paste(sub, (left - vx, top - vy))
            layout.append(WindowLayout(hwnd, left, top, right, bottom))
        with self._layout_lock:
            self.layout = layout
        return canvas

    def resolve_hwnd(self, x: int, y: int) -> tuple[int, int, int] | None:
        """Map an absolute coordinate to (hwnd, local_x, local_y); topmost window wins."""
        with self._layout_lock:
            for wl in reversed(self.layout):
                if wl.left <= x < wl.right and wl.top <= y < wl.bottom:
                    return wl.hwnd, x - wl.left, y - wl.top
        return None

    def foreground_hidden_hwnd(self) -> int | None:
        """Best-effort target for cursor-less typing: last window in the current layout."""
        with self._layout_lock:
            if self.layout:
                return self.layout[-1].hwnd
        return None

    # --- PostMessage actions (no cursor, primary path) ---------------------------
    def click_background(self, hwnd: int, lx: int, ly: int, button: str = "left", double: bool = False) -> None:
        down, up, mk = _BUTTONS[button]
        lp = _lparam(lx, ly)
        win32gui.PostMessage(hwnd, WM_MOUSEMOVE, 0, lp)
        win32gui.PostMessage(hwnd, down, mk, lp)
        win32gui.PostMessage(hwnd, up, 0, lp)
        if double:
            win32gui.PostMessage(hwnd, down, mk, lp)
            win32gui.PostMessage(hwnd, up, 0, lp)

    def move_background(self, hwnd: int, lx: int, ly: int) -> None:
        win32gui.PostMessage(hwnd, WM_MOUSEMOVE, 0, _lparam(lx, ly))

    def scroll_background(self, hwnd: int, sx: int, sy: int, dx: int, dy: int) -> None:
        # WM_MOUSEWHEEL/HWHEEL lParam is in SCREEN coords; wParam hiword = signed delta.
        if abs(dy) >= abs(dx):
            delta = (1 if dy > 0 else -1) * WHEEL_DELTA * max(1, abs(dy))
            win32gui.PostMessage(hwnd, WM_MOUSEWHEEL, (delta & 0xFFFF) << 16, _lparam(sx, sy))
        else:
            delta = (1 if dx > 0 else -1) * WHEEL_DELTA * max(1, abs(dx))
            win32gui.PostMessage(hwnd, WM_MOUSEHWHEEL, (delta & 0xFFFF) << 16, _lparam(sx, sy))

    def type_background(self, hwnd: int, text: str, press_enter: bool = False) -> None:
        for ch in text:
            win32gui.PostMessage(hwnd, WM_CHAR, ord(ch), 0)
        if press_enter:
            win32gui.PostMessage(hwnd, WM_CHAR, 0x0D, 0)

    def drag_background(self, hwnd: int, lx1: int, ly1: int, lx2: int, ly2: int, button: str = "left") -> None:
        down, up, mk = _BUTTONS[button]
        win32gui.PostMessage(hwnd, WM_MOUSEMOVE, 0, _lparam(lx1, ly1))
        win32gui.PostMessage(hwnd, down, mk, _lparam(lx1, ly1))
        win32gui.PostMessage(hwnd, WM_MOUSEMOVE, mk, _lparam((lx1 + lx2) // 2, (ly1 + ly2) // 2))
        win32gui.PostMessage(hwnd, WM_MOUSEMOVE, mk, _lparam(lx2, ly2))
        win32gui.PostMessage(hwnd, up, 0, _lparam(lx2, ly2))

    # --- SendInput fallback (runs on the desktop-attached worker thread) ---------
    def _abs_xy(self, x: int, y: int) -> tuple[int, int]:
        vx = win32api.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = win32api.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = max(1, win32api.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        vh = max(1, win32api.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        nx = int((x - vx) * 65535 / vw)
        ny = int((y - vy) * 65535 / vh)
        return nx, ny

    def send_input_click(self, x: int, y: int, button: str = "left", double: bool = False) -> None:
        nx, ny = self._abs_xy(x, y)
        down, up = _MOUSE_DOWN_UP[button]
        base = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        clicks = 2 if double else 1
        seq = [_mouse_input(base, nx, ny)]
        for _ in range(clicks):
            seq.append(_mouse_input(down | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))
            seq.append(_mouse_input(up | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))
        self._run_on_worker(lambda: _send_inputs(seq))

    def send_input_move(self, x: int, y: int) -> None:
        nx, ny = self._abs_xy(x, y)
        flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        self._run_on_worker(lambda: _send_inputs([_mouse_input(flags, nx, ny)]))

    def send_input_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        nx, ny = self._abs_xy(x, y)
        move = _mouse_input(
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny
        )
        if abs(dy) >= abs(dx):
            data = (1 if dy > 0 else -1) * WHEEL_DELTA * max(1, abs(dy))
            wheel = _mouse_input(MOUSEEVENTF_WHEEL, 0, 0, data)
        else:
            data = (1 if dx > 0 else -1) * WHEEL_DELTA * max(1, abs(dx))
            wheel = _mouse_input(MOUSEEVENTF_HWHEEL, 0, 0, data)
        self._run_on_worker(lambda: _send_inputs([move, wheel]))

    def _raw_type(self, text: str, press_enter: bool = False) -> None:
        """Build + dispatch unicode keystrokes inline (caller is already on the worker)."""
        seq: list[_INPUT] = []
        for ch in text:
            seq.append(_key_input(ch, keyup=False))
            seq.append(_key_input(ch, keyup=True))
        if press_enter:
            seq.append(_vk_input(VK_RETURN, keyup=False))
            seq.append(_vk_input(VK_RETURN, keyup=True))
        if seq:
            _send_inputs(seq)

    def _raw_select_all_delete(self) -> None:
        """Ctrl+A then Delete, inline (caller is already on the worker)."""
        _send_inputs([
            _vk_input(VK_CONTROL, False), _vk_input(VK_A, False),
            _vk_input(VK_A, True), _vk_input(VK_CONTROL, True),
            _vk_input(VK_DELETE, False), _vk_input(VK_DELETE, True),
        ])

    def send_input_type(self, text: str, press_enter: bool = False) -> None:
        self._run_on_worker(lambda: self._raw_type(text, press_enter))

    def send_input_drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left") -> None:
        nx1, ny1 = self._abs_xy(x1, y1)
        nx2, ny2 = self._abs_xy(x2, y2)
        down, up = _MOUSE_DOWN_UP[button]
        base = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        seq = [
            _mouse_input(MOUSEEVENTF_MOVE | base, nx1, ny1),
            _mouse_input(down | base, nx1, ny1),
            _mouse_input(MOUSEEVENTF_MOVE | base, nx2, ny2),
            _mouse_input(up | base, nx2, ny2),
        ]
        self._run_on_worker(lambda: _send_inputs(seq))

    # --- UIA layer (runs on the COM-initialized worker thread; zero cursor) ------
    def uia_snapshot(self) -> list[dict]:
        """Label every interactive UIA element across all hidden windows."""
        if not self.uia_ready:
            raise RuntimeError("UIA is not available on the hidden desktop")
        windows = self.enum_windows()
        return self._run_on_worker(lambda: self._uia_snapshot_impl(windows), timeout=30.0)

    def uia_click_label(self, label: int) -> str:
        if not self.uia_ready:
            raise RuntimeError("UIA is not available on the hidden desktop")
        return self._run_on_worker(lambda: self._uia_click_impl(label), timeout=20.0)

    def uia_type_label(self, label: int, text: str, clear: bool, press_enter: bool) -> str:
        if not self.uia_ready:
            raise RuntimeError("UIA is not available on the hidden desktop")
        return self._run_on_worker(
            lambda: self._uia_type_impl(label, text, clear, press_enter), timeout=20.0
        )

    # The _uia_*_impl methods always run ON the worker thread (single COM apartment).
    def _uia_snapshot_impl(self, windows: list[tuple[int, tuple[int, int, int, int]]]) -> list[dict]:
        from windows_mcp.uia.controls import ControlFromHandle, WalkControl

        elements: list[dict] = []
        labels: dict[int, object] = {}
        meta: dict[int, dict] = {}
        label = 0
        for hwnd, _rect in windows:
            try:
                root = ControlFromHandle(hwnd)
            except Exception:  # noqa: BLE001
                continue
            if root is None:
                continue
            win_name = _safe(lambda root=root: root.Name) or ""
            try:
                walker = WalkControl(root, includeTop=False, maxDepth=50)
            except Exception:  # noqa: BLE001
                continue
            for child, _depth in walker:
                if label >= 800:  # safety cap
                    break
                info = _describe_element(child)
                if info is None:
                    continue
                label += 1
                labels[label] = child
                meta[label] = {"hwnd": hwnd, "bounds": info["bounds"]}
                elements.append({"label": label, "window": win_name, **info})
        with self._layout_lock:
            self.uia_labels = labels
            self.uia_meta = meta
        return elements

    def _uia_click_impl(self, label: int) -> str:
        el = self.uia_labels.get(label)
        if el is None:
            raise KeyError(label)
        from windows_mcp.uia.enums import PatternId
        from windows_mcp.uia.patterns import (
            ExpandCollapsePattern,
            InvokePattern,
            LegacyIAccessiblePattern,
            SelectionItemPattern,
            TogglePattern,
        )

        try:
            el.SetFocus()
        except Exception:  # noqa: BLE001
            pass
        attempts = (
            (PatternId.InvokePattern, InvokePattern, "Invoke"),
            (PatternId.TogglePattern, TogglePattern, "Toggle"),
            (PatternId.SelectionItemPattern, SelectionItemPattern, "Select"),
            (PatternId.ExpandCollapsePattern, ExpandCollapsePattern, "Expand"),
            (PatternId.LegacyIAccessiblePattern, LegacyIAccessiblePattern, "DoDefaultAction"),
        )
        for pid, wrapper, method in attempts:
            raw = _safe(lambda pid=pid: el.GetPattern(pid))
            if not raw:
                continue
            try:
                w = wrapper(raw)
                fn = getattr(w, method, None)
                if fn is None:
                    continue
                fn()
                return method.lower()
            except Exception:  # noqa: BLE001
                continue
        # Fallback: PostMessage click at the element center inside its window.
        m = self.uia_meta.get(label)
        if m:
            left, top, right, bottom = m["bounds"]
            cx, cy = (left + right) // 2, (top + bottom) // 2
            self.click_background(m["hwnd"], cx - left, cy - top)
            return "postmessage"
        raise RuntimeError(f"no actionable pattern for label {label}")

    def _uia_type_impl(self, label: int, text: str, clear: bool, press_enter: bool) -> str:
        el = self.uia_labels.get(label)
        if el is None:
            raise KeyError(label)
        from windows_mcp.uia.enums import PatternId
        from windows_mcp.uia.patterns import ValuePattern

        try:
            el.SetFocus()
        except Exception:  # noqa: BLE001
            pass
        # DocumentControl.SetValue typically blocks for seconds then fails (rich text edits),
        # so go straight to keystrokes for documents; ValuePattern is for plain EditControls.
        ctn = _safe(lambda: el.ControlTypeName, "")
        raw = None if ctn == "DocumentControl" else _safe(lambda: el.GetPattern(PatternId.ValuePattern))
        if raw:
            try:
                vp = ValuePattern(raw)
                if clear or not text:
                    if vp.SetValue("") and not text:
                        return "setvalue"
                if vp.SetValue(text):
                    if press_enter:
                        self._raw_type("", press_enter=True)
                    return "setvalue"
            except Exception:  # noqa: BLE001
                pass
        # Fallback: focused-element keystrokes (works for Document/canvas edits).
        if clear:
            self._raw_select_all_delete()
        self._raw_type(text, press_enter=press_enter)
        return "keystrokes"
