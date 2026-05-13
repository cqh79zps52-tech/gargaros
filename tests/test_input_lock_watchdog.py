"""F9 watchdog — verify subprocess.Popen is invoked with correct args, and that the
watchdog itself runs the polling loop (tested in-process for speed).
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from gargaros import input_lock as il
from gargaros import watchdog


@pytest.fixture(autouse=True)
def mock_hooks(monkeypatch):
    monkeypatch.setattr(il, "_set_windows_hook", lambda *a: 12345)
    monkeypatch.setattr(il, "_unhook_windows_hook", lambda h: True)
    monkeypatch.setattr(il, "_call_next_hook", lambda *a: 0)
    monkeypatch.setattr(il, "_run_message_loop", lambda stop: None)


def test_acquire_spawns_watchdog(monkeypatch):
    spawned = {}

    def fake_popen(cmd, **kw):
        spawned["cmd"] = cmd
        spawned["kw"] = kw
        m = MagicMock()
        m.terminate = MagicMock()
        m.wait = MagicMock()
        return m

    monkeypatch.setattr(il.subprocess, "Popen", fake_popen)
    # Force the platform check to think we're on Windows so watchdog spawns
    monkeypatch.setattr(il, "_PLATFORM_WIN", True)

    lock = il.InputLock()
    lock.acquire(spawn_watchdog=True, watchdog_args={"port": 7331, "token": "tok"})
    try:
        assert "cmd" in spawned
        assert spawned["cmd"][2] == "gargaros.watchdog"
        # Args include --port, --token, --target-pid, --lock-id, --timeout-ms
        cmd = spawned["cmd"]
        assert "--port" in cmd
        assert "--token" in cmd
        assert "--lock-id" in cmd
    finally:
        lock.release()


def test_release_terminates_watchdog(monkeypatch):
    proc = MagicMock()
    proc.terminate = MagicMock()
    proc.wait = MagicMock()

    monkeypatch.setattr(il.subprocess, "Popen", lambda *a, **kw: proc)
    monkeypatch.setattr(il, "_PLATFORM_WIN", True)

    lock = il.InputLock()
    lock.acquire(spawn_watchdog=True)
    lock.release()
    proc.terminate.assert_called_once()


def test_watchdog_force_kills_unresponsive_server():
    """In-process: watchdog should call _force_kill when the server stays silent past timeout."""
    killed = []

    def fake_kill(pid):
        killed.append(pid)

    with patch.object(watchdog, "_force_kill", fake_kill), \
         patch("urllib.request.urlopen") as mock_open, \
         patch("sys.argv", [
             "watchdog",
             "--port", "7331",
             "--token", "x",
             "--target-pid", "12345",
             "--lock-id", "abc",
             "--timeout-ms", "100",
             "--poll-ms", "20",
         ]):
        # Make urlopen always raise → simulates a hung server
        mock_open.side_effect = OSError("connection refused")
        rc = watchdog.main()

    assert rc == 1
    assert killed == [12345]


def test_watchdog_exits_cleanly_when_unlocked():
    """If the server reports locked=false, watchdog should exit 0 without killing."""
    killed = []

    def fake_kill(pid):
        killed.append(pid)

    fake_response = BytesIO(json.dumps({"locked": False}).encode())
    fake_response.__enter__ = lambda self: self  # type: ignore
    fake_response.__exit__ = lambda *a: None  # type: ignore

    with patch.object(watchdog, "_force_kill", fake_kill), \
         patch("urllib.request.urlopen", return_value=fake_response), \
         patch("sys.argv", [
             "watchdog",
             "--port", "7331",
             "--token", "x",
             "--target-pid", "12345",
             "--lock-id", "abc",
             "--timeout-ms", "1000",
             "--poll-ms", "20",
         ]):
        rc = watchdog.main()

    assert rc == 0
    assert killed == []
