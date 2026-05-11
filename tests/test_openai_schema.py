from __future__ import annotations

from gargaros.openai_schema import tools


def test_tools_returns_list_of_function_schemas():
    ts = tools()
    assert isinstance(ts, list)
    assert len(ts) > 5
    for t in ts:
        assert t["type"] == "function"
        fn = t["function"]
        assert "name" in fn
        assert "description" in fn
        assert "parameters" in fn
        assert fn["parameters"]["type"] == "object"
        assert "properties" in fn["parameters"]
        assert "required" in fn["parameters"]


def test_tool_names_unique():
    names = [t["function"]["name"] for t in tools()]
    assert len(names) == len(set(names))


def test_required_keys_exist_in_properties():
    for t in tools():
        fn = t["function"]
        props = fn["parameters"]["properties"]
        for k in fn["parameters"]["required"]:
            assert k in props, f"{fn['name']}: required key {k!r} not in properties"


def test_expected_tools_present():
    names = {t["function"]["name"] for t in tools()}
    expected = {
        "screenshot", "click", "move", "type", "key", "scroll", "find",
        "ui_snapshot", "ui_click_label",
        "browser_snapshot", "browser_click", "browser_type", "browser_navigate", "browser_tabs",
    }
    assert expected.issubset(names)
