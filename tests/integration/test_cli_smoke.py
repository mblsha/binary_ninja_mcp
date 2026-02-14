from __future__ import annotations

import json
import subprocess

import pytest


pytestmark = pytest.mark.binja


def _run_cli(args: list[str]) -> dict:
    cmd = ["uv", "run", "python", "scripts/binja-cli.py", "--json", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"CLI failed: {' '.join(cmd)}\n{proc.stderr}\n{proc.stdout}"
    return json.loads(proc.stdout)


def test_cli_status_smoke(binja_process):
    out = _run_cli(["status"])
    assert "loaded" in out
    assert "_endpoint" in out


def test_cli_python_smoke(binja_process):
    out = _run_cli(["python", "1 + 1"])
    assert out.get("success") is True
    assert out.get("return_value") == 2


def test_cli_ui_commands_smoke(fixture_binary_path, binja_process):
    open_out = _run_cli(["open", fixture_binary_path, "--inspect-only"])
    assert "open_result" in open_out
    assert open_out["open_result"].get("endpoint") == "/ui/open"
    assert open_out["open_result"].get("schema_version") == 1

    statusbar_out = _run_cli(["statusbar"])
    assert "statusbar_result" in statusbar_out
    assert statusbar_out["statusbar_result"].get("endpoint") == "/ui/statusbar"
    assert statusbar_out["statusbar_result"].get("schema_version") == 1

    quit_out = _run_cli(["quit", "--inspect-only"])
    assert "quit_result" in quit_out
    assert quit_out["quit_result"].get("endpoint") == "/ui/quit"
    assert quit_out["quit_result"].get("schema_version") == 1
