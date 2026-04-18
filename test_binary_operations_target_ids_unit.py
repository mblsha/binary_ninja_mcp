#!/usr/bin/env python3

from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


THIS_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = THIS_DIR / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))


class _FakeFile:
    def __init__(self, filename: str):
        self.filename = filename


class _FakeBinaryView:
    def __init__(self, filename: str, *, legacy_view_id: str | None = None):
        self.file = _FakeFile(filename)
        if legacy_view_id is not None:
            self.view_id = legacy_view_id


class TestBinaryOperationsTargetIds(unittest.TestCase):
    def _import_modules(self):
        bn_module = types.ModuleType("binaryninja")
        bn_enums = types.ModuleType("binaryninja.enums")
        bn_enums.TypeClass = object()
        bn_enums.StructureVariant = object()
        bn_module.enums = bn_enums
        bn_module.BinaryView = _FakeBinaryView
        bn_module.Function = object
        bn_module.log_info = lambda *args, **kwargs: None
        bn_module.log_error = lambda *args, **kwargs: None

        with patch.dict(sys.modules, {"binaryninja": bn_module, "binaryninja.enums": bn_enums}):
            helper = importlib.import_module("core.view_identity")
            module = importlib.import_module("core.binary_operations")
        return helper, module

    def test_register_view_uses_stable_public_id(self):
        helper, module = self._import_modules()
        ops = module.BinaryOperations(config=object())
        view = _FakeBinaryView("/tmp/roms/primary.bin")

        ops.register_view(view)

        expected_view_id = helper.make_public_view_id("/tmp/roms/primary.bin")
        self.assertEqual(getattr(view, "_binja_mcp_view_id", None), expected_view_id)
        self.assertIs(ops.get_registered_view_by_id(expected_view_id), view)

    def test_register_view_keeps_legacy_id_aliases(self):
        helper, module = self._import_modules()
        ops = module.BinaryOperations(config=object())
        view = _FakeBinaryView("/tmp/roms/legacy.bin", legacy_view_id="0x1234")

        ops.register_view(view)

        expected_view_id = helper.make_public_view_id("/tmp/roms/legacy.bin")
        self.assertIs(ops.get_registered_view_by_id(expected_view_id), view)
        self.assertIs(ops.get_registered_view_by_id("0x1234"), view)


if __name__ == "__main__":
    unittest.main()
