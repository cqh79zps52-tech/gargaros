from __future__ import annotations

import pytest

from gargaros import window_driver


@pytest.fixture
def fake_windows(monkeypatch):
    """Fake out the OS calls so tests are deterministic and cross-platform."""
    state = {
        "hwnds": [1001, 1002, 1003, 1004],
        "foreground": 1002,
        "windows": {
            1001: {
                "title": "Notepad - Untitled",
                "pid": 100, "tid": 200,
                "process_name": "notepad.exe",
                "visible": True, "iconic": False,
                "rect": (100, 100, 900, 700),
            },
            1002: {
                "title": "Xbox - Google Chrome",
                "pid": 200, "tid": 201,
                "process_name": "chrome.exe",
                "visible": True, "iconic": False,
                "rect": (0, 0, 1920, 1080),
            },
            1003: {
                "title": "",  # blank title → filtered when visible_only=True
                "pid": 300, "tid": 301,
                "process_name": "Program Manager.exe",
                "visible": True, "iconic": False,
                "rect": (0, 0, 1920, 1080),
            },
            1004: {
                "title": "Minimized Window",
                "pid": 400, "tid": 401,
                "process_name": "other.exe",
                "visible": False, "iconic": True,
                "rect": (0, 0, 800, 600),
            },
        },
    }
    monkeypatch.setattr(window_driver, "_enum_windows", lambda: list(state["hwnds"]))
    monkeypatch.setattr(window_driver, "_get_window_text", lambda h: state["windows"][h]["title"])
    monkeypatch.setattr(
        window_driver, "_get_window_thread_process_id",
        lambda h: (state["windows"][h]["tid"], state["windows"][h]["pid"]),
    )
    monkeypatch.setattr(window_driver, "_get_process_name", lambda pid: next(
        (w["process_name"] for w in state["windows"].values() if w["pid"] == pid), ""
    ))
    monkeypatch.setattr(window_driver, "_is_window_visible", lambda h: state["windows"][h]["visible"])
    monkeypatch.setattr(window_driver, "_is_iconic", lambda h: state["windows"][h]["iconic"])
    monkeypatch.setattr(window_driver, "_get_window_rect", lambda h: state["windows"][h]["rect"])
    monkeypatch.setattr(window_driver, "_get_foreground_window", lambda: state["foreground"])

    focused = []
    def fake_set_fg(h):
        state["foreground"] = h
        focused.append(h)
        return True
    monkeypatch.setattr(window_driver, "_set_foreground_window", fake_set_fg)

    shown = []
    def fake_show(h, cmd):
        if cmd == window_driver.SW_RESTORE:
            state["windows"][h]["iconic"] = False
        shown.append((h, cmd))
        return True
    monkeypatch.setattr(window_driver, "_show_window", fake_show)

    state["_focused_calls"] = focused
    state["_shown_calls"] = shown
    return state


def test_window_list_filters_invisible(client, auth_headers, fake_windows):
    r = client.get("/window/list?visible_only=true", headers=auth_headers)
    assert r.status_code == 200
    titles = [w["title"] for w in r.json()]
    assert "Notepad - Untitled" in titles
    assert "Xbox - Google Chrome" in titles
    # Minimized + blank-title both filtered out
    assert "Minimized Window" not in titles
    assert "" not in titles


def test_window_list_includes_all_when_visible_only_false(client, auth_headers, fake_windows):
    r = client.get("/window/list?visible_only=false", headers=auth_headers)
    titles = [w["title"] for w in r.json()]
    assert "Minimized Window" in titles


def test_window_list_returns_pid_and_bounds(client, auth_headers, fake_windows):
    r = client.get("/window/list", headers=auth_headers)
    win = next(w for w in r.json() if "Xbox" in w["title"])
    assert win["pid"] == 200
    assert win["process_name"] == "chrome.exe"
    assert win["bounds"] == {"x": 0, "y": 0, "width": 1920, "height": 1080}
    assert win["is_foreground"] is True


def test_window_active(client, auth_headers, fake_windows):
    r = client.get("/window/active", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "Xbox" in body["title"]
    assert body["hwnd"] == 1002
