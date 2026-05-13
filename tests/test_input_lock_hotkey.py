"""F9 — verify the unlock hotkey detection releases the lock."""

from __future__ import annotations

import threading
import time

import pytest

from gargaros import input_lock as il


@pytest.fixture
def lock(monkeypatch):
    monkeypatch.setattr(il, "_set_windows_hook", lambda *a: 12345)
    monkeypatch.setattr(il, "_unhook_windows_hook", lambda h: True)
    monkeypatch.setattr(il, "_call_next_hook", lambda h, n, w, l: 0)
    monkeypatch.setattr(il, "_run_message_loop", lambda stop: None)
    L = il.InputLock()
    L.acquire(spawn_watchdog=False)
    yield L
    L.release()


def test_hotkey_combo_detected(lock):
    """ctrl down, shift down, f12 down → unlock triggered."""
    VK_CTRL = 0x11
    VK_SHIFT = 0x10
    VK_F12 = 0x7B
    # Press ctrl
    lock._classify_kb(VK_CTRL, flags=0, wparam=il.WM_KEYDOWN)
    # Press shift
    lock._classify_kb(VK_SHIFT, flags=0, wparam=il.WM_KEYDOWN)
    # Press F12 — this should match the unlock hotkey
    decision = lock._classify_kb(VK_F12, flags=0, wparam=il.WM_KEYDOWN)
    assert decision == "pass"
    # release() runs in a thread; give it a moment
    for _ in range(50):
        if not lock.locked:
            break
        time.sleep(0.02)
    assert lock.locked is False


def test_hotkey_primary_alone_does_not_unlock(lock):
    VK_F12 = 0x7B
    # F12 alone, no modifiers held → blocked, not unlock
    decision = lock._classify_kb(VK_F12, flags=0, wparam=il.WM_KEYDOWN)
    assert decision == "block"
    assert lock.locked is True


def test_hotkey_partial_mods_does_not_unlock(lock):
    """ctrl + F12 (missing shift) should not trigger unlock."""
    VK_CTRL = 0x11
    VK_F12 = 0x7B
    lock._classify_kb(VK_CTRL, flags=0, wparam=il.WM_KEYDOWN)
    decision = lock._classify_kb(VK_F12, flags=0, wparam=il.WM_KEYDOWN)
    assert decision == "block"  # missing shift modifier
    assert lock.locked is True
