#!/usr/bin/env python3
"""Unit tests for console capture signature adapter."""

import importlib
import sys
import unittest
from pathlib import Path


THIS_DIR = Path(__file__).resolve().parent
CORE_DIR = THIS_DIR / "plugin" / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

adapter_mod = importlib.import_module("console_capture_adapter")
ConsoleCaptureAdapter = adapter_mod.ConsoleCaptureAdapter


class _NewSignatureBackend:
    def execute_command(self, command: str, *, binary_view=None, timeout: float = 30.0):
        return {
            "path": "new",
            "command": command,
            "binary_view": binary_view,
            "timeout": timeout,
        }


class _TwoArgBackend:
    def execute_command(self, command: str, binary_view=None):
        return {"path": "two", "command": command, "binary_view": binary_view}


class _OneArgBackend:
    def execute_command(self, command: str):
        return {"path": "one", "command": command}


class _NoExecuteBackend:
    pass


class TestConsoleCaptureAdapter(unittest.TestCase):
    def test_prefers_new_signature(self):
        adapter = ConsoleCaptureAdapter(_NewSignatureBackend())
        result = adapter.execute_command("x = 1", binary_view="bv", timeout=12.5)
        self.assertEqual(result["path"], "new")
        self.assertEqual(result["binary_view"], "bv")
        self.assertEqual(result["timeout"], 12.5)

    def test_falls_back_to_two_arg_signature(self):
        adapter = ConsoleCaptureAdapter(_TwoArgBackend())
        result = adapter.execute_command("x = 1", binary_view="bv", timeout=12.5)
        self.assertEqual(result["path"], "two")
        self.assertEqual(result["binary_view"], "bv")

    def test_falls_back_to_one_arg_signature(self):
        adapter = ConsoleCaptureAdapter(_OneArgBackend())
        result = adapter.execute_command("x = 1", binary_view="bv", timeout=12.5)
        self.assertEqual(result["path"], "one")
        self.assertEqual(result["command"], "x = 1")

    def test_missing_execute_command_raises(self):
        adapter = ConsoleCaptureAdapter(_NoExecuteBackend())
        with self.assertRaises(RuntimeError):
            adapter.execute_command("x = 1")


if __name__ == "__main__":
    unittest.main()
