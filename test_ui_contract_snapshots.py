#!/usr/bin/env python3
"""Snapshot checks for normalized /ui endpoint contracts."""

import importlib
import json
import sys
import unittest
from pathlib import Path

from shared.api_versions import (
    DEFAULT_ENDPOINT_API_VERSION,
    ENDPOINT_API_VERSION_OVERRIDES,
    UI_CONTRACT_SCHEMA_VERSION,
    expected_api_version,
)

THIS_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = THIS_DIR / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

api_contracts = importlib.import_module("server.api_contracts")

SNAPSHOT_DIR = THIS_DIR / "tests" / "snapshots" / "ui_contracts"

RAW_CASES = {
    "ui_open": {
        "endpoint": "/ui/open",
        "raw": {
            "ok": True,
            "actions": ["dialog_detected", "click_open"],
            "warnings": [],
            "errors": [],
            "state": {"loaded_filename": "/tmp/sample.bin"},
            "dialog": {"present": True},
        },
    },
    "ui_quit": {
        "endpoint": "/ui/quit",
        "raw": {
            "ok": False,
            "actions": ["close_tab_action_queued"],
            "warnings": ["confirmation dialog still visible"],
            "errors": [],
            "state": {"stuck_confirmation": True},
            "policy": {"resolved_decision": "dont-save"},
        },
    },
    "ui_statusbar": {
        "endpoint": "/ui/statusbar",
        "raw": {
            "ok": True,
            "actions": [],
            "warnings": ["no status bar text found"],
            "errors": [],
            "state": {},
            "status_source": "status_bar",
            "status_text": "",
            "status_items": [],
        },
    },
}


class TestUIContractSnapshots(unittest.TestCase):
    def test_ui_contract_snapshots(self):
        for name, case in RAW_CASES.items():
            with self.subTest(snapshot=name):
                actual = api_contracts.normalize_ui_contract(case["endpoint"], case["raw"])
                snapshot_path = SNAPSHOT_DIR / f"{name}.json"
                expected = json.loads(snapshot_path.read_text(encoding="utf-8"))
                self.assertEqual(actual, expected)

    def test_ui_contract_shape_and_versions(self):
        for endpoint in ("/ui/open", "/ui/quit", "/ui/statusbar"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(expected_api_version(endpoint), 2)
                payload = api_contracts.normalize_ui_contract(endpoint, {"ok": True})
                self.assertTrue(api_contracts.has_ui_contract_shape(payload))
                self.assertEqual(payload.get("schema_version"), UI_CONTRACT_SCHEMA_VERSION)

    def test_shared_version_constants(self):
        self.assertEqual(api_contracts.DEFAULT_ENDPOINT_API_VERSION, DEFAULT_ENDPOINT_API_VERSION)
        self.assertEqual(
            api_contracts.ENDPOINT_API_VERSION_OVERRIDES,
            ENDPOINT_API_VERSION_OVERRIDES,
        )

    def test_scalar_errors_and_warnings_are_normalized_to_lists(self):
        payload = api_contracts.normalize_ui_contract(
            "/ui/open",
            {
                "ok": False,
                "actions": "would_set_view_type",
                "warnings": "dialog not visible",
                "errors": "something failed",
                "state": {},
            },
        )
        self.assertEqual(payload["actions"], ["would_set_view_type"])
        self.assertEqual(payload["warnings"], ["dialog not visible"])
        self.assertEqual(payload["errors"], ["something failed"])


if __name__ == "__main__":
    unittest.main()
