from __future__ import annotations

import threading
import time


def _post_in_background(client, headers, path, json_body, results: dict):
    def go():
        results["resp"] = client.post(path, json=json_body, headers=headers)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


def test_pull_returns_204_when_no_jobs(client, auth_headers):
    r = client.get("/browser/_pull?wait=0.2", headers=auth_headers)
    assert r.status_code == 204


def test_dispatch_then_pull_then_deliver(client, auth_headers):
    """Simulate the full extension loop: HTTP dispatcher → _pull → _result."""
    results: dict = {}

    # Kick off /browser/click in a thread (it blocks awaiting the extension's response)
    t = _post_in_background(client, auth_headers, "/browser/click",
                            {"selector": "#submit"}, results)

    # Give the dispatch a moment to enqueue the job
    deadline = time.time() + 3
    job = None
    while time.time() < deadline:
        r = client.get("/browser/_pull?wait=0.5", headers=auth_headers)
        if r.status_code == 200:
            job = r.json()
            break
    assert job is not None, "extension never received the job"
    assert job["op"] == "click"
    assert job["args"]["selector"] == "#submit"

    # Post result back
    r2 = client.post("/browser/_result",
                     json={"job_id": job["job_id"], "result": {"ok": True}},
                     headers=auth_headers)
    assert r2.status_code == 201
    assert r2.json() == {"ok": True}

    t.join(timeout=3)
    assert "resp" in results
    assert results["resp"].status_code == 201
    assert results["resp"].json() == {"ok": True}


def test_dispatch_propagates_error(client, auth_headers):
    results: dict = {}
    t = _post_in_background(client, auth_headers, "/browser/click",
                            {"selector": "#nope"}, results)

    job = None
    deadline = time.time() + 3
    while time.time() < deadline:
        r = client.get("/browser/_pull?wait=0.5", headers=auth_headers)
        if r.status_code == 200:
            job = r.json()
            break
    assert job is not None

    client.post("/browser/_result",
                json={"job_id": job["job_id"], "error": "selector not found"},
                headers=auth_headers)
    t.join(timeout=3)
    # Server-side error -> Litestar returns 500
    assert results["resp"].status_code == 500


def test_pull_wait_capped(client, auth_headers):
    r = client.get("/browser/_pull?wait=120", headers=auth_headers)
    assert r.status_code == 400
