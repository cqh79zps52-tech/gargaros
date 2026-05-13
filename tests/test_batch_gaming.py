"""F1 — verify the extended /batch handles all new gaming action types."""

from __future__ import annotations

import pytest

from gargaros import input_driver


@pytest.fixture(autouse=True)
def fake_send_input(monkeypatch):
    sent: list[list[input_driver.INPUT]] = []
    monkeypatch.setattr(input_driver, "_send_input", lambda inputs: sent.append(list(inputs)) or len(inputs))
    return sent


def test_batch_with_cdc_section_2_payload(client, auth_headers, app):
    """The exact example from CDC section 2 — should total ~870ms (200+500 sleeps + small ops)."""
    body = {
        "actions": [
            {"type": "mouse_move_smooth", "dx": 90, "dy": 0, "duration_ms": 150, "steps": 8, "mode": "relative"},
            {"type": "key_down", "name": "w"},
            {"type": "sleep", "ms": 200},
            {"type": "key", "name": "space"},
            {"type": "sleep", "ms": 500},
            {"type": "key_up", "name": "w"},
        ],
        "continue_on_error": False,
    }
    r = client.post("/batch", json=body, headers=auth_headers)
    assert r.status_code == 201
    out = r.json()
    assert out["succeeded"] == 6
    assert out["failed"] == 0
    assert 800 <= out["total_elapsed_ms"] <= 1100, f"total={out['total_elapsed_ms']}"
    # No keys should remain held after a clean batch
    assert app.state.input_state.held_keys == []


def test_batch_flat_field_syntax(client, auth_headers):
    """The CDC uses flat fields, e.g. {type, name, duration_ms} — not nested args."""
    r = client.post(
        "/batch",
        json={"actions": [{"type": "key_hold", "name": "w", "duration_ms": 30}]},
        headers=auth_headers,
    )
    out = r.json()
    assert out["results"][0]["status"] == "ok"
    assert out["results"][0]["type"] == "key_hold"


def test_batch_legacy_op_args_syntax_still_works(client, auth_headers):
    """Backward compat: existing clients use {op, args}."""
    r = client.post(
        "/batch",
        json={"actions": [{"op": "click", "args": {"x": 5, "y": 5}}]},
        headers=auth_headers,
    )
    assert r.json()["results"][0]["status"] == "ok"


def test_batch_sleep_caps_at_5000ms(client, auth_headers):
    r = client.post(
        "/batch",
        json={"actions": [{"type": "sleep", "ms": 9999}]},
        headers=auth_headers,
    )
    out = r.json()
    assert out["results"][0]["status"] == "error"
    assert "5000" in out["results"][0]["error"]


def test_batch_key_sequence_action(client, auth_headers, app):
    r = client.post(
        "/batch",
        json={
            "actions": [
                {
                    "type": "key_sequence",
                    "keys": [{"name": "w", "hold_ms": 20}, {"name": "space", "hold_ms": 20}],
                    "inter_key_delay_ms": 5,
                }
            ]
        },
        headers=auth_headers,
    )
    assert r.json()["results"][0]["status"] == "ok"
    assert app.state.input_state.held_keys == []


def test_batch_mouse_button_via_batch(client, auth_headers, app):
    r = client.post(
        "/batch",
        json={
            "actions": [
                {"type": "mouse_button", "button": "right", "action": "down"},
                {"type": "sleep", "ms": 10},
                {"type": "mouse_button", "button": "right", "action": "up"},
            ]
        },
        headers=auth_headers,
    )
    assert r.json()["succeeded"] == 3
    assert app.state.input_state.held_buttons == []


def test_batch_continue_on_error_with_gaming(client, auth_headers):
    r = client.post(
        "/batch",
        json={
            "continue_on_error": True,
            "actions": [
                {"type": "sleep", "ms": 10},
                {"type": "unknown_op"},
                {"type": "sleep", "ms": 10},
            ],
        },
        headers=auth_headers,
    )
    out = r.json()
    assert [x["status"] for x in out["results"]] == ["ok", "error", "ok"]


def test_batch_response_includes_per_action_elapsed_ms(client, auth_headers):
    r = client.post(
        "/batch",
        json={"actions": [{"type": "sleep", "ms": 50}, {"type": "sleep", "ms": 50}]},
        headers=auth_headers,
    )
    out = r.json()
    for res in out["results"]:
        assert "elapsed_ms" in res
        assert res["elapsed_ms"] >= 40  # allow some scheduling slop
