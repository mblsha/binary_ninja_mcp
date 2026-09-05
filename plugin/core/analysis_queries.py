"""Bounded queries over explicitly guarded function analysis, never global IL search."""

import importlib
import time

from shared.analysis_contract import (
    SEARCH_LEVELS,
    analysis_time_budget,
    constant_value,
    query_limit,
    query_scope,
    strict_bool,
)
from shared.build_info import snapshot_source
from .identifiers import AnalysisError, function_identity, function_key

LOADED_SOURCE = snapshot_source(__file__)
CONSTANT_OPS = {
    f"{prefix}_{suffix}" for prefix in ("HLIL", "MLIL", "LLIL") for suffix in ("CONST", "CONST_PTR")
}


def op_name(node):
    operation = getattr(node, "operation", None)
    return getattr(operation, "name", str(operation))


def il_instructions(ops, func, level):
    ops._require_analysis(func)
    il = getattr(func, level)
    if il is None:
        raise AnalysisError("il_unavailable", f"{level.upper()} is not available")
    for block in il:
        for instruction in block:
            ops._check_budget()
            yield instruction


def resolve_scope(ops, within, errors):
    functions = {}
    if not within:
        return sorted(ops.view.functions, key=function_key)
    for identifier in within:
        ops._check_budget()
        try:
            func = ops.resolver.function(identifier)
            functions[function_key(func)] = func
        except Exception as exc:
            errors.append({"identifier": identifier, "error": ops._error(exc)})
    return sorted(functions.values(), key=function_key)


def query_payload(rows, *, started, stopped, skipped, errors, total, scanned, scope):
    reasons = []
    if stopped:
        reasons.append(stopped)
    if skipped:
        reasons.append("analysis_skipped")
    if errors:
        reasons.append("errors")
    return {
        "success": not reasons,
        "complete": not reasons,
        "stopped_reason": stopped,
        "incomplete_reasons": reasons,
        "results": rows,
        "result_count": len(rows),
        "skipped_functions": skipped,
        "errors": errors,
        "functions_total": total,
        "functions_scanned": scanned,
        "scope": scope,
        "elapsed_seconds": round(time.monotonic() - started, 6),
    }


def _text_matcher(query, *, regex, case_sensitive, ops):
    if not isinstance(query, str) or not query or len(query) > 4096:
        raise AnalysisError("invalid_query", "Search text must contain 1 to 4096 characters")
    if not regex:
        needle = query if case_sensitive else query.casefold()
        return lambda text: needle in (text if case_sensitive else text.casefold())
    try:
        engine = importlib.import_module("regex")
    except ImportError:
        raise AnalysisError(
            "regex_unavailable",
            "Regex search requires the 'regex' package in Binary Ninja's Python environment; literal search needs no extra package",
        ) from None
    try:
        pattern = engine.compile(query, 0 if case_sensitive else engine.IGNORECASE)
        pattern.search("", timeout=0.05)  # Validate timeout support before any IL access.
    except Exception as exc:
        raise AnalysisError(
            "invalid_regex", f"Invalid regex or unsupported timeout API: {exc}"
        ) from exc

    def matches(text):
        remaining = ops._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Search deadline expired")
        return bool(pattern.search(text, timeout=remaining))

    return matches


def contains_constant(ops, root, expected):
    stack = [root]
    seen = set()
    containers = {}
    while stack:
        ops._check_budget()
        node = stack.pop()
        if isinstance(node, (tuple, list)):
            if id(node) in containers:
                continue
            containers[id(node)] = node  # Keep alive: transient operand lists can reuse ids.
            stack.extend(node)
            continue
        if not hasattr(node, "operation"):
            continue  # Raw integers in operands may be indices, not constants.
        marker = (type(node).__name__, getattr(node, "expr_index", id(node)))
        if marker in seen:
            continue
        seen.add(marker)
        if len(seen) > 100_000:
            raise AnalysisError(
                "expression_limit", "Constant expression traversal exceeded 100000 nodes"
            )
        if op_name(node) in CONSTANT_OPS:
            value = node.constant
            if isinstance(value, int) and not isinstance(value, bool) and value == expected:
                return True
        stack.extend(getattr(node, "operands", []))
    return False


def _matching_lines(ops, text, matcher):
    for index, line in enumerate(text.splitlines()):
        ops._check_budget()
        if matcher(line):
            yield index, line


def _search_rows(ops, func, level, mode, expected):
    if level == "disasm":
        for tokens, address in func.instructions:
            ops._check_budget()
            match_constant = (
                any(
                    getattr(getattr(token, "type", None), "name", "")
                    in {"IntegerToken", "PossibleAddressToken", "CodeRelativeAddressToken"}
                    and isinstance(getattr(token, "value", None), int)
                    and not isinstance(token.value, bool)
                    and token.value == expected
                    for token in tokens
                )
                if mode == "constant"
                else False
            )
            yield (
                {"address": hex(address), "index": None, "text": "".join(str(t) for t in tokens)},
                match_constant,
            )
        return
    for instruction in il_instructions(ops, func, level):
        match_constant = (
            contains_constant(ops, instruction, expected) if mode == "constant" else False
        )
        yield (
            {
                "address": hex(instruction.address),
                "index": int(instruction.instr_index),
                "text": str(instruction),
            },
            match_constant,
        )


def search(
    ops,
    query,
    *,
    mode="text",
    level="hlil",
    within=None,
    regex=False,
    case_sensitive=False,
    max_results=100,
    time_budget=5.0,
):
    if mode not in ("text", "constant") or level not in SEARCH_LEVELS:
        raise AnalysisError(
            "invalid_search", "Search mode must be text/constant and level hlil/mlil/llil/disasm"
        )
    within = query_scope(within)
    max_results = query_limit(max_results)
    time_budget = analysis_time_budget(time_budget)
    regex = strict_bool(regex, "regex")
    case_sensitive = strict_bool(case_sensitive, "case_sensitive")
    if mode == "constant" and (regex or case_sensitive):
        raise AnalysisError("invalid_search", "Regex/case options apply only to text search")
    expected = constant_value(query) if mode == "constant" else None
    matcher = (
        _text_matcher(query, regex=regex, case_sensitive=case_sensitive, ops=ops)
        if mode == "text"
        else None
    )
    rows, skipped, errors = [], [], []
    stopped = None
    scanned = 0
    total = None
    started = time.monotonic()
    with ops.budget(time_budget):
        try:
            functions = resolve_scope(ops, within, errors)
            total = len(functions)
            for func in functions:
                ops._check_budget()
                identity = function_identity(func)
                if func.analysis_skipped:
                    skipped.append(identity)
                    if level != "disasm":
                        continue
                try:
                    for row, matched_constant in _search_rows(ops, func, level, mode, expected):
                        if mode == "text":
                            matches = _matching_lines(ops, row["text"], matcher)
                        else:
                            matches = [(None, row["text"])] if matched_constant else []
                        for line_index, text in matches:
                            rows.append(
                                {
                                    **row,
                                    "text": text,
                                    "line": line_index,
                                    "function": identity,
                                    "level": level,
                                    **({"value": expected} if mode == "constant" else {}),
                                }
                            )
                            if len(rows) >= max_results:
                                stopped = "result_limit"
                                break
                        if stopped:
                            break
                    if stopped:
                        break
                    scanned += 1
                except TimeoutError:
                    stopped = "regex_timeout" if regex else "time_budget"
                    break
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
        **query_payload(
            rows,
            started=started,
            stopped=stopped,
            skipped=skipped,
            errors=errors,
            total=total,
            scanned=scanned,
            scope="selected_functions" if within else "analyzed_functions",
        ),
        "query": query,
        "mode": mode,
        "level": level,
        "max_results": max_results,
        "time_budget": time_budget,
        "regex": regex,
        "case_sensitive": case_sensitive,
    }
