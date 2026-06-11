from __future__ import annotations


def test_ui_snapshot(client, auth_headers, fake_backend):
    r = client.post("/ui/snapshot", json={}, headers=auth_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["isError"] is False
    assert body["text"] == ["ok: Snapshot"]
    name, args = fake_backend.calls[-1]
    assert name == "Snapshot"
    assert args["use_annotation"] is True
    assert args["use_ui_tree"] is True


def test_ui_click_label_success(client, auth_headers, fake_backend):
    r = client.post("/ui/click_label", json={"label": 7}, headers=auth_headers)
    assert r.status_code == 201
    name, args = fake_backend.calls[-1]
    assert name == "Click"
    assert args == {"label": 7, "button": "left", "clicks": 1}


def test_ui_click_label_no_snapshot_returns_409(client, auth_headers, fake_backend):
    fake_backend.set_error_result("Desktop state is empty. Please call Snapshot first.")
    r = client.post("/ui/click_label", json={"label": 7}, headers=auth_headers)
    assert r.status_code == 409


def test_ui_type_label(client, auth_headers, fake_backend):
    r = client.post(
        "/ui/type_label",
        json={"label": 3, "text": "hello", "press_enter": True},
        headers=auth_headers,
    )
    assert r.status_code == 201
    name, args = fake_backend.calls[-1]
    assert name == "Type"
    assert args == {"label": 3, "text": "hello", "clear": False, "press_enter": True}


def test_ui_scrape(client, auth_headers, fake_backend):
    r = client.post(
        "/ui/scrape",
        json={"url": "https://example.com", "query": "headline"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    name, args = fake_backend.calls[-1]
    assert name == "Scrape"
    assert args["url"] == "https://example.com"
    assert args["query"] == "headline"


def test_ui_launch_app(client, auth_headers, fake_backend):
    r = client.post(
        "/ui/launch_app",
        json={"name": "notepad", "mode": "launch"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    name, args = fake_backend.calls[-1]
    assert name == "App"
    assert args == {"mode": "launch", "name": "notepad"}


def test_agent_launch_app_409_when_hidden_desktop_disabled(client, auth_headers):
    # The test fixture disables the hidden desktop, so this endpoint must 409.
    r = client.post("/agent/launch_app", json={"cmdline": "notepad.exe"}, headers=auth_headers)
    assert r.status_code == 409


def test_agent_launch_app_launches_on_hidden_desktop(client, auth_headers):
    class FakeHidden:
        name = "agent_dsk"

        def __init__(self):
            self.launched = []

        def launch(self, cmdline):
            self.launched.append(cmdline)
            return 4321

    fake = FakeHidden()
    client.app.state.hidden_desktop = fake
    try:
        r = client.post("/agent/launch_app", json={"cmdline": "notepad.exe"}, headers=auth_headers)
        assert r.status_code == 201
        assert r.json() == {"ok": True, "pid": 4321, "desktop": "agent_dsk"}
        assert fake.launched == ["notepad.exe"]
    finally:
        client.app.state.hidden_desktop = None
