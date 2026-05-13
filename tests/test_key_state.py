from __future__ import annotations

import pytest

from gargaros import input_driver


@pytest.fixture(autouse=True)
def fake_send_input(monkeypatch):
    sent: list[list[input_driver.INPUT]] = []
    monkeypatch.setattr(input_driver, "_send_input", lambda inputs: sent.append(list(inputs)) or len(inputs))
    return sent


def test_key_down_records_state(client, auth_headers, app):
    r = client.post("/key/down", json={"name": "shift"}, headers=auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "down"
    assert body["newly_pressed"] is True
    assert "shift" in app.state.input_state.held_keys


def test_key_down_idempotent(client, auth_headers, app, fake_send_input):
    client.post("/key/down", json={"name": "shift"}, headers=auth_headers)
    r2 = client.post("/key/down", json={"name": "shift"}, headers=auth_headers)
    assert r2.json()["newly_pressed"] is False
    # only 1 SendInput call (the second is suppressed because already down)
    assert len(fake_send_input) == 1


def test_key_up_clears_state_and_returns_held_ms(client, auth_headers, app):
    client.post("/key/down", json={"name": "shift"}, headers=auth_headers)
    r = client.post("/key/up", json={"name": "shift"}, headers=auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "up"
    assert "was_held_ms" in body
    assert body["was_held_ms"] >= 0
    assert "shift" not in app.state.input_state.held_keys


def test_key_up_idempotent_when_not_down(client, auth_headers):
    r = client.post("/key/up", json={"name": "shift"}, headers=auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["state"] == "up"
    assert "warning" in body  # idempotent — no 4xx


def test_release_all_clears_everything(client, auth_headers, app):
    client.post("/key/down", json={"name": "w"}, headers=auth_headers)
    client.post("/key/down", json={"name": "shift"}, headers=auth_headers)
    r = client.post("/key/release_all", headers=auth_headers)
    body = r.json()
    assert sorted(body["keys"]) == ["shift", "w"]
    assert app.state.input_state.held_keys == []
