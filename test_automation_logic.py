#!/usr/bin/env python3
"""Pure-Python tests for automation helper logic."""

import sys
import tempfile
import unittest
import importlib
from pathlib import Path


# Import plugin automation modules without importing plugin/__init__.py.
THIS_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = THIS_DIR / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

quit_app = importlib.import_module("automation.quit_app")
text_helpers = importlib.import_module("automation.text")

choose_decision_label = quit_app.choose_decision_label
compute_database_save_target = quit_app.compute_database_save_target
resolve_policy = quit_app.resolve_policy
find_item_index = text_helpers.find_item_index


class TestAutomationText(unittest.TestCase):
    def test_find_item_index_exact_match(self):
        self.assertEqual(find_item_index(["Raw", "Mapped"], "Mapped"), 1)

    def test_find_item_index_partial_match(self):
        idx = find_item_index(["x86_64", "x86_16", "armv7"], "x86")
        self.assertIn(idx, (0, 1))

    def test_find_item_index_case_space_insensitive(self):
        self.assertEqual(find_item_index(["Don't Save", "Save"], "dont-save"), 0)


class TestQuitPolicy(unittest.TestCase):
    def test_resolve_policy_auto_with_companion(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            binary = Path(tmp_dir) / "a.out"
            binary.write_bytes(b"\x00")
            companion = Path(str(binary) + ".bndb")
            companion.write_bytes(b"db")

            resolved, loaded_is_bndb, companion_exists = resolve_policy(str(binary), "auto")
            self.assertEqual(resolved, "save")
            self.assertFalse(loaded_is_bndb)
            self.assertTrue(companion_exists)

    def test_compute_database_save_target_uses_companion_for_binary(self):
        target = compute_database_save_target(
            loaded_filename="/tmp/sample.bin",
            loaded_is_bndb=False,
            companion_exists=True,
        )
        self.assertEqual(target, "/tmp/sample.bin.bndb")

    def test_compute_database_save_target_keeps_bndb_path(self):
        target = compute_database_save_target(
            loaded_filename="/tmp/sample.bndb",
            loaded_is_bndb=True,
            companion_exists=False,
        )
        self.assertEqual(target, "/tmp/sample.bndb")

    def test_choose_decision_label_dont_save_variants(self):
        label = choose_decision_label(["Save", "Don't Save", "Cancel"], "dont-save")
        self.assertEqual(label, "Don't Save")


if __name__ == "__main__":
    unittest.main()
