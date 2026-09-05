"""Ambiguity-aware named type/field lookup, shared by reads and future edits."""

from shared.build_info import snapshot_source
from .identifiers import AnalysisError

LOADED_SOURCE = snapshot_source(__file__)


def find_type(view, name):
    if not isinstance(name, str) or not name.strip():
        raise AnalysisError("invalid_type", "Type name must not be empty")
    name = name.strip()
    found = view.get_type_by_name(name)
    if found is not None:
        return name, found
    matches = [(str(n), t) for n, t in view.types.items() if str(n).casefold() == name.casefold()]
    if len(matches) > 1:
        raise AnalysisError(
            "ambiguous_type", f"Ambiguous type {name!r}", candidates=[n for n, _ in matches]
        )
    if not matches:
        raise AnalysisError("not_found", f"Type not found: {name}")
    return matches[0]


def resolve_field(view, selector):
    if not isinstance(selector, str):
        raise AnalysisError("invalid_field", "Field selector must be Type.field or Type.0xOFFSET")
    type_name, separator, field_name = selector.rpartition(".")
    if not separator or not type_name or not field_name:
        raise AnalysisError("invalid_field", "Field selector must be Type.field or Type.0xOFFSET")
    type_name, type_obj = find_type(view, type_name)
    if getattr(type_obj.type_class, "name", "") != "StructureTypeClass":
        raise AnalysisError("invalid_type", f"{type_name} is not a structure/union type")
    members = list(type_obj.members)
    matches = [(i, m) for i, m in enumerate(members) if str(m.name) == field_name]
    if not matches:
        matches = [
            (i, m) for i, m in enumerate(members) if str(m.name).casefold() == field_name.casefold()
        ]
    if not matches:
        try:
            offset = int(field_name, 16 if field_name.lower().startswith("0x") else 10)
        except ValueError:
            offset = None
        if offset is not None:
            matches = [(i, m) for i, m in enumerate(members) if int(m.offset) == offset]
    if not matches:
        raise AnalysisError(
            "not_found", f"Field not found: {selector}", candidates=[str(m.name) for m in members]
        )
    if len(matches) > 1:
        raise AnalysisError(
            "ambiguous_field",
            f"Ambiguous field {selector!r}; use an exact member name",
            candidates=[
                {"name": str(m.name), "offset": int(m.offset), "index": i} for i, m in matches
            ],
        )
    index, member = matches[0]
    return (
        {
            "type_name": type_name,
            "name": str(member.name),
            "offset": int(member.offset),
            "index": index,
            "type": str(member.type),
        },
        type_obj,
        member,
    )


def field_references(view, selector, *, expired=lambda: False):
    field, _, _ = resolve_field(view, selector)
    code = []
    data = []
    stopped = None
    for ref in view.get_code_refs_for_type_field(field["type_name"], field["offset"]):
        if expired():
            stopped = "time_budget"
            break
        code.append(
            {
                "address": hex(ref.address),
                "function": str(ref.func.name) if ref.func is not None else None,
                "architecture": str(ref.arch.name) if ref.arch is not None else None,
                "size": int(ref.size),
                "incoming_type": str(ref.incomingType) if ref.incomingType is not None else None,
            }
        )
    if stopped is None:
        for address in view.get_data_refs_for_type_field(field["type_name"], field["offset"]):
            if expired():
                stopped = "time_budget"
                break
            data.append({"address": hex(address)})
    return {
        "success": stopped is None,
        "complete": stopped is None,
        "stopped_reason": stopped,
        "field": field,
        "direction": "incoming",
        "code": code,
        "data": data,
    }
