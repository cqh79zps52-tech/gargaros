from __future__ import annotations

import json

import httpx
import pytest

from gargaros.client import Client


def _make_client(handler):
    """Build a Client that talks to a MockTransport handler, bypassing the network."""
    transport = httpx.MockTransport(handler)
    return Client(token="t", base_url="http://gargaros.test", transport=transport)


def test_health_request_shape():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True, "version": "x"})

    with _make_client(handler) as c:
        body = c.health()
    assert body["ok"] is True
    assert captured["path"] == "/health"
    assert captured["auth"] == "Bearer t"


def test_click_method():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.read())
        return httpx.Response(201, json={"ok": True})

    with _make_client(handler) as c:
        c.click(100, 200, button="right", double=True)

    assert captured["path"] == "/click"
    assert captured["body"] == {"x": 100, "y": 200, "button": "right", "double": True}


def test_type_method():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        return httpx.Response(201, json={"ok": True})

    with _make_client(handler) as c:
        c.type("hello", press_enter=True)
    assert captured["body"] == {"text": "hello", "press_enter": True}


def test_key_method():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        return httpx.Response(201, json={"ok": True})

    with _make_client(handler) as c:
        c.key("c", modifiers=["ctrl"])
    assert captured["body"] == {"name": "c", "modifiers": ["ctrl"]}


def test_screenshot_returns_bytes():
    fake_jpeg = b"\xff\xd8\xff\xe0fake jpeg"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/screenshot"
        return httpx.Response(200, content=fake_jpeg, headers={"content-type": "image/jpeg"})

    with _make_client(handler) as c:
        body = c.screenshot()
    assert body == fake_jpeg


def test_drag_normalizes_tuples():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        return httpx.Response(201, json={"ok": True})

    with _make_client(handler) as c:
        c.drag((10, 20), (30, 40))
    assert captured["body"]["from"] == {"x": 10, "y": 20}
    assert captured["body"]["to"] == {"x": 30, "y": 40}


def test_no_token_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("GARGAROS_TOKEN", raising=False)
    monkeypatch.setattr(
        "gargaros.client.user_data_dir", lambda *a, **kw: str(tmp_path / "no-such")
    )
    with pytest.raises(RuntimeError, match="no Gargaros token"):
        Client()


def test_explicit_token():
    c = Client(token="abc", base_url="http://example.invalid")
    assert c._token == "abc"
    c.close()


def test_env_token_picked_up(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gargaros.client.user_data_dir", lambda *a, **kw: str(tmp_path / "no-such")
    )
    monkeypatch.setenv("GARGAROS_TOKEN", "env-tok")
    c = Client(base_url="http://example.invalid")
    assert c._token == "env-tok"
    c.close()
