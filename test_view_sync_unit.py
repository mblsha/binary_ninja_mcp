#!/usr/bin/env python3
"""Unit tests for server-side BinaryView/UI synchronization helpers."""

import importlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


THIS_DIR = Path(__file__).resolve().parent
SERVER_DIR = THIS_DIR / "plugin" / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

view_sync = importlib.import_module("view_sync")


class _FakeFile:
    def __init__(self, filename: str):
        self.filename = filename


class _FakeView:
    def __init__(self, filename: str, view_id: str | None = None):
        self.file = _FakeFile(filename)
        if view_id is not None:
            self.view_id = view_id


class _MockAnalysisState:
    def __init__(self, code: int, name: str):
        self._code = int(code)
        self.name = name

    def __int__(self):
        return self._code

    def __str__(self):
        return f"AnalysisState.{self.name}"


class _MockNumericAnalysisState:
    def __init__(self, code: int):
        self._code = int(code)

    def __int__(self):
        return self._code

    def __str__(self):
        return str(self._code)


class _FakeAnalysisStateEnum:
    _names = {
        0: "InitialState",
        1: "HoldState",
        2: "IdleState",
        3: "DiscoveryState",
        4: "DisassembleState",
        5: "AnalyzeState",
        6: "ExtendedAnalyzeState",
    }

    def __init__(self, code: int):
        code_int = int(code)
        if code_int not in self._names:
            raise ValueError(f"unknown code {code_int}")
        self.name = self._names[code_int]


class _FakeViewInterface:
    def __init__(self, view):
        self._view = view

    def getData(self):
        return self._view


class _FakeViewFrame:
    def __init__(self, current_binary_view=None, iface_view=None):
        self._current_binary_view = current_binary_view
        self._iface_view = iface_view

    def getCurrentViewInterface(self):
        return _FakeViewInterface(self._iface_view) if self._iface_view is not None else None

    def getCurrentBinaryView(self):
        return self._current_binary_view


class _FakeContext:
    def __init__(self, current_frame=None, tabs=None):
        self._current_frame = current_frame
        self._tabs = tabs or {}

    def getCurrentViewFrame(self):
        return self._current_frame

    def getTabs(self):
        return list(self._tabs.keys())

    def getViewFrameForTab(self, tab):
        return self._tabs.get(tab)


class _FakeUIContext:
    _active = None
    _contexts = []
    _current = None

    @classmethod
    def activeContext(cls):
        return cls._active

    @classmethod
    def allContexts(cls):
        return list(cls._contexts)

    @classmethod
    def currentBinaryView(cls):
        return cls._current


class TestViewSync(unittest.TestCase):
    def test_get_view_from_frame_prefers_interface_data(self):
        fallback = _FakeView("/tmp/fallback.bin")
        preferred = _FakeView("/tmp/preferred.bin")
        frame = _FakeViewFrame(current_binary_view=fallback, iface_view=preferred)
        got = view_sync.get_view_from_frame(frame)
        self.assertIs(got, preferred)

    def test_filename_matching_supports_basename_and_case(self):
        view = _FakeView("/tmp/Some/File/libBinary.so")
        self.assertTrue(view_sync.matches_requested_filename(view, "libbinary.so"))
        self.assertTrue(view_sync.matches_requested_filename(view, "/tmp/some/file/libBinary.so"))
        self.assertFalse(view_sync.matches_requested_filename(view, "other.so"))

    def test_view_id_matching_accepts_decimal_and_hex_forms(self):
        view = _FakeView("/tmp/rom.bin", view_id="4660")
        self.assertTrue(view_sync.matches_requested_view_id(view, "4660"))
        self.assertTrue(view_sync.matches_requested_view_id(view, "0x1234"))
        self.assertFalse(view_sync.matches_requested_view_id(view, "0x1235"))

    def test_describe_view_extracts_metadata_from_mock_type(self):
        view = _FakeView("/tmp/roms/primary.bin", view_id="202")
        view.view_type = "Mapped"
        view.arch = SimpleNamespace(name="m68000")
        view.analysis_info = SimpleNamespace(state="Idle")

        meta = view_sync.describe_view(view)
        self.assertEqual(meta["view_id"], "202")
        self.assertEqual(meta["filename"], "/tmp/roms/primary.bin")
        self.assertEqual(meta["basename"], "primary.bin")
        self.assertEqual(meta["view_type"], "Mapped")
        self.assertEqual(meta["architecture"], "m68000")
        self.assertEqual(meta["analysis_status"], "Idle")
        self.assertIsNone(meta["analysis_state_code"])
        self.assertEqual(meta["analysis_state_name"], "Idle")

    def test_describe_view_exposes_structured_analysis_state_fields_from_mock_enum(self):
        view = _FakeView("/tmp/roms/primary.bin", view_id="303")
        view.analysis_state = _MockAnalysisState(2, "IdleState")

        meta = view_sync.describe_view(view)

        self.assertEqual(meta["analysis_state_code"], 2)
        self.assertEqual(meta["analysis_state_name"], "IdleState")
        self.assertEqual(meta["analysis_status"], "AnalysisState.IdleState")

    def test_describe_view_derives_state_name_from_numeric_mock_state_code(self):
        view = _FakeView("/tmp/roms/numeric.bin", view_id="404")
        view.analysis_state = _MockNumericAnalysisState(2)

        bn_module = types.ModuleType("binaryninja")
        bn_enums = types.ModuleType("binaryninja.enums")
        bn_enums.AnalysisState = _FakeAnalysisStateEnum
        bn_module.enums = bn_enums

        with patch.dict(sys.modules, {"binaryninja": bn_module, "binaryninja.enums": bn_enums}):
            meta = view_sync.describe_view(view)

        self.assertEqual(meta["analysis_state_code"], 2)
        self.assertEqual(meta["analysis_state_name"], "IdleState")
        self.assertEqual(meta["analysis_status"], "2")

    def test_describe_view_raises_when_numeric_state_present_and_enum_import_missing(self):
        view = _FakeView("/tmp/roms/numeric.bin", view_id="505")
        view.analysis_state = _MockNumericAnalysisState(2)

        with patch.dict(sys.modules, {"binaryninja": None, "binaryninja.enums": None}):
            with self.assertRaises(RuntimeError):
                view_sync.describe_view(view)

    def test_resolve_target_view_prefers_explicit_view_id(self):
        v1 = _FakeView("/tmp/first.bin", view_id="101")
        v2 = _FakeView("/tmp/second.bin", view_id="202")

        by_id = {"101": v1, "202": v2}
        by_name = {"first.bin": v1, "second.bin": v2}
        selected, error = view_sync.resolve_target_view(
            "202",
            None,
            get_view_by_id=lambda raw: by_id.get(raw),
            get_view_by_filename=lambda raw: by_name.get(raw),
            fallback_view=v1,
        )

        self.assertIsNone(error)
        self.assertIs(selected, v2)

    def test_resolve_target_view_reports_conflicting_targets(self):
        v1 = _FakeView("/tmp/first.bin", view_id="101")
        v2 = _FakeView("/tmp/second.bin", view_id="202")
        selected, error = view_sync.resolve_target_view(
            "202",
            "first.bin",
            get_view_by_id=lambda raw: v2 if raw == "202" else None,
            get_view_by_filename=lambda raw: v1 if raw == "first.bin" else None,
            fallback_view=v1,
        )

        self.assertIsNone(selected)
        self.assertEqual(error.get("error"), "Conflicting BinaryView targets")

    def test_resolve_target_view_reports_missing_view_id(self):
        selected, error = view_sync.resolve_target_view(
            "404",
            None,
            get_view_by_id=lambda _: None,
            get_view_by_filename=lambda _: None,
            fallback_view=None,
        )
        self.assertIsNone(selected)
        self.assertEqual(error.get("error"), "Requested BinaryView not found")

    def test_list_ui_views_active_context_first_and_deduped(self):
        v_active = _FakeView("/tmp/active.bin")
        v_other = _FakeView("/tmp/other.bin")
        v_tab = _FakeView("/tmp/tab.bin")

        active_ctx = _FakeContext(
            current_frame=_FakeViewFrame(current_binary_view=v_active),
            tabs={"a": _FakeViewFrame(current_binary_view=v_active)},
        )
        other_ctx = _FakeContext(
            current_frame=_FakeViewFrame(current_binary_view=v_other),
            tabs={"b": _FakeViewFrame(current_binary_view=v_tab)},
        )

        _FakeUIContext._active = active_ctx
        _FakeUIContext._contexts = [other_ctx, active_ctx]
        _FakeUIContext._current = v_other

        fake_bnui = SimpleNamespace(UIContext=_FakeUIContext)
        views = view_sync.list_ui_views(fake_bnui)

        self.assertGreaterEqual(len(views), 3)
        self.assertIs(views[0], v_active)
        # Must include both non-duplicate views.
        self.assertIn(v_other, views)
        self.assertIn(v_tab, views)
        # No duplicates by object identity.
        self.assertEqual(len(views), len({id(v) for v in views}))

    def test_select_preferred_view_uses_filename_match(self):
        v1 = _FakeView("/tmp/first.bin")
        v2 = _FakeView("/tmp/target.bin")
        chosen = view_sync.select_preferred_view([v1, v2], requested_filename="target.bin")
        self.assertIs(chosen, v2)

    def test_select_preferred_view_prioritizes_view_id(self):
        v1 = _FakeView("/tmp/first.bin", view_id="101")
        v2 = _FakeView("/tmp/target.bin", view_id="202")
        chosen = view_sync.select_preferred_view(
            [v1, v2],
            requested_filename="first.bin",
            requested_view_id="0xca",
        )
        self.assertIs(chosen, v2)

    def test_select_preferred_view_prioritizes_exact_path_over_basename(self):
        v1 = _FakeView("/tmp/a/app.bin")
        v2 = _FakeView("/tmp/b/app.bin")
        chosen = view_sync.select_preferred_view([v1, v2], requested_filename="/tmp/b/app.bin")
        self.assertIs(chosen, v2)

    def test_select_preferred_view_falls_back_to_first(self):
        v1 = _FakeView("/tmp/first.bin")
        v2 = _FakeView("/tmp/second.bin")
        chosen = view_sync.select_preferred_view([v1, v2], requested_filename="missing.bin")
        self.assertIs(chosen, v1)


if __name__ == "__main__":
    unittest.main()
