#!/usr/bin/env python3
"""Integration tests for endpoint API version handshakes and /ui contracts.

These tests require a running Binary Ninja MCP server.
If no server is reachable, tests are skipped.
"""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path
from typing import Any

import requests


THIS_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = THIS_DIR / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

api_contracts = importlib.import_module("server.api_contracts")

ENDPOINT_CASES = [
    {"name": "status", "method": "GET", "path": "/status", "payload": None},
    {"name": "logs_stats", "method": "GET", "path": "/logs/stats", "payload": None},
    {"name": "console_stats", "method": "GET", "path": "/console/stats", "payload": None},
    {
        "name": "console_execute",
        "method": "POST",
        "path": "/console/execute",
        "payload": {"command": "1 + 1"},
    },
    {
        "name": "ui_statusbar",
        "method": "POST",
        "path": "/ui/statusbar",
        "payload": {"all_windows": False, "include_hidden": False},
    },
    {
        "name": "ui_open",
        "method": "POST",
        "path": "/ui/open",
        "payload": {"inspect_only": True, "click_open": False},
    },
    {
        "name": "ui_quit",
        "method": "POST",
        "path": "/ui/quit",
        "payload": {"inspect_only": True, "wait_ms": 0, "decision": "dont-save"},
    },
]


def _endpoint_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def _call_endpoint(
    base_url: str,
    endpoint_case: dict[str, Any],
    *,
    include_version: bool = True,
    version_override: int | None = None,
) -> requests.Response:
    method = endpoint_case["method"]
    path = endpoint_case["path"]
    payload = dict(endpoint_case.get("payload") or {})

    params: dict[str, Any] = {}
    headers: dict[str, str] = {}
    expected = api_contracts.expected_api_version(path)
    request_version = expected if version_override is None else int(version_override)

    if include_version:
        headers["X-Binja-MCP-Api-Version"] = str(request_version)
        if method == "GET":
            params["_api_version"] = request_version
        else:
            payload["_api_version"] = request_version

    if method == "GET":
        return requests.get(
            _endpoint_url(base_url, path),
            params=params,
            headers=headers,
            timeout=10,
        )
    return requests.post(
        _endpoint_url(base_url, path),
        params=params,
        json=payload,
        headers=headers,
        timeout=20,
    )


class TestEndpointApiIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_url = os.environ.get("BINJA_MCP_BASE_URL", "http://localhost:9009").rstrip("/")
        status_path = "/status"
        expected = api_contracts.expected_api_version(status_path)
        try:
            response = requests.get(
                _endpoint_url(cls.base_url, status_path),
                params={"_api_version": expected},
                headers={"X-Binja-MCP-Api-Version": str(expected)},
                timeout=3,
            )
            response.raise_for_status()
        except Exception as exc:
            raise unittest.SkipTest(f"Binary Ninja MCP server is not reachable: {exc}")

    def test_version_handshake_accepts_expected_version(self):
        for case in ENDPOINT_CASES:
            with self.subTest(endpoint=case["path"], method=case["method"]):
                response = _call_endpoint(self.base_url, case, include_version=True)
                self.assertEqual(
                    response.status_code,
                    200,
                    f"{case['path']} should accept expected API version; body={response.text}",
                )
                expected = api_contracts.expected_api_version(case["path"])
                header_version = int(response.headers.get("X-Binja-MCP-Api-Version", "-1"))
                self.assertEqual(header_version, expected)

                body = response.json()
                self.assertIsInstance(body, dict)
                self.assertEqual(int(body.get("_api_version", -1)), expected)
                self.assertEqual(body.get("_endpoint"), case["path"])

    def test_version_mismatch_rejected_for_each_endpoint(self):
        for case in ENDPOINT_CASES:
            with self.subTest(endpoint=case["path"], method=case["method"]):
                expected = api_contracts.expected_api_version(case["path"])
                wrong_version = expected + 100
                response = _call_endpoint(
                    self.base_url,
                    case,
                    include_version=True,
                    version_override=wrong_version,
                )
                self.assertEqual(response.status_code, 409, response.text)
                body = response.json()
                self.assertEqual(body.get("error"), "Endpoint API version mismatch")
                self.assertEqual(int(body.get("expected_api_version", -1)), expected)
                self.assertEqual(int(body.get("received_api_version", -1)), wrong_version)

    def test_missing_version_rejected_for_each_endpoint(self):
        for case in ENDPOINT_CASES:
            with self.subTest(endpoint=case["path"], method=case["method"]):
                response = _call_endpoint(self.base_url, case, include_version=False)
                if case["path"] == "/status":
                    self.assertEqual(response.status_code, 200, response.text)
                    body = response.json()
                    self.assertEqual(int(body.get("_api_version", -1)), 1)
                else:
                    self.assertEqual(response.status_code, 400, response.text)
                    body = response.json()
                    self.assertEqual(body.get("error"), "Missing endpoint API version")
                    self.assertEqual(
                        int(body.get("expected_api_version", -1)),
                        api_contracts.expected_api_version(case["path"]),
                    )

    def test_ui_endpoint_contract_shape(self):
        for path in ("/ui/open", "/ui/quit", "/ui/statusbar"):
            case = next(item for item in ENDPOINT_CASES if item["path"] == path)
            with self.subTest(endpoint=path):
                response = _call_endpoint(self.base_url, case, include_version=True)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertTrue(api_contracts.has_ui_contract_shape(body), body)
                self.assertEqual(body.get("schema_version"), 1)
                self.assertEqual(body.get("endpoint"), path)

    def test_ui_open_inspect_only_is_read_only(self):
        status_version = api_contracts.expected_api_version("/status")
        before = requests.get(
            _endpoint_url(self.base_url, "/status"),
            params={"_api_version": status_version},
            headers={"X-Binja-MCP-Api-Version": str(status_version)},
            timeout=5,
        )
        before.raise_for_status()
        before_body = before.json()

        open_case = {
            "method": "POST",
            "path": "/ui/open",
            "payload": {
                "inspect_only": True,
                "click_open": False,
                "filepath": "/bin/ls",
                "platform": "x86_64",
                "view_type": "Mapped",
            },
        }
        open_response = _call_endpoint(self.base_url, open_case, include_version=True)
        self.assertEqual(open_response.status_code, 200, open_response.text)
        open_body = open_response.json()
        self.assertTrue(api_contracts.has_ui_contract_shape(open_body), open_body)
        self.assertNotIn("set_current_view", open_body.get("actions", []))
        self.assertNotIn("set_loaded_view_arch", open_body.get("actions", []))
        self.assertNotIn("set_loaded_view_platform", open_body.get("actions", []))

        after = requests.get(
            _endpoint_url(self.base_url, "/status"),
            params={"_api_version": status_version},
            headers={"X-Binja-MCP-Api-Version": str(status_version)},
            timeout=5,
        )
        after.raise_for_status()
        after_body = after.json()
        self.assertEqual(before_body.get("loaded"), after_body.get("loaded"))
        self.assertEqual(before_body.get("filename"), after_body.get("filename"))


if __name__ == "__main__":
    unittest.main()
