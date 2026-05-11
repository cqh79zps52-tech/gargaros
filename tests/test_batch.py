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
    results = r.json()["results"]
    assert [x["ok"] for x in results] == [True, True, True]
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
    results = r.json()["results"]
    assert len(results) == 2  # stopped at unknown op
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False
    assert "unknown" in results[1]["error"].lower()


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
    results = r.json()["results"]
    assert len(results) == 3
    assert [x["ok"] for x in results] == [True, False, True]
