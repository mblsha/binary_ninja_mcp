#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


SCRIPT_PATH = Path(__file__).resolve().parent / "scripts" / "binja-cli.py"
SPEC = importlib.util.spec_from_file_location("binja_cli_script_unit", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
binja_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(binja_cli)


class _FakeResponse:
    def __init__(self, payload: dict, *, status_code: int = 200, api_version: int = 1):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"X-Binja-MCP-Api-Version": str(api_version)}
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise binja_cli.requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._payload


def _new_app():
    app = binja_cli.BinaryNinjaCLI("binja-mcp")
    app.server_url = "http://localhost:9009"
    app.request_timeout = 5.0
    app.verbose = False
    app.json_output = True
    return app


def test_filename_match_allows_basename_for_non_path_requests():
    app = _new_app()
    assert app._filename_matches_requested("/tmp/a/st1-slave.combined", "st1-slave.combined")
    assert not app._filename_matches_requested("/tmp/a/st2-maincpu.combined", "st1-slave.combined")


def test_strict_target_blocks_mismatched_view_before_command():
    app = _new_app()
    app.target_filename = "/tmp/target.bin"
    app.strict_target = True

    with (
        patch.object(
            binja_cli.requests,
            "get",
            return_value=_FakeResponse(
                {"loaded": True, "filename": "/tmp/other.bin", "_api_version": 1}
            ),
        ),
        patch.object(binja_cli.requests, "post") as post_mock,
        pytest.raises(SystemExit),
    ):
        app._request("POST", "console/execute", data={"command": "1 + 1"})

    post_mock.assert_not_called()


def test_strict_target_passes_and_sets_selected_view_context_fields():
    app = _new_app()
    app.target_filename = "/tmp/target.bin"
    app.strict_target = True

    with patch.object(
        binja_cli.requests,
        "get",
        return_value=_FakeResponse({"loaded": True, "filename": "/tmp/target.bin", "_api_version": 1}),
    ):
        with patch.object(
            binja_cli.requests,
            "post",
            return_value=_FakeResponse({"success": True, "_api_version": 1}),
        ):
            out = app._request("POST", "console/execute", data={"command": "1 + 1"})

    assert out.get("success") is True
    assert out.get("selected_view_filename") == "/tmp/target.bin"
    assert "selected_view_id" in out


def test_strict_target_open_uses_response_state_without_precheck():
    app = _new_app()
    app.target_filename = "/tmp/target.bin"
    app.strict_target = True

    with patch.object(binja_cli.requests, "get") as get_mock:
        with patch.object(
            binja_cli.requests,
            "post",
            return_value=_FakeResponse(
                {
                    "ok": True,
                    "state": {"loaded_filename": "/tmp/target.bin"},
                    "_api_version": 2,
                },
                api_version=2,
            ),
        ):
            out = app._request("POST", "ui/open", data={"filepath": "/tmp/target.bin"})

    get_mock.assert_not_called()
    assert out.get("selected_view_filename") == "/tmp/target.bin"


def test_strict_target_open_falls_back_to_status_when_response_has_no_filename():
    app = _new_app()
    app.target_filename = "/tmp/target.bin"
    app.strict_target = True

    with patch.object(
        binja_cli.requests,
        "post",
        return_value=_FakeResponse(
            {
                "ok": True,
                "state": {"loaded_filename": None},
                "_api_version": 2,
            },
            api_version=2,
        ),
    ):
        with patch.object(
            binja_cli.requests,
            "get",
            return_value=_FakeResponse(
                {"loaded": True, "filename": "/tmp/target.bin", "_api_version": 1},
                api_version=1,
            ),
        ) as get_mock:
            out = app._request("POST", "ui/open", data={"filepath": "/tmp/target.bin"})

    assert get_mock.call_count == 1
    assert out.get("selected_view_filename") == "/tmp/target.bin"
