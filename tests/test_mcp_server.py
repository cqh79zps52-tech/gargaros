from __future__ import annotations


def test_mcp_server_imports_and_registers_tools():
    """Smoke: import the module and confirm the expected tool names are registered."""
    from gargaros import mcp_server

    expected = {
        "health",
        "screenshot",
        "click",
        "move",
        "type_text",
        "key",
        "scroll",
        "find",
        "ui_snapshot",
        "ui_click_label",
        "ui_type_label",
        "ui_launch_app",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_navigate",
        "browser_tabs",
        # Gaming primitives (F3-F5)
        "key_hold",
        "key_down",
        "key_up",
        "key_sequence",
        "key_release_all",
        "mouse_move_smooth",
        "mouse_button",
        "mouse_release_all",
        "input_release_all",
    }
    # FastMCP exposes its registry via a private attribute that has shifted across versions —
    # try a few accessor patterns and assert at least one yields the expected set.
    found: set[str] = set()
    mgr = getattr(mcp_server.mcp, "_tool_manager", None)
    if mgr is not None and hasattr(mgr, "_tools"):
        found = set(mgr._tools.keys())
    if not found:
        listed = getattr(mcp_server.mcp, "list_tools", None)
        if callable(listed):
            try:
                found = {t.name for t in listed()}
            except TypeError:
                pass  # async or different signature
    assert found, "could not discover tools registered on the FastMCP instance"
    missing = expected - found
    assert not missing, f"missing expected tools: {missing}"


def test_main_is_callable():
    from gargaros import mcp_server

    assert callable(mcp_server.main)
