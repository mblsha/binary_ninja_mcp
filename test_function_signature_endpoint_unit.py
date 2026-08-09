#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parent / "plugin" / "api" / "endpoints.py"


def _load_module():
    binaryninja = types.ModuleType("binaryninja")
    binaryninja.FunctionUpdateType = types.SimpleNamespace(UserFunctionUpdate="user-update")

    archive = types.ModuleType("plugin.core.annotation_archive")
    archive.export_user_annotations = lambda *_args, **_kwargs: {}
    operations = types.ModuleType("plugin.core.binary_operations")
    operations.BinaryOperations = object

    spec = importlib.util.spec_from_file_location(
        "plugin.api.endpoints_signature_unit_target",
        MODULE_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "binaryninja": binaryninja,
            "plugin.core.annotation_archive": archive,
            "plugin.core.binary_operations": operations,
        },
    ):
        spec.loader.exec_module(module)
    return module


endpoints_module = _load_module()


class _FakeType:
    def __init__(self, text: str):
        self.text = text

    def __str__(self) -> str:
        return self.text


class _FakeFunction:
    def __init__(self, *, analysis_skipped: bool = False):
        self.start = 0x6C562
        self.platform = object()
        self.name = "EGiridaOTankFamily_6ba10::state_giridao_cannon_6c562"
        self.type = _FakeType("int32_t(struct WrongType * this_ @ a6)")
        self.analysis_skipped = analysis_skipped
        self.reanalysis_requests = []

    def reanalyze(self, update_type):
        self.reanalysis_requests.append(update_type)


class _FakeView:
    def __init__(self, function: _FakeFunction, requested_type: str):
        self.function = function
        self.requested_type = _FakeType(requested_type)
        self.parsed_name = "EGiridaOTankFamily_6ba10::state_giridao_cannon_6c562"
        self.parsed_signatures = []
        self.wait_count = 0
        self.get_function_at_calls = []

    def parse_type_string(self, signature: str):
        self.parsed_signatures.append(signature)
        return self.requested_type, self.parsed_name

    def update_analysis_and_wait(self):
        self.wait_count += 1

    def get_function_at(self, address: int, platform):
        self.get_function_at_calls.append((address, platform))
        return self.function


class _FakeOperations:
    def __init__(self, function: _FakeFunction, view: _FakeView):
        self.function = function
        self.current_view = view

    def get_function_by_name_or_address(self, _identifier: str):
        return self.function


def _new_endpoint(*, analysis_skipped: bool = False):
    requested_type = "int32_t(struct EGiridaOTankFamily_6ba10 * this_ @ a6)"
    function = _FakeFunction(analysis_skipped=analysis_skipped)
    view = _FakeView(function, requested_type)
    operations = _FakeOperations(function, view)
    endpoint = endpoints_module.BinaryNinjaEndpoints(operations)
    return endpoint, function, view, requested_type


def test_signature_dry_run_only_parses():
    endpoint, function, view, _requested_type = _new_endpoint()
    before_type = function.type

    result = endpoint.edit_function_signature("0x6c562", "int32_t probe(void);", dry_run=True)

    assert result["success"] is True
    assert result["dry_run"] is True
    assert function.type is before_type
    assert function.reanalysis_requests == []
    assert view.wait_count == 0


def test_signature_applies_reanalyzes_waits_and_verifies_readback():
    endpoint, function, view, requested_type = _new_endpoint()
    original_name = function.name

    result = endpoint.edit_function_signature("0x6c562", "int32_t probe(void);")

    assert result["success"] is True
    assert result["verified"] is True
    assert str(function.type) == requested_type
    assert function.name == original_name
    assert function.reanalysis_requests == ["user-update"]
    assert view.wait_count == 1
    assert view.get_function_at_calls == [(function.start, function.platform)]


def test_signature_can_apply_backtick_parsed_name():
    endpoint, function, _view, _requested_type = _new_endpoint()
    function.name = "sub_6c562"

    result = endpoint.edit_function_signature(
        "0x6c562",
        "int32_t `EGiridaOTankFamily_6ba10::state_giridao_cannon_6c562`(void);",
        apply_name=True,
    )

    assert result["success"] is True
    assert function.name == "EGiridaOTankFamily_6ba10::state_giridao_cannon_6c562"
    assert result["name_matches"] is True


def test_signature_refuses_to_mutate_when_analysis_is_skipped():
    endpoint, function, view, _requested_type = _new_endpoint(analysis_skipped=True)
    before_type = function.type

    result = endpoint.edit_function_signature("0x6c562", "int32_t probe(void);")

    assert result["success"] is False
    assert "analysis is skipped" in result["error"]
    assert function.type is before_type
    assert function.reanalysis_requests == []
    assert view.wait_count == 0


def test_signature_reports_readback_mismatch():
    endpoint, function, view, _requested_type = _new_endpoint()

    def replace_type_after_wait():
        view.wait_count += 1
        function.type = _FakeType("void(void)")

    view.update_analysis_and_wait = replace_type_after_wait
    result = endpoint.edit_function_signature("0x6c562", "int32_t probe(void);")

    assert result["success"] is False
    assert result["verified"] is False
    assert result["type_matches"] is False


def test_signature_wraps_binary_ninja_parser_diagnostics():
    endpoint, _function, view, _requested_type = _new_endpoint()

    def reject_declaration(_signature: str):
        raise SyntaxError(
            "out-of-line declaration of 'method' does not match any declaration in 'Class'"
        )

    view.parse_type_string = reject_declaration
    try:
        endpoint.edit_function_signature("0x6c562", "int32_t Class::method(void);")
    except endpoints_module.FunctionSignatureParseError as exc:
        assert "out-of-line declaration" in str(exc)
    else:
        raise AssertionError("parser failure should propagate as FunctionSignatureParseError")


def test_reanalyze_function_uses_explicit_user_update_and_waits():
    endpoint, function, view, _requested_type = _new_endpoint()

    result = endpoint.reanalyze_function("0x6c562")

    assert result["success"] is True
    assert function.reanalysis_requests == ["user-update"]
    assert view.wait_count == 1
