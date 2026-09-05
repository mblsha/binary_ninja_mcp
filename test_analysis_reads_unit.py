"""SDK-free checks for pinned reads, decoder boundaries and skip protection."""

from types import SimpleNamespace
from unittest.mock import patch
import json

import pytest

from binja_cli.cli import BinaryNinjaCLI
from shared.analysis_contract import bundle_sections, instruction_count
from test_http_server_disconnect_unit import _import_http_server


class Arch:
    name = "fake"
    max_instr_length = 4

    def get_associated_arch_by_address(self, address):
        return self, address

    def get_instruction_text(self, data, address):
        if data[0] == 0xFF:
            return None
        length = data[0]  # artificial variable-length ISA
        return [f"op_{address:x}"], length


class Function:
    def __init__(self, name="f", start=0x1000, skipped=False):
        self.name = self.raw_name = name
        self.start = start
        self.arch = Arch()
        self.type = "int f(int arg)"
        self.total_bytes = 4
        self.analysis_skipped = skipped
        self.address_ranges = [SimpleNamespace(start=start, end=start + 4)]
        self.comment = "header"
        self.comments = {start + 1: "function comment"}
        self.instructions = [([], start), ([], start + 2)]
        self.accesses = []
        parameter = SimpleNamespace(
            identifier=1, name="arg", type="int", storage=0, index=0, source_type="register"
        )
        local = SimpleNamespace(
            identifier=2, name="local", type="int", storage=-4, index=1, source_type="stack"
        )
        self._vars = [local, parameter]
        self._parameters = [parameter]

    def _access(self, what):
        self.accesses.append(what)
        if self.analysis_skipped:
            raise AssertionError(f"Accessed {what} on skipped function")

    @property
    def vars(self):
        self._access("vars")
        return self._vars

    @property
    def parameter_vars(self):
        self._access("parameter_vars")
        return self._parameters

    @property
    def hlil(self):
        self._access("hlil")
        return [[SimpleNamespace(instr_index=0, address=self.start)]]


class View:
    def __init__(self):
        self.arch = Arch()
        self.functions = [Function()]
        self.memory = bytes([2, 0, 2, 0, 1, 1, 1, 1])
        self.symbols = [SimpleNamespace(name="data", address=0x1004)]
        self.reads = []
        self.file = SimpleNamespace(filename="/scratch/read-tests.bin")
        self.address_comments = {0x1000: "view comment", 0x2000: "other function"}

    def read(self, address, length):
        self.reads.append((address, length))
        return self.memory[address - 0x1000 : address - 0x1000 + length]

    def get_segment_at(self, address):
        return (
            SimpleNamespace(end=0x1000 + len(self.memory))
            if self.is_offset_readable(address)
            else None
        )

    def is_offset_readable(self, address):
        return 0x1000 <= address < 0x1000 + len(self.memory)

    def get_symbols_by_name(self, name):
        return [s for s in self.symbols if s.name == name]

    get_symbols_by_raw_name = get_symbols_by_name

    def get_functions_at(self, address):
        return [f for f in self.functions if f.start == address]

    def get_functions_containing(self, address):
        return [
            f for f in self.functions if any(r.start <= address < r.end for r in f.address_ranges)
        ]

    def get_code_refs(self, address):
        return []

    def get_data_refs(self, address):
        return [0x1234]

    def get_code_refs_from(self, address, *, func, arch):
        assert func in self.functions and arch is func.arch
        return [0x2000]

    def get_data_refs_from(self, address):
        return [0x3000]


@pytest.fixture
def reads():
    module = _import_http_server()
    view = View()
    return module, module.AnalysisOperations(view), view


def test_disasm_count_needs_no_function_or_il(reads):
    _, operations, view = reads
    view.functions = []
    result = operations.disasm("0x1000", count=2)
    assert result["complete"] and result["next_address"] == "0x1004"
    assert [i["bytes"] for i in result["instructions"]] == ["02 00", "02 00"]


def test_disasm_end_is_exclusive_and_truncated_instruction_is_not_invented(reads):
    _, operations, view = reads
    result = operations.disasm("f", end="f+3")
    assert not result["success"]
    assert result["stopped_reason"] == "undecodable"
    assert result["next_address"] == "0x1002"
    assert len(result["instructions"]) == 1
    assert all(address + length <= 0x1003 for address, length in view.reads)


def test_disasm_mapping_boundary_and_zero_length_stop(reads):
    _, operations, view = reads
    result = operations.disasm("data+3", count=2)
    assert result["next_address"] == "0x1008" and result["stopped_reason"] == "unmapped"
    view.memory = b"\0"
    result = operations.disasm(0x1000, count=1)
    assert result["stopped_reason"] == "undecodable"
    assert result["instructions"] == []


@pytest.mark.parametrize(
    "options",
    [
        {"count": 0},
        {"count": 100001},
        {"count": 1, "end": 0x1004},
        {"end": 0x1000},
        {"count": 1.5},
        {"count": True},
    ],
)
def test_invalid_disasm_range_rejected_before_read(reads, options):
    _, operations, view = reads
    with pytest.raises(ValueError):
        operations.disasm(0x1000, **options)
    assert view.reads == []


def test_disasm_associated_architecture_normalizes_thumb_style_address(reads):
    _, operations, view = reads
    alternate = Arch()
    alternate.name = "alternate"
    view.functions = []
    view.arch.get_associated_arch_by_address = lambda address: (alternate, address & ~1)
    result = operations.disasm(0x1001, count=1)
    assert result["requested_address"] == "0x1001"
    assert result["address"] == "0x1000" and result["architecture"] == "alternate"


def test_disasm_prefers_function_architecture_and_can_explicitly_override(reads):
    module, operations, view = reads
    view.functions[0].arch.name = "function_arch"
    assert operations.disasm("f", count=1)["architecture"] == "function_arch"
    assert operations.disasm("f", count=1, arch=view.arch)["architecture"] == "fake"
    view.functions.append(Function("other_arch"))
    with pytest.raises(module.AnalysisError, match="Multiple architectures"):
        operations.disasm(0x1000, count=1)


def test_resolver_reports_all_ambiguous_name_candidates_and_allows_interior(reads):
    module, operations, view = reads
    assert operations.resolver.function("f+1") is view.functions[0]
    assert operations.resolver.address("data-4") == 0x1000
    view.functions.append(Function(start=0x2000))
    operations = module.AnalysisOperations(view)
    with pytest.raises(module.AnalysisError) as error:
        operations.resolver.address("f")
    assert error.value.as_dict()["candidates"] == ["0x1000", "0x2000"]


def test_resolver_exact_name_beats_offset_expression_and_preserves_zero(reads):
    _, operations, view = reads
    view.symbols.extend(
        [SimpleNamespace(name="f+2", address=0), SimpleNamespace(name="F", address=0x1006)]
    )
    assert operations.resolver.address("f+2") == 0
    assert operations.resolver.address("F") == 0x1006
    assert operations.resolver.address("00016") == 16


def test_overlapping_functions_fail_instead_of_first_match(reads):
    module, operations, view = reads
    view.functions.append(Function("overlap", 0x1001))
    with pytest.raises(module.AnalysisError, match="Multiple functions"):
        operations.resolver.function("0x1002")
    assert operations.resolver.function("0x1001").name == "overlap"  # exact entry wins


def test_info_is_compact_and_locals_are_canonical_with_stable_ids(reads):
    _, operations, view = reads
    compact = operations.info("f")
    assert compact["parameter_count"] == compact["local_count"] == 1
    assert "parameters" not in compact and "locals" not in compact
    detailed = operations.info("f", include_locals=True)
    assert detailed["parameters"][0]["id"] == "0x1000:fake:1"
    old_id = detailed["locals"][0]["id"]
    view.functions[0]._vars[0].name = "renamed"
    assert operations.info("f", include_locals=True)["locals"][0]["id"] == old_id


def test_skipped_info_omits_counts_and_bundle_protects_every_il_local_access(reads):
    module, operations, view = reads
    func = view.functions[0]
    func.analysis_skipped = True
    result = operations.info("f")
    assert result["parameter_count"] is None and result["warnings"]
    with pytest.raises(module.AnalysisError, match="Analysis is skipped"):
        operations.info("f", include_locals=True)
    result = operations.bundle(["f"], include=["decompile", "mlil", "llil", "locals", "comments"])
    item = result["functions"][0]
    assert not result["success"]
    assert set(item["errors"]) == {"decompile", "mlil", "llil", "locals"}
    assert "comments" in item["sections"]
    assert func.accesses == [] and func.analysis_skipped


def test_bundle_validates_all_arguments_before_any_function_resolution(reads):
    _, operations, view = reads
    operations.resolver.function = lambda _: pytest.fail("must validate first")
    for identifiers, include in [
        (["f"], ["bogus"]),
        (["f", {}], ["locals"]),
        ([], None),
        (["f"] * 257, None),
    ]:
        with pytest.raises(ValueError):
            operations.bundle(identifiers, include=include)
    assert view.reads == []


def test_bundle_deduplicates_resolved_aliases_and_keeps_missing_function_error(reads):
    _, operations, view = reads
    result = operations.bundle(["f", "0x1001", "absent"], include=["comments"])
    assert not result["success"]
    assert len(result["functions"]) == 2
    first, second = result["functions"]
    assert first["identifiers"] == ["f", "0x1001"]
    assert first["success"] and not second["success"]
    assert first["sections"]["comments"]["view_addresses"] == {"0x1000": "view comment"}
    assert view.functions[0].accesses == [] and view.reads == []


def test_bundle_budget_is_shared_by_all_functions_and_preserves_completed_sections(reads):
    _, operations, view = reads
    view.functions.append(Function("g", 0x2000))
    clock = [0.0]
    original_comments = operations.comments

    def slow_comments(func):
        value = original_comments(func)
        clock[0] += 2.0
        return value

    operations.comments = slow_comments
    time_module = operations.bundle.__globals__["time"]
    with patch.object(time_module, "monotonic", side_effect=lambda: clock[0]):
        result = operations.bundle(["f", "g"], include=["comments", "locals"], time_budget=1)
    first, second = result["functions"]
    assert result["budget_exhausted"] and not result["success"]
    assert "comments" in first["sections"]
    assert first["errors"]["locals"]["code"] == "time_budget"
    assert second["error"]["code"] == "time_budget"
    assert operations._deadline is None
    assert view.functions[0].accesses == []


def test_bundle_decoder_timeout_retains_partial_instructions(reads):
    _, operations, view = reads
    clock = [0.0]
    original_decode = view.functions[0].arch.get_instruction_text

    def slow_decode(data, address):
        clock[0] += 2
        return original_decode(data, address)

    view.functions[0].arch.get_instruction_text = slow_decode
    time_module = operations.bundle.__globals__["time"]
    with patch.object(time_module, "monotonic", side_effect=lambda: clock[0]):
        result = operations.bundle(["f"], include=["disasm"], time_budget=1)
    section = result["functions"][0]["sections"]["disasm"]
    assert not section["complete"]
    assert section["ranges"][0]["stopped_reason"] == "time_budget"
    assert len(section["ranges"][0]["instructions"]) == 1


@pytest.mark.parametrize("budget", [True, 0, -1, "nan", "inf", 3601, None])
def test_bundle_rejects_invalid_budget_before_resolution(reads, budget):
    _, operations, _ = reads
    operations.resolver.function = lambda _: pytest.fail("must validate first")
    with pytest.raises(ValueError, match="Time budget"):
        operations.bundle(["f"], time_budget=budget)


def test_refs_alias_is_incoming_and_outgoing_is_distinct(reads):
    _, operations, _ = reads
    result = operations.bundle(["f"], include=["refs", "refs_from"])
    sections = result["functions"][0]["sections"]
    assert sections["xrefs"]["direction"] == "incoming"
    assert sections["refs_from"]["direction"] == "outgoing"
    assert sections["refs_from"]["data"][0] == {"source": "0x1000", "target": "0x3000"}


def test_http_bundle_uses_resolved_view_even_if_current_view_changes(reads):
    module, _, view = reads
    handler = module.MCPRequestHandler.__new__(module.MCPRequestHandler)
    handler.responses = []
    handler.binary_ops = SimpleNamespace(current_view=View())
    handler.binary_ops.current_view.functions[0].name = "different"
    handler._send_json_response = lambda data, status=200: handler.responses.append((data, status))
    handler._handle_analysis_request(
        "/analysis/bundle", {"identifiers": ["f"], "include": ["comments"]}, view, method="POST"
    )
    payload, status = handler.responses[0]
    assert status == 200 and payload["success"]
    assert payload["selected_view_filename"] == view.file.filename
    assert len(payload["functions"]) == 1


@pytest.mark.parametrize(
    "args",
    [
        ["disasm", "f", "--count", "0"],
        ["disasm", "f", "--count", "1", "--end", "f+4"],
        ["bundle", "f", "--include", "bad"],
        ["bundle", "f", "--include", "all,locals"],
    ],
)
def test_cli_validates_before_any_request(args, capsys):
    with patch.object(BinaryNinjaCLI, "_request", side_effect=AssertionError("unexpected request")):
        _, code = BinaryNinjaCLI.run(["binja-cli", *args], exit=False)
    assert code == 2 and "Error:" in capsys.readouterr().err


def test_cli_bundle_json_default_partial_exit_and_common_options(capsys):
    payload = {"success": False, "functions": [{"success": True}, {"success": False}]}
    with patch.object(BinaryNinjaCLI, "_request", return_value=payload) as request:
        _, code = BinaryNinjaCLI.run(
            ["binja-cli", "bundle", "f", "missing", "--include", "comments", "--no-spill"],
            exit=False,
        )
    assert code == 1
    assert json.loads(capsys.readouterr().out) == payload
    assert request.call_args.kwargs["data"] == {
        "identifiers": ["f", "missing"],
        "include": ["comments"],
        "time_budget": 30.0,
    }


def test_cli_disasm_default_and_exclusive_end_do_not_send_conflicting_count(capsys):
    for args, expected in [([], {"count": 32}), (["--end", "f+4"], {"end": "f+4"})]:
        with patch.object(
            BinaryNinjaCLI, "_request", return_value={"success": True, "text": "op"}
        ) as request:
            _, code = BinaryNinjaCLI.run(["binja-cli", "disasm", "f", *args], exit=False)
        assert code == 0
        assert request.call_args.args[2] == {"identifier": "f", **expected}
    assert capsys.readouterr().out == "op\nop\n"


def test_shared_rules():
    assert bundle_sections("refs, xrefs") == ["xrefs"]
    assert len(bundle_sections("all")) == 8
    assert instruction_count("12") == 12
