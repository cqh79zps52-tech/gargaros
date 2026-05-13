"""F5 cloud — verify mouse_move_smooth supports both absolute (SetCursorPos)
and relative (SendInput MOUSEEVENTF_MOVE) modes."""

from __future__ import annotations

import pytest

from gargaros import input_driver


@pytest.fixture
def fake_send_input(monkeypatch):
    sent: list[list[input_driver.INPUT]] = []
    monkeypatch.setattr(input_driver, "_send_input", lambda inputs: sent.append(list(inputs)) or len(inputs))
    return sent


@pytest.fixture
def fake_cursor(monkeypatch):
    """Tracks calls to GetCursorPos / SetCursorPos for absolute-mode tests."""
    pos = [500, 500]
    get_calls: list[tuple[int, int]] = []
    set_calls: list[tuple[int, int]] = []

    def fake_get():
        get_calls.append(tuple(pos))
        return tuple(pos)

    def fake_set(x, y):
        pos[0], pos[1] = x, y
        set_calls.append((x, y))

    monkeypatch.setattr(input_driver, "_get_cursor_pos", fake_get)
    monkeypatch.setattr(input_driver, "_set_cursor_pos", fake_set)
    return {"pos": pos, "get": get_calls, "set": set_calls}


# ---- relative mode (cloud gaming / pointer lock) ----


def test_relative_mode_uses_send_input(client, auth_headers, fake_send_input, fake_cursor):
    r = client.post(
        "/mouse/move_smooth",
        json={"dx": 200, "dy": 0, "duration_ms": 50, "steps": 10, "mode": "relative"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["mode"] == "relative"
    assert body["steps_done"] == 10
    # 10 SendInput calls, 0 SetCursorPos calls
    assert len(fake_send_input) == 10
    assert len(fake_cursor["set"]) == 0


def test_relative_mode_cumulative_displacement(client, auth_headers, fake_send_input, fake_cursor):
    client.post(
        "/mouse/move_smooth",
        json={"dx": -540, "dy": 50, "duration_ms": 20, "steps": 20, "mode": "relative"},
        headers=auth_headers,
    )
    total_dx = sum(c[0].mi.dx for c in fake_send_input)
    total_dy = sum(c[0].mi.dy for c in fake_send_input)
    assert total_dx == -540
    assert total_dy == 50


def test_relative_mode_flags_no_absolute_flag(client, auth_headers, fake_send_input, fake_cursor):
    client.post(
        "/mouse/move_smooth",
        json={"dx": 100, "dy": 0, "duration_ms": 10, "steps": 5, "mode": "relative"},
        headers=auth_headers,
    )
    for call in fake_send_input:
        # MOUSEEVENTF_MOVE only, no MOUSEEVENTF_ABSOLUTE — the WM_INPUT raw input flavor
        assert call[0].mi.dwFlags == input_driver.MOUSEEVENTF_MOVE


# ---- absolute mode (desktop, backward compat) ----


def test_absolute_mode_uses_set_cursor_pos(client, auth_headers, fake_send_input, fake_cursor):
    r = client.post(
        "/mouse/move_smooth",
        json={"dx": 200, "dy": 0, "duration_ms": 50, "steps": 10, "mode": "absolute"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["mode"] == "absolute"
    assert body["steps_done"] == 10
    # 10 SetCursorPos calls, 0 SendInput calls
    assert len(fake_cursor["set"]) == 10
    assert len(fake_send_input) == 0


def test_absolute_mode_moves_cursor_by_total_delta(client, auth_headers, fake_cursor):
    start_x, start_y = fake_cursor["pos"]
    client.post(
        "/mouse/move_smooth",
        json={"dx": 200, "dy": -50, "duration_ms": 20, "steps": 10, "mode": "absolute"},
        headers=auth_headers,
    )
    end_x, end_y = fake_cursor["pos"]
    assert end_x - start_x == 200
    assert end_y - start_y == -50


def test_default_mode_is_absolute(client, auth_headers, fake_send_input, fake_cursor):
    """Backward compat: omitting mode keeps the pre-cloud SetCursorPos behavior."""
    r = client.post(
        "/mouse/move_smooth",
        json={"dx": 50, "dy": 0, "duration_ms": 10, "steps": 5},
        headers=auth_headers,
    )
    assert r.json()["mode"] == "absolute"
    assert len(fake_cursor["set"]) == 5
    assert len(fake_send_input) == 0


# ---- button tracking (unchanged from native) ----


def test_mouse_button_down_up(client, auth_headers, app, fake_send_input):
    r1 = client.post("/mouse/button", json={"button": "right", "action": "down"}, headers=auth_headers)
    assert r1.status_code == 201
    assert r1.json()["state"] == "down"
    assert "right" in app.state.input_state.held_buttons
    r2 = client.post("/mouse/button", json={"button": "right", "action": "up"}, headers=auth_headers)
    assert r2.json()["state"] == "up"
    assert "right" not in app.state.input_state.held_buttons


def test_mouse_button_up_idempotent(client, auth_headers, fake_send_input):
    r = client.post("/mouse/button", json={"button": "right", "action": "up"}, headers=auth_headers)
    assert r.status_code == 201
    assert "warning" in r.json()


def test_mouse_release_all(client, auth_headers, app, fake_send_input):
    client.post("/mouse/button", json={"button": "left", "action": "down"}, headers=auth_headers)
    client.post("/mouse/button", json={"button": "right", "action": "down"}, headers=auth_headers)
    r = client.post("/mouse/release_all", headers=auth_headers)
    assert sorted(r.json()["buttons"]) == ["left", "right"]
    assert app.state.input_state.held_buttons == []
