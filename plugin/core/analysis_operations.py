"""Read interfaces bound to one strong BinaryView reference for the whole request.

No method changes analysis-skip settings, requests reanalysis, or saves a database.
IL can still trigger normal lazy analysis of a non-skipped function.
"""

import time

from shared.analysis_contract import (
    MAX_BUNDLE_FUNCTIONS,
    MAX_INSTRUCTIONS,
    bundle_sections,
    instruction_count,
    analysis_time_budget,
)
from shared.build_info import snapshot_source
from .identifiers import AnalysisError, IdentifierResolver, function_identity, function_key

LOADED_SOURCE = snapshot_source(__file__)
ANALYSIS_READS_VERSION = 1


class AnalysisOperations:
    def __init__(self, view):
        if view is None:
            raise AnalysisError("no_binary", "No binary loaded")
        self.view = view
        self.resolver = IdentifierResolver(view)
        self._deadline = None

    def _budget_expired(self):
        return self._deadline is not None and time.monotonic() >= self._deadline

    def _check_budget(self):
        if self._budget_expired():
            raise AnalysisError(
                "time_budget", "Request time budget exhausted between Binary Ninja API calls"
            )

    @staticmethod
    def _require_analysis(func):
        if func.analysis_skipped:
            raise AnalysisError(
                "analysis_skipped",
                f"Analysis is skipped for {func.name} at {hex(func.start)}; no IL or locals were requested and skip state was not changed",
            )

    def disasm(self, identifier, *, count=None, end=None, arch=None):
        if count is not None and end is not None:
            raise AnalysisError("invalid_range", "Choose --count or --end, not both")
        count = instruction_count(count) if count is not None else (32 if end is None else None)
        requested_start = self.resolver.address(identifier)
        architecture = arch
        if architecture is None:
            functions = self.view.get_functions_at(requested_start)
            if not functions:
                functions = self.view.get_functions_containing(requested_start)
            architectures = {func.arch.name: func.arch for func in functions}
            if len(architectures) > 1:
                raise AnalysisError(
                    "ambiguous_architecture",
                    "Multiple architectures cover this address; supply --arch",
                    candidates=sorted(architectures),
                )
            architecture = next(iter(architectures.values()), self.view.arch)
        if architecture is None:
            raise AnalysisError("no_architecture", "The view has no architecture; supply --arch")
        architecture, start = architecture.get_associated_arch_by_address(requested_start)
        end = self.resolver.address(end) if end is not None else None
        if end is not None and end <= start:
            raise AnalysisError("invalid_range", "Exclusive end must be after the start address")
        address = start
        entries = []
        stopped = None
        while (count is None or len(entries) < count) and (end is None or address < end):
            if self._budget_expired():
                stopped = "time_budget"
                break
            if len(entries) >= MAX_INSTRUCTIONS:
                stopped = "instruction_limit"
                break
            segment = self.view.get_segment_at(address)
            if segment is None or not self.view.is_offset_readable(address):
                stopped = "unmapped"
                break
            size = min(int(architecture.max_instr_length), int(segment.end) - address)
            if end is not None:
                size = min(size, end - address)
            raw = bytes(self.view.read(address, size))
            if not raw:
                stopped = "unmapped"
                break
            decoded = architecture.get_instruction_text(raw, address)
            if decoded is None:
                stopped = "undecodable"
                break
            tokens, length = decoded
            if not isinstance(length, int) or length <= 0 or length > len(raw):
                stopped = "undecodable"
                break
            entries.append(
                {
                    "address": hex(address),
                    "bytes": raw[:length].hex(" "),
                    "length": length,
                    "text": "".join(str(token) for token in tokens),
                }
            )
            address += length
        return {
            "success": stopped is None,
            "address": hex(start),
            "requested_address": hex(requested_start),
            "architecture": architecture.name,
            "next_address": hex(address),
            "end": hex(end) if end is not None else None,
            "count": count,
            "instructions": entries,
            "complete": stopped is None,
            "stopped_reason": stopped,
            "text": "\n".join(
                f"{int(row['address'], 16):08x}  {row['bytes']:<23} {row['text']}"
                for row in entries
            ),
        }

    def locals(self, func):
        self._require_analysis(func)
        parameters = list(func.parameter_vars)
        parameter_ids = {int(var.identifier) for var in parameters}
        variables = {int(var.identifier): var for var in [*parameters, *func.vars]}
        return [
            {
                "id": f"{func.start:#x}:{func.arch.name}:{identifier}",
                "identifier": identifier,
                "name": str(var.name),
                "type": str(var.type),
                "storage": int(var.storage),
                "source_type": str(var.source_type),
                "index": int(var.index),
                "is_parameter": identifier in parameter_ids,
            }
            for identifier, var in sorted(variables.items())
        ]

    def info(self, identifier, *, include_locals=False):
        func = self.resolver.function(identifier)
        result = {
            "success": True,
            "function": function_identity(func),
            "prototype": str(func.type),
            "size": int(func.total_bytes),
            "analysis_skipped": bool(func.analysis_skipped),
            "parameter_count": None,
            "local_count": None,
            "warnings": [],
        }
        if func.analysis_skipped and not include_locals:
            result["warnings"].append("Local/parameter counts omitted because analysis is skipped")
            return result
        variables = self.locals(func)
        parameters = [var for var in variables if var["is_parameter"]]
        locals_only = [var for var in variables if not var["is_parameter"]]
        result.update(parameter_count=len(parameters), local_count=len(locals_only))
        if include_locals:
            result.update(parameters=parameters, locals=locals_only)
        return result

    def il(self, func, *, level="hlil", ssa=False):
        if level not in {"hlil", "mlil", "llil"}:
            raise AnalysisError("invalid_level", "IL level must be hlil, mlil or llil")
        self._require_analysis(func)
        il = getattr(func, level)
        if il is None:
            raise AnalysisError("il_unavailable", f"{level.upper()} is not available")
        if ssa:
            il = il.ssa_form
            if il is None:
                raise AnalysisError("il_unavailable", f"{level.upper()} SSA is not available")
        # Each instruction retains its real index and machine address; never
        # substitute another level when the requested IL is unavailable.
        lines = []
        stopped = None
        for block in il:
            for instruction in block:
                if self._budget_expired():
                    stopped = "time_budget"
                    break
                lines.append(
                    {
                        "index": int(instruction.instr_index),
                        "address": hex(instruction.address),
                        "text": str(instruction),
                    }
                )
            if stopped:
                break
        return {
            "level": level,
            "ssa": bool(ssa),
            "complete": stopped is None,
            "stopped_reason": stopped,
            "instructions": lines,
            "text": "\n".join(
                f"{line['index']:4} {line['address']}  {line['text']}" for line in lines
            ),
        }

    def function_disasm(self, func):
        ranges = sorted({(int(r.start), int(r.end)) for r in func.address_ranges})
        if not ranges:
            raise AnalysisError(
                "disasm_unavailable",
                "Function has no analyzed address ranges; use linear disasm with an explicit count/end",
            )
        results = []
        for start, end in ranges:
            result = self.disasm(start, end=end, arch=func.arch)
            results.append(result)
            if result["stopped_reason"] == "time_budget":
                break
        return {
            "ranges": results,
            "range_count": len(ranges),
            "complete": all(r["complete"] for r in results),
            "text": "\n".join(r["text"] for r in results),
        }

    def xrefs(self, address):
        code = [
            {
                "source": hex(ref.address),
                "function": function_identity(ref.function) if ref.function else None,
                "architecture": str(ref.arch.name),
            }
            for ref in self.view.get_code_refs(address)
        ]
        data = [{"source": hex(source)} for source in self.view.get_data_refs(address)]
        return {"address": hex(address), "direction": "incoming", "code": code, "data": data}

    def refs_from(self, func):
        # Use existing basic-block instruction addresses, without lifting IL.
        code = set()
        data = set()
        stopped = None
        for _tokens, address in func.instructions:
            if self._budget_expired():
                stopped = "time_budget"
                break
            code.update(
                (int(address), int(target))
                for target in self.view.get_code_refs_from(address, func=func, arch=func.arch)
            )
            data.update(
                (int(address), int(target)) for target in self.view.get_data_refs_from(address)
            )
        return {
            "direction": "outgoing",
            "complete": stopped is None,
            "stopped_reason": stopped,
            "code": [{"source": hex(s), "target": hex(t)} for s, t in sorted(code)],
            "data": [{"source": hex(s), "target": hex(t)} for s, t in sorted(data)],
        }

    def comments(self, func):
        ranges = [(int(r.start), int(r.end)) for r in func.address_ranges]
        return {
            "function": str(func.comment),
            "function_addresses": {hex(a): str(c) for a, c in sorted(func.comments.items())},
            "view_addresses": {
                hex(a): str(c)
                for a, c in sorted(self.view.address_comments.items())
                if a == func.start or any(start <= a < end for start, end in ranges)
            },
        }

    def bundle(self, identifiers, *, include=None, time_budget=30.0):
        sections = bundle_sections(include)
        time_budget = analysis_time_budget(time_budget)
        if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= MAX_BUNDLE_FUNCTIONS:
            raise AnalysisError(
                "invalid_identifiers", f"Provide 1 to {MAX_BUNDLE_FUNCTIONS} function identifiers"
            )
        if any(
            isinstance(i, bool) or not isinstance(i, (str, int)) or not str(i).strip()
            for i in identifiers
        ):
            raise AnalysisError(
                "invalid_identifiers", "Function identifiers must be nonempty names or addresses"
            )
        previous_deadline = self._deadline
        self._deadline = time.monotonic() + time_budget
        try:
            return self._collect_bundle(identifiers, sections, time_budget)
        finally:
            self._deadline = previous_deadline

    def _collect_bundle(self, identifiers, sections, time_budget):
        results = []
        resolved = {}
        for identifier in identifiers:
            try:
                self._check_budget()
                func = self.resolver.function(identifier)
            except Exception as exc:
                results.append(
                    {"identifiers": [identifier], "success": False, "error": self._error(exc)}
                )
                continue
            key = function_key(func)
            if key in resolved:
                resolved[key]["identifiers"].append(identifier)
                continue
            item = {
                "identifiers": [identifier],
                "function": function_identity(func),
                "success": True,
                "sections": {},
                "errors": {},
            }
            resolved[key] = item
            results.append(item)
            for section in sections:
                try:
                    self._check_budget()
                    if section in {"decompile", "mlil", "llil"}:
                        value = self.il(func, level="hlil" if section == "decompile" else section)
                    elif section == "disasm":
                        value = self.function_disasm(func)
                    elif section == "xrefs":
                        value = self.xrefs(func.start)
                    else:
                        value = getattr(self, section)(func)
                    item["sections"][section] = value
                    if isinstance(value, dict) and value.get("complete") is False:
                        item["success"] = False
                except Exception as exc:
                    item["errors"][section] = self._error(exc)
                    item["success"] = False
        return {
            "success": all(item["success"] for item in results),
            "include": sections,
            "functions": results,
            "time_budget": time_budget,
            "budget_exhausted": self._budget_expired(),
        }

    @staticmethod
    def _error(exc):
        return (
            exc.as_dict()
            if isinstance(exc, AnalysisError)
            else {"code": type(exc).__name__, "message": str(exc)}
        )
