import importlib.util
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parent
plugin_package = types.ModuleType("plugin")
plugin_package.__path__ = [str(ROOT / "plugin")]
automation_package = types.ModuleType("plugin.automation")
automation_package.__path__ = [str(ROOT / "plugin" / "automation")]
sys.modules.setdefault("plugin", plugin_package)
sys.modules.setdefault("plugin.automation", automation_package)

text_spec = importlib.util.spec_from_file_location(
    "plugin.automation.text",
    ROOT / "plugin" / "automation" / "text.py",
)
assert text_spec is not None and text_spec.loader is not None
text_module = importlib.util.module_from_spec(text_spec)
sys.modules[text_spec.name] = text_module
text_spec.loader.exec_module(text_module)

open_spec = importlib.util.spec_from_file_location(
    "plugin.automation.open_file",
    ROOT / "plugin" / "automation" / "open_file.py",
)
assert open_spec is not None and open_spec.loader is not None
open_module = importlib.util.module_from_spec(open_spec)
sys.modules[open_spec.name] = open_module
open_spec.loader.exec_module(open_module)
_looks_like_existing_database_dialog = open_module._looks_like_existing_database_dialog


class QMessageBox:
    def isVisible(self):
        return True

    def windowTitle(self):
        return ""

    def text(self):
        return "Open existing database?\n"

    def informativeText(self):
        return ""

    def detailedText(self):
        return ""


def test_titleless_existing_database_message_box_is_detected():
    assert _looks_like_existing_database_dialog(QMessageBox())
