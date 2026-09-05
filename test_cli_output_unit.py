"""Output contracts and argument validation must hold before bridge side effects."""

import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from binja_cli import cli as client
from binja_cli.output import OutputOptions, deliver_output, render_value


def deliver(text, options):
    stdout, stderr = io.StringIO(), io.StringIO()
    artifact = deliver_output(text, options, stdout, stderr)
    return stdout.getvalue(), stderr.getvalue(), artifact


def run(args, response=None):
    with patch.object(
        client.BinaryNinjaCLI, "_request", return_value=response or {"success": True}
    ) as request:
        _, code = client.BinaryNinjaCLI.run(["binja-cli", "--no-auto-errors", *args], exit=False)
    return code, request


def test_large_json_is_complete_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("BINJA_CLI_OUTPUT_DIR", str(tmp_path))
    value = {"items": ["x" * 1000] * 100}
    rendered = render_value(value)
    stdout, stderr, artifact = deliver(rendered, OutputOptions(format="json"))
    assert json.loads(stdout) == value
    assert not stderr and artifact is None
    assert list(tmp_path.iterdir()) == []


def test_ndjson_has_one_record_per_top_level_list_item():
    assert render_value([{"a": 1}, {"a": 2}], "ndjson").splitlines() == ['{"a": 1}', '{"a": 2}']
    assert len(render_value({"functions": [1, 2]}, "ndjson").splitlines()) == 1


def test_text_spill_preview_is_bounded_and_artifacts_are_unique(tmp_path, monkeypatch):
    monkeypatch.setenv("BINJA_CLI_OUTPUT_DIR", str(tmp_path))
    text = "line\n" * 100
    options = OutputOptions(spill_bytes=10)
    first, stderr, artifact = deliver(text, options)
    _, _, other = deliver(text, options)
    assert first == "line\n" * 20
    assert "Full output artifact:" in stderr
    assert artifact["artifact_path"] != other["artifact_path"]
    assert Path(artifact["artifact_path"]).read_text() == text
    assert artifact["bytes"] == len(text.encode())


def test_no_spill_text_remains_complete():
    text = "long line\n" * 10000
    stdout, stderr, artifact = deliver(text, OutputOptions(spill=False))
    assert stdout == text and not stderr and artifact is None


def test_structured_spill_is_an_explicit_artifact_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("BINJA_CLI_OUTPUT_DIR", str(tmp_path))
    text = render_value({"values": list(range(200))})
    stdout, stderr, artifact = deliver(
        text, OutputOptions(format="json", spill=True, spill_bytes=10)
    )
    assert json.loads(stdout) == artifact
    assert artifact["spilled"] and not stderr
    assert Path(artifact["artifact_path"]).read_text() == text


def test_out_no_clobber_and_explicit_overwrite(tmp_path):
    path = tmp_path / "result.json"
    options = OutputOptions(format="json", out=str(path))
    options.validate()
    stdout, _, artifact = deliver('{"first":true}\n', options)
    assert json.loads(stdout)["artifact_path"] == str(path)
    assert path.read_text() == '{"first":true}\n'
    with pytest.raises(ValueError, match="Output exists"):
        options.validate()
    # The delivery path independently enforces no-clobber (including races).
    with pytest.raises(FileExistsError):
        deliver("replacement", options)
    assert path.read_text() == '{"first":true}\n'
    options.overwrite = True
    options.validate()
    deliver("replacement", options)
    assert path.read_text() == "replacement"
    assert sorted(tmp_path.iterdir()) == [path]
    assert artifact["spilled"] is False


def test_text_match_context_and_empty_regex():
    text = "zero\none\ntwo\nthree\nfour\nfive\n"
    options = OutputOptions(match="one|five", before=1)
    options.validate()
    stdout, _, _ = deliver(text, options)
    assert stdout == "zero\none\n--\nfour\nfive\n"
    assert deliver(text, OutputOptions(match=""))[0] == text


def test_missing_tokenizer_does_not_lose_output():
    with patch.dict(sys.modules, {"tiktoken": None}):
        stdout, stderr, _ = deliver("all output\n", OutputOptions(tokens=True))
    assert stdout == "all output\n"
    assert "token_count_warning" in stderr


@pytest.mark.parametrize(
    "options",
    [
        ["--match", "["],
        ["--format", "invalid"],
        ["--before", "-1"],
        ["--after", "1"],
        ["--spill", "--no-spill"],
        ["--format", "json", "--match", "x"],
        ["--json", "--format", "ndjson"],
        ["--overwrite-output"],
        ["--request-timeout", "nan"],
    ],
)
def test_invalid_output_arguments_never_send_a_request(options, capsys):
    # Root-only timeout precedes the command; common output options work after it.
    args = (
        [*options, "rename", "function", "old", "new"]
        if "--request-timeout" in options
        else ["rename", "function", "old", "new", *options]
    )
    code, request = run(args)
    assert code == 2
    request.assert_not_called()
    assert "Error:" in capsys.readouterr().err


def test_existing_out_file_refuses_before_mutation(tmp_path, capsys):
    path = tmp_path / "existing"
    path.write_text("keep")
    code, request = run(["signature", "f", "void f(void);", "--out", str(path)])
    assert code == 2
    request.assert_not_called()
    assert path.read_text() == "keep"
    assert "Output exists" in capsys.readouterr().err


def test_json_after_subcommand_and_ndjson_before_subcommand(capsys):
    response = {"functions": ["main", "helper"]}
    code, _ = run(["functions", "--json"], response)
    assert code == 0
    assert json.loads(capsys.readouterr().out) == response
    code, _ = run(["--format", "ndjson", "functions"], response)
    assert code == 0
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1
    assert json.loads(captured.out) == response


def test_common_flags_do_not_reinterpret_python_source(capsys):
    code, request = run(["py", "--code", "--json"])
    assert code == 0
    assert request.call_args.kwargs["data"]["command"] == "--json"
    assert not capsys.readouterr().out.lstrip().startswith("{")


def test_common_flags_respect_double_dash(capsys):
    code, request = run(["py", "--", "--json"])
    assert code == 0
    assert request.call_args.kwargs["data"]["command"] == "--json"
    assert not capsys.readouterr().out.lstrip().startswith("{")


def test_unknown_switch_errors_and_help_go_to_stderr(capsys):
    code, request = run(["functions", "--unknown-switch"])
    assert code == 2
    request.assert_not_called()
    captured = capsys.readouterr()
    assert not captured.out
    assert "Unknown switch" in captured.err and "functions" in captured.err


def test_file_delivery_failure_preserves_result_and_committed_state(capsys):
    response = {"success": True, "committed": True}
    with patch.object(client, "deliver_output", side_effect=OSError("disk full")):
        code, _ = run(["signature", "f", "void f(void);", "--json"], response)
    assert code == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == response
    assert "remain committed" in captured.err


def test_help_ignores_output_delivery_options(tmp_path, capsys):
    path = tmp_path / "not-created"
    code, request = run(["functions", "--out", str(path), "--help"])
    assert code == 0
    request.assert_not_called()
    assert not path.exists()
    assert "Usage:" in capsys.readouterr().out


def test_text_renderers_also_use_file_output(tmp_path, capsys):
    path = tmp_path / "functions.txt"
    code, _ = run(["functions", "--out", str(path)], {"functions": ["main"]})
    assert code == 0
    assert "main" in path.read_text()
    assert json.loads(capsys.readouterr().out)["format"] == "text"


def test_ndjson_artifact_envelope_is_one_record(tmp_path):
    path = tmp_path / "records.ndjson"
    stdout, _, _ = deliver('{"a":1}\n{"a":2}\n', OutputOptions(format="ndjson", out=str(path)))
    assert len(stdout.splitlines()) == 1
    assert json.loads(stdout)["format"] == "ndjson"


def test_empty_output_path_is_rejected_before_request(capsys):
    code, request = run(["functions", "--out", ""])
    assert code == 2
    request.assert_not_called()
    assert "non-empty" in capsys.readouterr().err


def test_invalid_command_arguments_do_not_create_output_artifact(tmp_path, capsys):
    path = tmp_path / "not-created"
    code, request = run(["schema", "missing", "--out", str(path)])
    assert code == 2
    request.assert_not_called()
    assert not path.exists()
    assert not capsys.readouterr().out
