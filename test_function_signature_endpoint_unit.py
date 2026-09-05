#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch
import pytest


MODULE_PATH = Path(__file__).resolve().parent / "plugin" / "api" / "endpoints.py"


def _load_module():
    binaryninja = types.ModuleType("binaryninja")
    binaryninja.FunctionUpdateType = types.SimpleNamespace(UserFunctionUpdate="user-update")
    binaryninja.TypeClass = types.SimpleNamespace(FunctionTypeClass="function")

    archive = types.ModuleType("plugin.core.annotation_archive")
    archive.export_user_annotations = lambda *_args, **_kwargs: {}
    operations = types.ModuleType("plugin.core.binary_operations")
    operations.BinaryOperations = object

    mutation_spec = importlib.util.spec_from_file_location(
        "plugin.core.mutations", MODULE_PATH.parent.parent / "core/mutations.py"
    )
    mutations = importlib.util.module_from_spec(mutation_spec)
    mutation_spec.loader.exec_module(mutations)

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
            "plugin.core.mutations": mutations,
        },
    ):
        spec.loader.exec_module(module)
    return module


endpoints_module = _load_module()


class _FakeType:
    def __init__(self, text: str):
        self.text = text
        self.type_class = "function"

    def __str__(self) -> str:
        return self.text


class _FakeFunction:
    def __init__(self, *, analysis_skipped: bool = False):
        self.start = 0x6C562
        self.platform = object()
        self.name = "EGiridaOTankFamily_6ba10::state_giridao_cannon_6c562"
        self._type = _FakeType("int32_t(struct WrongType * this_ @ a6)")
        self.has_user_type = False
        self.analysis_skipped = analysis_skipped
        self.reanalysis_requests = []

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        self._type = value
        self.has_user_type = True

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
        self.undo_events = []
        self.before = None

    def begin_undo_actions(self):
        self.before = self.function.type, self.function.name, self.function.has_user_type
        self.undo_events.append("begin")
        return "signature-test"

    def commit_undo_actions(self, state):
        assert state == "signature-test"
        self.undo_events.append("commit")

    def revert_undo_actions(self, state):
        assert state == "signature-test"
        self.function.type, self.function.name, self.function.has_user_type = self.before
        self.undo_events.append("revert")

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


def _new_endpoint(*, analysis_skipped: bool = False, user_type: bool = True):
    requested_type = "int32_t(struct EGiridaOTankFamily_6ba10 * this_ @ a6)"
    function = _FakeFunction(analysis_skipped=analysis_skipped)
    function.has_user_type = user_type
    view = _FakeView(function, requested_type)
    operations = _FakeOperations(function, view)
    endpoint = endpoints_module.BinaryNinjaEndpoints(operations)
    return endpoint, function, view, requested_type


@pytest.mark.parametrize("attribute", ["pure", "can_return"])
def test_signature_ignores_only_unspecified_inferred_attributes(attribute):
    import copy

    class Signature:
        def __init__(self, text="int32_t(int arg)"):
            self.text = text
            self.pure = types.SimpleNamespace(value=False, confidence=0)
            self.can_return = types.SimpleNamespace(value=True, confidence=0)

        def mutable_copy(self):
            return copy.deepcopy(self)

        def __str__(self):
            return (
                self.text
                + (" __pure" if self.pure.value else "")
                + (" __noreturn" if not self.can_return.value else "")
            )

    requested, observed = Signature(), Signature()
    inferred = getattr(observed, attribute)
    inferred.value = not inferred.value
    inferred.confidence = 200
    matches = endpoints_module.BinaryNinjaEndpoints._signature_types_match
    assert matches(requested, observed)
    assert getattr(observed, attribute).confidence == 200  # original untouched
    getattr(requested, attribute).confidence = 255  # explicit opposite must fail
    assert not matches(requested, observed)
    getattr(requested, attribute).confidence = 0
    observed.text = "int64_t(int arg)"
    assert not matches(requested, observed)


def test_signature_dry_run_only_parses():
    endpoint, function, view, _requested_type = _new_endpoint()
    before_type = function.type

    result = endpoint.edit_function_signature("0x6c562", "int32_t probe(void);", dry_run=True)

    assert result["success"] is True
    assert result["dry_run"] is True
    assert function.type is before_type
    assert function.reanalysis_requests == []
    assert view.wait_count == 0
    assert view.undo_events == []


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
    assert view.undo_events == ["begin", "commit"]
    assert result["committed"] is True


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
    before_type = function.type

    def replace_type_after_wait():
        view.wait_count += 1
        if view.wait_count == 1:
            function.type = _FakeType("void(void)")

    view.update_analysis_and_wait = replace_type_after_wait
    result = endpoint.edit_function_signature("0x6c562", "int32_t probe(void);")

    assert result["success"] is False
    assert result["verified"] is False
    assert result["type_matches"] is False
    assert result["rolled_back"] is True
    assert result["committed"] is False
    assert function.type is before_type


def test_signature_rolls_back_unexpected_analysis_exception():
    endpoint, function, view, _requested_type = _new_endpoint()
    before = function.type, function.name
    view.parsed_name = "changed_name"

    def fail_analysis():
        raise RuntimeError("injected analysis failure")

    view.update_analysis_and_wait = fail_analysis
    result = endpoint.edit_function_signature(
        "0x6c562", "void changed_name(void);", apply_name=True
    )
    assert result["success"] is False
    assert result["rolled_back"] is True
    assert "injected analysis failure" in result["error"]
    assert (function.type, function.name) == before


def test_signature_preview_verifies_then_restores_type_and_name():
    endpoint, function, view, _requested_type = _new_endpoint()
    before = function.type, function.name
    view.parsed_name = "preview_name"
    result = endpoint.edit_function_signature(
        "0x6c562", "void preview_name(void);", apply_name=True, preview=True
    )
    assert result["success"] is True
    assert result["verified"] is True
    assert result["preview"] is True
    assert result["committed"] is False
    assert result["rolled_back"] is True
    assert (function.type, function.name) == before
    assert result["restoration_verified"] is True
    assert result["restored_user_type"] is True


@pytest.mark.parametrize("failure", ["unchanged", "user_status"])
def test_signature_preview_rejects_silent_or_incomplete_undo(failure):
    endpoint, function, view, _ = _new_endpoint()
    revert = view.revert_undo_actions

    def broken_revert(state):
        if failure == "user_status":
            revert(state)
            function.has_user_type = False

    view.revert_undo_actions = broken_revert
    result = endpoint.edit_function_signature("0x6c562", "void test(void);", preview=True)
    assert not result["success"] and not result["restoration_verified"]
    assert result["rolled_back"] and result["state_unknown"]


def test_automatic_signature_preview_refuses_before_mutation_but_dry_run_works():
    endpoint, function, view, _ = _new_endpoint(user_type=False)
    result = endpoint.edit_function_signature("0x6c562", "void test(void);", preview=True)
    assert not result["success"] and result["error_code"] == "AUTOMATIC_SIGNATURE_PREVIEW_UNSAFE"
    assert not result["committed"] and not result["rolled_back"]
    assert view.undo_events == [] and not function.has_user_type
    assert endpoint.edit_function_signature("0x6c562", "void test(void);", dry_run=True)["success"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"preview": True, "dry_run": True},
        {"preview": True, "wait": False},
        {"preview": True, "verify": False},
        {"verify": True, "wait": False},
    ],
)
def test_signature_rejects_incompatible_flags_before_parsing(kwargs):
    endpoint, _function, view, _requested_type = _new_endpoint()
    with pytest.raises(ValueError):
        endpoint.edit_function_signature("0x6c562", "void test(void);", **kwargs)
    assert view.parsed_signatures == []
    assert view.undo_events == []


def test_signature_rollback_failure_reports_unknown_state():
    endpoint, _function, view, _requested_type = _new_endpoint()

    def fail_rollback(_state):
        raise RuntimeError("injected rollback failure")

    view.revert_undo_actions = fail_rollback
    result = endpoint.edit_function_signature("0x6c562", "void test(void);", preview=True)
    assert result["success"] is False
    assert result["state_unknown"] is True
    assert result["rolled_back"] is False
    assert "Rollback failed" in result["error"]


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


def test_signature_rejects_non_function_types_before_mutation():
    endpoint, _function, view, _requested_type = _new_endpoint()
    view.requested_type.type_class = "integer"
    with pytest.raises(
        endpoints_module.FunctionSignatureParseError, match="must describe a function"
    ):
        endpoint.edit_function_signature("0x6c562", "int32_t data;")
    assert view.undo_events == []


def test_reanalyze_function_uses_explicit_user_update_and_waits():
    endpoint, function, view, _requested_type = _new_endpoint()

    result = endpoint.reanalyze_function("0x6c562")

    assert result["success"] is True
    assert function.reanalysis_requests == ["user-update"]
    assert view.wait_count == 1
