#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parent / "scripts" / "binja-cli.py"
SPEC = importlib.util.spec_from_file_location("binja_cli_script_auto_errors", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
binja_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(binja_cli)


def _new_app():
    app = binja_cli.BinaryNinjaCLI("binja-mcp")
    app.server_url = "http://localhost:9009"
    app.request_timeout = 1.0
    app.verbose = False
    app.json_output = True
    app.no_auto_errors = False
    app.fail_on_new_errors = False
    app.error_probe_count = 50
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


def test_new_error_entries_detects_incremental_duplicates():
    before = [
        {"type": "error", "text": "boom"},
        {"type": "error", "text": "boom"},
    ]
    after = [
        {"type": "error", "text": "boom"},
        {"type": "error", "text": "boom"},
        {"type": "error", "text": "boom"},
        {"type": "error", "text": "different"},
    ]
    new_entries = binja_cli.BinaryNinjaCLI._new_error_entries(
        before,
        after,
        source="console",
    )
    assert len(new_entries) == 2
    assert new_entries[0].get("text") == "boom"
    assert new_entries[1].get("text") == "different"


def test_apply_post_command_error_report_attaches_new_errors_to_payload():
    app = _new_app()
    before_snapshot = {
        "count": 50,
        "console_errors": [{"type": "error", "text": "old console error"}],
        "log_errors": [{"level": "ErrorLog", "message": "old log error"}],
        "probe_warnings": [],
    }
    after_snapshot = {
        "count": 50,
        "console_errors": [
            {"type": "error", "text": "old console error"},
            {"type": "error", "text": "new console error"},
        ],
        "log_errors": [
            {"level": "ErrorLog", "message": "old log error"},
            {"level": "ErrorLog", "message": "new log error"},
        ],
        "probe_warnings": [],
    }
    payload = {"success": True}

    with patch.object(app, "_capture_error_snapshot", return_value=after_snapshot):
        should_fail = app._apply_post_command_error_report(
            "decompile",
            before_snapshot,
            output_payload=payload,
        )

    assert should_fail is False
    report = payload.get("new_errors")
    assert isinstance(report, dict)
    assert report.get("new_console_error_count") == 1
    assert report.get("new_log_error_count") == 1
    assert report.get("new_error_count") == 2


def test_apply_post_command_error_report_respects_fail_on_new_errors():
    app = _new_app()
    app.fail_on_new_errors = True
    before_snapshot = {
        "count": 50,
        "console_errors": [],
        "log_errors": [],
        "probe_warnings": [],
    }
    after_snapshot = {
        "count": 50,
        "console_errors": [{"type": "error", "text": "new console error"}],
        "log_errors": [],
        "probe_warnings": [],
    }

    with patch.object(app, "_capture_error_snapshot", return_value=after_snapshot):
        should_fail = app._apply_post_command_error_report("assembly", before_snapshot)

    assert should_fail is True


def test_open_json_includes_new_errors_and_fails_when_requested():
    app = _new_app()
    app.fail_on_new_errors = True
    target = "/tmp/target.bin"
    open_cmd = binja_cli.Open("open")
    open_cmd.parent = app
    open_cmd.platform = None
    open_cmd.view_type = None
    open_cmd.no_click = False
    open_cmd.inspect_only = False
    open_cmd.wait_open_target = 0.0
    open_cmd.wait_analysis = False
    open_cmd.analysis_timeout = 120.0

    before_snapshot = {
        "count": 50,
        "console_errors": [],
        "log_errors": [],
        "probe_warnings": [],
    }
    after_snapshot = {
        "count": 50,
        "console_errors": [{"type": "error", "text": "new console error"}],
        "log_errors": [],
        "probe_warnings": [],
    }

    with (
        patch.object(app, "_ensure_server_for_open", return_value={"ok": True}),
        patch.object(app, "_request", return_value=_ui_open_contract(target)),
        patch.object(app, "_capture_error_snapshot", side_effect=[before_snapshot, after_snapshot]),
        patch.object(app, "_output") as output_mock,
    ):
        rc = open_cmd.main(target)

    assert rc == 1
    out = output_mock.call_args.args[0]
    report = out.get("new_errors")
    assert isinstance(report, dict)
    assert report.get("command") == "open"
    assert report.get("new_error_count") == 1


def test_python_execute_json_fails_on_new_errors_when_requested():
    app = _new_app()
    app.fail_on_new_errors = True
    py_cmd = binja_cli.Python("python")
    py_cmd.parent = app
    py_cmd.file = None
    py_cmd.interactive = False
    py_cmd.stdin = False
    py_cmd.complete = None
    py_cmd.exec_timeout = 30.0

    before_snapshot = {
        "count": 50,
        "console_errors": [],
        "log_errors": [],
        "probe_warnings": [],
    }
    after_snapshot = {
        "count": 50,
        "console_errors": [{"type": "error", "text": "new console error"}],
        "log_errors": [],
        "probe_warnings": [],
    }
    execute_result = {
        "success": True,
        "stdout": "",
        "stderr": "",
        "return_value": None,
        "variables": {},
    }

    with (
        patch.object(app, "_request", return_value=execute_result),
        patch.object(app, "_capture_error_snapshot", side_effect=[before_snapshot, after_snapshot]),
        patch.object(app, "_output") as output_mock,
    ):
        rc = py_cmd.main("print('hello')")

    assert rc == 1
    out = output_mock.call_args.args[0]
    report = out.get("new_errors")
    assert isinstance(report, dict)
    assert report.get("command") == "python.execute"
    assert report.get("new_error_count") == 1
