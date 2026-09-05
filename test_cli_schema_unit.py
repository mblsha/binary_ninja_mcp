import json
from unittest.mock import patch

from binja_cli.cli import BinaryNinjaCLI
from binja_cli.schema import command_schema
from shared.build_info import (
    CAPABILITY_PROTOCOL_VERSION,
    REQUIRED_CAPABILITIES,
    assess_compatibility,
    snapshot_source,
    source_diagnostics,
)


def test_scoped_schema_is_offline_and_matches_parser_defaults(capsys):
    with patch.object(BinaryNinjaCLI, "_request", side_effect=AssertionError("must be offline")):
        _, code = BinaryNinjaCLI.run(["binja-cli", "schema", "signature"], exit=False)
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["scope"] == ["signature"]
    assert len(result["commands"]) == 1
    switches = {option["names"][0]: option for option in result["commands"][0]["arguments"]}
    assert switches["--analysis-timeout"]["default"] == 1800.0
    assert switches["--preview"]["takes_value"] is False
    assert result["commands"][0]["positionals"][0]["required"]


def test_nested_schema_scope_and_output_file(tmp_path, capsys):
    path = tmp_path / "schema.json"
    _, code = BinaryNinjaCLI.run(
        ["binja-cli", "schema", "rename", "function", "--out", str(path)], exit=False
    )
    assert code == 0
    result = json.loads(path.read_text())
    assert result["commands"][0]["path"] == ["rename", "function"]
    assert json.loads(capsys.readouterr().out)["artifact_path"] == str(path)


def test_unknown_schema_path_gives_context(capsys):
    _, code = BinaryNinjaCLI.run(["binja-cli", "schema", "rename", "bad"], exit=False)
    assert code == 2
    assert "available: data, function" in capsys.readouterr().err


def test_schema_includes_python_alias_and_source_arguments():
    result = command_schema(BinaryNinjaCLI, ["py"])
    names = {name for option in result["commands"][0]["arguments"] for name in option["names"]}
    assert {"--code", "--script", "-c", "--no-syntax-check"} <= names
    common = {name for option in result["common_arguments"] for name in option["names"]}
    assert {"--json", "--format", "--out", "--no-spill"} <= common
    fmt = next(option for option in result["common_arguments"] if "--format" in option["names"])
    assert fmt["choices"] == ["text", "json", "ndjson"]
    source = next(
        option for option in result["commands"][0]["arguments"] if "--script" in option["names"]
    )
    assert source["type"] == "ExistingFile"


def test_root_schema_defines_common_arguments_only_once():
    result = command_schema(BinaryNinjaCLI)
    assert result["commands"][0]["arguments"] == []
    assert result["common_arguments"]


def test_analysis_command_schema_includes_selection_and_budget_arguments():
    bundle = command_schema(BinaryNinjaCLI, ["bundle"])["commands"][0]
    switches = {name: option for option in bundle["arguments"] for name in option["names"]}
    assert switches["--time-budget"]["default"] == 30.0
    assert "--include" in switches
    assert bundle["positionals"][0]["required"]
    assert bundle["positionals"][1]["variadic"]
    disasm = command_schema(BinaryNinjaCLI, ["disasm"])["commands"][0]
    names = {name for option in disasm["arguments"] for name in option["names"]}
    assert {"--count", "-n", "--end", "--arch"} <= names


def test_runtime_diagnostics_compare_import_snapshot_with_disk(tmp_path):
    path = tmp_path / "server.py"
    path.write_text("before")
    snapshot = snapshot_source(path)
    first = source_diagnostics({"server": snapshot})
    assert first["reload_required"] is False
    path.write_text("after")
    changed = source_diagnostics({"server": snapshot})
    assert changed["reload_required"] is True
    assert changed["changed_modules"] == ["server"]
    assert changed["build_id"] == first["build_id"]  # loaded build remains the same
    assert (
        changed["sources"]["server"]["loaded_sha256"] != changed["sources"]["server"]["disk_sha256"]
    )


def test_unverifiable_sources_are_not_claimed_current():
    result = source_diagnostics({"legacy": None})
    assert result["unverifiable_modules"] == ["legacy"]


def test_doctor_reports_legacy_and_stale_companions(capsys):
    servers = [
        {"instance_id": "legacy"},
        {
            "instance_id": "stale",
            "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
            "capabilities": REQUIRED_CAPABILITIES,
            "runtime": {"reload_required": True},
        },
    ]
    with patch.object(BinaryNinjaCLI, "_discover_servers", return_value=servers):
        _, code = BinaryNinjaCLI.run(["binja-cli", "doctor"], exit=False)
    assert code == 1
    result = json.loads(capsys.readouterr().out)
    assert len(result["instances"]) == 2
    assert "predates" in result["instances"][0]["compatibility"]["warnings"][0]
    assert "listener is insufficient" in result["instances"][1]["compatibility"]["warnings"][0]


def test_compatible_doctor_and_explicit_text_format(capsys):
    metadata = {
        "instance_id": "current",
        "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
        "capabilities": REQUIRED_CAPABILITIES,
    }
    assert assess_compatibility(metadata)["compatible"]
    with patch.object(BinaryNinjaCLI, "_discover_servers", return_value=[metadata]):
        _, code = BinaryNinjaCLI.run(["binja-cli", "doctor", "--format", "text"], exit=False)
    assert code == 0
    assert "current" in capsys.readouterr().out
