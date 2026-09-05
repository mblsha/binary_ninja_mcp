"""Keep current user-facing examples and distribution metadata executable."""

import json
from pathlib import Path
import re
import shlex
import tomllib
from unittest.mock import patch

import pytest

from binja_cli.cli import BinaryNinjaCLI
from shared.build_info import TOOL_VERSION


ROOT = Path(__file__).resolve().parent
GUIDES = ("README.md", "CLI_README.md", "CLAUDE.md", "AGENTS.md", "docs/PYTHON_CLI_GUIDE.md")


def test_package_plugin_and_client_versions_match_and_fork_urls_are_current():
    metadata = json.loads((ROOT / "plugin.json").read_text())
    package = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert metadata["version"] == package["project"]["version"] == TOOL_VERSION
    assert metadata["author"] == "fosdick.io"
    assert metadata["license"]["name"] == "GPL-3.0"
    for instruction in metadata["installinstructions"].values():
        assert "github.com/mblsha/binary_ninja_mcp" in instruction
    assert "github.com/mblsha/binary_ninja_mcp" in metadata["longdescription"]


@pytest.mark.parametrize("guide", GUIDES)
def test_current_guides_have_balanced_fences_and_no_obsolete_cli_or_test_claims(guide):
    text = (ROOT / guide).read_text()
    assert sum(line.startswith("```") for line in text.splitlines()) % 2 == 0
    assert "./cli.py" not in text
    assert "No test suite exists" not in text
    assert "No unit tests exist" not in text
    assert "No linting configuration" not in text
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if "://" not in target and not target.startswith("#"):
            assert ((ROOT / guide).parent / target.split("#")[0]).exists(), target


@pytest.mark.parametrize(
    "command",
    [
        "functions --search crypt --limit 50",
        "info main --locals",
        "disasm 'main+0x10' --count 24",
        "il main --level mlil --ssa",
        "read 0x1000 --type u32 --count 8 --endian little",
        "bundle main helper --include decompile,comments,xrefs",
        "search text malloc --within main",
        "search constant 42 --within main --time-budget 5",
        "callsites malloc --context 3",
        "locals list main",
        "locals rename main VARIABLE_ID input --preview",
        "locals retype main VARIABLE_ID 'uint32_t *' --preview",
        "struct show Packet",
        "struct field set Packet 0x10 count uint32_t --preview",
        "struct field rename Packet count item_count --preview",
        "struct field delete Packet item_count --preview",
        "xrefs Packet.count --field",
        "refs-from main",
    ],
)
def test_documented_analysis_arguments_parse_and_reach_the_request(command, capsys):
    # Empty successful responses are sufficient to check actual parser dispatch,
    # including nested positional arguments, option placement, and short aliases.
    with patch.object(BinaryNinjaCLI, "_request", return_value={"success": True}) as request:
        _, code = BinaryNinjaCLI.run(
            ["binja-cli", "--view-id", "INSTANCE:VIEW", *shlex.split(command), "--json"],
            exit=False,
        )
    assert code == 0 and request.called
    capsys.readouterr()
