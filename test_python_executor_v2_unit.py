"""Offline regression coverage for the in-process Python response contract."""

import importlib.util
import json
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "python_executor_v2_unit_target",
    Path(__file__).parent / "plugin/core/python_executor_v2.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.bn = None


def executor():
    return module.SmartPythonExecutor()


def test_large_list_and_nested_dictionary_are_complete():
    value = {str(i): list(range(150)) for i in range(150)}
    result = executor()._serialize_value(value)
    assert len(result["items"]) == 150
    assert result["items"]["149"]["items"] == list(range(150))
    json.dumps(result, allow_nan=False)


def test_execute_preserves_all_returned_items():
    result = executor().execute("list(range(150))")
    assert result["success"]
    assert result["serialization_version"] == 2
    assert result["return_value"]["items"] == list(range(150))


def test_cycle_has_explicit_reference_and_shared_values_are_not_cycles():
    value = []
    value.append(value)
    result = executor()._serialize_value(value)
    assert result["items"] == [{"type": "reference", "path": "$", "circular": True}]
    shared = [1, 2]
    result = executor()._serialize_value([shared, shared])
    assert result["items"][0] == result["items"][1] == {"type": "list", "items": [1, 2]}


def test_non_string_dictionary_keys_do_not_collide():
    result = executor()._serialize_value({1: "integer", "1": "string"})
    assert result["entries"] == [
        {"key": 1, "value": "integer"},
        {"key": "1", "value": "string"},
    ]


def test_non_finite_floats_are_valid_json():
    result = executor()._serialize_value([float("nan"), float("inf"), float("-inf")])
    assert [item["value"] for item in result["items"]] == ["nan", "inf", "-inf"]
    json.dumps(result, allow_nan=False)


def test_opaque_representation_is_not_silently_sliced():
    class LongRepresentation:
        def __str__(self):
            return "x" * 1000

    assert executor()._serialize_value(LongRepresentation())["repr"] == "x" * 1000


def test_worker_traceback_keeps_console_line_and_exception():
    result = executor().execute("def fail():\n    raise ValueError('test failure')\nfail()")
    assert result["success"] is False
    assert result["error"]["type"] == "ValueError"
    assert 'File "<console>", line 2, in fail' in result["error"]["traceback"]
    assert "ValueError: test failure" in result["error"]["traceback"]
    assert "NoneType: None" not in result["stderr"]
    assert result["error"]["traceback"] in result["stderr"]


def test_name_error_keeps_suggestions_and_real_traceback():
    result = executor().execute("get_funct('main')")
    assert "get_func" in result["error"]["suggestions"]
    assert "NameError" in result["error"]["traceback"]
