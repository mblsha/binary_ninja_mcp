#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parent / "scripts" / "binja-cli.py"
SPEC = importlib.util.spec_from_file_location("binja_cli_annotations_unit", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
binja_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(binja_cli)


def test_annotations_export_builds_endpoint_payload(capsys):
    argv = [
        "binja-mcp",
        "--json",
        "annotations",
        "export",
        "/tmp/example.annotations.json",
        "--type-library",
        "/tmp/example.types.bntl",
        "--source-id",
        "sha256:example",
        "--force",
        "--include-unannotated-function-types",
    ]
    response = {
        "success": True,
        "json": {"path": "/tmp/example.annotations.json"},
        "type_library": {"path": "/tmp/example.types.bntl"},
        "counts": {},
        "_api_version": 1,
    }

    with (
        patch.object(binja_cli.sys, "argv", argv),
        patch.object(
            binja_cli.BinaryNinjaCLI,
            "_request",
            return_value=response,
        ) as request,
    ):
        _instance, return_code = binja_cli.BinaryNinjaCLI.run(argv, exit=False)

    assert return_code == 0
    request.assert_called_once_with(
        "POST",
        "annotations/export",
        data={
            "output_path": "/tmp/example.annotations.json",
            "type_library_path": "/tmp/example.types.bntl",
            "source_id": "sha256:example",
            "overwrite": True,
            "include_unannotated_function_types": True,
        },
    )
    assert '"success": true' in capsys.readouterr().out
