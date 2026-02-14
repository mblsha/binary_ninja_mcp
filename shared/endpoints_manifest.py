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
    EndpointSpec("GET", "/searchFunctions", True, minimal_params={"query": "main", "limit": 5}),
    EndpointSpec("GET", "/decompile", True),
    EndpointSpec("GET", "/assembly", True),
    EndpointSpec("GET", "/functionAt", True),
    EndpointSpec("GET", "/codeReferences", True),
    EndpointSpec("GET", "/getUserDefinedType", True),
    EndpointSpec("GET", "/comment", True),
    EndpointSpec("GET", "/comment/function", True),
    EndpointSpec("GET", "/getComment", True),
    EndpointSpec("GET", "/getFunctionComment", True),
    EndpointSpec("GET", "/editFunctionSignature", True),
    EndpointSpec("GET", "/retypeVariable", True),
    EndpointSpec("GET", "/renameVariable", True),
    EndpointSpec("GET", "/defineTypes", True),
    EndpointSpec("GET", "/logs", False, minimal_params={"count": 5}),
    EndpointSpec("GET", "/logs/stats", False),
    EndpointSpec("GET", "/logs/errors", False, minimal_params={"count": 5}),
    EndpointSpec("GET", "/logs/warnings", False, minimal_params={"count": 5}),
    EndpointSpec("GET", "/console", False, minimal_params={"count": 5}),
    EndpointSpec("GET", "/console/stats", False),
    EndpointSpec("GET", "/console/errors", False, minimal_params={"count": 5}),
    EndpointSpec("GET", "/console/complete", False, minimal_params={"partial": "bv.fun"}),
    EndpointSpec("POST", "/load", True),
    EndpointSpec("POST", "/rename/function", True),
    EndpointSpec("POST", "/renameFunction", True),
    EndpointSpec("POST", "/rename/data", True),
    EndpointSpec("POST", "/renameData", True),
    EndpointSpec("POST", "/comment", True),
    EndpointSpec("POST", "/comment/function", True),
    EndpointSpec("POST", "/getComment", True),
    EndpointSpec("POST", "/getFunctionComment", True),
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
        minimal_json={"inspect_only": True, "click_open": False},
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
