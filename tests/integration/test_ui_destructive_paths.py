from __future__ import annotations

import os

import pytest


pytestmark = [pytest.mark.binja, pytest.mark.binja_destructive]


def test_quit_workflow_handles_real_close_path(
    client, analysis_context, destructive_ui_enabled, binja_process
):
    if not destructive_ui_enabled:
        pytest.skip("Set BINJA_UI_DESTRUCTIVE=1 to run destructive UI tests")
    if os.environ.get("BINJA_SPAWN") != "1":
        pytest.skip("Destructive UI tests require BINJA_SPAWN=1")

    open_response, open_body = client.request(
        "POST",
        "/ui/open",
        json={
            "filepath": analysis_context["fixture_binary_path"],
            "view_type": "Raw",
            "click_open": True,
            "inspect_only": False,
        },
        timeout=60.0,
    )
    assert open_response.status_code == 200, open_body

    quit_response, quit_body = client.request(
        "POST",
        "/ui/quit",
        json={
            "decision": "dont-save",
            "mark_dirty": True,
            "inspect_only": False,
            "wait_ms": 5000,
            "quit_app": False,
        },
        timeout=60.0,
    )
    assert quit_response.status_code == 200, quit_body
    assert quit_body.get("schema_version") == 1
    assert quit_body.get("endpoint") == "/ui/quit"
    assert isinstance(quit_body.get("actions"), list)
    assert "set_quit_on_last_window_closed:false" in quit_body.get("actions", [])

    state = quit_body.get("state", {})
    assert isinstance(state.get("dialogs_before_action"), list)
    assert isinstance(state.get("dialogs_after_action"), list)
