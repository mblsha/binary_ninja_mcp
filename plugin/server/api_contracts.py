"""Shared endpoint versioning and UI contract helpers."""

from __future__ import annotations

from typing import Any

DEFAULT_ENDPOINT_API_VERSION = 1
ENDPOINT_API_VERSION_OVERRIDES = {
    "/ui/open": 2,
    "/ui/quit": 2,
    "/ui/statusbar": 2,
}

UI_CONTRACT_SCHEMA_VERSION = 1
UI_CONTRACT_REQUIRED_KEYS = (
    "ok",
    "schema_version",
    "endpoint",
    "actions",
    "warnings",
    "errors",
    "state",
    "result",
)


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


def as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return []


def as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    return {}


def normalize_ui_contract(endpoint_path: str, raw_result: Any) -> dict[str, Any]:
    endpoint = normalize_endpoint_path(endpoint_path)
    raw = raw_result if isinstance(raw_result, dict) else {"ok": False, "errors": [str(raw_result)]}
    return {
        "ok": bool(raw.get("ok", not bool(raw.get("errors")))),
        "schema_version": UI_CONTRACT_SCHEMA_VERSION,
        "endpoint": endpoint,
        "actions": as_list(raw.get("actions")),
        "warnings": as_list(raw.get("warnings")),
        "errors": as_list(raw.get("errors")),
        "state": as_dict(raw.get("state")),
        "result": raw,
    }


def has_ui_contract_shape(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return all(key in payload for key in UI_CONTRACT_REQUIRED_KEYS)
