"""F9 — basic lock acquire/release lifecycle and status."""

from __future__ import annotations

import pytest

from gargaros import input_lock as il


@pytest.fixture(autouse=True)
def mock_hooks(monkeypatch):
    """Stub out the Win32 hook plumbing so tests don't install real hooks."""
    monkeypatch.setattr(il, "_set_windows_hook", lambda hook_id, proc, hmod, tid: 12345)
    monkeypatch.setattr(il, "_unhook_windows_hook", lambda h: True)
    monkeypatch.setattr(il, "_call_next_hook", lambda h, n, w, l: 0)
    monkeypatch.setattr(il, "_run_message_loop", lambda stop: None)


@pytest.fixture(autouse=True)
def mock_watchdog(monkeypatch):
    """Don't actually spawn the watchdog child process during tests."""
    monkeypatch.setattr(il.InputLock, "_spawn_watchdog", lambda self, args: None)
    monkeypatch.setattr(il.InputLock, "_stop_watchdog", lambda self: None)


def test_lock_status_when_unlocked(client, auth_headers):
    r = client.get("/input/lock_status", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["locked"] is False


def test_lock_acquire_returns_locked_status(client, auth_headers):
    r = client.post("/input/lock", json={}, headers=auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["locked"] is True
    assert body["unlock_hotkey"] == "ctrl+shift+f12"
    assert "lock_id" in body
    assert "started_at" in body


def test_lock_status_after_acquire(client, auth_headers):
    client.post("/input/lock", json={}, headers=auth_headers)
    r = client.get("/input/lock_status", headers=auth_headers)
    body = r.json()
    assert body["locked"] is True
    assert body["blocked_physical_events"] == 0
    assert body["passed_injected_events"] == 0
    # Always cleanup
    client.post("/input/unlock", headers=auth_headers)


def test_lock_acquire_twice_returns_409(client, auth_headers):
    client.post("/input/lock", json={}, headers=auth_headers)
    r2 = client.post("/input/lock", json={}, headers=auth_headers)
    assert r2.status_code == 409
    client.post("/input/unlock", headers=auth_headers)


def test_unlock_returns_was_locked_ms(client, auth_headers):
    client.post("/input/lock", json={}, headers=auth_headers)
    r = client.post("/input/unlock", headers=auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "unlocked"
    assert body["was_locked_ms"] >= 0


def test_unlock_idempotent_when_not_locked(client, auth_headers):
    r = client.post("/input/unlock", headers=auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "unlocked"
    assert body["was_locked_ms"] == 0


def test_lock_with_custom_hotkey(client, auth_headers):
    r = client.post(
        "/input/lock",
        json={"unlock_hotkey": "ctrl+alt+f1"},
        headers=auth_headers,
    )
    assert r.json()["unlock_hotkey"] == "ctrl+alt+f1"
    client.post("/input/unlock", headers=auth_headers)


def test_lock_invalid_hotkey_returns_400(client, auth_headers):
    r = client.post(
        "/input/lock",
        json={"unlock_hotkey": "plonk+blarg"},
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_hotkey_parser_accepts_common_combos():
    mods, primary = il._parse_hotkey("ctrl+shift+f12")
    assert mods == {0x11, 0x10}
    assert primary == 0x7B  # F12

    mods, primary = il._parse_hotkey("alt+f4")
    assert mods == {0x12}
    assert primary == 0x73  # F4

    mods, primary = il._parse_hotkey("ctrl+c")
    assert mods == {0x11}
    assert primary == 0x43  # C
