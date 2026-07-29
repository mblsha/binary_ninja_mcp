import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock, patch

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.platform import get_platform_adapter  # noqa: E402

SCRIPT_PATH = REPO_ROOT / "scripts" / "binja-cli.py"
SPEC = importlib.util.spec_from_file_location("binja_cli_platform_unit", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
binja_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(binja_cli)


def test_macos_launch_uses_launchservices_for_app_bundle():
    adapter = get_platform_adapter("darwin")

    command = adapter.build_launch_command(
        binary_path="/Users/test/Applications/Binary Ninja.app/Contents/MacOS/binaryninja",
        filepath="/tmp/target.bndb",
        log_path="/tmp/binja.log",
        env={"QT_QPA_PLATFORM": "cocoa"},
    )

    assert command == [
        "/usr/bin/open",
        "-n",
        "-o",
        "/tmp/binja.log",
        "--stderr",
        "/tmp/binja.log",
        "--env",
        "QT_QPA_PLATFORM=cocoa",
        "/Users/test/Applications/Binary Ninja.app",
        "--args",
        "-e",
        "/tmp/target.bndb",
    ]


def test_macos_launch_falls_back_to_direct_for_non_bundle_binary():
    adapter = get_platform_adapter("darwin")

    command = adapter.build_launch_command(
        binary_path="/opt/binaryninja/binaryninja",
        filepath="/tmp/target.bndb",
    )

    assert command == [
        "/opt/binaryninja/binaryninja",
        "-e",
        "/tmp/target.bndb",
    ]


def test_linux_launch_remains_direct():
    adapter = get_platform_adapter("linux")

    command = adapter.build_launch_command(
        binary_path="/opt/binaryninja/binaryninja",
        filepath="/tmp/target.bndb",
        log_path="/tmp/binja.log",
        env={"DISPLAY": ":1"},
    )

    assert command == [
        "/opt/binaryninja/binaryninja",
        "-e",
        "/tmp/target.bndb",
    ]


def test_launchservices_missing_binary_pid_is_a_launch_failure(tmp_path: Path, monkeypatch):
    app = object.__new__(binja_cli.BinaryNinjaCLI)
    app.verbose = False
    adapter = Mock()
    adapter.supports_auto_launch.return_value = True
    adapter.prepare_gui_env.return_value = {}
    adapter.build_launch_command.return_value = [
        "/usr/bin/open",
        "-n",
        "/Applications/Binary Ninja.app",
    ]
    launcher = Mock(pid=1234)
    launcher.wait.return_value = 0
    log_path = tmp_path / "launch.log"
    monkeypatch.setenv("BINJA_LAUNCH_LOG_PATH", str(log_path))
    monkeypatch.delenv("BINJA_FORCE_RESTART_ON_OPEN", raising=False)

    with (
        patch.object(app, "_platform_adapter", return_value=adapter),
        patch.object(
            app,
            "_resolve_binary_path",
            return_value="/Applications/Binary Ninja.app/Contents/MacOS/binaryninja",
        ),
        patch.object(app, "_find_running_binja_pids", return_value=[]),
        patch.object(app, "_kill_existing_binja_processes") as kill_existing,
        patch.object(app, "_wait_for_new_binja_pid", return_value=0),
        patch.object(binja_cli.subprocess, "Popen", return_value=launcher),
    ):
        result = app._launch_binary_ninja()

    assert result["ok"] is False
    assert "application process was not observed" in result["error"]
    assert result["launch_method"] == "launchservices"
    kill_existing.assert_not_called()
