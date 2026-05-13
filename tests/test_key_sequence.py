from __future__ import annotations

import pytest

from gargaros import input_driver


@pytest.fixture(autouse=True)
def fake_send_input(monkeypatch):
    sent: list[list[input_driver.INPUT]] = []
    monkeypatch.setattr(input_driver, "_send_input", lambda inputs: sent.append(list(inputs)) or len(inputs))
    return sent


def test_key_sequence_cumulative_timing(client, auth_headers):
    r = client.post(
        "/key/sequence",
        json={
            "keys": [
                {"name": "w", "hold_ms": 500},
                {"name": "space", "hold_ms": 500},
                {"name": "w", "hold_ms": 500},
            ],
            "inter_key_delay_ms": 30,
        },
        headers=auth_headers,
    )
    assert r.status_code == 201
    total = r.json()["total_elapsed_ms"]
    # 3 holds @ 500 ms + 2 inter-delays @ 30 ms = ~1560 ms (allow slop)
    assert 1500 <= total <= 1800, f"total_elapsed_ms={total}"


def test_key_sequence_leaves_no_keys_down(client, auth_headers, app):
    client.post(
        "/key/sequence",
        json={
            "keys": [
                {"name": "w", "hold_ms": 30},
                {"name": "space", "hold_ms": 30},
            ],
            "inter_key_delay_ms": 5,
        },
        headers=auth_headers,
    )
    assert app.state.input_state.held_keys == []


def test_key_sequence_sends_paired_events(client, auth_headers, fake_send_input):
    client.post(
        "/key/sequence",
        json={"keys": [{"name": "w", "hold_ms": 10}, {"name": "space", "hold_ms": 10}]},
        headers=auth_headers,
    )
    # 4 events: w_down, w_up, space_down, space_up
    assert len(fake_send_input) == 4
    assert fake_send_input[0][0].ki.wVk == 0x57 and fake_send_input[0][0].ki.dwFlags == 0
    assert fake_send_input[1][0].ki.wVk == 0x57 and fake_send_input[1][0].ki.dwFlags & input_driver.KEYEVENTF_KEYUP
    assert fake_send_input[2][0].ki.wVk == 0x20 and fake_send_input[2][0].ki.dwFlags == 0
    assert fake_send_input[3][0].ki.wVk == 0x20 and fake_send_input[3][0].ki.dwFlags & input_driver.KEYEVENTF_KEYUP


def test_key_sequence_rollback_on_error(client, auth_headers, app, monkeypatch):
    # Force an error mid-sequence by making the second release fail.
    real_key_up = input_driver.key_up
    call_count = {"n": 0}

    def flaky_key_up(name):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated driver failure")
        return real_key_up(name)

    monkeypatch.setattr(input_driver, "key_up", flaky_key_up)
    # Even if the driver throws, we want rollback to leave no held keys.
    try:
        client.post(
            "/key/sequence",
            json={"keys": [{"name": "w", "hold_ms": 5}, {"name": "a", "hold_ms": 5}]},
            headers=auth_headers,
        )
    except Exception:
        pass
    # NOTE: input_state.release_key catches its own driver exceptions internally;
    # the rollback path in the route releases anything still held. End state must be clean.
    assert app.state.input_state.held_keys == []
