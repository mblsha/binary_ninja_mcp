#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parent / "scripts" / "binja-cli.py"
SPEC = importlib.util.spec_from_file_location("binja_cli_script_open_item6", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
binja_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(binja_cli)


def _new_app():
    app = binja_cli.BinaryNinjaCLI("binja-mcp")
    app.server_url = "http://localhost:9009"
    app.request_timeout = 5.0
    app.verbose = False
    app.json_output = True
    return app


def _ui_open_contract(filepath: str) -> dict:
    return {
        "ok": True,
        "schema_version": 1,
        "endpoint": "/ui/open",
        "actions": ["scheduled_open_workflow_on_main_thread"],
        "warnings": [],
        "errors": [],
        "state": {"loaded_filename": filepath},
        "result": {"ok": True, "input": {"filepath": filepath}},
        "_api_version": 2,
    }


def test_wait_for_open_target_in_views_matches_requested_file():
    app = _new_app()
    target = "/tmp/target.bin"

    with patch.object(
        app,
        "_request",
        side_effect=[
            {
                "views": [
                    {
                        "filename": "/tmp/other.bin",
                        "view_id": "11",
                        "is_current": True,
                    }
                ],
                "current_filename": "/tmp/other.bin",
                "current_view_id": "11",
                "_api_version": 1,
            },
            {
                "views": [
                    {
                        "filename": target,
                        "view_id": "22",
                        "is_current": True,
                    }
                ],
                "current_filename": target,
                "current_view_id": "22",
                "_api_version": 1,
            },
        ],
    ):
        out = app._wait_for_open_target_in_views(target, timeout=0.3, poll_interval=0.01)

    assert out.get("ok") is True
    assert out.get("matched_view", {}).get("filename") == target
    assert out.get("matched_view", {}).get("view_id") == "22"


def test_wait_for_analysis_on_target_uses_console_execute_with_target_fields():
    app = _new_app()

    with patch.object(app, "_request", return_value={"success": True}) as req_mock:
        out = app._wait_for_analysis_on_target(
            filename="/tmp/target.bin",
            view_id="22",
            timeout=33.0,
        )

    assert out.get("success") is True
    req_mock.assert_called_once()
    args, kwargs = req_mock.call_args
    assert args[0] == "POST"
    assert args[1] == "console/execute"
    payload = kwargs.get("data", {})
    assert payload.get("filename") == "/tmp/target.bin"
    assert payload.get("view_id") == "22"
    assert payload.get("timeout") == 33.0
    assert "update_analysis_and_wait" in payload.get("command", "")


def test_open_main_no_ui_confirms_target_and_reports_view_context():
    app = _new_app()
    open_cmd = binja_cli.Open("open")
    open_cmd.parent = app
    open_cmd.platform = None
    open_cmd.view_type = None
    open_cmd.no_click = False
    open_cmd.inspect_only = False
    open_cmd.no_ui = True
    open_cmd.wait_open_target = 0.5
    open_cmd.wait_analysis = False
    open_cmd.analysis_timeout = 120.0

    target = "/tmp/target.bin"

    with (
        patch.object(app, "_ensure_server_for_open", return_value={"ok": True}),
        patch.object(
            app,
            "_request",
            side_effect=[
                _ui_open_contract(target),
                {
                    "views": [
                        {
                            "filename": target,
                            "view_id": "44",
                            "basename": "target.bin",
                            "is_current": True,
                        }
                    ],
                    "current_filename": target,
                    "current_view_id": "44",
                    "_api_version": 1,
                },
            ],
        ) as req_mock,
        patch.object(app, "_output") as output_mock,
    ):
        rc = open_cmd.main(target)

    assert rc is None
    first_payload = req_mock.call_args_list[0].kwargs.get("data", {})
    assert first_payload.get("prefer_ui_open") is False

    out = output_mock.call_args.args[0]
    open_result = out.get("open_result", {})
    state = open_result.get("state", {})
    assert state.get("confirmed_target_filename") == target
    assert state.get("confirmed_target_view_id") == "44"
    assert "confirmed_target_via_views" in open_result.get("actions", [])


def test_open_main_returns_structured_error_when_target_not_confirmed():
    app = _new_app()
    open_cmd = binja_cli.Open("open")
    open_cmd.parent = app
    open_cmd.platform = None
    open_cmd.view_type = None
    open_cmd.no_click = False
    open_cmd.inspect_only = False
    open_cmd.no_ui = False
    open_cmd.wait_open_target = 0.1
    open_cmd.wait_analysis = False
    open_cmd.analysis_timeout = 120.0

    target = "/tmp/target.bin"

    with (
        patch.object(app, "_ensure_server_for_open", return_value={"ok": True}),
        patch.object(app, "_request", return_value=_ui_open_contract(target)),
        patch.object(
            app,
            "_wait_for_open_target_in_views",
            return_value={
                "ok": False,
                "observed_current_filename": "/tmp/other.bin",
                "observed_current_view_id": "77",
                "views": [{"filename": "/tmp/other.bin", "view_id": "77"}],
            },
        ),
        patch.object(app, "_output") as output_mock,
    ):
        rc = open_cmd.main(target)

    assert rc == 1
    out = output_mock.call_args.args[0]
    assert out.get("error") == "open target confirmation failed"
    assert out.get("requested_filename") == target
    assert out.get("observed_current_filename") == "/tmp/other.bin"
    assert isinstance(out.get("views"), list)
