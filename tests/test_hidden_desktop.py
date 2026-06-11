"""Pure-logic unit tests for the hidden desktop layer.

These avoid creating a real Windows desktop / input thread by instantiating HiddenDesktop
via ``object.__new__`` and populating only the fields the method under test reads.
"""

from __future__ import annotations

import gargaros.hidden_desktop as hd
from gargaros.hidden_desktop import HiddenDesktop, WindowLayout, _lparam


def _bare(layout):
    import threading

    obj = object.__new__(HiddenDesktop)
    obj.layout = layout
    obj._layout_lock = threading.Lock()
    return obj


def test_lparam_packs_x_low_y_high():
    assert _lparam(3, 5) == 0x00050003
    assert _lparam(0, 0) == 0
    # negative/large values are masked to 16 bits each
    assert _lparam(-1, -1) == 0xFFFFFFFF


def test_resolve_hwnd_hit_returns_local_coords():
    obj = _bare([WindowLayout(hwnd=111, left=100, top=200, right=300, bottom=400)])
    assert obj.resolve_hwnd(150, 250) == (111, 50, 50)


def test_resolve_hwnd_miss_returns_none():
    obj = _bare([WindowLayout(hwnd=111, left=100, top=200, right=300, bottom=400)])
    assert obj.resolve_hwnd(10, 10) is None
    # right/bottom are exclusive
    assert obj.resolve_hwnd(300, 400) is None


def test_resolve_hwnd_topmost_wins_on_overlap():
    bottom_win = WindowLayout(hwnd=1, left=0, top=0, right=200, bottom=200)
    top_win = WindowLayout(hwnd=2, left=50, top=50, right=150, bottom=150)
    obj = _bare([bottom_win, top_win])  # later entries are pasted on top
    hwnd, _lx, _ly = obj.resolve_hwnd(100, 100)
    assert hwnd == 2


def test_foreground_hidden_hwnd_is_last_layout_entry():
    obj = _bare([WindowLayout(1, 0, 0, 10, 10), WindowLayout(2, 0, 0, 10, 10)])
    assert obj.foreground_hidden_hwnd() == 2
    assert _bare([]).foreground_hidden_hwnd() is None


def test_abs_xy_normalizes_over_virtual_screen(monkeypatch):
    metrics = {
        hd.SM_XVIRTUALSCREEN: 0,
        hd.SM_YVIRTUALSCREEN: 0,
        hd.SM_CXVIRTUALSCREEN: 1920,
        hd.SM_CYVIRTUALSCREEN: 1080,
    }
    monkeypatch.setattr(hd.win32api, "GetSystemMetrics", lambda i: metrics[i])
    obj = object.__new__(HiddenDesktop)
    assert obj._abs_xy(0, 0) == (0, 0)
    assert obj._abs_xy(1920, 1080) == (65535, 65535)
    mid = obj._abs_xy(960, 540)
    assert 32000 < mid[0] < 33000 and 32000 < mid[1] < 33000


def test_abs_xy_handles_negative_virtual_origin(monkeypatch):
    metrics = {
        hd.SM_XVIRTUALSCREEN: -1920,
        hd.SM_YVIRTUALSCREEN: 0,
        hd.SM_CXVIRTUALSCREEN: 3840,
        hd.SM_CYVIRTUALSCREEN: 1080,
    }
    monkeypatch.setattr(hd.win32api, "GetSystemMetrics", lambda i: metrics[i])
    obj = object.__new__(HiddenDesktop)
    # leftmost pixel of the secondary monitor maps to 0
    assert obj._abs_xy(-1920, 0) == (0, 0)
