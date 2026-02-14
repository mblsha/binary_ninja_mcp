from __future__ import annotations

import json
import subprocess

import pytest


pytestmark = pytest.mark.binja


def _run_cli(args: list[str], base_url: str) -> dict:
    cmd = [
        "uv",
        "run",
        "python",
        "scripts/binja-cli.py",
        "--server",
        base_url,
        "--json",
        *args,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"CLI failed: {' '.join(cmd)}\n{proc.stderr}\n{proc.stdout}"
    return json.loads(proc.stdout)


def test_cli_status_smoke(base_url, binja_process):
    out = _run_cli(["status"], base_url)
    assert "loaded" in out
    assert "_endpoint" in out


def test_cli_python_smoke(base_url, binja_process):
    out = _run_cli(["python", "1 + 1"], base_url)
    assert out.get("success") is True
    assert out.get("return_value") == 2


def test_cli_analysis_commands_smoke(base_url, analysis_context, binja_process):
    function_name = analysis_context["function_name"]
    function_prefix = analysis_context["function_prefix"]

    functions_out = _run_cli(["functions", "--limit", "5"], base_url)
    assert isinstance(functions_out.get("functions"), list)

    search_out = _run_cli(["functions", "--search", function_prefix, "--limit", "5"], base_url)
    assert isinstance(search_out.get("matches"), list)

    decompile_out = _run_cli(["decompile", function_name], base_url)
    assert "decompiled" in decompile_out

    assembly_out = _run_cli(["assembly", function_name], base_url)
    assert "assembly" in assembly_out

    refs_out = _run_cli(["refs", function_name], base_url)
    assert "code_references" in refs_out

    imports_out = _run_cli(["imports", "--limit", "5"], base_url)
    assert isinstance(imports_out.get("imports"), list)

    exports_out = _run_cli(["exports", "--limit", "5"], base_url)
    assert isinstance(exports_out.get("exports"), list)


def test_cli_mutation_and_logs_smoke(base_url, analysis_context, binja_process):
    function_name = analysis_context["function_name"]
    entry_address = analysis_context["entry_address"]
    comment_text = "mcp-cli-comment"
    func_comment = "mcp-cli-function-comment"

    set_comment = _run_cli(["comment", entry_address, comment_text], base_url)
    assert set_comment.get("success") is True

    get_comment = _run_cli(["comment", entry_address], base_url)
    assert get_comment.get("comment") == comment_text

    set_func_comment = _run_cli(
        ["comment", "--function", function_name, func_comment],
        base_url,
    )
    assert set_func_comment.get("success") is True

    get_func_comment = _run_cli(["comment", "--function", function_name], base_url)
    assert get_func_comment.get("comment") == func_comment

    logs_stats = _run_cli(["logs", "--stats"], base_url)
    assert "total_logs" in logs_stats
    assert isinstance(logs_stats.get("levels"), dict)

    completions = _run_cli(["python", "--complete", "bv."], base_url)
    assert isinstance(completions.get("completions"), list)


def test_cli_ui_commands_smoke(base_url, analysis_context, binja_process):
    fixture_binary_path = analysis_context["fixture_binary_path"]

    open_out = _run_cli(["open", fixture_binary_path, "--view-type", "Raw"], base_url)
    assert "open_result" in open_out
    assert open_out["open_result"].get("endpoint") == "/ui/open"
    assert open_out["open_result"].get("schema_version") == 1

    statusbar_out = _run_cli(["statusbar"], base_url)
    assert "statusbar_result" in statusbar_out
    assert statusbar_out["statusbar_result"].get("endpoint") == "/ui/statusbar"
    assert statusbar_out["statusbar_result"].get("schema_version") == 1

    quit_out = _run_cli(["quit", "--inspect-only"], base_url)
    assert "quit_result" in quit_out
    assert quit_out["quit_result"].get("endpoint") == "/ui/quit"
    assert quit_out["quit_result"].get("schema_version") == 1
