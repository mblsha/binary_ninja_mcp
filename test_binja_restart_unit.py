#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock


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


def test_resolve_launch_target_prefers_existing_database(tmp_path: Path):
    app = _new_app()
    target = tmp_path / "RFIRE.EXE"
    target.write_bytes(b"MZ")
    database = tmp_path / "RFIRE.EXE.bndb"
    database.write_bytes(b"BNDB")

    resolved = app._resolve_launch_target(str(target))

    assert resolved == str(database)
    app.log.assert_called_once_with(f"Using existing database: {database}")


def test_resolve_launch_target_supports_suffix_replaced_database(tmp_path: Path):
    app = _new_app()
    target = tmp_path / "RFIRE.EXE"
    target.write_bytes(b"MZ")
    database = tmp_path / "RFIRE.bndb"
    database.write_bytes(b"BNDB")

    resolved = app._resolve_launch_target(str(target))

    assert resolved == str(database)
    app.log.assert_called_once_with(f"Using existing database: {database}")


def test_resolve_launch_target_prefers_raw_when_requested(tmp_path: Path):
    app = _new_app(prefer_raw=True)
    target = tmp_path / "RFIRE.EXE"
    target.write_bytes(b"MZ")
    database = tmp_path / "RFIRE.EXE.bndb"
    database.write_bytes(b"BNDB")

    resolved = app._resolve_launch_target(str(target))

    assert resolved == str(target)
    app.log.assert_not_called()


def test_resolve_launch_target_returns_original_when_no_database_exists(tmp_path: Path):
    app = _new_app()
    target = tmp_path / "RFIRE.EXE"
    target.write_bytes(b"MZ")

    resolved = app._resolve_launch_target(str(target))

    assert resolved == str(target)
    app.log.assert_not_called()


def test_resolve_launch_target_handles_missing_input():
    app = _new_app()

    resolved = app._resolve_launch_target(None)

    assert resolved is None
    app.log.assert_not_called()
