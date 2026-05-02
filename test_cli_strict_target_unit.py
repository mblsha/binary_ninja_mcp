#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
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
    app.no_auto_errors = True
    app.fail_on_new_errors = False
    app.error_probe_count = 50
    return app


def test_filename_match_allows_basename_for_non_path_requests():
    app = _new_app()
    assert app._filename_matches_requested("/tmp/a/secondary.bin", "secondary.bin")
    assert not app._filename_matches_requested("/tmp/a/primary.bin", "secondary.bin")


def test_strict_target_blocks_mismatched_view_before_command():
    app = _new_app()
    app.target_filename = "/tmp/target.bin"
    app.strict_target = True

    with (
        patch.object(
            binja_cli.requests,
            "get",
            return_value=_FakeResponse(
                {
                    "error_code": "TARGET_NOT_FOUND",
                    "error": "Requested filename is not loaded",
                    "filename": "/tmp/target.bin",
                    "_api_version": 1,
                },
                status_code=404,
            ),
        ),
        patch.object(binja_cli.requests, "post") as post_mock,
        pytest.raises(SystemExit),
    ):
        app._request("POST", "console/execute", data={"command": "1 + 1"})

    post_mock.assert_not_called()


def test_target_defaults_to_strict_and_blocks_mismatch():
    app = _new_app()
    app.target_filename = "/tmp/target.bin"

    with (
        patch.object(
            binja_cli.requests,
            "get",
            return_value=_FakeResponse(
                {
                    "error_code": "TARGET_NOT_FOUND",
                    "error": "Requested filename is not loaded",
                    "filename": "/tmp/target.bin",
                    "_api_version": 1,
                },
                status_code=404,
            ),
        ),
        patch.object(binja_cli.requests, "post") as post_mock,
        pytest.raises(SystemExit),
    ):
        app._request("POST", "console/execute", data={"command": "1 + 1"})

    post_mock.assert_not_called()


def test_filename_strict_precheck_uses_target_resolve_endpoint():
    app = _new_app()
    app.target_filename = "/tmp/target.bin"
    app.strict_target = True

    with patch.object(
        binja_cli.requests,
        "get",
        return_value=_FakeResponse(
            {
                "resolved": True,
                "target": {
                    "view_id": "view-1234",
                    "filename": "/tmp/target.bin",
                    "target_hint": "--view-id view-1234",
                },
                "_api_version": 1,
            }
        ),
    ) as get_mock:
        with patch.object(
            binja_cli.requests,
            "post",
            return_value=_FakeResponse({"success": True, "_api_version": 1}),
        ):
            out = app._request("POST", "console/execute", data={"command": "1 + 1"})

    assert get_mock.call_args.args[0] == "http://localhost:9009/target/resolve"
    assert out.get("selected_view_filename") == "/tmp/target.bin"


def test_filename_strict_precheck_selects_matching_view_from_multiple_open_views():
    app = _new_app()
    app.target_filename = "/tmp/target.bin"
    app.strict_target = True

    with patch.object(
        binja_cli.requests,
        "get",
        return_value=_FakeResponse(
            {
                "resolved": True,
                "target": {
                    "view_id": "view-1234",
                    "filename": "/tmp/target.bin",
                    "target_hint": "--view-id view-1234",
                },
                "open_views": [
                    {"view_id": "view-2222", "filename": "/tmp/other.bin", "is_current": True},
                    {"view_id": "view-1234", "filename": "/tmp/target.bin", "is_current": False},
                ],
                "_api_version": 1,
            }
        ),
    ):
        with patch.object(
            binja_cli.requests,
            "post",
            return_value=_FakeResponse({"success": True, "_api_version": 1}),
        ):
            out = app._request("POST", "console/execute", data={"command": "1 + 1"})

    assert out.get("selected_view_filename") == "/tmp/target.bin"
    assert out.get("selected_view_id") == "view-1234"


def test_strict_target_passes_and_sets_selected_view_context_fields():
    app = _new_app()
    app.target_filename = "/tmp/target.bin"
    app.strict_target = True

    with patch.object(
        binja_cli.requests,
        "get",
        return_value=_FakeResponse(
            {
                "resolved": True,
                "target": {"view_id": "view-1234", "filename": "/tmp/target.bin"},
                "_api_version": 1,
            }
        ),
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
    app.server_url = "http://testserver:9009"
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
    app.server_url = "http://testserver:9009"
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
                {
                    "resolved": True,
                    "target": {"view_id": "view-1234", "filename": "/tmp/target.bin"},
                    "_api_version": 1,
                },
                api_version=1,
            ),
        ) as get_mock:
            out = app._request("POST", "ui/open", data={"filepath": "/tmp/target.bin"})

    assert get_mock.call_count == 1
    assert out.get("selected_view_filename") == "/tmp/target.bin"


def test_console_execute_injects_view_id_target():
    app = _new_app()
    app.server_url = "http://testserver:9009"
    app.target_view_id = "0x1234"
    app.allow_target_fallback = True

    with patch.object(
        binja_cli.requests,
        "post",
        return_value=_FakeResponse(
            {"success": True, "selected_view_id": "0x1234", "_api_version": 1}
        ),
    ) as post_mock:
        out = app._request("POST", "console/execute", data={"command": "id(bv)"})

    sent_json = post_mock.call_args.kwargs.get("json", {})
    assert sent_json.get("view_id") == "0x1234"
    assert out.get("selected_view_id") == "0x1234"


def test_global_view_id_routes_to_discovered_instance_and_sends_local_id():
    app = _new_app()
    app.target_view_id = "inst-b:view-22"
    app.allow_target_fallback = True

    def fake_get(url, **kwargs):
        if url.endswith("/meta/instance"):
            if ":9001/" in url:
                return _FakeResponse(
                    {
                        "service": "binary_ninja_mcp",
                        "instance_id": "inst-b",
                        "base_url": "http://localhost:9001",
                        "_api_version": 1,
                    }
                )
            return _FakeResponse({}, status_code=404)
        return _FakeResponse({}, status_code=404)

    with (
        patch.object(binja_cli.requests, "get", side_effect=fake_get),
        patch.object(
            binja_cli.requests,
            "post",
            return_value=_FakeResponse(
                {"success": True, "selected_view_id": "view-22", "_api_version": 1}
            ),
        ) as post_mock,
    ):
        out = app._request("POST", "console/execute", data={"command": "id(bv)"})

    assert app.server_url == "http://localhost:9001"
    sent_json = post_mock.call_args.kwargs.get("json", {})
    assert sent_json.get("view_id") == "view-22"
    assert out.get("selected_view_id") == "view-22"


def test_local_view_id_fails_in_discovery_mode_and_lists_global_targets(capsys):
    app = _new_app()
    app.target_view_id = "view-33"
    app.allow_target_fallback = True

    def fake_get(url, **kwargs):
        if url.endswith("/meta/instance"):
            if ":9002/" in url:
                return _FakeResponse(
                    {
                        "service": "binary_ninja_mcp",
                        "instance_id": "inst-c",
                        "base_url": "http://localhost:9002",
                        "_api_version": 1,
                    }
                )
            return _FakeResponse({}, status_code=404)
        if url.endswith("/views"):
            return _FakeResponse(
                {
                    "views": [{"view_id": "view-33", "filename": "/tmp/c.bin"}],
                    "count": 1,
                    "_api_version": 1,
                }
            )
        return _FakeResponse({}, status_code=404)

    with patch.object(binja_cli.requests, "get", side_effect=fake_get), pytest.raises(SystemExit):
        app._request("POST", "console/execute", data={"command": "id(bv)"})

    captured = capsys.readouterr()
    assert "local --view-id 'view-33' is not valid in discovery mode" in captured.err
    assert "inst-c:view-33  /tmp/c.bin" in captured.err


def test_discovered_views_add_global_target_hints():
    app = _new_app()

    def fake_get(url, **kwargs):
        if url.endswith("/meta/instance"):
            if ":9000/" in url:
                return _FakeResponse(
                    {
                        "service": "binary_ninja_mcp",
                        "instance_id": "inst-a",
                        "base_url": "http://localhost:9000",
                        "_api_version": 1,
                    }
                )
            return _FakeResponse({}, status_code=404)
        if url.endswith("/views"):
            return _FakeResponse(
                {
                    "views": [{"view_id": "view-11", "filename": "/tmp/a.bin"}],
                    "count": 1,
                    "_api_version": 1,
                }
            )
        return _FakeResponse({}, status_code=404)

    with patch.object(binja_cli.requests, "get", side_effect=fake_get):
        views = app._get_discovered_views()

    assert len(views) == 1
    assert views[0]["global_view_id"] == "inst-a:view-11"
    assert views[0]["target_hint"] == "--view-id inst-a:view-11"


def test_discovery_includes_legacy_9009_alongside_new_instances():
    app = _new_app()

    def fake_get(url, **kwargs):
        if url.endswith("/meta/instance"):
            if ":9000/" in url:
                return _FakeResponse(
                    {
                        "service": "binary_ninja_mcp",
                        "instance_id": "inst-a",
                        "base_url": "http://localhost:9000",
                        "_api_version": 1,
                    }
                )
            return _FakeResponse({}, status_code=404)
        if url.endswith("/status") and url.startswith("http://localhost:9009/"):
            return _FakeResponse({"loaded": True, "_api_version": 1})
        return _FakeResponse({}, status_code=404)

    with patch.object(binja_cli.requests, "get", side_effect=fake_get):
        servers = app._discover_servers()

    assert [server["instance_id"] for server in servers] == ["inst-a", "legacy-9009"]
    assert servers[1]["legacy"] is True


def test_discovered_views_include_legacy_global_target_hint():
    app = _new_app()

    def fake_get(url, **kwargs):
        if url.endswith("/meta/instance"):
            if ":9000/" in url:
                return _FakeResponse(
                    {
                        "service": "binary_ninja_mcp",
                        "instance_id": "inst-a",
                        "base_url": "http://localhost:9000",
                        "_api_version": 1,
                    }
                )
            return _FakeResponse({}, status_code=404)
        if url.endswith("/status") and url.startswith("http://localhost:9009/"):
            return _FakeResponse({"loaded": True, "_api_version": 1})
        if url == "http://localhost:9000/views":
            return _FakeResponse(
                {
                    "views": [{"view_id": "view-new", "filename": "/tmp/new.bin"}],
                    "count": 1,
                    "_api_version": 1,
                }
            )
        if url == "http://localhost:9009/views":
            return _FakeResponse(
                {
                    "views": [{"view_id": "view-old", "filename": "/tmp/old.bin"}],
                    "count": 1,
                    "_api_version": 1,
                }
            )
        return _FakeResponse({}, status_code=404)

    with patch.object(binja_cli.requests, "get", side_effect=fake_get):
        views = app._get_discovered_views()

    assert [view["global_view_id"] for view in views] == [
        "inst-a:view-new",
        "legacy-9009:view-old",
    ]


def test_binary_view_scoped_command_requires_view_id_in_discovery_mode(capsys):
    app = _new_app()
    app._cached_discovered_servers = [
        {
            "instance_id": "inst-a",
            "base_url": "http://localhost:9000",
        }
    ]
    app._cached_discovered_views = [
        {
            "global_view_id": "inst-a:view-1",
            "view_id": "view-1",
            "filename": "/tmp/a.bin",
            "server_url": "http://localhost:9000",
        },
        {
            "global_view_id": "legacy-9009:view-2",
            "view_id": "view-2",
            "filename": "/tmp/b.bin",
            "server_url": "http://localhost:9009",
        },
    ]

    with (
        patch.object(binja_cli.requests, "post") as post_mock,
        pytest.raises(SystemExit),
    ):
        app._request("POST", "console/execute", data={"command": "id(bv)"})

    post_mock.assert_not_called()
    captured = capsys.readouterr()
    assert "missing required --view-id" in captured.err
    assert "inst-a:view-1  /tmp/a.bin" in captured.err
    assert "legacy-9009:view-2  /tmp/b.bin" in captured.err


def test_non_view_scoped_status_does_not_require_view_id():
    app = _new_app()
    app._cached_discovered_views = [
        {
            "global_view_id": "inst-a:view-1",
            "view_id": "view-1",
            "filename": "/tmp/a.bin",
            "server_url": "http://localhost:9000",
        }
    ]

    with patch.object(
        binja_cli.requests,
        "get",
        return_value=_FakeResponse({"loaded": True, "_api_version": 1}),
    ) as get_mock:
        out = app._request("GET", "status")

    assert out.get("loaded") is True
    assert get_mock.called


def test_open_requires_view_id_in_discovery_mode():
    app = _new_app()
    app._cached_discovered_views = [
        {
            "global_view_id": "inst-a:view-1",
            "view_id": "view-1",
            "filename": "/tmp/a.bin",
            "server_url": "http://localhost:9000",
        }
    ]

    with pytest.raises(SystemExit):
        app._request("POST", "ui/open", data={"filepath": "/tmp/new.bin"})


def test_ensure_server_for_open_selects_instance_from_global_view_id():
    app = _new_app()
    app.target_view_id = "inst-a:view-1"
    app._cached_discovered_servers = [
        {
            "instance_id": "inst-a",
            "base_url": "http://localhost:9000",
        }
    ]

    with patch.object(app, "_probe_server_reachable", return_value=True) as probe_mock:
        out = app._ensure_server_for_open(filepath="/tmp/new.bin")

    assert out == {"ok": True, "launched": False}
    assert app.server_url == "http://localhost:9000"
    probe_mock.assert_called_with("http://localhost:9000", timeout=1.0)


def test_open_without_filepath_prints_help_without_contacting_server(capsys):
    app = _new_app()
    app.json_output = False
    app._cached_discovered_views = [
        {
            "global_view_id": "inst-a:view-1",
            "view_id": "view-1",
            "filename": "/tmp/a.bin",
            "server_url": "http://localhost:9000",
        }
    ]
    command = object.__new__(binja_cli.Open)
    command.parent = app
    command.inspect_only = False

    with (
        patch.object(app, "_ensure_server_for_open") as ensure_mock,
        patch.object(app, "_request") as request_mock,
    ):
        rc = command.main()

    assert rc == 1
    ensure_mock.assert_not_called()
    request_mock.assert_not_called()
    captured = capsys.readouterr()
    assert "missing file path for open" in captured.err
    assert "binja-mcp open --new-server <file>" in captured.err
    assert "binja-mcp --view-id <global-view-id> open <file>" in captured.err
    assert "inst-a:view-1  /tmp/a.bin" in captured.err


def test_open_without_filepath_json_outputs_help_without_contacting_server(capsys):
    app = _new_app()
    app.json_output = True
    app._cached_discovered_views = []
    command = object.__new__(binja_cli.Open)
    command.parent = app
    command.inspect_only = False

    with (
        patch.object(app, "_ensure_server_for_open") as ensure_mock,
        patch.object(app, "_request") as request_mock,
    ):
        rc = command.main()

    assert rc == 1
    ensure_mock.assert_not_called()
    request_mock.assert_not_called()
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "missing file path for open"
    assert "usage" in payload
    assert "binja-mcp open --new-server <file>" in payload["usage"]


def test_open_with_file_without_target_prints_instance_selection_help(capsys):
    app = _new_app()
    app.json_output = False
    app._cached_discovered_views = [
        {
            "global_view_id": "inst-a:view-1",
            "view_id": "view-1",
            "filename": "/tmp/a.bin",
            "server_url": "http://localhost:9000",
        }
    ]
    command = object.__new__(binja_cli.Open)
    command.parent = app
    command.inspect_only = False

    with (
        patch.object(app, "_ensure_server_for_open") as ensure_mock,
        patch.object(app, "_request") as request_mock,
    ):
        rc = command.main("/tmp/new.bin")

    assert rc == 1
    ensure_mock.assert_not_called()
    request_mock.assert_not_called()
    captured = capsys.readouterr()
    assert "target Binary Ninja instance required for open" in captured.err
    assert "binja-mcp open --new-server /tmp/new.bin" in captured.err
    assert "binja-mcp --server http://localhost:<port> open /tmp/new.bin" in captured.err
    assert "binja-mcp --view-id inst-a:view-1 open /tmp/new.bin" in captured.err


def test_open_with_file_without_target_json_outputs_instance_selection_help(capsys):
    app = _new_app()
    app.json_output = True
    app._cached_discovered_views = []
    command = object.__new__(binja_cli.Open)
    command.parent = app
    command.inspect_only = False

    with (
        patch.object(app, "_ensure_server_for_open") as ensure_mock,
        patch.object(app, "_request") as request_mock,
    ):
        rc = command.main("/tmp/new.bin")

    assert rc == 1
    ensure_mock.assert_not_called()
    request_mock.assert_not_called()
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "target Binary Ninja instance required for open"
    assert "binja-mcp open --new-server /tmp/new.bin" in payload["examples"]


def test_views_command_falls_back_to_legacy_default_server_when_discovery_empty(capsys):
    app = _new_app()
    command = object.__new__(binja_cli.Views)
    command.parent = app

    with (
        patch.object(app, "_discover_servers", return_value=[]),
        patch.object(app, "_server_reachable", return_value=True),
        patch.object(
            app,
            "_request",
            return_value={
                "views": [{"view_id": "view-old", "filename": "/tmp/old.bin"}],
                "count": 1,
                "_api_version": 1,
            },
        ) as request_mock,
    ):
        command.main()

    request_mock.assert_called_once_with("GET", "views")
    captured = capsys.readouterr()
    assert "view-old" in captured.out


def test_allow_target_fallback_disables_default_strict_behavior():
    app = _new_app()
    app.server_url = "http://testserver:9009"
    app.target_filename = "/tmp/target.bin"
    app.allow_target_fallback = True

    with patch.object(
        binja_cli.requests,
        "post",
        return_value=_FakeResponse({"success": True, "_api_version": 1}),
    ) as post_mock:
        out = app._request("POST", "console/execute", data={"command": "1 + 1"})

    post_mock.assert_called_once()
    assert out.get("success") is True


def test_print_target_views_hint_lists_open_views(capsys):
    error_data = {
        "open_views": [
            {
                "view_id": "view-101",
                "basename": "first.bin",
                "filename": "/tmp/first.bin",
                "target_hint": "--view-id view-101",
            },
            {
                "view_id": "view-202",
                "basename": "second.bin",
                "filename": "/tmp/second.bin",
                "is_current": True,
                "source": "ui",
                "target_hint": "--view-id view-202",
            },
        ]
    }

    binja_cli.BinaryNinjaCLI._print_target_views_hint(error_data)
    captured = capsys.readouterr()

    assert "Currently open views:" in captured.err
    assert "view-101  first.bin" in captured.err
    assert "[*] view-202  second.bin" in captured.err
    assert "hint: --view-id view-202" in captured.err


def test_views_endpoint_includes_filename_and_view_id_targets():
    app = _new_app()
    app.target_filename = "primary.bin"
    app.target_view_id = "202"

    with patch.object(
        binja_cli.requests,
        "get",
        return_value=_FakeResponse({"views": [], "count": 0, "_api_version": 1}),
    ) as get_mock:
        out = app._request("GET", "views")

    sent_params = get_mock.call_args.kwargs.get("params", {})
    assert sent_params.get("filename") == "primary.bin"
    assert sent_params.get("view_id") == "202"
    assert out.get("count") == 0


def test_strict_target_blocks_mismatched_view_id_before_command():
    app = _new_app()
    app.target_view_id = "0x1234"
    app.strict_target = True

    with (
        patch.object(
            binja_cli.requests,
            "get",
            return_value=_FakeResponse(
                {
                    "error_code": "TARGET_NOT_FOUND",
                    "error": "Requested BinaryView not found",
                    "view_id": "0x1234",
                    "_api_version": 1,
                },
                status_code=404,
            ),
        ),
        patch.object(binja_cli.requests, "post") as post_mock,
        pytest.raises(SystemExit),
    ):
        app._request("POST", "console/execute", data={"command": "1 + 1"})

    post_mock.assert_not_called()


def test_strict_target_passes_for_view_id_and_sets_context_fields():
    app = _new_app()
    app.server_url = "http://testserver:9009"
    app.target_view_id = "0x1234"
    app.strict_target = True

    with patch.object(
        binja_cli.requests,
        "get",
        return_value=_FakeResponse(
            {
                "resolved": True,
                "target": {
                    "view_id": "0x1234",
                    "filename": "/tmp/target.bin",
                    "target_hint": "--view-id 0x1234",
                },
                "_api_version": 1,
            }
        ),
    ):
        with patch.object(
            binja_cli.requests,
            "post",
            return_value=_FakeResponse({"success": True, "_api_version": 1}),
        ):
            out = app._request("POST", "console/execute", data={"command": "1 + 1"})

    assert out.get("success") is True
    assert out.get("selected_view_id") == "0x1234"
    assert out.get("selected_view_filename") == "/tmp/target.bin"
