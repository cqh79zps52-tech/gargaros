"""F8 — verify expect_focus on input endpoints returns 412 when focus doesn't match."""

from __future__ import annotations

import pytest

from gargaros import input_driver, window_driver


@pytest.fixture
def fake_windows(monkeypatch):
    state = {
        "foreground": 1002,
        "windows": {
            1001: {"title": "Notepad", "pid": 100, "tid": 200, "process_name": "notepad.exe", "visible": True, "iconic": False, "rect": (0, 0, 800, 600)},
            1002: {"title": "Xbox - Google Chrome", "pid": 200, "tid": 201, "process_name": "chrome.exe", "visible": True, "iconic": False, "rect": (0, 0, 1920, 1080)},
        },
    }
    monkeypatch.setattr(window_driver, "_enum_windows", lambda: list(state["windows"].keys()))
    monkeypatch.setattr(window_driver, "_get_window_text", lambda h: state["windows"][h]["title"])
    monkeypatch.setattr(window_driver, "_get_window_thread_process_id", lambda h: (state["windows"][h]["tid"], state["windows"][h]["pid"]))
    monkeypatch.setattr(window_driver, "_get_process_name", lambda pid: next((w["process_name"] for w in state["windows"].values() if w["pid"] == pid), ""))
    monkeypatch.setattr(window_driver, "_is_window_visible", lambda h: state["windows"][h]["visible"])
    monkeypatch.setattr(window_driver, "_is_iconic", lambda h: state["windows"][h]["iconic"])
    monkeypatch.setattr(window_driver, "_get_window_rect", lambda h: state["windows"][h]["rect"])
    monkeypatch.setattr(window_driver, "_get_foreground_window", lambda: state["foreground"])
    return state


@pytest.fixture(autouse=True)
def fake_send_input(monkeypatch):
    sent: list = []
    monkeypatch.setattr(input_driver, "_send_input", lambda inputs: sent.append(list(inputs)) or len(inputs))
    return sent


def test_key_hold_matching_focus_succeeds(client, auth_headers, fake_windows, fake_send_input):
    r = client.post(
        "/key/hold",
        json={"name": "w", "duration_ms": 20, "expect_focus": {"title_contains": "Xbox"}},
        headers=auth_headers,
    )
    assert r.status_code == 201
    # Sent down + up
    assert len(fake_send_input) == 2


def test_key_hold_mismatched_focus_returns_412(client, auth_headers, fake_windows, fake_send_input):
    # foreground is Xbox but we expect Notepad → mismatch
    r = client.post(
        "/key/hold",
        json={"name": "w", "duration_ms": 20, "expect_focus": {"title_contains": "Notepad"}},
        headers=auth_headers,
    )
    assert r.status_code == 412
    # No input was sent
    assert len(fake_send_input) == 0


def test_mouse_move_smooth_mismatched_focus_returns_412(client, auth_headers, fake_windows, fake_send_input):
    r = client.post(
        "/mouse/move_smooth",
        json={
            "dx": 100, "dy": 0, "duration_ms": 10, "steps": 5,
            "mode": "relative",
            "expect_focus": {"title_contains": "DoesNotExist"},
        },
        headers=auth_headers,
    )
    assert r.status_code == 412
    assert len(fake_send_input) == 0


def test_batch_mismatched_focus_returns_412(client, auth_headers, fake_windows, fake_send_input):
    r = client.post(
        "/batch",
        json={
            "actions": [{"type": "sleep", "ms": 10}],
            "expect_focus": {"title_contains": "NotMatching"},
        },
        headers=auth_headers,
    )
    assert r.status_code == 412


def test_batch_matching_focus_executes(client, auth_headers, fake_windows, fake_send_input):
    r = client.post(
        "/batch",
        json={
            "actions": [{"type": "sleep", "ms": 5}],
            "expect_focus": {"hwnd": 1002},
        },
        headers=auth_headers,
    )
    assert r.status_code == 201
    assert r.json()["succeeded"] == 1


def test_no_expect_focus_skips_check(client, auth_headers, fake_send_input):
    """Backward compat: omitting expect_focus = no focus check (existing behavior)."""
    r = client.post(
        "/key/hold",
        json={"name": "w", "duration_ms": 10},
        headers=auth_headers,
    )
    assert r.status_code == 201
    assert len(fake_send_input) == 2
