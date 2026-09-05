import io
from unittest.mock import patch

import pytest

from binja_cli import cli as client


def run(args):
    with patch.object(client.BinaryNinjaCLI, "_request", return_value={"success": True}) as request:
        _, code = client.BinaryNinjaCLI.run(
            ["binja-cli", "--no-auto-errors", "py", *args], exit=False
        )
    return code, request


def test_explicit_code_never_probes_path():
    with patch.object(client.Path, "exists", side_effect=AssertionError("no path probe")):
        code, request = run(["--code", "value = 3"])
    assert code == 0
    assert request.call_args.kwargs["data"]["command"] == "value = 3"


def test_script_alias_reads_source(tmp_path):
    script = tmp_path / "probe.py"
    script.write_text("value = 4")
    code, request = run(["--script", str(script)])
    assert code == 0
    assert request.call_args.kwargs["data"]["command"] == "value = 4"


def test_automatic_stdin_is_preserved():
    with patch.object(client.sys, "stdin", io.StringIO("value = 5")):
        code, request = run([])
    assert code == 0
    assert request.call_args.kwargs["data"]["command"] == "value = 5"


@pytest.mark.parametrize(
    "args",
    [
        ["--code", "x ="],
        ["--code", "return 1"],
        ["--code", "x = 1", "--stdin"],
        ["--interactive", "--code", "x = 1"],
        ["--complete", "bv", "--code", "x = 1"],
        ["--exec-timeout", "inf", "1"],
    ],
)
def test_invalid_source_or_mode_is_rejected_before_request(args, capsys):
    code, request = run(args)
    assert code == 2
    request.assert_not_called()
    assert capsys.readouterr().err


def test_syntax_check_can_be_deferred_for_newer_embedded_python():
    code, request = run(["--no-syntax-check", "--code", "x ="])
    assert code == 0
    request.assert_called_once()


def test_completion_keeps_short_c():
    code, request = run(["-c", "bv.fun"])
    assert code == 0
    assert request.call_args.args == ("GET", "console/complete")


def test_interactive_prompts_are_not_buffered(capsys):
    with patch.object(client.Python, "_interactive_mode", side_effect=lambda: print("live prompt")):
        code, request = run(["-i"])
    assert code == 0
    request.assert_not_called()
    assert capsys.readouterr().out == "live prompt\n"


def test_python_execution_failure_returns_nonzero(capsys):
    with patch.object(
        client.BinaryNinjaCLI,
        "_request",
        return_value={"success": False, "error": {"type": "ValueError", "message": "failed"}},
    ):
        _, code = client.BinaryNinjaCLI.run(
            ["binja-cli", "--no-auto-errors", "py", "--code", "1"], exit=False
        )
    assert code == 1
