from __future__ import annotations


def test_health_no_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_protected_route_no_token(client):
    r = client.post("/click", json={"x": 1, "y": 2}, headers={"Host": "127.0.0.1:7331"})
    assert r.status_code == 401


def test_protected_route_wrong_token(client):
    r = client.post(
        "/click",
        json={"x": 1, "y": 2},
        headers={"Authorization": "Bearer wrong-token", "Host": "127.0.0.1:7331"},
    )
    assert r.status_code == 401


def test_protected_route_bad_host(client, token):
    r = client.post(
        "/click",
        json={"x": 1, "y": 2},
        headers={"Authorization": f"Bearer {token}", "Host": "evil.com"},
    )
    assert r.status_code == 403


def test_protected_route_ok(client, auth_headers):
    r = client.post("/click", json={"x": 1, "y": 2}, headers=auth_headers)
    assert r.status_code == 201
    assert r.json() == {"ok": True}


def test_localhost_host_allowed(client, token):
    r = client.post(
        "/click",
        json={"x": 1, "y": 2},
        headers={"Authorization": f"Bearer {token}", "Host": "localhost:7331"},
    )
    assert r.status_code == 201
