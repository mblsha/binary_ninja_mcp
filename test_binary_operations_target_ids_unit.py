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

    def test_register_view_assigns_distinct_public_id_and_logical_id(self):
        helper, module = self._import_modules()
        ops = module.BinaryOperations(config=object())
        view = _FakeBinaryView("/tmp/roms/primary.bin")

        ops.register_view(view)

        assigned_view_id = getattr(view, "_binja_mcp_view_id", None)
        expected_logical_id = helper.make_logical_view_id("/tmp/roms/primary.bin")
        self.assertIsNotNone(assigned_view_id)
        self.assertTrue(str(assigned_view_id).startswith("view-"))
        self.assertEqual(getattr(view, "_binja_mcp_logical_view_id", None), expected_logical_id)
        self.assertIs(ops.get_registered_view_by_id(assigned_view_id), view)

    def test_register_view_assigns_distinct_ids_for_duplicate_same_file_views(self):
        helper, module = self._import_modules()
        ops = module.BinaryOperations(config=object())
        v1 = _FakeBinaryView("/tmp/roms/shared.bin")
        v2 = _FakeBinaryView("/tmp/roms/shared.bin")

        ops.register_view(v1)
        ops.register_view(v2)

        id1 = getattr(v1, "_binja_mcp_view_id", None)
        id2 = getattr(v2, "_binja_mcp_view_id", None)
        logical = helper.make_logical_view_id("/tmp/roms/shared.bin")
        self.assertIsNotNone(id1)
        self.assertIsNotNone(id2)
        self.assertNotEqual(id1, id2)
        self.assertEqual(getattr(v1, "_binja_mcp_logical_view_id", None), logical)
        self.assertEqual(getattr(v2, "_binja_mcp_logical_view_id", None), logical)
        self.assertIs(ops.get_registered_view_by_id(id1), v1)
        self.assertIs(ops.get_registered_view_by_id(id2), v2)

    def test_register_view_keeps_legacy_id_aliases(self):
        helper, module = self._import_modules()
        ops = module.BinaryOperations(config=object())
        view = _FakeBinaryView("/tmp/roms/legacy.bin", legacy_view_id="0x1234")

        ops.register_view(view)

        expected_view_id = getattr(view, "_binja_mcp_view_id", None)
        self.assertIsNotNone(expected_view_id)
        self.assertIs(ops.get_registered_view_by_id(expected_view_id), view)
        self.assertIs(ops.get_registered_view_by_id("0x1234"), view)


if __name__ == "__main__":
    unittest.main()
