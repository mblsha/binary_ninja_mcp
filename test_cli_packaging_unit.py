"""Source and installed entry points must use one implementation, without BN."""

import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).parent


def test_project_exposes_installed_cli_and_compatibility_alias():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["scripts"] == {
        "binja-cli": "binja_cli.cli:main",
        "binja-mcp": "binja_cli.cli:main",
    }


def test_module_and_source_wrapper_help_agree():
    module = subprocess.run(
        [sys.executable, "-m", "binja_cli", "--help"], capture_output=True, text=True, check=True
    )
    wrapper = subprocess.run(
        [sys.executable, str(ROOT / "scripts/binja-cli.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert module.stdout == wrapper.stdout
    assert "binja-cli 0.2.8" in module.stdout


def test_client_import_does_not_load_binary_ninja():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import binja_cli.cli; assert 'binaryninja' not in sys.modules; assert 'plugin' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
