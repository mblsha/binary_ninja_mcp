#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import subprocess
import sys
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


def _ui_open_contract_with_state(
    filepath: str,
    *,
    loaded_filename: str | None = None,
    warnings: list[str] | None = None,
) -> dict:
    payload = _ui_open_contract(filepath)
    payload["state"]["loaded_filename"] = loaded_filename if loaded_filename is not None else filepath
    payload["warnings"] = list(warnings or [])
    payload["result"]["warnings"] = list(warnings or [])
    payload["result"]["state"] = dict(payload["state"])
    return payload


class _MockStateCode:
    def __init__(self, value: int):
        self.value = int(value)

    def __int__(self):
        return self.value

    def __str__(self):
        return str(self.value)


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


def test_open_help_does_not_expose_no_ui_switch():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "open", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    text = (result.stdout or "") + (result.stderr or "")
    assert "--no-ui" not in text
    assert "UI-only open workflow" in text


def test_open_rejects_no_ui_switch():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "open", "--no-ui"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    text = (result.stdout or "") + (result.stderr or "")
    assert "Unknown switch --no-ui" in text


def test_wait_for_analysis_on_target_polls_views_until_idle():
    app = _new_app()
    resolved_idle = 2
    idle_token = str(resolved_idle)

    with (
        patch.object(app, "_resolve_idle_analysis_state_value", return_value=resolved_idle),
        patch.object(
            app,
            "_request",
            side_effect=[
                {
                    "views": [
                        {
                            "filename": "/tmp/target.bin",
                            "view_id": "22",
                            "analysis_state_code": 5,
                            "analysis_status": "5",
                        }
                    ],
                    "_api_version": 1,
                },
                {
                    "views": [
                        {
                            "filename": "/tmp/target.bin",
                            "view_id": "22",
                            "analysis_state_code": resolved_idle,
                            "analysis_status": idle_token,
                        }
                    ],
                    "_api_version": 1,
                },
            ],
        ) as req_mock,
    ):
        out = app._wait_for_analysis_on_target(
            filename="/tmp/target.bin",
            view_id="22",
            timeout=2.0,
        )

    assert out.get("success") is True
    assert out.get("analysis_status") == idle_token
    assert out.get("selected_view_filename") == "/tmp/target.bin"
    assert out.get("selected_view_id") == "22"
    assert req_mock.call_count == 2
    for call in req_mock.call_args_list:
        args, kwargs = call
        assert args[0] == "GET"
        assert args[1] == "views"
        params = kwargs.get("params", {})
        assert params.get("filename") == "/tmp/target.bin"
        assert params.get("view_id") == "22"


def test_wait_for_analysis_uses_runtime_idle_enum_value_not_literal_zero():
    app = _new_app()
    resolved_idle = 7

    with (
        patch.object(app, "_resolve_idle_analysis_state_value", return_value=resolved_idle),
        patch.object(
            app,
            "_request",
            side_effect=[
                {
                    "views": [
                        {
                            "filename": "/tmp/target.bin",
                            "view_id": "22",
                            "analysis_state_code": 6,
                            "analysis_status": "6",
                        }
                    ],
                    "_api_version": 1,
                },
                {
                    "views": [
                        {
                            "filename": "/tmp/target.bin",
                            "view_id": "22",
                            "analysis_state_code": resolved_idle,
                            "analysis_status": "7",
                        }
                    ],
                    "_api_version": 1,
                },
            ],
        ),
    ):
        out = app._wait_for_analysis_on_target(
            filename="/tmp/target.bin",
            view_id="22",
            timeout=2.0,
        )

    assert out.get("success") is True
    assert out.get("analysis_status") == "7"


def test_wait_for_analysis_prefers_analysis_state_code_over_status_text():
    app = _new_app()
    resolved_idle = 2

    with (
        patch.object(app, "_resolve_idle_analysis_state_value", return_value=resolved_idle),
        patch.object(
            app,
            "_request",
            side_effect=[
                {
                    "views": [
                        {
                            "filename": "/tmp/target.bin",
                            "view_id": "22",
                            "analysis_state_code": 5,
                            "analysis_state_name": "AnalyzeState",
                            "analysis_status": "still-running",
                        }
                    ],
                    "_api_version": 1,
                },
                {
                    "views": [
                        {
                            "filename": "/tmp/target.bin",
                            "view_id": "22",
                            "analysis_state_code": 2,
                            "analysis_state_name": "IdleState",
                            "analysis_status": "still-running",
                        }
                    ],
                    "_api_version": 1,
                },
            ],
        ),
    ):
        out = app._wait_for_analysis_on_target(
            filename="/tmp/target.bin",
            view_id="22",
            timeout=2.0,
        )

    assert out.get("success") is True
    assert out.get("analysis_state_code") == 2
    assert out.get("analysis_state_name") == "IdleState"
    # Kept for compatibility; completion is driven by analysis_state_code.
    assert out.get("analysis_status") == "still-running"


def test_wait_for_analysis_transition_5_6_2_succeeds_with_mock_state_types():
    app = _new_app()
    resolved_idle = 2

    with (
        patch.object(app, "_resolve_idle_analysis_state_value", return_value=resolved_idle),
        patch.object(
            app,
            "_request",
            side_effect=[
                {
                    "views": [
                        {
                            "filename": "/tmp/target.bin",
                            "view_id": "22",
                            "analysis_state_code": _MockStateCode(5),
                            "analysis_state_name": "AnalyzeState",
                            "analysis_status": "running",
                        }
                    ],
                    "_api_version": 1,
                },
                {
                    "views": [
                        {
                            "filename": "/tmp/target.bin",
                            "view_id": "22",
                            "analysis_state_code": _MockStateCode(6),
                            "analysis_state_name": "ExtendedAnalyzeState",
                            "analysis_status": "running",
                        }
                    ],
                    "_api_version": 1,
                },
                {
                    "views": [
                        {
                            "filename": "/tmp/target.bin",
                            "view_id": "22",
                            "analysis_state_code": _MockStateCode(2),
                            "analysis_state_name": "IdleState",
                            "analysis_status": "running",
                        }
                    ],
                    "_api_version": 1,
                },
            ],
        ) as req_mock,
    ):
        out = app._wait_for_analysis_on_target(
            filename="/tmp/target.bin",
            view_id="22",
            timeout=2.0,
        )

    assert out.get("success") is True
    assert int(out.get("analysis_state_code")) == 2
    assert out.get("analysis_state_name") == "IdleState"
    assert req_mock.call_count == 3


def test_wait_for_analysis_persistent_5_6_times_out_with_mock_state_types():
    app = _new_app()
    resolved_idle = 2
    call_count = {"value": 0}

    def _views_payload(*_args, **_kwargs):
        call_count["value"] += 1
        code = 5 if (call_count["value"] % 2) else 6
        name = "AnalyzeState" if code == 5 else "ExtendedAnalyzeState"
        return {
            "views": [
                {
                    "filename": "/tmp/target.bin",
                    "view_id": "22",
                    "analysis_state_code": _MockStateCode(code),
                    "analysis_state_name": name,
                    "analysis_status": "running",
                }
            ],
            "_api_version": 1,
        }

    with (
        patch.object(app, "_resolve_idle_analysis_state_value", return_value=resolved_idle),
        patch.object(app, "_request", side_effect=_views_payload),
    ):
        out = app._wait_for_analysis_on_target(
            filename="/tmp/target.bin",
            view_id="22",
            timeout=1.0,
        )

    assert out.get("success") is False
    assert int(out.get("analysis_state_code")) in {5, 6}
    assert out.get("analysis_state_name") in {"AnalyzeState", "ExtendedAnalyzeState"}
    err = out.get("error", {})
    assert err.get("type") == "TimeoutError"
    assert "analysis wait timed out" in err.get("message", "")


def test_resolve_idle_analysis_state_value_queries_console_execute():
    app = _new_app()

    with (
        patch.object(
            app,
            "_request",
            return_value={
                "success": True,
                "stdout": "2\n",
                "_api_version": 1,
            },
        ) as req_mock,
    ):
        resolved = app._resolve_idle_analysis_state_value(
            filename="/tmp/target.bin",
            view_id="22",
            timeout=4.0,
        )

    assert resolved == 2
    args, kwargs = req_mock.call_args
    assert args[:2] == ("POST", "console/execute")
    payload = kwargs.get("data", {})
    assert "AnalysisState.IdleState" in payload.get("command", "")
    assert payload.get("filename") == "/tmp/target.bin"
    assert payload.get("view_id") == "22"


def test_wait_for_analysis_returns_contract_error_when_state_code_missing():
    app = _new_app()

    with (
        patch.object(app, "_resolve_idle_analysis_state_value", return_value=2),
        patch.object(
            app,
            "_request",
            return_value={
                "views": [
                    {
                        "filename": "/tmp/target.bin",
                        "view_id": "22",
                        "analysis_state_name": "AnalyzeState",
                        "analysis_status": "running",
                    }
                ],
                "_api_version": 1,
            },
        ),
    ):
        out = app._wait_for_analysis_on_target(
            filename="/tmp/target.bin",
            view_id="22",
            timeout=1.0,
        )

    assert out.get("success") is False
    err = out.get("error", {})
    assert err.get("type") == "RuntimeContractError"
    assert "analysis_state_code" in err.get("message", "")


def test_open_main_confirms_target_and_reports_view_context():
    app = _new_app()
    open_cmd = binja_cli.Open("open")
    open_cmd.parent = app
    open_cmd.platform = None
    open_cmd.view_type = None
    open_cmd.no_click = False
    open_cmd.inspect_only = False
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
    args0, kwargs0 = req_mock.call_args_list[0]
    assert args0[:2] == ("POST", "ui/open")
    assert "prefer_ui_open" not in kwargs0.get("data", {})

    out = output_mock.call_args.args[0]
    open_result = out.get("open_result", {})
    state = open_result.get("state", {})
    assert state.get("confirmed_target_filename") == target
    assert state.get("confirmed_target_view_id") == "44"
    assert state.get("effective_target_filename") == target
    assert state.get("effective_target_view_id") == "44"
    assert out.get("effective_target_filename") == target
    assert out.get("effective_target_view_id") == "44"
    assert "confirmed_target_via_views" in open_result.get("actions", [])


def test_open_main_uses_confirmed_target_as_effective_when_loaded_filename_is_stale():
    app = _new_app()
    open_cmd = binja_cli.Open("open")
    open_cmd.parent = app
    open_cmd.platform = None
    open_cmd.view_type = None
    open_cmd.no_click = False
    open_cmd.inspect_only = False
    open_cmd.wait_open_target = 0.5
    open_cmd.wait_analysis = False
    open_cmd.analysis_timeout = 120.0

    target = "/tmp/target.bin"
    stale_loaded = "/tmp/other.bin"

    with (
        patch.object(app, "_ensure_server_for_open", return_value={"ok": True}),
        patch.object(
            app,
            "_request",
            side_effect=[
                _ui_open_contract_with_state(
                    target,
                    loaded_filename=stale_loaded,
                    warnings=[
                        f"loaded filename differs (expected {target}, got {stale_loaded})",
                    ],
                ),
                {
                    "views": [
                        {
                            "filename": target,
                            "view_id": "77",
                            "basename": "target.bin",
                            "is_current": True,
                        }
                    ],
                    "current_filename": target,
                    "current_view_id": "77",
                    "_api_version": 1,
                },
            ],
        ),
        patch.object(app, "_output") as output_mock,
    ):
        rc = open_cmd.main(target)

    assert rc is None
    out = output_mock.call_args.args[0]
    open_result = out.get("open_result", {})
    state = open_result.get("state", {})
    assert state.get("confirmed_target_filename") == target
    assert state.get("effective_target_filename") == target
    assert state.get("effective_target_view_id") == "77"
    assert state.get("observed_loaded_filename") == stale_loaded
    assert state.get("loaded_filename") == target
    warnings = open_result.get("warnings", [])
    assert not any("loaded filename differs" in str(item) for item in warnings)
    assert "effective_target_from_confirmed_view" in open_result.get("actions", [])
    assert out.get("effective_target_filename") == target
    assert out.get("effective_target_view_id") == "77"


def test_open_main_returns_structured_error_when_target_not_confirmed():
    app = _new_app()
    open_cmd = binja_cli.Open("open")
    open_cmd.parent = app
    open_cmd.platform = None
    open_cmd.view_type = None
    open_cmd.no_click = False
    open_cmd.inspect_only = False
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


def test_wait_for_analysis_on_target_times_out_with_structured_error():
    app = _new_app()

    with (
        patch.object(app, "_resolve_idle_analysis_state_value", return_value=2),
        patch.object(
            app,
            "_request",
            return_value={
                "views": [
                    {
                        "filename": "/tmp/target.bin",
                        "view_id": "22",
                        "analysis_state_code": 5,
                        "analysis_status": "5",
                    }
                ],
                "_api_version": 1,
            },
        ),
    ):
        out = app._wait_for_analysis_on_target(
            filename="/tmp/target.bin",
            view_id="22",
            timeout=1.0,
        )

    assert out.get("success") is False
    assert out.get("analysis_status") == "5"
    err = out.get("error", {})
    assert err.get("type") == "TimeoutError"
    assert "analysis wait timed out" in err.get("message", "")
