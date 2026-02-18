#!/usr/bin/env python3
"""Unit tests for server-side BinaryView/UI synchronization helpers."""

import importlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


THIS_DIR = Path(__file__).resolve().parent
SERVER_DIR = THIS_DIR / "plugin" / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

view_sync = importlib.import_module("view_sync")


class _FakeFile:
    def __init__(self, filename: str):
        self.filename = filename


class _FakeView:
    def __init__(self, filename: str):
        self.file = _FakeFile(filename)


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
