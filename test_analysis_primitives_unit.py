"""IL, scalar decoding and reference contracts without loading the BN SDK."""

from enum import IntEnum
from types import SimpleNamespace
from unittest.mock import patch
import json
import struct

import pytest

from binja_cli.cli import BinaryNinjaCLI
from test_analysis_reads_unit import make_reads


@pytest.fixture
def reads():
    return make_reads()


class Endianness(IntEnum):
    LittleEndian = 0
    BigEndian = 1


def test_sdk_endianness_property_failure_is_actionable_and_can_be_overridden(reads):
    _, operations, view = reads

    class BrokenEndianView(type(view)):
        @property
        def endianness(self):
            raise ValueError("76 is not a valid Endianness")

    view.__class__ = BrokenEndianView
    with pytest.raises(Exception, match="supply --endian little or big"):
        operations.read("0x1000", value_type="u16", count=1)
    assert view.reads == []
    result = operations.read("0x1000", value_type="u16", count=1, endian="little")
    assert result["values"] == [{"address": "0x1000", "value": 2}]


class Instruction:
    def __init__(self, index, text):
        self.instr_index = index
        self.address = 0x1000 + index
        self.text = text

    def __str__(self):
        return self.text


class IL(list):
    def __init__(self, text):
        super().__init__([[Instruction(4, text)], [Instruction(8, "return")]])
        self.ssa_form = [[Instruction(14, text + "#ssa")]]


@pytest.mark.parametrize("level", ["hlil", "mlil", "llil"])
@pytest.mark.parametrize("ssa", [False, True])
def test_il_levels_and_ssa_keep_real_instruction_addresses_indices(reads, level, ssa):
    _, operations, view = reads
    func = SimpleNamespace(
        name="f", raw_name="f", start=0x1000, arch=view.arch, analysis_skipped=False
    )
    setattr(func, level, IL(level))
    view.functions = [func]
    result = operations.function_il("f", level=level, ssa=ssa)
    assert result["success"] and result["level"] == level and result["ssa"] is ssa
    assert result["instructions"][0] == {
        "index": 14 if ssa else 4,
        "address": "0x100e" if ssa else "0x1004",
        "text": level + ("#ssa" if ssa else ""),
    }
    assert operations._deadline is None


def test_il_no_fallback_and_skip_guard_before_property_access(reads):
    module, operations, view = reads
    func = view.functions[0]
    func.analysis_skipped = True
    for level in ("hlil", "mlil", "llil"):
        with pytest.raises(module.AnalysisError, match="Analysis is skipped"):
            operations.function_il("f", level=level, ssa=True)
    assert func.accesses == []
    view.functions = [
        SimpleNamespace(
            name="f",
            raw_name="f",
            start=0x1000,
            arch=view.arch,
            analysis_skipped=False,
            hlil=None,
            mlil=IL("mlil"),
        )
    ]
    with pytest.raises(module.AnalysisError, match="HLIL is not available"):
        operations.function_il("f")


@pytest.mark.parametrize("options", [{"level": "bogus"}, {"ssa": "maybe"}, {"time_budget": 0}])
def test_il_invalid_options_before_identifier_resolution(reads, options):
    _, operations, _ = reads
    operations.resolver.function = lambda _: pytest.fail("must validate first")
    with pytest.raises(ValueError):
        operations.function_il("f", **options)


@pytest.mark.parametrize("order", [Endianness.LittleEndian, Endianness.BigEndian])
@pytest.mark.parametrize(
    "value_type,width,signed",
    [
        ("u8", 1, False),
        ("u16", 2, False),
        ("u32", 4, False),
        ("u64", 8, False),
        ("i8", 1, True),
        ("i16", 2, True),
        ("i32", 4, True),
        ("i64", 8, True),
    ],
)
def test_integer_read_uses_enum_endianness_and_sign(reads, order, value_type, width, signed):
    _, operations, view = reads
    view.endianness = order
    values = [-2, 3] if signed else [254, 3]
    view.memory = b"".join(
        v.to_bytes(width, "big" if order == 1 else "little", signed=signed) for v in values
    )
    result = operations.read("0x1000", value_type=value_type, count=2)
    assert result["complete"]
    assert [entry["value"] for entry in result["values"]] == values
    assert result["raw"] == view.memory.hex()


def test_pointer_width_and_endianness_override(reads):
    _, operations, view = reads
    view.address_size = 4
    view.endianness = Endianness.BigEndian
    view.memory = b"\x00\x10\x00\x00"
    result = operations.read("f", value_type="ptr", endian="little")
    assert result["width"] == 4 and result["values"] == [
        {"address": "0x1000", "value": 0x1000, "hex": "0x1000"}
    ]


@pytest.mark.parametrize(
    "kind,fmt,value",
    [
        ("f32", "f", 1.25),
        ("f64", "d", -5.5),
        ("f32", "f", float("inf")),
        ("f64", "d", float("nan")),
    ],
)
def test_float_reads_are_strict_json_and_preserve_raw_bytes(reads, kind, fmt, value):
    _, operations, view = reads
    view.endianness = Endianness.BigEndian
    view.memory = struct.pack(">" + fmt, value)
    result = operations.read("f", value_type=kind)
    json.dumps(result, allow_nan=False)
    assert result["raw"] == view.memory.hex()
    if value in (1.25, -5.5):
        assert result["values"][0]["value"] == value
    else:
        assert result["values"][0]["value"]["type"] == "float"


def test_scalar_short_read_preserves_complete_values_and_partial_element(reads):
    _, operations, view = reads
    view.endianness = Endianness.LittleEndian
    view.memory = b"\x01\0\0\0\x02\0"
    result = operations.read("f", value_type="u32", count=2)
    assert not result["success"] and result["stopped_reason"] == "short_read"
    assert result["count"] == 1 and result["trailing_bytes"] == "0200"
    assert result["next_address"] == "0x1004" and result["read_end_address"] == "0x1006"
    assert result["raw"] == view.memory.hex()


@pytest.mark.parametrize(
    "memory,count,terminated,reason",
    [
        (b"hi\0tail", 7, True, None),
        (b"hi", 2, False, "unterminated"),
        (b"hi", 9, False, "short_read"),
        (b"\0", 1, True, None),
        (b"\xff\0", 2, True, None),
    ],
)
def test_cstr_termination_bounds_and_invalid_utf8_are_explicit(
    reads, memory, count, terminated, reason
):
    _, operations, view = reads
    view.memory = memory
    result = operations.read("f", value_type="cstr", count=count)
    assert result["terminated"] is terminated and result["complete"] is terminated
    assert result["stopped_reason"] == reason
    assert result["encoding_errors"] is (b"\xff" in memory)
    assert result["raw"] == memory.hex()


def test_byte_read_needs_no_architecture_or_functions(reads):
    _, operations, view = reads
    view.functions = []
    view.arch = None
    result = operations.read(0x1000, count=4)
    assert result["value"] == "02000200" and result["byte_order"] is None


@pytest.mark.parametrize(
    "options",
    [
        {"value_type": "u128"},
        {"count": 0},
        {"count": 1_000_001},
        {"count": True},
        {"count": 1.5},
        {"endian": "native"},
    ],
)
def test_invalid_read_arguments_before_memory_access(reads, options):
    _, operations, view = reads
    with pytest.raises(ValueError):
        operations.read("f", **options)
    assert view.reads == []


def test_unknown_endianness_never_silently_defaults_and_address_overflow_rejected(reads):
    _, operations, view = reads
    view.endianness = 42
    with pytest.raises(ValueError, match="unknown endianness"):
        operations.read("f", value_type="u16")
    with pytest.raises(ValueError, match="64-bit address"):
        operations.read(0xFFFFFFFFFFFFFFFF, count=2)
    assert view.reads == []


def struct_type(*members):
    return SimpleNamespace(
        type_class=SimpleNamespace(name="StructureTypeClass"),
        members=[
            SimpleNamespace(name=name, offset=offset, type="int32_t") for name, offset in members
        ],
    )


def test_field_references_use_type_offset_and_native_reference_fields(reads):
    _, operations, view = reads
    view.types = {"Thing": struct_type(("first", 0), ("last", 4))}
    view.get_type_by_name = view.types.get
    ref = SimpleNamespace(
        address=0x1234, func=view.functions[0], arch=view.arch, size=4, incomingType="Thing*"
    )
    calls = []
    view.get_code_refs_for_type_field = lambda name, offset: calls.append((name, offset)) or [ref]
    view.get_data_refs_for_type_field = lambda name, offset: [0x2222]
    result = operations.references("Thing.last", field=True)
    assert result["success"] and result["field"]["offset"] == 4
    assert calls == [("Thing", 4)] and result["data"] == [{"address": "0x2222"}]
    assert result["code"][0]["incoming_type"] == "Thing*"
    assert operations.references("Thing.0x4", field=True)["field"]["name"] == "last"
    assert view.functions[0].accesses == []


def test_ambiguous_union_offset_and_type_case_folding_fail_closed(reads):
    _, operations, view = reads
    view.types = {"Thing": struct_type(("one", 0), ("two", 0)), "THING": struct_type(("one", 0))}
    view.get_type_by_name = view.types.get
    with pytest.raises(ValueError, match="Ambiguous field"):
        operations.references("Thing.0x0", field=True)
    with pytest.raises(ValueError, match="Ambiguous type"):
        operations.references("thing.one", field=True)


@pytest.mark.parametrize(
    "args",
    [
        ["il", "f", "--level", "bad"],
        ["il", "f", "--time-budget", "nan"],
        ["read", "f", "--count", "0"],
        ["read", "f", "--type", "bad"],
        ["xrefs", "bad", "--field"],
    ],
)
def test_cli_invalid_primitive_arguments_make_no_request(args, capsys):
    with patch.object(BinaryNinjaCLI, "_request", side_effect=AssertionError("no request")):
        _, code = BinaryNinjaCLI.run(["binja-cli", *args], exit=False)
    assert code == 2
    assert capsys.readouterr().err


def test_cli_read_local_short_type_flag_and_json_default(capsys):
    payload = {"success": True, "values": [{"value": 7}]}
    with patch.object(BinaryNinjaCLI, "_request", return_value=payload) as request:
        _, code = BinaryNinjaCLI.run(["binja-cli", "read", "f", "-t", "u32", "-n", "2"], exit=False)
    assert code == 0 and json.loads(capsys.readouterr().out) == payload
    assert request.call_args.args[2] == {
        "identifier": "f",
        "type": "u32",
        "count": 2,
        "endian": "auto",
    }


def test_cli_il_view_alias_ssa_and_trailing_json(capsys):
    payload = {"success": False, "complete": False, "text": "partial"}
    with patch.object(BinaryNinjaCLI, "_request", return_value=payload) as request:
        _, code = BinaryNinjaCLI.run(
            ["binja-cli", "il", "f", "--view", "mlil", "--ssa", "--json"], exit=False
        )
    assert code == 1 and json.loads(capsys.readouterr().out) == payload
    assert request.call_args.args[2]["level"] == "mlil" and request.call_args.args[2]["ssa"] is True


def test_cli_reference_directions_and_legacy_refs_remain_distinct(capsys):
    for command, direction in [("xrefs", "incoming"), ("refs-from", "outgoing")]:
        with patch.object(BinaryNinjaCLI, "_request", return_value={"success": True}) as request:
            _, code = BinaryNinjaCLI.run(["binja-cli", command, "f"], exit=False)
        assert code == 0 and request.call_args.args[2]["direction"] == direction
    with patch.object(BinaryNinjaCLI, "_request", return_value={"references": []}) as request:
        BinaryNinjaCLI.run(["binja-cli", "refs", "f", "--json"], exit=False)
    assert request.call_args.args[1] == "codeReferences"
    capsys.readouterr()


@pytest.mark.parametrize(
    "path",
    [
        "/analysis/read?identifier=0x1000&type=bytes&count=2",
        "/analysis/il?identifier=f&level=hlil&ssa=false",
        "/analysis/refs?identifier=f&direction=incoming",
    ],
)
def test_http_primitive_routes_use_explicit_resolved_view(reads, path):
    module, _, view = reads
    handler = module.MCPRequestHandler.__new__(module.MCPRequestHandler)
    handler.path = path + "&_api_version=1&view_id=scratch"
    handler.headers = {}
    handler.binary_ops = SimpleNamespace(current_view=None)
    handler.responses = []
    selections = []

    def resolve(params, *, require_explicit_target):
        selections.append((params["view_id"], require_explicit_target))
        return view, None, [], {}

    handler._resolve_request_view = resolve
    handler._send_json_response = lambda data, status=200: handler.responses.append((data, status))
    handler.do_GET()
    payload, status = handler.responses[0]
    assert status == 200 and payload["success"]
    assert payload["selected_view_filename"] == view.file.filename
    assert selections == [("scratch", True)]
