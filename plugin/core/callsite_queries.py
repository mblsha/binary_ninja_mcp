"""Direct callsites with bounded instruction and structural HLIL context."""

import time

from shared.analysis_contract import (
    analysis_time_budget,
    callsite_context,
    query_limit,
    query_scope,
    strict_bool,
)
from shared.build_info import snapshot_source
from . import analysis_queries
from .identifiers import AnalysisError, function_identity, function_key

LOADED_SOURCE = snapshot_source(__file__)


def _marker(node):
    return getattr(node, "expr_index", id(node))


def _hlil_context(ops, func, instruction):
    ops._require_analysis(func)  # Mapping LLIL to HLIL may request lazy analysis.
    candidates = getattr(instruction, "hlils", None)
    if candidates is None:
        candidate = getattr(instruction, "hlil", None)
        candidates = [candidate] if candidate is not None else []
    result = []
    seen_candidates = set()
    for candidate in sorted((c for c in candidates if c is not None), key=_marker):
        if _marker(candidate) in seen_candidates:
            continue
        seen_candidates.add(_marker(candidate))
        current = statement = candidate
        conditions = []
        seen = set()
        local_statement = True
        while current is not None:
            ops._check_budget()
            marker = _marker(current)
            if marker in seen or len(seen) >= 256:
                raise AnalysisError(
                    "hlil_parent_cycle", "HLIL parent traversal is cyclic or exceeds 256 ancestors"
                )
            seen.add(marker)
            parent = getattr(current, "parent", None)
            if parent is None:
                break
            kind = analysis_queries.op_name(parent)
            if kind in {"HLIL_IF", "HLIL_WHILE", "HLIL_DO_WHILE", "HLIL_FOR", "HLIL_SWITCH"}:
                condition = getattr(parent, "condition", None)
                branch = None
                if kind == "HLIL_IF":
                    branch = next(
                        (
                            name
                            for name in ("true", "false", "condition")
                            if (child := getattr(parent, name, None)) is not None
                            and _marker(child) == marker
                        ),
                        None,
                    )
                conditions.append(
                    {
                        "kind": kind,
                        "condition": str(condition) if condition is not None else None,
                        "branch": branch,
                        "address": hex(parent.address),
                    }
                )
                local_statement = False
            elif kind == "HLIL_BLOCK":
                local_statement = False
            elif local_statement:
                statement = parent
            current = parent
        result.append(
            {
                "expression_index": _marker(candidate),
                "statement": str(statement),
                "enclosing_conditions": conditions,
            }
        )
    return result


def _instruction_context(ops, func, address, count, cache):
    if count == 0:
        return {"previous_instructions": [], "next_instructions": []}
    if not cache:
        entries = []
        for tokens, instruction_address in func.instructions:
            ops._check_budget()
            if len(entries) >= 100_000:
                raise AnalysisError(
                    "instruction_limit", "Callsite context exceeds 100000 function instructions"
                )
            entries.append(
                {"address": hex(instruction_address), "text": "".join(str(t) for t in tokens)}
            )
        entries.sort(key=lambda item: int(item["address"], 16))
        cache["entries"] = entries
        cache["indices"] = {int(row["address"], 16): index for index, row in enumerate(entries)}
    index = cache["indices"].get(address)
    if index is None:
        raise AnalysisError(
            "instruction_not_found", f"No disassembly instruction at callsite {hex(address)}"
        )
    return {
        "previous_instructions": cache["entries"][max(0, index - count) : index],
        "next_instructions": cache["entries"][index + 1 : index + 1 + count],
    }


def _static_return(ops, func, address, row, tailcall):
    decoded = ops.disasm(address, count=1, arch=func.arch)
    if not decoded["complete"]:
        raise AnalysisError(
            "call_decode_failed", f"Cannot decode callsite: {decoded['stopped_reason']}"
        )
    instruction = decoded["instructions"][0]
    row["call_instruction"] = instruction
    row["instruction_length"] = instruction["length"]
    row["sequential_next_address"] = decoded["next_address"]
    row["static_return_address"] = None
    if tailcall:
        row["return_address_reason"] = "tailcall"
        return
    info = func.arch.get_instruction_info(bytes.fromhex(instruction["bytes"]), address)
    if info is None:
        row["return_address_reason"] = "instruction_info_unavailable"
        return
    delay = int(info.branch_delay)
    row["delay_slots"] = delay
    if not 0 <= delay <= 16:
        row["return_address_reason"] = "unsupported_delay_slot_count"
        return
    next_address = int(decoded["next_address"], 16)
    if delay:
        slots = ops.disasm(next_address, count=delay, arch=func.arch)
        if not slots["complete"]:
            row["return_address_reason"] = "delay_slot_decode_failed"
            return
        next_address = int(slots["next_address"], 16)
    row["static_return_address"] = hex(next_address)
    row["return_address_reason"] = "sequential_after_delay_slots"


def callsites(
    ops,
    identifier,
    *,
    within=None,
    context=3,
    include_tailcalls=False,
    hlil=True,
    max_results=100,
    time_budget=30.0,
):
    within = query_scope(within)
    context = callsite_context(context)
    max_results = query_limit(max_results)
    time_budget = analysis_time_budget(time_budget)
    include_tailcalls = strict_bool(include_tailcalls, "include_tailcalls")
    hlil = strict_bool(hlil, "hlil")
    address = ops.resolver.address(identifier)
    started = time.monotonic()
    rows, skipped, errors = [], [], []
    stopped = None
    scanned = 0
    total = None
    with ops.budget(time_budget):
        try:
            if within:
                functions = analysis_queries.resolve_scope(ops, within, errors)
            else:
                candidates = {}
                for ref in ops.view.get_callers(address):
                    ops._check_budget()
                    if ref.function is None:
                        errors.append(
                            {
                                "address": hex(ref.address),
                                "error": {
                                    "code": "missing_caller_function",
                                    "message": "Call reference has no containing function",
                                },
                            }
                        )
                    else:
                        candidates[function_key(ref.function)] = ref.function
                functions = sorted(candidates.values(), key=function_key)
            total = len(functions)
            seen_calls = set()
            for func in functions:
                ops._check_budget()
                identity = function_identity(func)
                if func.analysis_skipped:
                    skipped.append(identity)
                    continue
                cache = {}
                try:
                    for instruction in analysis_queries.il_instructions(ops, func, "llil"):
                        kind = analysis_queries.op_name(instruction)
                        tailcall = kind == "LLIL_TAILCALL"
                        if kind not in {"LLIL_CALL", "LLIL_CALL_STACK_ADJUST"} and not (
                            include_tailcalls and tailcall
                        ):
                            continue
                        dest = instruction.dest
                        if (
                            analysis_queries.op_name(dest) not in {"LLIL_CONST", "LLIL_CONST_PTR"}
                            or dest.constant != address
                        ):
                            continue
                        marker = (function_key(func), int(instruction.address), tailcall)
                        if marker in seen_calls:
                            continue
                        seen_calls.add(marker)
                        row = {
                            "callee_address": hex(address),
                            "function": identity,
                            "call_address": hex(instruction.address),
                            "il_index": int(instruction.instr_index),
                            "kind": "tailcall" if tailcall else "call",
                            "llil": str(instruction),
                            "hlil_requested": hlil,
                            "hlil": [],
                            "errors": [],
                        }
                        rows.append(row)
                        for phase, enrich in (
                            (
                                "decode",
                                lambda: _static_return(
                                    ops, func, instruction.address, row, tailcall
                                ),
                            ),
                            (
                                "context",
                                lambda: row.update(
                                    _instruction_context(
                                        ops, func, instruction.address, context, cache
                                    )
                                ),
                            ),
                            (
                                "hlil",
                                lambda: row.update(hlil=_hlil_context(ops, func, instruction))
                                if hlil
                                else None,
                            ),
                        ):
                            try:
                                ops._check_budget()
                                enrich()
                            except Exception as exc:
                                row["errors"].append({"phase": phase, **ops._error(exc)})
                                errors.append(
                                    {
                                        "function": identity,
                                        "address": row["call_address"],
                                        "phase": phase,
                                        "error": ops._error(exc),
                                    }
                                )
                                if isinstance(exc, AnalysisError) and exc.code == "time_budget":
                                    raise
                        if len(rows) >= max_results:
                            stopped = "result_limit"
                            break
                    if stopped:
                        break
                    scanned += 1
                except AnalysisError as exc:
                    if exc.code == "time_budget":
                        raise
                    errors.append({"function": identity, "error": exc.as_dict()})
                except Exception as exc:
                    errors.append({"function": identity, "error": ops._error(exc)})
        except AnalysisError as exc:
            if exc.code == "time_budget":
                stopped = "time_budget"
            else:
                raise
    return {
        **analysis_queries.query_payload(
            rows,
            started=started,
            stopped=stopped,
            skipped=skipped,
            errors=errors,
            total=total,
            scanned=scanned,
            scope="selected_functions" if within else "indexed_callers",
        ),
        "callee_address": hex(address),
        "direct_only": True,
        "include_tailcalls": include_tailcalls,
        "context": context,
        "max_results": max_results,
        "time_budget": time_budget,
    }
