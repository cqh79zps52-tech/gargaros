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


def test_screenshot_raw_return_meta():
    fake_png = b"\x89PNG\r\nfake"

    def handler(request: httpx.Request) -> httpx.Response:
        assert "raw=true" in str(request.url)
        return httpx.Response(
            200,
            content=fake_png,
            headers={
                "content-type": "image/png",
                "x-capture-time-ms": "42",
                "x-encode-time-ms": "1",
            },
        )

    with _make_client(handler) as c:
        img, meta = c.screenshot(raw=True, return_meta=True)
    assert img == fake_png
    assert meta["capture_time_ms"] == 42
    assert meta["encode_time_ms"] == 1
    assert meta["content_type"] == "image/png"


def test_key_hold_method():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.read())
        return httpx.Response(201, json={"elapsed_ms": 500})

    with _make_client(handler) as c:
        r = c.key_hold("w", 500)
    assert captured["path"] == "/key/hold"
    assert captured["body"] == {"name": "w", "duration_ms": 500, "modifiers": []}
    assert r["elapsed_ms"] == 500


def test_key_down_up_methods():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(201, json={"state": "down"})

    with _make_client(handler) as c:
        c.key_down("shift")
        c.key_up("shift")
    assert paths == ["/key/down", "/key/up"]


def test_key_sequence_method():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.read())
        return httpx.Response(201, json={"total_elapsed_ms": 100})

    with _make_client(handler) as c:
        c.key_sequence([{"name": "w", "hold_ms": 50}], inter_key_delay_ms=10)
    assert captured["path"] == "/key/sequence"
    assert captured["body"]["inter_key_delay_ms"] == 10


def test_mouse_move_smooth_method():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.read())
        return httpx.Response(201, json={"steps_done": 20, "elapsed_ms": 200, "mode": "absolute"})

    with _make_client(handler) as c:
        c.mouse_move_smooth(540, 0, duration_ms=200, steps=20)
    assert captured["path"] == "/mouse/move_smooth"
    assert captured["body"] == {"dx": 540, "dy": 0, "duration_ms": 200, "steps": 20, "mode": "absolute"}


def test_mouse_move_smooth_relative_mode():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        return httpx.Response(201, json={"mode": "relative"})

    with _make_client(handler) as c:
        c.mouse_move_smooth(540, 0, mode="relative")
    assert captured["body"]["mode"] == "relative"


def test_mouse_button_method():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        return httpx.Response(201, json={"state": "down"})

    with _make_client(handler) as c:
        c.mouse_button("right", "down")
    assert captured["body"] == {"button": "right", "action": "down"}


def test_input_release_all_method():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(201, json={"keys": [], "buttons": []})

    with _make_client(handler) as c:
        c.input_release_all()
        c.key_release_all()
        c.mouse_release_all()
    assert paths == ["/input/release_all", "/key/release_all", "/mouse/release_all"]


def test_batch_returns_full_response():
    """batch() returns the whole response dict (total_elapsed_ms, succeeded, failed, results)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={
                "total_elapsed_ms": 100,
                "succeeded": 1,
                "failed": 0,
                "results": [{"index": 0, "type": "sleep", "status": "ok", "elapsed_ms": 50}],
            },
        )

    with _make_client(handler) as c:
        r = c.batch([{"type": "sleep", "ms": 50}])
    assert r["succeeded"] == 1
    assert r["results"][0]["status"] == "ok"
