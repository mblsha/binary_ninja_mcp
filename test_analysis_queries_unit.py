"""Bounded search/callsite behavior with SDK-shaped instructions and access traps."""

from types import SimpleNamespace
from unittest.mock import patch
import json
import re

import pytest

from binja_cli.cli import BinaryNinjaCLI
from test_analysis_reads_unit import make_reads, Function


class Node:
    def __init__(
        self, operation="LLIL_NOP", *, index=1, address=0x1000, text="nop", operands=(), **fields
    ):
        self.operation = SimpleNamespace(name=operation)
        self.expr_index = self.instr_index = index
        self.address = address
        self.text = text
        self.operands = list(operands)
        self.parent = None
        self.hlils = []
        self.__dict__.update(fields)

    def __str__(self):
        return self.text


class Token:
    def __init__(self, text, value=0, kind="TextToken"):
        self.text = text
        self.value = value
        self.type = SimpleNamespace(name=kind)

    def __str__(self):
        return self.text


def function(view, name="f", start=0x1000, nodes=None):
    return SimpleNamespace(
        name=name,
        raw_name=name,
        start=start,
        arch=view.arch,
        analysis_skipped=False,
        hlil=[nodes or []],
        mlil=[nodes or []],
        llil=[nodes or []],
        instructions=[
            ([Token("nop")], 0x1000),
            ([Token("call target")], 0x1002),
            ([Token("return")], 0x1004),
        ],
    )


@pytest.fixture
def query():
    module, ops, view = make_reads()
    return module, ops, view


def test_text_search_reports_lines_addresses_case_and_scope(query):
    _, ops, view = query
    view.functions = [
        function(view, nodes=[Node(text="Hello\nHELLO", address=0x1002, index=5)]),
        function(view, "g", 0x2000, [Node(text="Hello")]),
    ]
    result = ops.search("hello", within=["f", "0x1000"])
    assert result["complete"] and result["functions_total"] == 1
    assert [(r["address"], r["index"], r["line"]) for r in result["results"]] == [
        ("0x1002", 5, 0),
        ("0x1002", 5, 1),
    ]
    exact = ops.search("Hello", within=["f"], case_sensitive=True)
    assert exact["result_count"] == 1


def test_search_limit_retains_prefix_and_does_not_claim_complete(query):
    _, ops, view = query
    view.functions = [function(view, nodes=[Node(text="hit\nhit\nhit")])]
    result = ops.search("hit", max_results=2)
    assert len(result["results"]) == 2 and result["stopped_reason"] == "result_limit"
    assert not result["success"] and result["functions_scanned"] == 0


def test_skipped_functions_are_never_lifted_and_other_matches_survive(query):
    _, ops, view = query
    skipped = Function("skip", start=0x2000, skipped=True)
    view.functions = [function(view, nodes=[Node(text="hit")]), skipped]
    result = ops.search("hit")
    assert result["result_count"] == 1 and not result["complete"]
    assert result["incomplete_reasons"] == ["analysis_skipped"]
    assert result["skipped_functions"][0]["address"] == "0x2000"
    assert skipped.accesses == [] and skipped.analysis_skipped


def test_missing_scope_and_unavailable_il_are_errors_not_no_matches(query):
    _, ops, view = query
    view.functions = [function(view)]
    view.functions[0].hlil = None
    result = ops.search("none", within=["f", "missing"])
    assert not result["success"] and result["results"] == []
    assert len(result["errors"]) == 2 and result["functions_scanned"] == 0


def test_constants_traverse_expressions_but_ignore_ssa_indices_and_cycles(query):
    _, ops, view = query
    constant = Node("LLIL_CONST", index=2, constant=42)
    root = Node("LLIL_ADD", operands=[constant, 42], text="x + 42")
    root.operands.append(root)
    view.functions = [function(view, nodes=[root, Node(index=3, operands=[42], text="ssa#42")])]
    result = ops.search("0x2a", mode="constant", level="llil")
    assert result["complete"] and result["result_count"] == 1
    assert result["results"][0]["value"] == 42


def test_disasm_constants_use_only_numeric_tokens(query):
    _, ops, view = query
    func = function(view)
    func.instructions = [
        ([Token("r0", 0, "RegisterToken")], 0x1000),
        ([Token("0", 0, "IntegerToken")], 0x1002),
    ]
    view.functions = [func]
    result = ops.search(0, mode="constant", level="disasm")
    assert [r["address"] for r in result["results"]] == ["0x1002"]


def test_global_time_budget_retains_matches_and_stops_before_next_function(query):
    _, ops, view = query
    clock = [0.0]

    class SlowNode(Node):
        def __str__(self):
            clock[0] += 0.6
            return "hit"

    view.functions = [
        function(view, nodes=[SlowNode(), SlowNode(index=2)]),
        function(view, "g", 0x2000),
    ]
    with patch.object(
        ops.budget.__wrapped__.__globals__["time"], "monotonic", side_effect=lambda: clock[0]
    ):
        result = ops.search("hit", time_budget=1)
    assert result["result_count"] == 1 and result["stopped_reason"] == "time_budget"
    assert ops._deadline is None


@pytest.mark.parametrize(
    "options",
    [
        {"max_results": 0},
        {"time_budget": float("inf")},
        {"within": [False]},
        {"level": "bad"},
        {"mode": "constant", "regex": True},
        {"regex": "maybe"},
    ],
)
def test_search_invalid_options_fail_before_function_resolution(query, options):
    _, ops, _ = query
    ops.resolver.function = lambda _: pytest.fail("No analysis before validation")
    with pytest.raises(ValueError):
        ops.search("42", **{"within": ["f"], **options})


def test_regex_missing_dependency_fails_before_analysis(query):
    _, ops, _ = query
    queries = ops.search.__globals__["analysis_queries"]
    with patch.object(queries.importlib, "import_module", side_effect=ImportError("missing")):
        with pytest.raises(ValueError, match="Binary Ninja's Python environment"):
            ops.search("hit", regex=True)


def test_regex_timeout_and_real_regex_syntax_errors_keep_prevalidation(query):
    _, ops, view = query
    queries = ops.search.__globals__["analysis_queries"]
    view.functions = [function(view, nodes=[Node(text="hit")])]

    class Pattern:
        def search(self, text, *, timeout):
            assert timeout > 0
            if text:
                raise TimeoutError("injected matcher deadline")
            return None

    engine = SimpleNamespace(IGNORECASE=1, compile=lambda *args: Pattern())
    with patch.object(queries.importlib, "import_module", return_value=engine):
        result = ops.search("hit", regex=True)
    assert result["stopped_reason"] == "regex_timeout" and not result["complete"]
    engine.compile = lambda *args: re.compile("[")
    with patch.object(queries.importlib, "import_module", return_value=engine):
        with pytest.raises(ValueError, match="Invalid regex"):
            ops.search("[", regex=True)


def test_real_timeout_regex_engine_when_installed(query):
    pytest.importorskip("regex")
    _, ops, view = query
    view.functions = [function(view, nodes=[Node(text="hello 123")])]
    result = ops.search(r"hello\s+\d+", regex=True)
    assert result["success"] and result["result_count"] == 1


def test_real_pathological_regex_is_stopped_by_matching_deadline(query):
    pytest.importorskip("regex")
    _, ops, view = query
    view.functions = [function(view, nodes=[Node(text="a" * 10000 + "!")])]
    result = ops.search(r"(a|aa)+$", regex=True, time_budget=0.005)
    assert not result["complete"]
    assert result["stopped_reason"] in {"regex_timeout", "time_budget"}


def call_fixture(view, *, tail=False, indirect=False):
    dest = Node("LLIL_REG" if indirect else "LLIL_CONST_PTR", index=2, constant=0x2000)
    call = Node(
        "LLIL_TAILCALL" if tail else "LLIL_CALL",
        dest=dest,
        text="call 0x2000",
        address=0x1002,
        index=3,
    )
    caller = function(view, nodes=[call])
    view.functions = [caller]
    view.arch.get_instruction_info = lambda raw, address: SimpleNamespace(branch_delay=0)
    view.get_callers = lambda address: [SimpleNamespace(function=caller, address=0x1002)]
    return caller, call


def test_callsites_include_context_and_static_fallthrough(query):
    _, ops, view = query
    call_fixture(view)
    result = ops.callsites(0x2000, context=1, hlil=False)
    assert result["success"] and result["result_count"] == 1
    call = result["results"][0]
    assert call["static_return_address"] == "0x1004" and call["instruction_length"] == 2
    assert call["previous_instructions"][0]["address"] == "0x1000"
    assert call["next_instructions"][0]["address"] == "0x1004"
    assert call["hlil_requested"] is False


def test_callsites_account_for_delay_slots(query):
    _, ops, view = query
    call_fixture(view)
    view.arch.get_instruction_info = lambda raw, address: SimpleNamespace(branch_delay=2)
    result = ops.callsites(0x2000, context=0, hlil=False)
    call = result["results"][0]
    assert call["sequential_next_address"] == "0x1004"
    assert call["static_return_address"] == "0x1006" and call["delay_slots"] == 2


def test_callsites_tailcall_is_opt_in_and_indirect_is_not_claimed_direct(query):
    _, ops, view = query
    call_fixture(view, tail=True)
    assert ops.callsites(0x2000, context=0, hlil=False)["results"] == []
    result = ops.callsites(0x2000, context=0, hlil=False, include_tailcalls=True)
    assert result["results"][0]["static_return_address"] is None
    assert result["results"][0]["return_address_reason"] == "tailcall"
    call_fixture(view, indirect=True)
    assert ops.callsites(0x2000, context=0, hlil=False)["results"] == []


def test_callsites_hlil_structural_condition_preserves_false_branch(query):
    _, ops, view = query
    _, call = call_fixture(view)
    hlil_call = Node("HLIL_CALL", text="target()", index=5)
    assign = Node("HLIL_ASSIGN", text="result = target()", index=6)
    block = Node("HLIL_BLOCK", index=7)
    branch = Node(
        "HLIL_IF", index=8, condition=Node(text="ready", index=9), true=Node(index=10), false=block
    )
    hlil_call.parent, assign.parent, block.parent = assign, block, branch
    call.hlils = [hlil_call]
    result = ops.callsites(0x2000, context=0)
    context = result["results"][0]["hlil"][0]
    assert context["statement"] == "result = target()"
    assert context["enclosing_conditions"][0]["branch"] == "false"
    assert context["enclosing_conditions"][0]["condition"] == "ready"


def test_callsites_skip_guard_and_decode_failure_preserve_other_results(query):
    _, ops, view = query
    caller, _ = call_fixture(view)
    skipped = Function("skip", start=0x3000, skipped=True)
    view.functions.append(skipped)
    view.get_callers = lambda address: [
        SimpleNamespace(function=f, address=f.start) for f in view.functions
    ]
    view.memory = b"\xff" * 8
    result = ops.callsites(0x2000, context=0, hlil=False)
    assert not result["success"] and len(result["results"]) == 1
    assert result["results"][0]["call_address"] == "0x1002"
    assert result["results"][0]["errors"][0]["phase"] == "decode"
    assert result["skipped_functions"][0]["address"] == "0x3000"
    assert skipped.accesses == []


def test_callsite_limit_and_no_context_no_hlil_are_selective(query):
    _, ops, view = query
    caller, call = call_fixture(view)

    class Trap:
        def __iter__(self):
            pytest.fail("No instruction context requested")

    caller.instructions = Trap()
    caller.llil = [[call, Node("LLIL_CALL", dest=call.dest, index=4, address=0x1004)]]
    with patch.object(
        Node, "hlils", property(lambda self: pytest.fail("No HLIL requested")), create=True
    ):
        result = ops.callsites(0x2000, within=["f"], context=0, hlil=False, max_results=1)
    assert result["stopped_reason"] == "result_limit" and len(result["results"]) == 1


@pytest.mark.parametrize(
    "endpoint,body",
    [
        ("/analysis/search", {"query": "call", "mode": "text", "level": "llil"}),
        ("/analysis/callsites", {"identifier": "0x2000", "context": 0, "hlil": False}),
    ],
)
def test_http_query_routes_dispatch_with_one_explicit_view(query, endpoint, body):
    module, _, view = query
    call_fixture(view)
    handler = module.MCPRequestHandler.__new__(module.MCPRequestHandler)
    handler.path = endpoint + "?view_id=scratch"
    handler.headers = {}
    handler.binary_ops = SimpleNamespace(current_view=None)
    handler._parse_post_params = lambda: {"_api_version": 1, **body}
    calls = []

    def resolve(params, *, require_explicit_target):
        calls.append((params["view_id"], require_explicit_target))
        return view, None, [], {}

    handler._resolve_request_view = resolve
    replies = []
    handler._send_json_response = lambda data, status=200: replies.append((data, status))
    handler.do_POST()
    assert calls == [("scratch", True)]
    payload, status = replies[0]
    assert status == 200 and payload["success"]
    assert payload["result_count"] == 1 and payload["selected_view_filename"] == view.file.filename


@pytest.mark.parametrize(
    "args",
    [
        ["search", "text", ""],
        ["search", "text", "hi", "--limit", "0"],
        ["search", "constant", "bad"],
        ["callsites", "f", "--context", "-1"],
        ["callsites", "f", "--time-budget", "nan"],
    ],
)
def test_cli_invalid_queries_send_no_request(args, capsys):
    with patch.object(BinaryNinjaCLI, "_request", side_effect=AssertionError("no request")):
        _, code = BinaryNinjaCLI.run(["binja-cli", *args], exit=False)
    assert code == 2 and capsys.readouterr().err


def test_nested_search_json_default_repeated_scope_and_output_options(capsys):
    payload = {"success": False, "results": [{"text": "partial"}], "complete": False}
    with patch.object(BinaryNinjaCLI, "_request", return_value=payload) as request:
        _, code = BinaryNinjaCLI.run(
            [
                "binja-cli",
                "search",
                "text",
                "foo",
                "--within",
                "one",
                "--within",
                "two",
                "--regex",
                "--format",
                "ndjson",
            ],
            exit=False,
        )
    assert code == 1 and json.loads(capsys.readouterr().out) == payload
    data = request.call_args.kwargs["data"]
    assert data["within"] == ["one", "two"] and data["time_budget"] == 5.0 and data["regex"]


def test_search_constant_and_callsites_wire_arguments(capsys):
    with patch.object(BinaryNinjaCLI, "_request", return_value={"success": True}) as request:
        _, code = BinaryNinjaCLI.run(["binja-cli", "search", "constant", "0x2a"], exit=False)
    assert code == 0 and request.call_args.kwargs["data"]["query"] == 42
    assert request.call_args.kwargs["data"]["level"] == "llil"
    with patch.object(BinaryNinjaCLI, "_request", return_value={"success": True}) as request:
        _, code = BinaryNinjaCLI.run(
            [
                "binja-cli",
                "callsites",
                "target",
                "--no-hlil",
                "--include-tailcalls",
                "--context",
                "0",
            ],
            exit=False,
        )
    assert code == 0 and not request.call_args.kwargs["data"]["hlil"]
    assert request.call_args.kwargs["data"]["include_tailcalls"]
    capsys.readouterr()
