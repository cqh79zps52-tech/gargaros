from __future__ import annotations

from gargaros.ocr import Match


def _stub_runner(image_bytes: bytes) -> list[Match]:
    return [
        Match(text="Hello World", bbox=(100, 200, 80, 20), confidence=0.95),
        Match(text="GARGAROS TEST 12345", bbox=(50, 50, 200, 30), confidence=0.99),
        Match(text="low conf trash", bbox=(0, 0, 10, 10), confidence=0.30),
    ]


def test_find_substring_match(client, auth_headers, app):
    app.state.ocr_runner = _stub_runner
    r = client.get("/find?text=GARGAROS", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["matches"]) == 1
    m = body["matches"][0]
    assert m["text"] == "GARGAROS TEST 12345"
    assert m["bbox"] == [50, 50, 200, 30]
    assert m["center"] == [150, 65]


def test_find_filters_by_confidence(client, auth_headers, app):
    app.state.ocr_runner = _stub_runner
    r = client.get("/find?text=trash&min_confidence=0.5", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["matches"] == []


def test_find_no_query_400(client, auth_headers):
    r = client.get("/find?text=", headers=auth_headers)
    assert r.status_code == 400


def test_find_caches_per_frame(client, auth_headers, app):
    calls = {"n": 0}

    def counting_runner(image_bytes: bytes) -> list[Match]:
        calls["n"] += 1
        return [Match(text="cached", bbox=(0, 0, 10, 10), confidence=0.99)]

    app.state.ocr_runner = counting_runner
    r1 = client.get("/find?text=cached", headers=auth_headers)
    r2 = client.get("/find?text=cached", headers=auth_headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Same dummy frame each call -> OCR runs once, second call hits cache
    assert calls["n"] == 1
