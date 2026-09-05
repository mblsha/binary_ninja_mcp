"""Shared API version and contract constants."""

from __future__ import annotations
from .build_info import snapshot_source

LOADED_SOURCE = snapshot_source(__file__)

DEFAULT_ENDPOINT_API_VERSION = 1
ENDPOINT_API_VERSION_OVERRIDES = {
    # Safety semantics changed: old servers must reject these requests rather
    # than silently ignore preview/skip protection or omit rollback guarantees.
    "/function/signature": 2,
    "/editFunctionSignature": 2,
    "/decompile": 2,
    "/rename/function": 2,
    "/renameFunction": 2,
    "/rename/data": 2,
    "/renameData": 2,
    "/comment": 2,
    "/comment/function": 2,
    "/defineTypes": 2,
    "/renameVariable": 2,
    "/retypeVariable": 2,
    "/ui/open": 2,
    "/ui/close": 2,
    "/ui/quit": 2,
    "/ui/statusbar": 2,
}

# Keep server liveliness checks easy for manual usage (`curl /status`).
MISSING_VERSION_ALLOWED_ENDPOINTS = {
    "/status",
}

UI_CONTRACT_SCHEMA_VERSION = 1
SUPPORTED_UI_CONTRACT_SCHEMA_VERSIONS = {
    UI_CONTRACT_SCHEMA_VERSION,
}


def normalize_endpoint_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return "/"
    if "?" in raw:
        raw = raw.split("?", 1)[0]
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return raw


def expected_api_version(path: str) -> int:
    endpoint_path = normalize_endpoint_path(path)
    return ENDPOINT_API_VERSION_OVERRIDES.get(endpoint_path, DEFAULT_ENDPOINT_API_VERSION)


def allows_missing_api_version(path: str) -> bool:
    endpoint_path = normalize_endpoint_path(path)
    return endpoint_path in MISSING_VERSION_ALLOWED_ENDPOINTS
