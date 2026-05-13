"""F9 — verify Win+L and Alt+F4 pass through when allow_safe_keys=true.
(Ctrl+Alt+Del is intercepted by Windows above any user-mode hook — no test possible.)
"""

from __future__ import annotations

import pytest

from gargaros import input_lock as il


VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_F4 = 0x73
VK_L = 0x4C


@pytest.fixture
def lock():
    L = il.InputLock()
    L._hotkey_mods, L._hotkey_primary = il._parse_hotkey("ctrl+shift+f12")
    L.unlock_hotkey = "ctrl+shift+f12"
    L.allow_safe_keys = True
    L.locked = True
    return L


def test_win_alone_passes(lock):
    """Win key down alone should pass (it's a safe modifier)."""
    decision = lock._classify_kb(VK_LWIN, flags=0, wparam=il.WM_KEYDOWN)
    assert decision == "pass"


def test_alt_alone_passes(lock):
    decision = lock._classify_kb(VK_LMENU, flags=0, wparam=il.WM_KEYDOWN)
    assert decision == "pass"


def test_win_plus_L_passes(lock):
    """Win held + L pressed = Win+L (session lock) should pass."""
    lock._classify_kb(VK_LWIN, flags=0, wparam=il.WM_KEYDOWN)
    decision = lock._classify_kb(VK_L, flags=0, wparam=il.WM_KEYDOWN)
    assert decision == "pass"


def test_alt_plus_F4_passes(lock):
    """Alt held + F4 pressed = close window combo, allows the user to bail out."""
    lock._classify_kb(VK_LMENU, flags=0, wparam=il.WM_KEYDOWN)
    decision = lock._classify_kb(VK_F4, flags=0, wparam=il.WM_KEYDOWN)
    assert decision == "pass"


def test_F4_alone_blocked(lock):
    """F4 without Alt held is just F4 — should be blocked."""
    decision = lock._classify_kb(VK_F4, flags=0, wparam=il.WM_KEYDOWN)
    assert decision == "block"


def test_L_alone_blocked(lock):
    decision = lock._classify_kb(VK_L, flags=0, wparam=il.WM_KEYDOWN)
    assert decision == "block"


def test_safe_keys_disabled(lock):
    lock.allow_safe_keys = False
    lock._classify_kb(VK_LMENU, flags=0, wparam=il.WM_KEYDOWN)
    # Alt+F4 should NOT pass when safe_keys disabled
    decision = lock._classify_kb(VK_F4, flags=0, wparam=il.WM_KEYDOWN)
    assert decision == "block"
