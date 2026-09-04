"""Failure injection for each legacy built-in mutation lane."""

import copy
from types import SimpleNamespace

import pytest

import test_binary_operations_target_ids_unit as operations_tests
from test_function_signature_endpoint_unit import endpoints_module


class View:
    def __init__(self):
        self.comments = {0x1000: "before"}
        self.types = {}
        self.symbols = {}
        self.variable = SimpleNamespace(name="old", type="int32_t")
        self.function = SimpleNamespace(name="before", start=0x1000, analysis_skipped=False)
        self.function.get_variable_by_name = (
            lambda name: self.variable if name == self.variable.name else None
        )
        self.events = []
        self.fail_on_type = None
        self.mismatch_comment = False
        self.mismatch_symbol = False
        self.reject_type_parse = False

    def begin_undo_actions(self):
        self.before = copy.deepcopy(
            (self.comments, self.types, self.symbols, self.variable.__dict__, self.function.name)
        )
        self.events.append("begin")
        return "test"

    def commit_undo_actions(self, state):
        assert state == "test"
        self.events.append("commit")

    def revert_undo_actions(self, state):
        assert state == "test"
        self.comments, self.types, self.symbols, variable, self.function.name = self.before
        self.variable.__dict__.update(variable)
        self.events.append("revert")

    def is_valid_offset(self, address):
        return address == 0x1000

    def set_comment_at(self, address, comment):
        self.comments[address] = "mismatch" if self.mismatch_comment else comment

    def get_comment_at(self, address):
        return self.comments.get(address)

    def define_user_symbol(self, symbol):
        self.symbols[symbol.address] = symbol

    def get_symbol_at(self, address):
        return None if self.mismatch_symbol else self.symbols.get(address)

    def parse_types_from_string(self, code):
        return SimpleNamespace(types={"first": "int32_t", "second": "int64_t"})

    def define_user_type(self, name, value):
        self.types[name] = value
        if name == self.fail_on_type:
            raise RuntimeError("injected type failure")

    def get_type_by_name(self, name):
        return self.types.get(name)

    def parse_type_string(self, declaration):
        if self.reject_type_parse:
            raise ValueError("injected parse failure")
        return declaration, ""


def make_operations():
    _, module = operations_tests.TestBinaryOperationsTargetIds()._import_modules()
    module.bn.SymbolType = SimpleNamespace(DataSymbol="data")
    module.bn.Symbol = lambda kind, address, name: SimpleNamespace(
        type=kind, address=address, name=name
    )
    view = View()
    operations = module.BinaryOperations(config=object())
    operations._current_view = view
    operations.get_function_by_name_or_address = lambda _: view.function
    return operations, view


def test_function_rename_commits_verified_name():
    operations, view = make_operations()
    assert operations.rename_function("before", "after")
    assert view.function.name == "after"
    assert view.events == ["begin", "commit"]


@pytest.mark.parametrize("operation", ["rename_function", "rename_data"])
def test_invalid_names_do_not_start_a_mutation(operation):
    operations, view = make_operations()
    assert getattr(operations, operation)(0x1000, "") is False
    assert view.events == []


def test_data_rename_mismatch_restores_symbols():
    operations, view = make_operations()
    view.mismatch_symbol = True
    assert operations.rename_data(0x1000, "after") is False
    assert view.symbols == {}
    assert view.events == ["begin", "revert"]


def test_comment_mismatch_restores_original_comment():
    operations, view = make_operations()
    view.mismatch_comment = True
    assert operations.set_comment(0x1000, "after") is False
    assert view.comments[0x1000] == "before"
    assert view.events == ["begin", "revert"]


def test_function_comment_delete_uses_same_storage_as_set_and_get():
    operations, view = make_operations()
    assert operations.set_function_comment("before", "new comment")
    assert operations.get_function_comment("before") == "new comment"
    assert operations.delete_function_comment("before")
    assert operations.get_function_comment("before") is None
    assert view.events == ["begin", "commit", "begin", "commit"]


def test_batch_type_definition_rolls_back_all_types_if_later_definition_fails():
    operations, view = make_operations()
    endpoint = endpoints_module.BinaryNinjaEndpoints(operations)
    view.fail_on_type = "second"
    with pytest.raises(ValueError, match="injected type failure"):
        endpoint.define_types("types")
    assert view.types == {}
    assert view.events == ["begin", "revert"]


def test_local_rename_and_retype_are_transactional():
    operations, view = make_operations()
    endpoint = endpoints_module.BinaryNinjaEndpoints(operations)
    endpoint.rename_variable("before", "old", "new")
    endpoint.retype_variable("before", "new", "int64_t")
    assert view.variable.name == "new"
    assert view.variable.type == "int64_t"
    assert view.events == ["begin", "commit", "begin", "commit"]


def test_invalid_local_type_is_rejected_before_transaction():
    operations, view = make_operations()
    endpoint = endpoints_module.BinaryNinjaEndpoints(operations)
    view.reject_type_parse = True
    with pytest.raises(ValueError, match="parse failure"):
        endpoint.retype_variable("before", "old", "invalid")
    assert view.variable.type == "int32_t"
    assert view.events == []


@pytest.mark.parametrize(
    "operation,args", [("rename_variable", ("old", "new")), ("retype_variable", ("old", "int64_t"))]
)
def test_skipped_analysis_rejects_local_edits_before_variable_access(operation, args):
    operations, view = make_operations()
    endpoint = endpoints_module.BinaryNinjaEndpoints(operations)
    view.function.analysis_skipped = True

    def forbidden(_name):
        pytest.fail("must not access variables on skipped functions")

    view.function.get_variable_by_name = forbidden
    with pytest.raises(ValueError, match="analysis is skipped"):
        getattr(endpoint, operation)("before", *args)
    assert view.events == []
