"""Section 8 — cleanup mechanism (release_all on shutdown, watchdog, atexit)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from gargaros import input_driver
from gargaros.input_state import InputState, emergency_release_sync


@pytest.fixture(autouse=True)
def fake_send_input(monkeypatch):
    sent: list[list[input_driver.INPUT]] = []
    monkeypatch.setattr(input_driver, "_send_input", lambda inputs: sent.append(list(inputs)) or len(inputs))
    return sent


async def test_release_all_releases_keys_and_buttons():
    driver = MagicMock()
    state = InputState(driver=driver)
    await state.press_key("w")
    await state.press_key("shift")
    await state.press_button("right")

    released = await state.release_all()

    assert sorted(released["keys"]) == ["shift", "w"]
    assert released["buttons"] == ["right"]
    assert state.held_keys == []
    assert state.held_buttons == []
    # Verify key_up + mouse_button up were dispatched
    assert any(c[0] == "key_up" for c in driver.method_calls)
    assert any(c[0] == "mouse_button" and c[1][1] == "up" for c in driver.method_calls)


async def test_watchdog_releases_stuck_key():
    driver = MagicMock()
    state = InputState(driver=driver)
    await state.press_key("w")

    # Manually backdate the pressed_at timestamp to simulate a >30 s hold.
    state._keys["w"] = time.monotonic() - 40

    # Run a single sweep (threshold default 30 s).
    await state._sweep_stuck(threshold=30.0)
    assert state.held_keys == []
    driver.key_up.assert_called_once_with("w")


async def test_watchdog_leaves_fresh_keys_alone():
    driver = MagicMock()
    state = InputState(driver=driver)
    await state.press_key("w")
    await state._sweep_stuck(threshold=30.0)
    assert state.held_keys == ["w"]
    driver.key_up.assert_not_called()  # mark_down used it, not key_up


def test_emergency_release_sync_clears_state_without_event_loop():
    driver = MagicMock()
    state = InputState(driver=driver)
    state._keys["w"] = time.monotonic()
    state._buttons["right"] = time.monotonic()
    emergency_release_sync(state)
    assert state._keys == {}
    assert state._buttons == {}
    driver.key_up.assert_called_with("w")
    driver.mouse_button.assert_called_with("right", "up")


def test_lifespan_release_all_on_shutdown(settings, fake_backend, token):
    """When TestClient exits, lifespan shutdown must release any held inputs."""
    from litestar.testing import TestClient

    from gargaros.app import build_app

    app = build_app(settings, backend=fake_backend, token=token)
    headers = {"Authorization": f"Bearer {token}", "Host": "127.0.0.1:7331"}
    with TestClient(app=app, base_url="http://127.0.0.1:7331") as c:
        c.post("/key/down", json={"name": "w"}, headers=headers)
        assert "w" in app.state.input_state.held_keys
    # After lifespan shutdown:
    assert app.state.input_state.held_keys == []


def test_lifespan_startup_release_all_recovers_from_dirty_state(settings, fake_backend, token):
    """Startup should release any pre-existing held inputs (recovery from crashed session)."""
    from litestar.testing import TestClient

    from gargaros.app import build_app
    from gargaros.input_state import InputState

    state = InputState(driver=input_driver)
    state._keys["w"] = time.monotonic()  # simulate leftover from crash
    state._buttons["right"] = time.monotonic()

    app = build_app(settings, backend=fake_backend, token=token, input_state=state)
    with TestClient(app=app, base_url="http://127.0.0.1:7331"):
        # Startup ran release_all
        assert app.state.input_state.held_keys == []
        assert app.state.input_state.held_buttons == []
