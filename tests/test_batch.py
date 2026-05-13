from __future__ import annotations


def test_batch_runs_actions_in_order(client, auth_headers, fake_backend):
    body = {
        "actions": [
            {"op": "move", "args": {"x": 1, "y": 1}},
            {"op": "click", "args": {"x": 2, "y": 2}},
            {"op": "type", "args": {"text": "hi"}},
        ]
    }
    r = client.post("/batch", json=body, headers=auth_headers)
    assert r.status_code == 201
    body_out = r.json()
    assert body_out["succeeded"] == 3
    assert body_out["failed"] == 0
    assert "total_elapsed_ms" in body_out
    results = body_out["results"]
    assert [x["status"] for x in results] == ["ok", "ok", "ok"]
    assert all("elapsed_ms" in x for x in results)
    tool_names = [name for (name, _) in fake_backend.calls]
    assert tool_names == ["Move", "Click", "Type"]


def test_batch_caps_at_max(client, auth_headers):
    body = {"actions": [{"op": "move", "args": {"x": 1, "y": 1}}] * 101}
    r = client.post("/batch", json=body, headers=auth_headers)
    assert r.status_code == 400


def test_batch_unknown_op_stops_by_default(client, auth_headers, fake_backend):
    body = {
        "actions": [
            {"op": "click", "args": {"x": 1, "y": 1}},
            {"op": "fly", "args": {}},
            {"op": "click", "args": {"x": 2, "y": 2}},
        ]
    }
    r = client.post("/batch", json=body, headers=auth_headers)
    out = r.json()
    results = out["results"]
    assert len(results) == 2  # stopped at unknown op
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "error"
    assert "unknown" in results[1]["error"].lower()
    assert out["succeeded"] == 1
    assert out["failed"] == 1


def test_batch_continue_on_error(client, auth_headers):
    body = {
        "continue_on_error": True,
        "actions": [
            {"op": "click", "args": {"x": 1, "y": 1}},
            {"op": "fly", "args": {}},
            {"op": "click", "args": {"x": 2, "y": 2}},
        ],
    }
    r = client.post("/batch", json=body, headers=auth_headers)
    out = r.json()
    assert out["succeeded"] == 2
    assert out["failed"] == 1
    assert [x["status"] for x in out["results"]] == ["ok", "error", "ok"]
