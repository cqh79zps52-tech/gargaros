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
import threading
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

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


def _lparam(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


# --- process-tree resolution ------------------------------------------------------
# Some launchers (e.g. notepad.exe on Windows 11) are stubs that spawn the real window
# under a different PID, so an exact-PID match misses the window. We therefore match any
# descendant of a launched PID.
TH32CS_SNAPPROCESS = 0x00000002


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


@dataclass
class WindowLayout:
    """Absolute screen rect of a hidden window in the last composite frame."""

    hwnd: int
    left: int
    top: int
    right: int
    bottom: int


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

    def send_input_type(self, text: str, press_enter: bool = False) -> None:
        seq: list[_INPUT] = []
        for ch in text:
            seq.append(_key_input(ch, keyup=False))
            seq.append(_key_input(ch, keyup=True))
        if press_enter:
            seq.append(_key_input("\r", keyup=False))
            seq.append(_key_input("\r", keyup=True))
        if seq:
            self._run_on_worker(lambda: _send_inputs(seq))

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
