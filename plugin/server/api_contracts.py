"""Shared endpoint versioning and UI contract helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

try:
    from shared.api_versions import (
        DEFAULT_ENDPOINT_API_VERSION,
        ENDPOINT_API_VERSION_OVERRIDES,
        UI_CONTRACT_SCHEMA_VERSION,
        allows_missing_api_version,
        expected_api_version,
        normalize_endpoint_path,
    )
except ImportError:
    # Binary Ninja can import plugin modules with plugin/ as sys.path[0].
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from shared.api_versions import (
        DEFAULT_ENDPOINT_API_VERSION,
        ENDPOINT_API_VERSION_OVERRIDES,
        UI_CONTRACT_SCHEMA_VERSION,
        allows_missing_api_version,
        expected_api_version,
        normalize_endpoint_path,
    )

__all__ = [
    "DEFAULT_ENDPOINT_API_VERSION",
    "ENDPOINT_API_VERSION_OVERRIDES",
    "UI_CONTRACT_SCHEMA_VERSION",
    "UI_CONTRACT_REQUIRED_KEYS",
    "allows_missing_api_version",
    "expected_api_version",
    "normalize_endpoint_path",
    "as_list",
    "as_contract_list",
    "as_dict",
    "normalize_ui_contract",
    "has_ui_contract_shape",
]

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


def as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    return [value]


def as_contract_list(value: Any) -> list:
    # UI contract keys must always be arrays in JSON.
    return as_list(value)


def as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    return {}


def normalize_ui_contract(endpoint_path: str, raw_result: Any) -> dict[str, Any]:
    endpoint = normalize_endpoint_path(endpoint_path)
    raw = raw_result if isinstance(raw_result, dict) else {"ok": False, "errors": [str(raw_result)]}
    actions = as_contract_list(raw.get("actions"))
    warnings = as_contract_list(raw.get("warnings"))
    errors = as_contract_list(raw.get("errors"))
    return {
        "ok": bool(raw.get("ok", not bool(errors))),
        "schema_version": UI_CONTRACT_SCHEMA_VERSION,
        "endpoint": endpoint,
        "actions": actions,
        "warnings": warnings,
        "errors": errors,
        "state": as_dict(raw.get("state")),
        "result": raw,
    }


def has_ui_contract_shape(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return all(key in payload for key in UI_CONTRACT_REQUIRED_KEYS)
