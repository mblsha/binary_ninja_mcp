"""Authoritative HTTP endpoint registry for the MCP server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .api_versions import expected_api_version, normalize_endpoint_path


@dataclass(frozen=True)
class EndpointSpec:
    method: str
    path: str
    requires_binary: bool
    minimal_params: dict[str, Any] | None = None
    minimal_json: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        path = normalize_endpoint_path(self.path)
        out: dict[str, Any] = {
            "method": self.method.upper(),
            "path": path,
            "requires_binary": bool(self.requires_binary),
            "api_version": expected_api_version(path),
        }
        if self.minimal_params:
            out["minimal_params"] = dict(self.minimal_params)
        if self.minimal_json:
            out["minimal_json"] = dict(self.minimal_json)
        return out


# Keep this registry aligned with plugin/server/http_server.py route handlers.
# Placeholder values (for integration tests) are resolved from live fixture context:
# - __FIXTURE_BINARY__
# - __FUNCTION__
# - __FUNCTION_PREFIX__
# - __ENTRY_ADDRESS__
# - __MISSING_TYPE__
ENDPOINT_SPECS: tuple[EndpointSpec, ...] = (
    EndpointSpec("GET", "/meta/endpoints", False),
    EndpointSpec("GET", "/status", False),
    EndpointSpec("GET", "/functions", True, minimal_params={"limit": 5}),
    EndpointSpec("GET", "/methods", True, minimal_params={"limit": 5}),
    EndpointSpec("GET", "/classes", True, minimal_params={"limit": 5}),
    EndpointSpec("GET", "/segments", True, minimal_params={"limit": 5}),
    EndpointSpec("GET", "/imports", True, minimal_params={"limit": 5}),
    EndpointSpec("GET", "/exports", True, minimal_params={"limit": 5}),
    EndpointSpec("GET", "/namespaces", True, minimal_params={"limit": 5}),
    EndpointSpec("GET", "/data", True, minimal_params={"limit": 5}),
    EndpointSpec(
        "GET",
        "/searchFunctions",
        True,
        minimal_params={"query": "__FUNCTION_PREFIX__", "limit": 5},
    ),
    EndpointSpec("GET", "/decompile", True, minimal_params={"name": "__FUNCTION__"}),
    EndpointSpec("GET", "/assembly", True, minimal_params={"name": "__FUNCTION__"}),
    EndpointSpec("GET", "/functionAt", True, minimal_params={"address": "__ENTRY_ADDRESS__"}),
    EndpointSpec("GET", "/codeReferences", True, minimal_params={"function": "__FUNCTION__"}),
    EndpointSpec("GET", "/getUserDefinedType", True, minimal_params={"name": "__MISSING_TYPE__"}),
    EndpointSpec("GET", "/comment", True, minimal_params={"address": "__ENTRY_ADDRESS__"}),
    EndpointSpec("GET", "/comment/function", True, minimal_params={"name": "__FUNCTION__"}),
    EndpointSpec("GET", "/getComment", True, minimal_params={"address": "__ENTRY_ADDRESS__"}),
    EndpointSpec("GET", "/getFunctionComment", True, minimal_params={"name": "__FUNCTION__"}),
    EndpointSpec("GET", "/editFunctionSignature", True),
    EndpointSpec("GET", "/retypeVariable", True),
    EndpointSpec("GET", "/renameVariable", True),
    EndpointSpec(
        "GET",
        "/defineTypes",
        True,
        minimal_params={"cCode": "typedef unsigned int mcp_endpoint_type_t;"},
    ),
    EndpointSpec("GET", "/logs", False, minimal_params={"count": 5}),
    EndpointSpec("GET", "/logs/stats", False),
    EndpointSpec("GET", "/logs/errors", False, minimal_params={"count": 5}),
    EndpointSpec("GET", "/logs/warnings", False, minimal_params={"count": 5}),
    EndpointSpec("GET", "/console", False, minimal_params={"count": 5}),
    EndpointSpec("GET", "/console/stats", False),
    EndpointSpec("GET", "/console/errors", False, minimal_params={"count": 5}),
    EndpointSpec("GET", "/console/complete", False, minimal_params={"partial": "bv.fun"}),
    EndpointSpec("POST", "/load", True, minimal_json={"filepath": "__FIXTURE_BINARY__"}),
    EndpointSpec("POST", "/rename/function", True),
    EndpointSpec("POST", "/renameFunction", True),
    EndpointSpec(
        "POST",
        "/rename/data",
        True,
        minimal_json={"address": "__ENTRY_ADDRESS__", "newName": "mcp_integration_data"},
    ),
    EndpointSpec(
        "POST",
        "/renameData",
        True,
        minimal_json={"address": "__ENTRY_ADDRESS__", "newName": "mcp_integration_data"},
    ),
    EndpointSpec(
        "POST",
        "/comment",
        True,
        minimal_json={"address": "__ENTRY_ADDRESS__", "comment": "mcp endpoint comment"},
    ),
    EndpointSpec(
        "POST",
        "/comment/function",
        True,
        minimal_json={"name": "__FUNCTION__", "comment": "mcp endpoint function comment"},
    ),
    EndpointSpec("POST", "/getComment", True, minimal_json={"address": "__ENTRY_ADDRESS__"}),
    EndpointSpec("POST", "/getFunctionComment", True, minimal_json={"name": "__FUNCTION__"}),
    EndpointSpec("POST", "/logs/clear", False, minimal_json={}),
    EndpointSpec("POST", "/console/clear", False, minimal_json={}),
    EndpointSpec("POST", "/console/execute", False, minimal_json={"command": "1 + 1"}),
    EndpointSpec(
        "POST",
        "/ui/statusbar",
        False,
        minimal_json={"all_windows": False, "include_hidden": False},
    ),
    EndpointSpec(
        "POST",
        "/ui/open",
        False,
        minimal_json={
            "filepath": "__FIXTURE_BINARY__",
            "view_type": "Raw",
            "inspect_only": True,
            "click_open": False,
        },
    ),
    EndpointSpec(
        "POST",
        "/ui/quit",
        False,
        minimal_json={"inspect_only": True, "decision": "dont-save", "wait_ms": 0},
    ),
)


def get_endpoint_registry() -> list[EndpointSpec]:
    return list(ENDPOINT_SPECS)


def get_endpoint_registry_json() -> list[dict[str, Any]]:
    return [spec.as_dict() for spec in ENDPOINT_SPECS]
