from __future__ import annotations

import time

import pytest

from gargaros import input_driver


@pytest.fixture(autouse=True)
def fake_send_input(monkeypatch):
    sent: list[list[input_driver.INPUT]] = []
    monkeypatch.setattr(input_driver, "_send_input", lambda inputs: sent.append(list(inputs)) or len(inputs))
    return sent


def test_key_hold_returns_elapsed_within_tolerance(client, auth_headers):
    start = time.monotonic()
    r = client.post("/key/hold", json={"name": "w", "duration_ms": 500}, headers=auth_headers)
    wall_ms = int((time.monotonic() - start) * 1000)
    assert r.status_code == 201
    elapsed_ms = r.json()["elapsed_ms"]
    assert 490 <= elapsed_ms <= 700, f"elapsed_ms={elapsed_ms}, wall={wall_ms}"


def test_key_hold_sends_down_then_up(client, auth_headers, fake_send_input):
    r = client.post("/key/hold", json={"name": "w", "duration_ms": 30}, headers=auth_headers)
    assert r.status_code == 201
    assert len(fake_send_input) == 2
    down, up = fake_send_input
    assert down[0].ki.wVk == 0x57
    assert down[0].ki.dwFlags == 0
    assert up[0].ki.wVk == 0x57
    assert up[0].ki.dwFlags & input_driver.KEYEVENTF_KEYUP


def test_key_hold_with_modifiers_orders_events(client, auth_headers, fake_send_input):
    r = client.post(
        "/key/hold",
        json={"name": "w", "duration_ms": 20, "modifiers": ["shift"]},
        headers=auth_headers,
    )
    assert r.status_code == 201
    # shift down, w down, w up, shift up
    assert len(fake_send_input) == 4
    shift_vk = 0x10
    w_vk = 0x57
    assert fake_send_input[0][0].ki.wVk == shift_vk
    assert fake_send_input[0][0].ki.dwFlags == 0
    assert fake_send_input[1][0].ki.wVk == w_vk
    assert fake_send_input[1][0].ki.dwFlags == 0
    assert fake_send_input[2][0].ki.wVk == w_vk
    assert fake_send_input[2][0].ki.dwFlags & input_driver.KEYEVENTF_KEYUP
    assert fake_send_input[3][0].ki.wVk == shift_vk
    assert fake_send_input[3][0].ki.dwFlags & input_driver.KEYEVENTF_KEYUP


def test_key_hold_leaves_no_held_keys(client, auth_headers, app):
    client.post("/key/hold", json={"name": "w", "duration_ms": 30}, headers=auth_headers)
    assert app.state.input_state.held_keys == []
