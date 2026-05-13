"""F9 — verify injected events pass and physical events are blocked.

We test the classification logic (_classify_kb) directly. Going through the real
Win32 hook plumbing requires actually being inside a hook callback called by the
kernel, which we can't simulate in pytest. The classification logic is the
substantive part of the F9 contract — Win32 mechanics are integration territory.
"""

from __future__ import annotations

import pytest

from gargaros import input_lock as il


@pytest.fixture
def lock():
    L = il.InputLock()
    # Pre-fill the hotkey config so _classify_kb can run without acquire()
    L._hotkey_mods, L._hotkey_primary = il._parse_hotkey("ctrl+shift+f12")
    L.unlock_hotkey = "ctrl+shift+f12"
    L.allow_safe_keys = True
    L.locked = True
    return L


def test_injected_keydown_passes(lock):
    decision = lock._classify_kb(vk=0x57, flags=il.LLKHF_INJECTED, wparam=il.WM_KEYDOWN)
    assert decision == "injected"


def test_injected_keyup_passes(lock):
    decision = lock._classify_kb(vk=0x57, flags=il.LLKHF_INJECTED, wparam=il.WM_KEYUP)
    assert decision == "injected"


def test_physical_keydown_blocked(lock):
    # 'A' physical down (no INJECTED flag) → block
    decision = lock._classify_kb(vk=0x41, flags=0, wparam=il.WM_KEYDOWN)
    assert decision == "block"


def test_physical_keyup_blocked(lock):
    decision = lock._classify_kb(vk=0x41, flags=0, wparam=il.WM_KEYUP)
    assert decision == "block"


def test_hook_proc_returns_1_for_blocked(lock, monkeypatch):
    """End-to-end: when classify says block, _kb_proc returns 1 (Windows = consumed)."""
    monkeypatch.setattr(lock, "_classify_kb", lambda *a, **kw: "block")
    # Build a minimal lparam pointing to a real KBDLLHOOKSTRUCT
    s = il.KBDLLHOOKSTRUCT(vkCode=0x41, scanCode=0, flags=0, time=0, dwExtraInfo=None)
    import ctypes
    lparam = ctypes.cast(ctypes.byref(s), ctypes.c_void_p).value
    ret = lock._kb_proc(0, il.WM_KEYDOWN, lparam)
    assert ret == 1
    assert lock.blocked_physical_events == 1


def test_hook_proc_passes_through_for_injected(lock, monkeypatch):
    """When classify says injected, _kb_proc calls CallNextHookEx (returns 0 in our stub)."""
    called = []
    monkeypatch.setattr(il, "_call_next_hook", lambda h, n, w, l: called.append((h, n, w, l)) or 0)
    monkeypatch.setattr(lock, "_classify_kb", lambda *a, **kw: "injected")
    import ctypes
    s = il.KBDLLHOOKSTRUCT(vkCode=0x41, scanCode=0, flags=il.LLKHF_INJECTED, time=0, dwExtraInfo=None)
    lparam = ctypes.cast(ctypes.byref(s), ctypes.c_void_p).value
    lock._kb_proc(0, il.WM_KEYDOWN, lparam)
    assert lock.passed_injected_events == 1
    assert len(called) == 1


def test_mouse_injected_passes(lock, monkeypatch):
    called = []
    monkeypatch.setattr(il, "_call_next_hook", lambda h, n, w, l: called.append(1) or 0)
    import ctypes
    s = il.MSLLHOOKSTRUCT(pt_x=0, pt_y=0, mouseData=0, flags=il.LLMHF_INJECTED, time=0, dwExtraInfo=None)
    lparam = ctypes.cast(ctypes.byref(s), ctypes.c_void_p).value
    lock._mouse_proc(0, 0x0200, lparam)  # WM_MOUSEMOVE
    assert lock.passed_injected_events == 1
    assert len(called) == 1


def test_mouse_physical_blocked(lock):
    import ctypes
    s = il.MSLLHOOKSTRUCT(pt_x=0, pt_y=0, mouseData=0, flags=0, time=0, dwExtraInfo=None)
    lparam = ctypes.cast(ctypes.byref(s), ctypes.c_void_p).value
    ret = lock._mouse_proc(0, 0x0200, lparam)
    assert ret == 1
    assert lock.blocked_physical_events == 1
