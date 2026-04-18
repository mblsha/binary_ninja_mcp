#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPT_PATH = Path(__file__).resolve().parent / "scripts" / "binja-restart.py"
SPEC = importlib.util.spec_from_file_location("binja_restart_script", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
binja_restart = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(binja_restart)


def _new_app(*, prefer_raw: bool = False):
    app = object.__new__(binja_restart.BinaryNinjaAdvancedController)
    app.prefer_raw = prefer_raw
    app.verbose = False
    app.log = Mock()
    return app


def test_handle_open_existing_database_prompt_clicks_yes_by_default():
    app = _new_app()
    completed = subprocess.CompletedProcess(
        args=["osascript"],
        returncode=0,
        stdout="clicked:Yes\n",
        stderr="",
    )

    with patch.object(binja_restart.subprocess, "run", return_value=completed) as run_mock:
        result = app._handle_open_existing_database_prompt()

    assert result is True
    command = run_mock.call_args.args[0]
    assert command[:2] == ["osascript", "-e"]
    script = command[2]
    assert 'promptText does not contain "Open existing database"' in script
    assert 'checkbox "Remember for next time"' in script
    assert 'button "Yes" of group 1 of promptSheet' in script
    app.log.assert_called_once_with("Resolved 'Open existing database?' prompt via 'Yes'")


def test_handle_open_existing_database_prompt_clicks_no_when_prefer_raw():
    app = _new_app(prefer_raw=True)
    completed = subprocess.CompletedProcess(
        args=["osascript"],
        returncode=0,
        stdout="clicked:No\n",
        stderr="",
    )

    with patch.object(binja_restart.subprocess, "run", return_value=completed) as run_mock:
        result = app._handle_open_existing_database_prompt()

    assert result is True
    script = run_mock.call_args.args[0][2]
    assert 'button "No" of group 1 of promptSheet' in script
    app.log.assert_called_once_with("Resolved 'Open existing database?' prompt via 'No'")


def test_handle_open_existing_database_prompt_logs_unexpected_outcome():
    app = _new_app()
    completed = subprocess.CompletedProcess(
        args=["osascript"],
        returncode=0,
        stdout="missing-button:Yes\n",
        stderr="",
    )

    with patch.object(binja_restart.subprocess, "run", return_value=completed):
        result = app._handle_open_existing_database_prompt()

    assert result is False
    app.log.assert_called_once_with("Database prompt check result: missing-button:Yes")


def test_handle_open_existing_database_prompt_handles_timeout():
    app = _new_app()

    with patch.object(
        binja_restart.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(cmd=["osascript"], timeout=2),
    ):
        result = app._handle_open_existing_database_prompt()

    assert result is False
    app.log.assert_called_once_with(
        "Timed out while checking for 'Open existing database?' prompt",
        "WARNING",
    )
