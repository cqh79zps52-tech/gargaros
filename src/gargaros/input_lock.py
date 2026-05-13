"""F9 — physical input lock via Windows low-level hooks.

Installs WH_KEYBOARD_LL and WH_MOUSE_LL on a dedicated thread that runs a message
loop. Filters events by the LLKHF_INJECTED / LLMHF_INJECTED flag — Gargaros' own
SendInput events carry it and pass through; physical events get dropped (return 1
instead of CallNextHookEx). Configurable unlock hotkey and a safe-keys allowlist
(Win+L, Alt+F4 if allow_safe_keys=true; Ctrl+Alt+Del is intercepted by Windows
above any user hook and can't be blocked anyway).

A child watchdog process pings the server's /input/lock_status every 500ms and
TerminateProcesses Gargaros if it stops responding for longer than `watchdog_ms`
— ensuring the keyboard never stays jammed if the server hangs. See section 4
of the v2 CDC.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import threading
import time
import uuid
from ctypes import wintypes
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

_PLATFORM_WIN = sys.platform == "win32"

# LRESULT is LONG_PTR on x64 (8 bytes), not c_long (4 bytes). Without this fix
# the hook proc's return value gets truncated and Windows silently uninstalls
# the hook — passed/blocked counters stay at 0 because the hook is dead.
LRESULT = ctypes.c_ssize_t
HHOOK = ctypes.c_void_p
HINSTANCE = ctypes.c_void_p

if _PLATFORM_WIN:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    # Pin argtypes/restype so pointer-sized values aren't truncated on x64.
    _user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, HINSTANCE, wintypes.DWORD]
    _user32.SetWindowsHookExW.restype = HHOOK
    _user32.UnhookWindowsHookEx.argtypes = [HHOOK]
    _user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    _user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    _user32.CallNextHookEx.restype = LRESULT
    _user32.GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _user32.GetMessageW.restype = wintypes.BOOL
    _user32.TranslateMessage.argtypes = [ctypes.c_void_p]
    _user32.TranslateMessage.restype = wintypes.BOOL
    _user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
    _user32.DispatchMessageW.restype = LRESULT
    _user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.PostThreadMessageW.restype = wintypes.BOOL
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = HINSTANCE
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
else:
    _user32 = None
    _kernel32 = None


WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01

# Virtual-key codes used for safe-key detection
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LMENU = 0xA4  # left alt
VK_RMENU = 0xA5  # right alt
VK_F4 = 0x73
VK_L = 0x4C


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


# ---- monkey-patchable Win32 hooks (for tests) ----


def _real_set_windows_hook(hook_id: int, proc, hmod: int, thread_id: int) -> int:
    if _user32 is None:
        raise RuntimeError("SetWindowsHookExW unavailable: not on Windows")
    h = _user32.SetWindowsHookExW(hook_id, proc, hmod, thread_id)
    if not h:
        raise OSError(ctypes.get_last_error(), "SetWindowsHookExW failed")
    return int(h)


def _real_unhook_windows_hook(handle: int) -> bool:
    if _user32 is None:
        return False
    return bool(_user32.UnhookWindowsHookEx(handle))


def _real_call_next_hook(handle: int, n_code: int, wparam: int, lparam: int) -> int:
    if _user32 is None:
        return 0
    return int(_user32.CallNextHookEx(handle, n_code, wparam, lparam))


def _real_run_message_loop(stop_check: Callable[[], bool]) -> None:
    """Blocking GetMessageW loop — required for WH_KEYBOARD_LL.

    Low-level hooks dispatch their callbacks via the installing thread's message
    queue. The thread must be in GetMessageW (or equivalent wait-state) for the
    OS to deliver them — a PeekMessageW + sleep(10ms) loop is too laggy and the
    OS's 300ms LowLevelHooksTimeout silently uninstalls the hook.

    To exit the loop, the manager PostThreadMessageW's WM_QUIT to this thread.
    """
    if _user32 is None:
        return
    msg = wintypes.MSG()
    while True:
        ret = _user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
        if ret == 0 or ret == -1:  # WM_QUIT or error
            return
        if stop_check():
            return
        _user32.TranslateMessage(ctypes.byref(msg))
        _user32.DispatchMessageW(ctypes.byref(msg))


_set_windows_hook = _real_set_windows_hook
_unhook_windows_hook = _real_unhook_windows_hook
_call_next_hook = _real_call_next_hook
_run_message_loop = _real_run_message_loop


# ---- hotkey parsing ----

_VK_NAMES = {
    "ctrl": 0x11, "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B,
}


def _parse_hotkey(combo: str) -> tuple[set[int], int]:
    """'ctrl+shift+f12' → ({0x11, 0x10}, 0x7B). Returns (modifier_vks, primary_vk)."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    mods: set[int] = set()
    primary: int | None = None
    for p in parts[:-1]:
        if p in _VK_NAMES:
            mods.add(_VK_NAMES[p])
        else:
            raise ValueError(f"unknown modifier: {p!r}")
    last = parts[-1]
    if last in _VK_NAMES:
        primary = _VK_NAMES[last]
    elif len(last) == 1:
        c = last.upper()
        if "A" <= c <= "Z" or "0" <= c <= "9":
            primary = ord(c)
    elif last.startswith("f") and last[1:].isdigit():
        n = int(last[1:])
        if 1 <= n <= 24:
            primary = 0x70 + n - 1
    if primary is None:
        raise ValueError(f"unknown key: {last!r}")
    return mods, primary


# ---- InputLock manager ----


class InputLock:
    """Singleton-ish lock manager held in app.state.input_lock."""

    def __init__(self) -> None:
        self.locked = False
        self.lock_id: str | None = None
        self.started_at: datetime | None = None
        self.unlock_hotkey: str = ""
        self._hotkey_mods: set[int] = set()
        self._hotkey_primary: int = 0
        self.allow_safe_keys: bool = True
        self.block_keyboard: bool = True
        self.block_mouse: bool = True
        self.watchdog_ms: int = 2000
        self.blocked_physical_events: int = 0
        self.passed_injected_events: int = 0
        self.blocked_kb_events: int = 0
        self.blocked_mouse_events: int = 0
        self.passed_kb_injected: int = 0
        self.passed_mouse_injected: int = 0
        self._sample_blocked_kb: list[dict] = []  # last few blocked keys for debug
        self._held_vks: set[int] = set()
        self._hook_thread: threading.Thread | None = None
        self._hook_thread_id: int = 0
        self._stop_event = threading.Event()
        self._kb_proc_ref: Any = None
        self._mouse_proc_ref: Any = None
        self._kb_handle: int = 0
        self._mouse_handle: int = 0
        self._watchdog_proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._on_unlock_request: Callable[[], None] | None = None

    # ---- lifecycle ----

    def acquire(
        self,
        unlock_hotkey: str = "ctrl+shift+f12",
        allow_safe_keys: bool = True,
        block_keyboard: bool = True,
        block_mouse: bool = True,
        watchdog_ms: int = 2000,
        spawn_watchdog: bool = True,
        watchdog_args: dict | None = None,
    ) -> dict:
        with self._lock:
            if self.locked:
                raise RuntimeError("input lock already active")
            mods, primary = _parse_hotkey(unlock_hotkey)
            self._hotkey_mods = mods
            self._hotkey_primary = primary
            self.unlock_hotkey = unlock_hotkey
            self.allow_safe_keys = allow_safe_keys
            self.block_keyboard = block_keyboard
            self.block_mouse = block_mouse
            self.watchdog_ms = watchdog_ms
            self.lock_id = str(uuid.uuid4())
            self.started_at = datetime.now(timezone.utc)
            self.blocked_physical_events = 0
            self.passed_injected_events = 0
            self._held_vks = set()
            self._stop_event.clear()

            self._start_hook_thread()

            if spawn_watchdog:
                try:
                    self._spawn_watchdog(watchdog_args or {})
                except Exception:
                    logger.exception("failed to spawn watchdog — releasing lock as safety")
                    self._stop_hook_thread()
                    self.locked = False
                    raise

            self.locked = True
            return self.status_dict()

    def release(self) -> dict:
        with self._lock:
            if not self.locked:
                return {"status": "unlocked", "was_locked_ms": 0}
            elapsed_ms = 0
            if self.started_at:
                elapsed_ms = int(
                    (datetime.now(timezone.utc) - self.started_at).total_seconds() * 1000
                )
            self._stop_hook_thread()
            self._stop_watchdog()
            self.locked = False
            return {"status": "unlocked", "was_locked_ms": elapsed_ms}

    def status_dict(self) -> dict:
        if not self.locked:
            return {"locked": False}
        elapsed_ms = 0
        if self.started_at:
            elapsed_ms = int(
                (datetime.now(timezone.utc) - self.started_at).total_seconds() * 1000
            )
        return {
            "locked": True,
            "lock_id": self.lock_id,
            "started_at": self.started_at.isoformat().replace("+00:00", "Z") if self.started_at else None,
            "elapsed_ms": elapsed_ms,
            "unlock_hotkey": self.unlock_hotkey,
            "blocked_physical_events": self.blocked_physical_events,
            "passed_injected_events": self.passed_injected_events,
            "blocked_kb_events": self.blocked_kb_events,
            "blocked_mouse_events": self.blocked_mouse_events,
            "passed_kb_injected": self.passed_kb_injected,
            "passed_mouse_injected": self.passed_mouse_injected,
            "sample_blocked_kb": list(self._sample_blocked_kb),
        }

    # ---- hook thread ----

    def _start_hook_thread(self) -> None:
        self._kb_proc_ref = HOOKPROC(self._kb_proc)
        self._mouse_proc_ref = HOOKPROC(self._mouse_proc)
        thread = threading.Thread(target=self._hook_thread_main, daemon=True, name="gargaros-input-lock")
        self._hook_thread = thread
        thread.start()
        # Give the thread a beat to install the hooks
        for _ in range(50):
            if self._kb_handle or self._mouse_handle:
                break
            time.sleep(0.01)

    def _hook_thread_main(self) -> None:
        try:
            # Capture our thread id so the manager can PostThreadMessage WM_QUIT us.
            if _kernel32:
                self._hook_thread_id = _kernel32.GetCurrentThreadId()
            hmod = _kernel32.GetModuleHandleW(None) if _kernel32 else 0
            if self.block_keyboard:
                self._kb_handle = _set_windows_hook(WH_KEYBOARD_LL, self._kb_proc_ref, hmod, 0)
            if self.block_mouse:
                self._mouse_handle = _set_windows_hook(WH_MOUSE_LL, self._mouse_proc_ref, hmod, 0)
            _run_message_loop(self._stop_event.is_set)
        except Exception:
            logger.exception("hook thread crashed")
        finally:
            if self._kb_handle:
                try:
                    _unhook_windows_hook(self._kb_handle)
                except Exception:
                    logger.exception("UnhookWindowsHookEx failed (kb)")
                self._kb_handle = 0
            if self._mouse_handle:
                try:
                    _unhook_windows_hook(self._mouse_handle)
                except Exception:
                    logger.exception("UnhookWindowsHookEx failed (mouse)")
                self._mouse_handle = 0

    def _stop_hook_thread(self) -> None:
        self._stop_event.set()
        # Kick the blocking GetMessageW so the thread can exit.
        if _user32 and self._hook_thread_id:
            try:
                _user32.PostThreadMessageW(self._hook_thread_id, WM_QUIT, 0, 0)
            except Exception:
                logger.exception("PostThreadMessageW WM_QUIT failed")
        if self._hook_thread:
            self._hook_thread.join(timeout=2.0)
            self._hook_thread = None
        self._hook_thread_id = 0

    # ---- hook callbacks ----

    def _kb_proc(self, n_code: int, wparam: int, lparam: int) -> int:
        if n_code < 0:
            return _call_next_hook(self._kb_handle, n_code, wparam, lparam)
        try:
            kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = int(kb.vkCode)
            flags = int(kb.flags)
            decision = self._classify_kb(vk, flags, int(wparam))
        except Exception:
            logger.exception("kb hook proc raised — passing through to avoid system lockout")
            return _call_next_hook(self._kb_handle, n_code, wparam, lparam)
        if decision == "block":
            self.blocked_physical_events += 1
            self.blocked_kb_events += 1
            # Keep a short sample for diagnostics.
            if len(self._sample_blocked_kb) < 10:
                self._sample_blocked_kb.append({"vk": vk, "flags": flags, "wparam": int(wparam)})
            return 1
        if decision == "injected":
            self.passed_injected_events += 1
            self.passed_kb_injected += 1
        return _call_next_hook(self._kb_handle, n_code, wparam, lparam)

    def _mouse_proc(self, n_code: int, wparam: int, lparam: int) -> int:
        if n_code < 0:
            return _call_next_hook(self._mouse_handle, n_code, wparam, lparam)
        try:
            ms = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            is_injected = bool(int(ms.flags) & LLMHF_INJECTED)
        except Exception:
            logger.exception("mouse hook proc raised — passing through")
            return _call_next_hook(self._mouse_handle, n_code, wparam, lparam)
        if is_injected:
            self.passed_injected_events += 1
            self.passed_mouse_injected += 1
            return _call_next_hook(self._mouse_handle, n_code, wparam, lparam)
        self.blocked_physical_events += 1
        self.blocked_mouse_events += 1
        return 1

    def _classify_kb(self, vk: int, flags: int, wparam: int) -> str:
        is_injected = bool(flags & LLKHF_INJECTED)
        is_keydown = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        if is_injected:
            return "injected"
        # Track held physical keys for hotkey detection
        if is_keydown:
            self._held_vks.add(vk)
            if self._matches_unlock_hotkey(vk):
                # Trigger async release (in a thread to avoid blocking the hook callback)
                threading.Thread(target=self.release, daemon=True).start()
                return "pass"  # let the hotkey go through
            if self.allow_safe_keys and self._is_safe_key(vk):
                return "pass"
        else:
            self._held_vks.discard(vk)
            # KEYUP for safe keys also passes; for the primary unlock it passes.
            if self.allow_safe_keys and self._is_safe_key(vk):
                return "pass"
            if vk == self._hotkey_primary:
                return "pass"
        return "block"

    def _matches_unlock_hotkey(self, just_pressed_vk: int) -> bool:
        if just_pressed_vk != self._hotkey_primary:
            return False
        return self._hotkey_mods.issubset(self._held_vks)

    def _is_safe_key(self, vk: int) -> bool:
        """Win+L (LWIN/RWIN + L) and Alt+F4 (LMENU/RMENU + F4) pass through.
        Ctrl+Alt+Del is intercepted by Windows above our hook, no special case needed.
        """
        # Win+L: L is the primary; we let any L pass when Win is held.
        if vk == VK_L and (VK_LWIN in self._held_vks or VK_RWIN in self._held_vks):
            return True
        if vk in (VK_LWIN, VK_RWIN, VK_LMENU, VK_RMENU):
            # Always pass modifier press/release individually; only the combo is special
            return True
        if vk == VK_F4 and (VK_LMENU in self._held_vks or VK_RMENU in self._held_vks):
            return True
        return False

    # ---- watchdog ----

    def _spawn_watchdog(self, watchdog_args: dict) -> None:
        if not _PLATFORM_WIN:
            return  # no-op on non-Windows test runs
        port = watchdog_args.get("port", 7331)
        token = watchdog_args.get("token", "")
        cmd = [
            sys.executable, "-m", "gargaros.watchdog",
            "--port", str(port),
            "--token", token,
            "--target-pid", str(_kernel32.GetCurrentProcessId()),
            "--lock-id", self.lock_id or "",
            "--timeout-ms", str(self.watchdog_ms),
        ]
        # Detached so it survives Gargaros' termination
        DETACHED_PROCESS = 0x00000008
        self._watchdog_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS,
        )

    def _stop_watchdog(self) -> None:
        if self._watchdog_proc is not None:
            try:
                self._watchdog_proc.terminate()
                self._watchdog_proc.wait(timeout=2)
            except Exception:
                try:
                    self._watchdog_proc.kill()
                except Exception:
                    pass
            self._watchdog_proc = None
