#!/usr/bin/env python3
"""Pure-Python tests for automation helper logic."""

import sys
import tempfile
import types
import unittest
import importlib
from pathlib import Path
from unittest.mock import patch


# Import plugin automation modules without importing plugin/__init__.py.
THIS_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = THIS_DIR / "plugin"
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

quit_app = importlib.import_module("automation.quit_app")
close_tabs = importlib.import_module("automation.close_tabs")
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


class _FakeCloseView:
    def __init__(self, filename, view_id, *, modified=False):
        self.file = type("FakeFile", (), {"filename": filename, "modified": modified})()
        self._binja_mcp_view_id = view_id


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self):
        for callback in list(self.callbacks):
            callback()


class _FakeQTimer:
    queued = []
    active = []
    single_shot_calls = 0

    def __init__(self):
        self.timeout = _FakeSignal()
        self.interval = 0

    @classmethod
    def reset(cls):
        cls.queued = []
        cls.active = []
        cls.single_shot_calls = 0

    @classmethod
    def singleShot(cls, _ms, callback):
        cls.single_shot_calls += 1
        cls.queued.append(callback)

    @classmethod
    def process(cls):
        while cls.queued:
            cls.queued.pop(0)()
        for timer in list(cls.active):
            timer.timeout.emit()

    def setInterval(self, value):
        self.interval = value

    def start(self):
        if self not in self.active:
            self.active.append(self)

    def stop(self):
        if self in self.active:
            self.active.remove(self)


class _FakeButton:
    def __init__(self, label, on_click=None):
        self.label = label
        self.on_click = on_click
        self.clicked = False

    def text(self):
        return self.label

    def isVisible(self):
        return True

    def isEnabled(self):
        return True

    def click(self):
        self.clicked = True
        if self.on_click:
            self.on_click()


class _FakeEnum:
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return f"StandardButton.{self.name}"


class _FakeQDialogButtonBox:
    pass


class _FakeSaveDialog:
    def __init__(self, finish_close, *, title="Analysis database has been modified"):
        self.visible = True
        self.title = title
        self.buttons = [
            _FakeButton("Save", lambda: self._save(finish_close)),
            _FakeButton("Don't Save", lambda: self._dont_save(finish_close)),
            _FakeButton("Cancel", self._cancel),
        ]

    def _save(self, finish_close):
        self.visible = False
        finish_close()

    def _dont_save(self, finish_close):
        self.visible = False
        finish_close()

    def _cancel(self):
        self.visible = False

    def isVisible(self):
        return self.visible

    def windowTitle(self):
        return self.title

    def findChildren(self, cls):
        if cls is _FakeButton:
            return self.buttons
        return []


class _FakeMessageBox(_FakeSaveDialog):
    def __init__(
        self,
        finish_close,
        *,
        title="Analysis database has been modified. Do you want to save changes?",
        button_labels=None,
    ):
        super().__init__(finish_close, title=title)
        labels = button_labels or {
            "Save": "Save",
            "Discard": "Don't Save",
            "Cancel": "Cancel",
        }
        self.buttons = [
            _FakeButton(labels["Save"], lambda: self._save(finish_close)),
            _FakeButton(labels["Discard"], lambda: self._dont_save(finish_close)),
            _FakeButton(labels["Cancel"], self._cancel),
        ]
        self._standards_by_button = {
            self.buttons[0]: _FakeEnum("Save"),
            self.buttons[1]: _FakeEnum("Discard"),
            self.buttons[2]: _FakeEnum("Cancel"),
        }

    def standardButton(self, button):
        return self._standards_by_button.get(button, _FakeEnum("NoButton"))


class _FakeApplication:
    current = None

    def __init__(self):
        self.dialogs = []
        _FakeApplication.current = self

    @classmethod
    def instance(cls):
        return cls.current

    def topLevelWidgets(self):
        return [dialog for dialog in self.dialogs if dialog.isVisible()]

    def processEvents(self):
        _FakeQTimer.process()


class _FakeViewInterface:
    def __init__(self, view):
        self.view = view

    def getData(self):
        return self.view


class _FakeFrame:
    def __init__(self, view):
        self.view = view

    def getCurrentViewInterface(self):
        return _FakeViewInterface(self.view)

    def getCurrentBinaryView(self):
        return self.view


class _FakeTab:
    def __init__(self, view):
        self.view = view


class _FakeContext:
    def __init__(self, app, views):
        self.app = app
        self.tabs = [_FakeTab(view) for view in views]
        self.close_calls = []

    def getTabs(self):
        return list(self.tabs)

    def getViewFrameForTab(self, tab):
        return _FakeFrame(tab.view)

    def closeTab(self, tab):
        self.close_calls.append(tab.view.file.filename)
        if tab.view.file.modified:
            self.app.dialogs.append(_FakeSaveDialog(lambda: self._finish_close(tab)))
        else:
            self._finish_close(tab)

    def _finish_close(self, tab):
        if tab in self.tabs:
            self.tabs.remove(tab)


class _FakeUIContext:
    contexts = []

    @classmethod
    def activeContext(cls):
        return cls.contexts[0] if cls.contexts else None

    @classmethod
    def allContexts(cls):
        return list(cls.contexts)


def _fake_ui_modules(contexts):
    _FakeQTimer.reset()
    _FakeUIContext.contexts = contexts
    pyside = types.ModuleType("PySide6")
    qtcore = types.ModuleType("PySide6.QtCore")
    qtwidgets = types.ModuleType("PySide6.QtWidgets")
    binaryninjaui = types.ModuleType("binaryninjaui")
    qtcore.QTimer = _FakeQTimer
    qtwidgets.QApplication = _FakeApplication
    qtwidgets.QPushButton = _FakeButton
    qtwidgets.QDialogButtonBox = _FakeQDialogButtonBox
    qtwidgets.QMessageBox = _FakeMessageBox
    binaryninjaui.UIContext = _FakeUIContext
    return {
        "PySide6": pyside,
        "PySide6.QtCore": qtcore,
        "PySide6.QtWidgets": qtwidgets,
        "binaryninjaui": binaryninjaui,
    }


class TestCloseTabSelection(unittest.TestCase):
    def test_select_records_by_view_id(self):
        v1 = _FakeCloseView("/tmp/a.bndb", "view-a")
        v2 = _FakeCloseView("/tmp/b.bndb", "view-b")
        records = [
            {"view": v1, "filename": v1.file.filename},
            {"view": v2, "filename": v2.file.filename},
        ]

        selected, warnings = close_tabs._select_records(records, view_id="view-b")

        self.assertEqual(warnings, [])
        self.assertEqual([item["filename"] for item in selected], ["/tmp/b.bndb"])

    def test_select_all_records_with_except_filename(self):
        v1 = _FakeCloseView("/tmp/a.bndb", "view-a")
        v2 = _FakeCloseView("/tmp/b.bndb", "view-b")
        records = [
            {"view": v1, "filename": v1.file.filename},
            {"view": v2, "filename": v2.file.filename},
        ]

        selected, warnings = close_tabs._select_records(
            records,
            all_tabs=True,
            except_filename="b.bndb",
        )

        self.assertEqual(warnings, [])
        self.assertEqual([item["filename"] for item in selected], ["/tmp/a.bndb"])

    def test_close_workflow_without_selector_fails_before_ui_imports(self):
        result = close_tabs.close_tabs_workflow(wait_ms=0)

        self.assertFalse(result["ok"])
        self.assertEqual(result["input"]["wait_ms"], 0)
        self.assertIn("close requires --view-id, --filename, or --all", result["errors"])

    def test_save_dialog_detection_requires_save_prompt_text(self):
        self.assertTrue(
            close_tabs._looks_like_save_prompt(
                "Analysis database has been modified. Do you want to save it before closing?"
            )
        )
        self.assertFalse(close_tabs._looks_like_save_prompt("Software Update Available OK Cancel"))

    def test_qmessagebox_standard_button_enum_is_used_before_text(self):
        app = _FakeApplication()
        dialog = _FakeMessageBox(
            lambda: None,
            button_labels={
                "Save": "Alpha",
                "Discard": "Beta",
                "Cancel": "Gamma",
            },
        )
        app.dialogs.append(dialog)

        with patch.dict(sys.modules, _fake_ui_modules([])):
            dialogs = close_tabs._collect_save_dialogs(app)
            clicked = close_tabs._click_save_dialog(app, "dont-save")

        self.assertEqual(dialogs[0]["buttons"][1]["standard_buttons"], ["Discard"])
        self.assertTrue(clicked)
        self.assertTrue(dialog.buttons[1].clicked)
        self.assertFalse(dialog.visible)

    def test_qt_save_dialog_detection_requires_modified_prompt_text(self):
        app = _FakeApplication()
        app.dialogs.append(_FakeSaveDialog(lambda: None, title="Software Update Available"))

        with patch.dict(sys.modules, _fake_ui_modules([])):
            self.assertEqual(close_tabs._collect_save_dialogs(app), [])

    def test_close_workflow_with_fake_qt_closes_dirty_tab(self):
        app = _FakeApplication()
        dirty = _FakeCloseView("/tmp/dirty.bndb", "view-dirty", modified=True)
        ctx = _FakeContext(app, [dirty])

        with patch.dict(sys.modules, _fake_ui_modules([ctx])):
            result = close_tabs.close_tabs_workflow(
                view_id="view-dirty",
                decision="dont-save",
                wait_ms=100,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(ctx.tabs, [])
        self.assertEqual(ctx.close_calls, ["/tmp/dirty.bndb"])
        self.assertEqual(_FakeQTimer.single_shot_calls, 1)
        self.assertIn("clicked_confirmation_button:dont-save", result["actions"])
        self.assertTrue(
            any(
                action.startswith("close_tab_queued:/tmp/dirty.bndb")
                for action in result["actions"]
            )
        )
        self.assertEqual(result["state"]["tabs_after"], [])

    def test_close_workflow_cancel_keeps_dirty_tab_without_manual_error(self):
        app = _FakeApplication()
        dirty = _FakeCloseView("/tmp/dirty.bndb", "view-dirty", modified=True)
        ctx = _FakeContext(app, [dirty])

        with patch.dict(sys.modules, _fake_ui_modules([ctx])):
            result = close_tabs.close_tabs_workflow(
                view_id="view-dirty",
                decision="cancel",
                wait_ms=100,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual([tab.view for tab in ctx.tabs], [dirty])
        self.assertEqual(ctx.close_calls, ["/tmp/dirty.bndb"])
        self.assertIn("clicked_confirmation_button:cancel", result["actions"])
        self.assertEqual(
            [item["filename"] for item in result["state"]["unclosed_selected_tabs"]],
            ["/tmp/dirty.bndb"],
        )
        self.assertNotIn(close_tabs.MANUAL_CLOSE_RESOLUTION_ERROR, result["errors"])

    def test_close_workflow_explicit_missing_target_fails(self):
        app = _FakeApplication()
        visible = _FakeCloseView("/tmp/visible.bndb", "view-visible", modified=True)
        ctx = _FakeContext(app, [visible])

        with patch.dict(sys.modules, _fake_ui_modules([ctx])):
            result = close_tabs.close_tabs_workflow(
                view_id="view-missing",
                decision="dont-save",
                wait_ms=0,
            )

        self.assertFalse(result["ok"], result)
        self.assertEqual([tab.view for tab in ctx.tabs], [visible])
        self.assertEqual(ctx.close_calls, [])
        self.assertIn("no visible UI tab matched the requested selector", result["errors"])

    def test_close_workflow_reports_manual_resolution_when_tab_stays_open(self):
        app = _FakeApplication()
        dirty = _FakeCloseView("/tmp/dirty.bndb", "view-dirty", modified=True)
        ctx = _FakeContext(app, [dirty])

        with (
            patch.dict(sys.modules, _fake_ui_modules([ctx])),
            patch.object(close_tabs, "_collect_save_dialogs", return_value=[]),
        ):
            result = close_tabs.close_tabs_workflow(
                view_id="view-dirty",
                decision="dont-save",
                wait_ms=0,
            )

        self.assertFalse(result["ok"], result)
        self.assertTrue(result["state"]["stuck_confirmation"])
        self.assertEqual(
            [item["filename"] for item in result["state"]["unclosed_selected_tabs"]],
            ["/tmp/dirty.bndb"],
        )
        self.assertIn(close_tabs.MANUAL_CLOSE_RESOLUTION_ERROR, result["errors"])
        self.assertFalse(
            any(action.startswith("clicked_confirmation_button:") for action in result["actions"]),
            result["actions"],
        )

    def test_close_workflow_missing_except_selector_does_not_close_all(self):
        app = _FakeApplication()
        close_me = _FakeCloseView("/tmp/close-me.bndb", "view-close", modified=True)
        ctx = _FakeContext(app, [close_me])

        with patch.dict(sys.modules, _fake_ui_modules([ctx])):
            result = close_tabs.close_tabs_workflow(
                all_tabs=True,
                except_view_id="view-missing",
                decision="dont-save",
                wait_ms=0,
            )

        self.assertFalse(result["ok"], result)
        self.assertEqual([tab.view for tab in ctx.tabs], [close_me])
        self.assertEqual(ctx.close_calls, [])
        self.assertIn("no visible UI tab matched any exclusion selector", result["errors"])

    def test_close_workflow_auto_policy_is_per_tab(self):
        app = _FakeApplication()
        raw = _FakeCloseView("/tmp/raw.bin", "view-raw", modified=True)
        database = _FakeCloseView("/tmp/db.bndb", "view-db", modified=True)
        ctx = _FakeContext(app, [raw, database])

        with patch.dict(sys.modules, _fake_ui_modules([ctx])):
            result = close_tabs.close_tabs_workflow(
                all_tabs=True,
                decision="auto",
                wait_ms=100,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(ctx.tabs, [])
        self.assertIn("clicked_confirmation_button:dont-save", result["actions"])
        self.assertIn("clicked_confirmation_button:save", result["actions"])
        self.assertEqual(result["policy"]["resolved_decision"], "mixed")
        self.assertEqual(
            [item["resolved_decision"] for item in result["policy"]["selected_tab_decisions"]],
            ["dont-save", "save"],
        )

    def test_close_workflow_with_fake_qt_all_except_keeps_requested_tab(self):
        app = _FakeApplication()
        close_me = _FakeCloseView("/tmp/close-me.bndb", "view-close", modified=True)
        keep_me = _FakeCloseView("/tmp/keep-me.bndb", "view-keep", modified=True)
        ctx = _FakeContext(app, [close_me, keep_me])

        with patch.dict(sys.modules, _fake_ui_modules([ctx])):
            result = close_tabs.close_tabs_workflow(
                all_tabs=True,
                except_view_id="view-keep",
                decision="dont-save",
                wait_ms=100,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual([tab.view for tab in ctx.tabs], [keep_me])
        self.assertEqual(ctx.close_calls, ["/tmp/close-me.bndb"])
        self.assertEqual(
            [item["filename"] for item in result["state"]["tabs_after"]],
            ["/tmp/keep-me.bndb"],
        )


if __name__ == "__main__":
    unittest.main()
