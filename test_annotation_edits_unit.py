"""SDK-shaped edit tests: no real BinaryView, database, or running GUI touched."""

import copy
from enum import Enum
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from binja_cli.cli import BinaryNinjaCLI
from test_analysis_reads_unit import make_reads


class Variant(Enum):
    StructStructureType = 0
    UnionStructureType = 1


class ValueType:
    def __init__(self, text="int32_t", width=4, kind="IntegerTypeClass"):
        self.text, self.width = text, width
        self.type_class = SimpleNamespace(name=kind)

    def __str__(self):
        return self.text


def member(name, offset, width=4):
    return SimpleNamespace(
        name=name, offset=offset, type=ValueType(width=width), access="NoAccess", scope="NoScope"
    )


class Structure:
    type_class = SimpleNamespace(name="StructureTypeClass")
    type = Variant.StructStructureType
    width = 16
    alignment = 4
    packed = False
    pointer_offset = 2
    propagate_data_var_refs = True

    def __init__(self):
        self.members = [member("a", 0), member("b", 4)]
        self.base_structures = []

    def mutable_copy(self):
        return copy.deepcopy(self)

    def immutable_copy(self):
        return copy.deepcopy(self)

    def remove(self, index):
        del self.members[index]

    def replace(self, index, type, name, *, overwrite_existing):
        assert not overwrite_existing
        self.members[index].type = type
        self.members[index].name = name

    def insert(self, offset, type, name, *, overwrite_existing, access="NoAccess", scope="NoScope"):
        assert not overwrite_existing
        self.members.append(
            SimpleNamespace(offset=offset, type=type, name=name, access=access, scope=scope)
        )
        self.members.sort(key=lambda m: m.offset)
        self.width = max(self.width, offset + type.width)


@pytest.fixture
def edit():
    module, _, view = make_reads()
    func = view.functions[0]
    for var in func._vars:
        var.type = ValueType()
    func.users = set()
    view.events = []
    view.types = {"Packet": Structure()}
    view.auto_types = {"Packet"}
    view.get_type_by_name = lambda name: view.types.get(name)
    view.get_type_id = lambda name: "type-" + name
    view.is_type_auto_defined = lambda name: name in view.auto_types

    def create(var, type, name):
        view.events.append("apply-local")
        var.name, var.type = name, type
        func.users.add(var.identifier)

    func.create_user_var = create
    func.is_var_user_defined = lambda var: var.identifier in func.users

    def parse(declaration):
        view.events.append("parse")
        if declaration == "bad":
            raise ValueError("syntax")
        if declaration == "void":
            return ValueType("void", 0, "VoidTypeClass"), None
        return ValueType(declaration, 8 if declaration == "uint64_t" else 4), None

    def begin():
        view.events.append("begin")
        view.saved = copy.deepcopy(
            (func._vars, func._parameters, func.users, view.types, view.auto_types)
        )
        return "scoped-undo"

    def revert(state):
        assert state == "scoped-undo"
        view.events.append("revert")
        func._vars, func._parameters, func.users, view.types, view.auto_types = copy.deepcopy(
            view.saved
        )

    def define(name, type):
        view.events.append("apply-type")
        view.types[name] = type
        view.auto_types.discard(name)

    view.parse_type_string = parse
    view.begin_undo_actions = begin
    view.revert_undo_actions = revert
    view.commit_undo_actions = lambda state: view.events.append("commit")
    view.update_analysis_and_wait = lambda: view.events.append("wait")
    view.define_user_type = define
    return module, module.AnnotationEdits(view), view, func


def test_list_ids_and_rename_target_survive_duplicate_names(edit):
    _, edits, view, func = edit
    for var in func._vars:
        var.name = "same"
    result = edits.locals("f")
    assert [v["id"] for v in result["variables"]] == ["0x1000:fake:1", "0x1000:fake:2"]
    with pytest.raises(Exception, match="Ambiguous variable"):
        edits.local("f", "same", "rename", "new")
    assert view.events == []
    result = edits.local("f", "0x1000:fake:2", "rename", "new")
    assert result["success"] and result["committed"] and result["after"]["name"] == "new"
    assert func._parameters[0].name == "same"
    assert view.events == ["begin", "apply-local", "wait", "commit"]


@pytest.mark.parametrize(
    "selector", ["0x2000:fake:2", "0x1000:other:2", "missing", "0x1000:fake:no"]
)
def test_wrong_local_ids_fail_before_undo(edit, selector):
    _, edits, view, _ = edit
    with pytest.raises(Exception, match="Unknown variable"):
        edits.local("f", selector, "rename", "new")
    assert view.events == []


def test_local_preview_restores_name_type_and_user_annotation(edit):
    _, edits, view, _ = edit
    result = edits.local("f", "2", "retype", "uint64_t", preview=True)
    assert result["success"] and not result["committed"] and result["restoration_verified"]
    assert result["after"]["type"]["width"] == 8
    assert result["current"] == result["before"] and not result["current"]["user_defined"]
    assert view.events == ["parse", "begin", "apply-local", "wait", "revert", "wait"]


def test_skipped_local_never_reads_variables_or_changes_skip(edit):
    _, edits, view, func = edit
    func.analysis_skipped = True
    for action in (lambda: edits.locals("f"), lambda: edits.local("f", "2", "rename", "new")):
        with pytest.raises(Exception, match="Analysis is skipped"):
            action()
    assert func.analysis_skipped and func.accesses == [] and view.events == []


@pytest.mark.parametrize("value", ["bad", "void", "", "int\0x"])
def test_local_invalid_types_fail_before_transaction(edit, value):
    _, edits, view, _ = edit
    with pytest.raises(Exception):
        edits.local("f", "local", "retype", value)
    assert "begin" not in view.events


def test_ignored_local_write_rolls_back_and_verifies_restoration(edit):
    _, edits, view, func = edit
    func.create_user_var = lambda *args: None
    result = edits.local("f", "local", "rename", "new")
    assert not result["success"] and result["rolled_back"] and result["restoration_verified"]
    assert not result["state_unknown"] and "commit" not in view.events


def test_silent_undo_failure_is_not_reported_as_successful_preview(edit):
    _, edits, view, _ = edit
    view.revert_undo_actions = lambda state: None
    result = edits.local("f", "local", "rename", "new", preview=True)
    assert not result["success"] and result["state_unknown"]
    assert result["rolled_back"] and not result["restoration_verified"]
    assert "original state was not restored" in result["error"]


def test_undo_exception_reports_unknown_state(edit):
    _, edits, view, _ = edit

    def fail(state):
        raise RuntimeError("undo unavailable")

    view.revert_undo_actions = fail
    result = edits.local("f", "local", "rename", "new", preview=True)
    assert not result["success"] and result["state_unknown"] and not result["rolled_back"]
    assert result["restoration_verified"] is None


def test_function_variable_disappearance_is_reported_and_undone(edit):
    _, edits, view, func = edit

    def disappear(*args):
        func._vars = func._parameters = []

    func.create_user_var = disappear
    result = edits.local("f", "2", "rename", "new")
    assert not result["success"] and result["restoration_verified"]
    assert "disappeared" in result["error"]


def test_same_name_can_promote_explicit_annotation(edit):
    _, edits, _, _ = edit
    result = edits.local("f", "local", "rename", "local")
    assert result["committed"] and not result["before"]["user_defined"]
    assert result["after"]["user_defined"]


def test_structure_show_and_add_preserve_metadata(edit):
    _, edits, view, _ = edit
    before = edits.structure("packet")
    assert before["name"] == "Packet" and before["auto_defined"]
    result = edits.field("Packet", "set", offset="0x10", member_name="c", declaration="uint64_t")
    assert result["committed"] and result["success"]
    assert result["after"]["layout"]["width"] == 24
    assert result["after"]["layout"]["pointer_offset"] == 2
    assert not result["after"]["auto_defined"]
    assert view.events.index("parse") < view.events.index("begin")


@pytest.mark.parametrize(
    "action,kwargs",
    [
        ("rename", {"field": "0x4", "member_name": "new"}),
        ("delete", {"field": "b"}),
        ("set", {"offset": 8, "member_name": "new", "declaration": "int32_t"}),
    ],
)
def test_structure_preview_restores_layout_and_auto_status(edit, action, kwargs):
    _, edits, _, _ = edit
    result = edits.field("Packet", action, preview=True, **kwargs)
    assert result["success"] and result["restoration_verified"] and not result["committed"]
    assert result["current"] == result["before"]
    assert result["after"]["layout"]["width"] == 16
    assert result["after"]["layout"]["alignment"] == 4


def test_structure_overlap_requires_explicit_overwrite(edit):
    _, edits, view, _ = edit
    kwargs = {"offset": 0, "member_name": "wide", "declaration": "uint64_t"}
    with pytest.raises(Exception, match="overwrite explicitly"):
        edits.field("Packet", "set", **kwargs)
    assert "begin" not in view.events
    result = edits.field("Packet", "set", overwrite=True, **kwargs)
    assert result["success"] and len(result["removed_members"]) == 2
    assert [m["name"] for m in result["after"]["layout"]["members"]] == ["wide"]


def test_union_distinct_members_do_not_need_overwrite_and_offsets_are_ambiguous(edit):
    _, edits, view, _ = edit
    type = view.types["Packet"]
    type.type = Variant.UnionStructureType
    type.members[1].offset = 0
    result = edits.field("Packet", "set", offset=0, member_name="wide", declaration="uint64_t")
    assert result["success"] and len(result["after"]["layout"]["members"]) == 3
    with pytest.raises(Exception, match="Ambiguous field"):
        edits.field("Packet", "delete", field="0")
    result = edits.field(
        "Packet", "set", offset=0, member_name="a", declaration="uint64_t", overwrite=True
    )
    assert result["success"] and len(result["removed_members"]) == 1
    assert len(result["after"]["layout"]["members"]) == 3


def test_replace_named_member_preserves_access_and_scope(edit):
    _, edits, view, _ = edit
    view.types["Packet"].members[0].access = "PrivateAccess"
    view.types["Packet"].members[0].scope = "StaticScope"
    result = edits.field(
        "Packet", "set", offset=0, member_name="a", declaration="int32_t", overwrite=True
    )
    assert result["success"]
    row = result["after"]["layout"]["members"][0]
    assert row["access"] == "PrivateAccess" and row["scope"] == "StaticScope"


@pytest.mark.parametrize(
    "action,kwargs",
    [
        ("set", {"offset": -1, "member_name": "x", "declaration": "int32_t"}),
        ("set", {"offset": True, "member_name": "x", "declaration": "int32_t"}),
        ("set", {"offset": 2**64 - 1, "member_name": "x", "declaration": "int32_t"}),
        ("set", {"offset": 8, "member_name": "x", "declaration": "void"}),
        ("set", {"offset": 8, "member_name": "a", "declaration": "int32_t", "overwrite": True}),
        ("rename", {"field": "a", "member_name": "b"}),
        ("delete", {"field": "a", "overwrite": True}),
    ],
)
def test_invalid_field_edits_do_not_start_undo(edit, action, kwargs):
    _, edits, view, _ = edit
    with pytest.raises(Exception):
        edits.field("Packet", action, **kwargs)
    assert "begin" not in view.events


def test_inherited_overlap_is_rejected_even_with_overwrite(edit):
    _, edits, view, _ = edit
    view.types["Packet"].base_structures = [SimpleNamespace(type="Base", offset=8, width=8)]
    with pytest.raises(Exception, match="inherited storage"):
        edits.field(
            "Packet", "set", offset=8, member_name="x", declaration="int32_t", overwrite=True
        )
    assert "begin" not in view.events


def test_sdk_builder_cannot_silently_change_other_members(edit):
    _, edits, view, _ = edit
    original = Structure.replace

    def corrupt(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.members[1].offset = 9

    with (
        patch.object(Structure, "replace", corrupt),
        pytest.raises(Exception, match="unaffected member"),
    ):
        edits.field("Packet", "rename", field="a", member_name="new")
    assert "begin" not in view.events


def test_type_write_failure_rolls_back_partial_changes(edit):
    _, edits, view, _ = edit
    original = view.define_user_type

    def fail(*args):
        original(*args)
        raise RuntimeError("apply failed")

    view.define_user_type = fail
    result = edits.field("Packet", "delete", field="b")
    assert not result["success"] and result["restoration_verified"]
    assert result["current"]["auto_defined"]


@pytest.mark.parametrize(
    "args,endpoint,expected",
    [
        (
            ["locals", "rename", "f", "0x1000:fake:2", "new", "--preview"],
            "edit/local",
            {"action": "rename", "preview": True},
        ),
        (
            ["locals", "retype", "f", "local", "int *"],
            "edit/local",
            {"action": "retype", "value": "int *"},
        ),
        (
            ["struct", "field", "set", "Packet", "0x10", "x", "int", "--overwrite", "--preview"],
            "edit/struct-field",
            {"offset": "0x10", "overwrite": True, "preview": True},
        ),
        (
            ["struct", "field", "rename", "Packet", "x", "y"],
            "edit/struct-field",
            {"field": "x", "member_name": "y"},
        ),
        (["struct", "field", "delete", "Packet", "x"], "edit/struct-field", {"action": "delete"}),
    ],
)
def test_cli_edit_arguments_nested_output_and_timeout(args, endpoint, expected, capsys):
    payload = {"success": True, "committed": False}
    with patch.object(BinaryNinjaCLI, "_request", return_value=payload) as request:
        _, code = BinaryNinjaCLI.run(["binja-cli", *args, "--format", "ndjson"], exit=False)
    assert code == 0 and json.loads(capsys.readouterr().out) == payload
    assert request.call_args.args == ("POST", endpoint)
    assert request.call_args.kwargs["timeout"] >= 1800
    assert expected.items() <= request.call_args.kwargs["data"].items()


def test_edit_http_route_uses_the_pinned_view(edit):
    module, _, view, _ = edit
    handler = object.__new__(module.MCPRequestHandler)
    handler._view_context_fields = lambda target: {"pinned": target is view}
    responses = []
    handler._send_json_response = lambda data, status=200: responses.append((data, status))
    handler._handle_edit_request(
        "/edit/local",
        {"identifier": "f", "variable": "2", "action": "rename", "value": "new", "preview": True},
        view,
    )
    payload, status = responses[0]
    assert (
        status == 200
        and payload["success"]
        and payload["pinned"]
        and payload["restoration_verified"]
    )
