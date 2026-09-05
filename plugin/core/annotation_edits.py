"""View-pinned local/structure edits with prevalidation and verified undo.

The undo group is an in-memory edit, never a database save. GUI/raw-Python
writers are not coordinated; readback detects conflicts but cannot prevent them.
"""

from shared.analysis_contract import strict_bool
from shared.build_info import snapshot_source
from .analysis_operations import AnalysisOperations
from .identifiers import AnalysisError, function_identity
from .mutations import MutationTransaction, MutationVerificationError
from .type_queries import find_type, resolve_field

LOADED_SOURCE = snapshot_source(__file__)
ANNOTATION_EDITS_VERSION = 1


def _name(value, label):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise AnalysisError("invalid_name", f"{label} must be nonempty and contain no NUL")
    return value


def _type_record(type_obj):
    return {
        "text": str(type_obj),
        "width": int(type_obj.width),
        "class": getattr(type_obj.type_class, "name", ""),
        "confidence": getattr(type_obj, "confidence", None),
    }


def _member_record(member):
    return {
        "name": str(member.name),
        "offset": int(member.offset),
        "type": _type_record(member.type),
        "access": str(member.access),
        "scope": str(member.scope),
    }


def _layout(type_obj):
    return {
        "variant": str(type_obj.type),
        "width": int(type_obj.width),
        "alignment": int(type_obj.alignment),
        "packed": bool(type_obj.packed),
        "pointer_offset": int(type_obj.pointer_offset),
        "propagate_data_var_refs": bool(type_obj.propagate_data_var_refs),
        "bases": [
            {"type": str(base.type), "offset": int(base.offset), "width": int(base.width)}
            for base in type_obj.base_structures
        ],
        "members": [_member_record(member) for member in type_obj.members],
    }


class AnnotationEdits:
    def __init__(self, view):
        self.view = view
        self.reads = AnalysisOperations(view)

    def _parse_type(self, declaration, *, field=False):
        _name(declaration, "Type declaration")
        try:
            type_obj, _ = self.view.parse_type_string(declaration)
        except Exception as exc:
            raise AnalysisError("invalid_type", f"Cannot parse type: {exc}") from exc
        if type_obj is None:
            raise AnalysisError("invalid_type", "Parser returned no type")
        kind = getattr(type_obj.type_class, "name", "")
        if kind in {"VoidTypeClass", "FunctionTypeClass"} or (field and type_obj.width <= 0):
            raise AnalysisError(
                "invalid_type", "Use a sized value type (or pointer), not void/function"
            )
        return type_obj

    def _variables(self, func):
        self.reads._require_analysis(func)
        return {int(v.identifier): v for v in [*func.parameter_vars, *func.vars]}

    def _variable(self, func, selector):
        variables = self._variables(func)
        prefix = f"{func.start:#x}:{func.arch.name}:"
        selector = str(selector)
        if selector.startswith(prefix):
            try:
                identifier = int(selector[len(prefix) :], 10)
            except ValueError:
                identifier = None
            matches = [v for i, v in variables.items() if i == identifier]
        elif ":" in selector:
            matches = []  # A full ID from another function must not fall back to a name.
        else:
            matches = [v for v in variables.values() if str(v.name) == selector]
            if not matches:
                try:
                    identifier = int(selector, 16 if selector.lower().startswith("0x") else 10)
                except ValueError:
                    identifier = None
                matches = [v for i, v in variables.items() if i == identifier]
        if len(matches) != 1:
            raise AnalysisError(
                "ambiguous_variable" if matches else "not_found",
                f"{'Ambiguous' if matches else 'Unknown'} variable {selector!r}; use a listed full ID",
                candidates=[
                    {"id": f"{prefix}{i}", "name": str(v.name)} for i, v in variables.items()
                ],
            )
        return matches[0]

    def _local_snapshot(self, func, identifier):
        variables = self._variables(func)
        if identifier not in variables:
            raise MutationVerificationError(f"Variable ID {identifier} disappeared during analysis")
        var = variables[identifier]
        return {
            "id": f"{func.start:#x}:{func.arch.name}:{identifier}",
            "name": str(var.name),
            "type": _type_record(var.type),
            "user_defined": bool(func.is_var_user_defined(var)),
            "analysis_skipped": bool(func.analysis_skipped),
        }

    def locals(self, identifier):
        func = self.reads.resolver.function(identifier)
        return {
            "success": True,
            "function": function_identity(func),
            "variables": self.reads.locals(func),
        }

    def _run(self, before, expected, snapshot, apply, *, preview, restored_matches=None):
        transaction = MutationTransaction(self.view, preview=preview)
        result = {
            "before": before,
            "expected": expected,
            "after": None,
            "restoration_verified": None,
        }
        error = None
        try:
            with transaction:
                if snapshot() != before:
                    raise MutationVerificationError(
                        "Target changed after prevalidation; edit not applied"
                    )
                apply()
                self.view.update_analysis_and_wait()
                result["after"] = snapshot()
                if result["after"] != expected:
                    raise MutationVerificationError(
                        "Applied state does not match the requested edit"
                    )
        except Exception as exc:
            error = str(exc)
        if transaction.rolled_back:
            try:
                self.view.update_analysis_and_wait()
                result["current"] = snapshot()
                result["restoration_verified"] = (
                    restored_matches(before, result["current"])
                    if restored_matches is not None
                    else result["current"] == before
                )
                if result["restoration_verified"] and result["current"] != before:
                    result["analysis_metadata_changed"] = True
                if not result["restoration_verified"]:
                    raise MutationVerificationError(
                        "Undo returned, but original state was not restored"
                    )
            except Exception as exc:
                transaction.state_unknown = True
                result["restoration_verified"] = False
                error = f"{error + '; ' if error else ''}Restoration verification failed: {exc}"
        result.update(transaction.as_dict(), success=error is None, verified=error is None)
        if error is not None:
            result["error"] = error
        return result

    def local(self, identifier, selector, action, value, *, preview=False):
        preview = strict_bool(preview, "preview")
        if action not in {"rename", "retype"}:
            raise AnalysisError("invalid_action", "Local action must be rename or retype")
        func = self.reads.resolver.function(identifier)
        var = self._variable(func, selector)
        variable_id = int(var.identifier)
        before = self._local_snapshot(func, variable_id)
        name = _name(value, "Variable name") if action == "rename" else str(var.name)
        type_obj = self._parse_type(value) if action == "retype" else var.type
        if (action == "rename" and _type_record(type_obj) != before["type"]) or (
            action == "retype" and name != before["name"]
        ):
            raise AnalysisError(
                "target_changed",
                "Variable changed during prevalidation; retry with a fresh listing",
            )
        expected = {**before, "name": name, "type": _type_record(type_obj), "user_defined": True}
        # Re-resolve by the original entry/architecture/platform, not a name that
        # another writer may rename or move during an analysis wait.
        key = (func.start, func.arch.name, str(getattr(func, "platform", None)))

        def fresh_function():
            matches = [
                f
                for f in self.view.get_functions_at(key[0])
                if (f.start, f.arch.name, str(getattr(f, "platform", None))) == key
            ]
            if len(matches) != 1:
                raise MutationVerificationError("Original function identity is no longer unique")
            return matches[0]

        def apply():
            current = fresh_function()
            variables = self._variables(current)
            current.create_user_var(variables[variable_id], type_obj, name)

        def restored_matches(original, current):
            # An automatic variable's confidence is analysis metadata, not a
            # user annotation. Native undo can restore the same automatic type
            # at a different confidence. Never ignore confidence for user vars,
            # nor any name, type, ID, skip-state or auto/user-status difference.
            if not original["user_defined"] and not current["user_defined"]:
                original = {**original, "type": {**original["type"], "confidence": None}}
                current = {**current, "type": {**current["type"], "confidence": None}}
            return original == current

        result = self._run(
            before,
            expected,
            lambda: self._local_snapshot(fresh_function(), variable_id),
            apply,
            preview=preview,
            restored_matches=restored_matches,
        )
        return {**result, "function": function_identity(func), "action": action}

    def _structure(self, name):
        name, type_obj = find_type(self.view, name)
        if getattr(type_obj.type_class, "name", "") != "StructureTypeClass":
            raise AnalysisError("invalid_type", f"{name} is not a structure/union")
        return name, type_obj

    def _structure_snapshot(self, name):
        name, type_obj = self._structure(name)
        return {
            "name": name,
            "type_id": str(self.view.get_type_id(name)),
            "auto_defined": bool(self.view.is_type_auto_defined(name)),
            "layout": _layout(type_obj),
        }

    def structure(self, name):
        return {"success": True, **self._structure_snapshot(name)}

    def field(
        self,
        name,
        action,
        *,
        field=None,
        offset=None,
        member_name=None,
        declaration=None,
        overwrite=False,
        preview=False,
    ):
        preview = strict_bool(preview, "preview")
        overwrite = strict_bool(overwrite, "overwrite")
        if action not in {"set", "rename", "delete"}:
            raise AnalysisError("invalid_action", "Field action must be set, rename or delete")
        if overwrite and action != "set":
            raise AnalysisError("invalid_option", "overwrite only applies to field set")
        name, original = self._structure(name)
        before = self._structure_snapshot(name)
        if _layout(original) != before["layout"]:
            raise AnalysisError(
                "target_changed",
                "Type changed during prevalidation; retry after inspecting its layout",
            )
        builder = original.mutable_copy()
        members = list(original.members)
        removed = []
        if action == "set":
            member_name = _name(member_name, "Field name")
            if isinstance(offset, bool):
                raise AnalysisError("invalid_offset", "Field offset must be a nonnegative integer")
            try:
                position = int(str(offset), 16 if str(offset).lower().startswith("0x") else 10)
            except ValueError:
                raise AnalysisError(
                    "invalid_offset", "Field offset must be a nonnegative integer"
                ) from None
            type_obj = self._parse_type(declaration, field=True)
            end = position + int(type_obj.width)
            if position < 0 or end > 2**64 - 1:
                raise AnalysisError(
                    "invalid_offset", "Field range is outside unsigned 64-bit offsets"
                )
            union = getattr(original.type, "name", "") == "UnionStructureType"
            if union and position != 0:
                raise AnalysisError("invalid_offset", "Union fields must start at offset zero")
            same_name = [i for i, m in enumerate(members) if str(m.name) == member_name]
            overlaps = [
                i
                for i, m in enumerate(members)
                if position < int(m.offset) + int(m.type.width) and int(m.offset) < end
            ]
            removed = sorted(set(same_name if union else [*same_name, *overlaps]))
            if removed and not overwrite:
                raise AnalysisError(
                    "field_overlap", "Field name/range already exists; pass overwrite explicitly"
                )
            if any(int(members[i].offset) != position for i in same_name):
                raise AnalysisError(
                    "duplicate_field",
                    "Existing name has another offset; delete it explicitly before moving it",
                )
            # Editing inherited storage would change a base-class contract; this
            # command only manages directly declared members.
            if any(
                position < int(b.offset) + int(b.width) and int(b.offset) < end
                for b in original.base_structures
            ):
                raise AnalysisError(
                    "inherited_overlap",
                    "Field overlaps inherited storage; edit the base type instead",
                )
            access_scope = {}
            if len(same_name) == 1:
                member = members[same_name[0]]
                access_scope = {"access": member.access, "scope": member.scope}
            for i in reversed(removed):
                builder.remove(i)
            builder.insert(
                position, type_obj, member_name, overwrite_existing=False, **access_scope
            )
            builder.width = max(int(original.width), int(builder.width), end)
            builder.alignment = max(int(original.alignment), int(builder.alignment))
        else:
            selected, _, member = resolve_field(self.view, f"{name}.{field}")
            index = selected["index"]
            if action == "rename":
                member_name = _name(member_name, "Field name")
                if any(i != index and str(m.name) == member_name for i, m in enumerate(members)):
                    raise AnalysisError(
                        "duplicate_field", f"Field name already exists: {member_name}"
                    )
                builder.replace(index, member.type, member_name, overwrite_existing=False)
            else:
                removed = [index]
                builder.remove(index)
            builder.width = int(original.width)  # Never silently shrink an ABI layout.
            builder.alignment = int(original.alignment)
        candidate = builder.immutable_copy()
        proposed = _layout(candidate)
        for attribute in (
            "variant",
            "packed",
            "pointer_offset",
            "propagate_data_var_refs",
            "bases",
        ):
            if proposed[attribute] != before["layout"][attribute]:
                raise AnalysisError(
                    "layout_changed", f"Builder unexpectedly changed {attribute}; no edit applied"
                )
        # Unaffected members and renamed member metadata must survive the SDK
        # builder intact. Reject a builder that silently replaces union members.
        expected_members = [_member_record(m) for i, m in enumerate(members) if i not in removed]
        if action == "rename":
            expected_members[index] = {**expected_members[index], "name": member_name}
        for record in expected_members:
            if record not in proposed["members"]:
                raise AnalysisError(
                    "layout_changed", "Builder changed an unaffected member; no edit applied"
                )
        if action == "set":
            matching = [m for m in proposed["members"] if m["name"] == member_name]
            if (
                len(matching) != 1
                or matching[0]["offset"] != position
                or matching[0]["type"] != _type_record(type_obj)
            ):
                raise AnalysisError("layout_changed", "Builder did not create the requested member")
        if len(proposed["members"]) != len(expected_members) + (action == "set"):
            raise AnalysisError("layout_changed", "Builder changed an unexpected number of members")
        expected = {**before, "auto_defined": False, "layout": proposed}
        result = self._run(
            before,
            expected,
            lambda: self._structure_snapshot(name),
            lambda: self.view.define_user_type(name, candidate),
            preview=preview,
        )
        return {
            **result,
            "action": action,
            "removed_members": [_member_record(members[i]) for i in removed],
        }
