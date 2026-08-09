#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parent / "scripts" / "binja-cli.py"
SPEC = importlib.util.spec_from_file_location("binja_cli_signature_unit", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
binja_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(binja_cli)


DECLARATION = """int32_t __convention(\"default\")
`EGiridaOTankFamily_6ba10::state_giridao_cannon_6c562`(
    struct EGiridaOTankFamily_6ba10* this_ @ a6
);
"""


def _signature_response(*, dry_run: bool = False) -> dict:
    return {
        "success": True,
        "dry_run": dry_run,
        "function_start": "0x6c562",
        "before_name": "EGiridaOTankFamily_6ba10::state_giridao_cannon_6c562",
        "after_name": "EGiridaOTankFamily_6ba10::state_giridao_cannon_6c562",
        "before_type": "int32_t(struct WrongType * this_ @ a6)",
        "after_type": "int32_t(struct EGiridaOTankFamily_6ba10 * this_ @ a6)",
        "verified": None if dry_run else True,
    }


def test_signature_reads_literal_backticks_from_stdin(capsys):
    argv = [
        "binja-mcp",
        "--json",
        "--no-auto-errors",
        "signature",
        "0x6c562",
        "--stdin",
    ]
    with (
        patch.object(binja_cli.sys, "argv", argv),
        patch.object(binja_cli.sys, "stdin", io.StringIO(DECLARATION)),
        patch.object(
            binja_cli.BinaryNinjaCLI,
            "_request",
            return_value=_signature_response(),
        ) as request,
    ):
        _instance, return_code = binja_cli.BinaryNinjaCLI.run(argv, exit=False)

    assert return_code == 0
    request.assert_called_once_with(
        "POST",
        "function/signature",
        data={
            "function": "0x6c562",
            "signature": DECLARATION.strip(),
            "apply_name": False,
            "dry_run": False,
            "reanalyze": True,
            "wait": True,
            "verify": True,
        },
        timeout=1800.0,
    )
    assert "`EGiridaOTankFamily_6ba10::state_giridao_cannon_6c562`" in DECLARATION
    assert '"verified": true' in capsys.readouterr().out


def test_signature_reads_file_and_supports_dry_run(tmp_path):
    declaration_path = tmp_path / "declaration.c"
    declaration_path.write_text(DECLARATION, encoding="utf-8")
    argv = [
        "binja-mcp",
        "--json",
        "--no-auto-errors",
        "signature",
        "0x6c562",
        "--file",
        str(declaration_path),
        "--dry-run",
        "--apply-name",
    ]
    with (
        patch.object(binja_cli.sys, "argv", argv),
        patch.object(
            binja_cli.BinaryNinjaCLI,
            "_request",
            return_value=_signature_response(dry_run=True),
        ) as request,
    ):
        _instance, return_code = binja_cli.BinaryNinjaCLI.run(argv, exit=False)

    assert return_code == 0
    payload = request.call_args.kwargs["data"]
    assert payload["signature"] == DECLARATION.strip()
    assert payload["dry_run"] is True
    assert payload["apply_name"] is True
    assert request.call_args.kwargs["timeout"] == 120.0


def test_signature_no_wait_disables_verification():
    argv = [
        "binja-mcp",
        "--json",
        "--no-auto-errors",
        "signature",
        "0x6c562",
        "--no-wait",
        "void probe(void);",
    ]
    with (
        patch.object(binja_cli.sys, "argv", argv),
        patch.object(
            binja_cli.BinaryNinjaCLI,
            "_request",
            return_value=_signature_response(),
        ) as request,
    ):
        _instance, return_code = binja_cli.BinaryNinjaCLI.run(argv, exit=False)

    assert return_code == 0
    payload = request.call_args.kwargs["data"]
    assert payload["wait"] is False
    assert payload["verify"] is False
    assert request.call_args.kwargs["timeout"] == 120.0


def test_signature_returns_failure_for_unverified_response():
    argv = [
        "binja-mcp",
        "--json",
        "--no-auto-errors",
        "signature",
        "0x6c562",
        "void probe(void);",
    ]
    response = {
        "success": False,
        "error": "Function signature readback did not match the requested declaration.",
    }
    with (
        patch.object(binja_cli.sys, "argv", argv),
        patch.object(binja_cli.BinaryNinjaCLI, "_request", return_value=response),
    ):
        _instance, return_code = binja_cli.BinaryNinjaCLI.run(argv, exit=False)

    assert return_code == 1


def test_reanalyze_waits_by_default():
    argv = [
        "binja-mcp",
        "--json",
        "--no-auto-errors",
        "reanalyze",
        "0x6c562",
    ]
    response = {"success": True, "message": "Function reanalysis completed."}
    with (
        patch.object(binja_cli.sys, "argv", argv),
        patch.object(binja_cli.BinaryNinjaCLI, "_request", return_value=response) as request,
    ):
        _instance, return_code = binja_cli.BinaryNinjaCLI.run(argv, exit=False)

    assert return_code == 0
    request.assert_called_once_with(
        "POST",
        "function/reanalyze",
        data={"function": "0x6c562", "wait": True},
        timeout=1800.0,
    )
