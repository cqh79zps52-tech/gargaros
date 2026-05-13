from __future__ import annotations

import pytest

from gargaros import window_driver


@pytest.fixture
def fake_windows(monkeypatch):
    state = {
        "hwnds": [1001, 1002, 1003],
        "foreground": 1001,  # Notepad starts foreground
        "windows": {
            1001: {"title": "Notepad", "pid": 100, "tid": 200, "process_name": "notepad.exe", "visible": True, "iconic": False, "rect": (0, 0, 800, 600)},
            1002: {"title": "Xbox - Google Chrome", "pid": 200, "tid": 201, "process_name": "chrome.exe", "visible": True, "iconic": False, "rect": (0, 0, 1920, 1080)},
            1003: {"title": "Another Chrome", "pid": 200, "tid": 202, "process_name": "chrome.exe", "visible": True, "iconic": False, "rect": (0, 0, 1920, 1080)},
        },
    }
    monkeypatch.setattr(window_driver, "_enum_windows", lambda: list(state["hwnds"]))
    # Unknown hwnds get OS-like default values (empty strings, pid=0) instead of KeyError,
    # so the production code path that catches invalid hwnds via pid==0 works in tests too.
    monkeypatch.setattr(window_driver, "_get_window_text", lambda h: state["windows"].get(h, {}).get("title", ""))
    monkeypatch.setattr(window_driver, "_get_window_thread_process_id", lambda h: ((state["windows"].get(h, {}).get("tid", 0), state["windows"].get(h, {}).get("pid", 0))))
    monkeypatch.setattr(window_driver, "_get_process_name", lambda pid: next((w["process_name"] for w in state["windows"].values() if w["pid"] == pid), ""))
    monkeypatch.setattr(window_driver, "_is_window_visible", lambda h: state["windows"].get(h, {}).get("visible", False))
    monkeypatch.setattr(window_driver, "_is_iconic", lambda h: state["windows"].get(h, {}).get("iconic", False))
    monkeypatch.setattr(window_driver, "_get_window_rect", lambda h: state["windows"].get(h, {}).get("rect", (0, 0, 0, 0)))
    monkeypatch.setattr(window_driver, "_get_foreground_window", lambda: state["foreground"])
    def fake_set(h):
        state["foreground"] = h
        return True
    monkeypatch.setattr(window_driver, "_set_foreground_window", fake_set)
    def fake_show(h, cmd):
        if cmd == window_driver.SW_RESTORE:
            state["windows"][h]["iconic"] = False
        return True
    monkeypatch.setattr(window_driver, "_show_window", fake_show)
    return state


def test_focus_by_hwnd(client, auth_headers, fake_windows):
    r = client.post("/window/focus", json={"selector": {"hwnd": 1002}}, headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["hwnd"] == 1002
    assert fake_windows["foreground"] == 1002


def test_focus_by_title_contains(client, auth_headers, fake_windows):
    r = client.post(
        "/window/focus",
        json={"selector": {"title_contains": "Notepad"}},
        headers=auth_headers,
    )
    assert r.status_code == 201
    assert r.json()["hwnd"] == 1001


def test_focus_by_process_name(client, auth_headers, fake_windows):
    # process_name=notepad.exe matches only one window → unique → succeeds
    r = client.post(
        "/window/focus",
        json={"selector": {"process_name": "notepad.exe"}},
        headers=auth_headers,
    )
    assert r.status_code == 201
    assert r.json()["hwnd"] == 1001


def test_focus_ambiguous_returns_409(client, auth_headers, fake_windows):
    # process_name=chrome.exe matches BOTH 1002 and 1003 → ambiguous
    r = client.post(
        "/window/focus",
        json={"selector": {"process_name": "chrome.exe"}},
        headers=auth_headers,
    )
    assert r.status_code == 409


def test_focus_no_match_returns_404(client, auth_headers, fake_windows):
    r = client.post(
        "/window/focus",
        json={"selector": {"title_contains": "DefinitelyNotThere"}},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_focus_empty_selector_returns_400(client, auth_headers, fake_windows):
    r = client.post("/window/focus", json={"selector": {}}, headers=auth_headers)
    assert r.status_code == 400


def test_focus_restores_minimized(client, auth_headers, fake_windows):
    fake_windows["windows"][1001]["iconic"] = True
    r = client.post(
        "/window/focus",
        json={"selector": {"hwnd": 1001}, "restore_if_minimized": True},
        headers=auth_headers,
    )
    assert r.status_code == 201
    assert fake_windows["windows"][1001]["iconic"] is False


def test_wait_for_focus_immediate(client, auth_headers, fake_windows):
    # Notepad is already foreground
    r = client.post(
        "/window/wait_for_focus",
        json={"selector": {"hwnd": 1001}, "timeout_ms": 1000},
        headers=auth_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["matched"] is True


def test_wait_for_focus_timeout_returns_408(client, auth_headers, fake_windows):
    r = client.post(
        "/window/wait_for_focus",
        json={"selector": {"hwnd": 9999}, "timeout_ms": 200, "poll_interval_ms": 50},
        headers=auth_headers,
    )
    assert r.status_code == 408


def test_focus_by_title_regex(client, auth_headers, fake_windows):
    r = client.post(
        "/window/focus",
        json={"selector": {"title_regex": r"^Notepad$"}},
        headers=auth_headers,
    )
    assert r.status_code == 201
    assert r.json()["hwnd"] == 1001
