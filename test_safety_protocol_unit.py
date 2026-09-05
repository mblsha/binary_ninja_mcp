"""An old server must reject safety-sensitive requests before dispatch."""

from unittest.mock import patch
import sys
import types

import pytest

from binja_cli.cli import BinaryNinjaCLI
from shared.api_versions import expected_api_version
from shared.build_info import REQUIRED_CAPABILITIES, CAPABILITY_PROTOCOL_VERSION
import test_http_server_disconnect_unit as server_tests
from test_cli_strict_target_unit import _FakeResponse


SAFETY_PATHS = (
    "/edit/local",
    "/edit/struct-field",
    "/function/signature",
    "/editFunctionSignature",
    "/decompile",
    "/rename/function",
    "/renameFunction",
    "/rename/data",
    "/renameData",
    "/comment",
    "/comment/function",
    "/defineTypes",
    "/renameVariable",
    "/retypeVariable",
)


@pytest.mark.parametrize("path", SAFETY_PATHS)
def test_changed_safety_contracts_require_api_v2(path):
    assert expected_api_version(path) == 2


def test_legacy_server_rejects_new_preview_request_before_dispatch():
    module = server_tests._import_http_server()
    handler = module.MCPRequestHandler.__new__(module.MCPRequestHandler)
    handler.headers = {}
    handler.responses = []
    handler._expected_api_version = lambda _path: 1  # pre-preview server
    handler._send_json_response = lambda data, status: handler.responses.append((data, status))
    assert (
        handler._validate_endpoint_version(
            "/function/signature",
            {"_api_version": expected_api_version("/function/signature"), "preview": True},
        )
        is False
    )
    assert handler.responses[0][1] == 409


def test_new_server_rejects_old_mutation_contract():
    module = server_tests._import_http_server()
    handler = module.MCPRequestHandler.__new__(module.MCPRequestHandler)
    handler.headers = {}
    handler.responses = []
    handler._send_json_response = lambda data, status: handler.responses.append((data, status))
    assert handler._validate_endpoint_version("/function/signature", {"_api_version": 1}) is False
    assert handler.responses[0][0]["expected_api_version"] == 2


def test_client_sends_preview_with_versioned_contract():
    app = BinaryNinjaCLI("binja-cli")
    app.server_url = "http://isolated-test:9009"
    response = _FakeResponse(
        {"success": True, "committed": False, "_api_version": 2}, api_version=2
    )
    metadata = _FakeResponse(
        {
            "_api_version": 1,
            "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
            "capabilities": REQUIRED_CAPABILITIES,
        }
    )
    with (
        patch("binja_cli.cli.requests.get", return_value=metadata),
        patch("binja_cli.cli.requests.post", return_value=response) as post,
    ):
        app._request(
            "POST",
            "function/signature",
            data={"function": "f", "signature": "void f(void);", "preview": True},
        )
    assert post.call_args.kwargs["json"]["_api_version"] == 2
    assert post.call_args.kwargs["headers"]["X-Binja-MCP-Api-Version"] == "2"


@pytest.mark.parametrize(
    "metadata",
    [
        {"_api_version": 1},
        {
            "_api_version": 1,
            "capability_protocol_version": 1,
            "capabilities": REQUIRED_CAPABILITIES,
            "runtime": {"reload_required": True},
        },
        {
            "_api_version": 1,
            "capability_protocol_version": 1,
            "capabilities": REQUIRED_CAPABILITIES,
            "runtime": {"unverifiable_modules": ["endpoints"]},
        },
    ],
)
def test_client_refuses_missing_or_stale_capabilities_before_preview(metadata, capsys):
    app = BinaryNinjaCLI("binja-cli")
    app.server_url = "http://isolated-test:9009"
    with (
        patch("binja_cli.cli.requests.get", return_value=_FakeResponse(metadata)),
        patch("binja_cli.cli.requests.post") as post,
        pytest.raises(SystemExit),
    ):
        app._request(
            "POST",
            "function/signature",
            data={"function": "f", "signature": "void f(void);", "preview": True},
        )
    post.assert_not_called()
    assert "no operation was sent" in capsys.readouterr().err


@pytest.mark.parametrize(
    "path", ["edit/local", "edit/struct-field", "analysis/locals", "analysis/struct"]
)
def test_annotation_capability_preflight_refuses_an_old_loaded_server(path, capsys):
    app = BinaryNinjaCLI("binja-cli")
    app.server_url = "http://isolated-test:9009"
    capabilities = {
        k: v for k, v in REQUIRED_CAPABILITIES.items() if k != "annotation_edits_version"
    }
    metadata = {"_api_version": 1, "capability_protocol_version": 1, "capabilities": capabilities}
    with (
        patch("binja_cli.cli.requests.get", return_value=_FakeResponse(metadata)),
        patch("binja_cli.cli.requests.post") as post,
        pytest.raises(SystemExit),
    ):
        app._request("POST", path, data={"preview": True})
    post.assert_not_called()
    assert "annotation_edits_version" in capsys.readouterr().err


def test_server_diagnostics_report_loaded_capabilities_and_stale_instances():
    module = server_tests._import_http_server()
    # The isolation helper removes imports from sys.modules when its BN mock
    # exits. Reconstruct the real imported namespaces for introspection, without
    # importing the SDK or starting another server.
    namespaces = [
        vars(module),
        module.BinaryOperations.__init__.__globals__,
        module.BinaryNinjaEndpoints.__init__.__globals__,
        module.get_console_capture.__globals__,
        module.snapshot_source.__globals__,
        module.expected_api_version.__globals__,
        module.AnalysisOperations.__init__.__globals__,
        module.AnnotationEdits.__init__.__globals__,
        module.IdentifierResolver.__init__.__globals__,
    ]
    mutation_type = module.BinaryOperations.__init__.__globals__["MutationTransaction"]
    namespaces.append(mutation_type.__init__.__globals__)
    imported = {}
    for namespace in namespaces:
        imported_module = types.ModuleType(namespace["__name__"])
        vars(imported_module).update(namespace)
        imported[namespace["__name__"]] = imported_module
    with patch.dict(sys.modules, imported):
        server = module.MCPServer(module.Config())
        metadata = server.instance_metadata()
        assert metadata["capabilities"]["signature_workflow_version"] == 2
        assert metadata["capabilities"]["python_serialization_version"] == 2
        assert metadata["capabilities"]["annotation_edits_version"] == 1
        assert metadata["runtime"]["stale_bindings"] == []
        assert "mutations" in metadata["runtime"]["sources"]
        assert {"memory_reads", "type_queries"} <= metadata["runtime"]["sources"].keys()
        operations_module = sys.modules[type(server.binary_ops).__module__]
        replacement = type("ReloadedOperations", (), {})
        with patch.object(operations_module, "BinaryOperations", replacement):
            metadata = server.instance_metadata()
        assert metadata["runtime"]["reload_required"]
        assert "binary_operations_instance" in metadata["runtime"]["stale_bindings"]
