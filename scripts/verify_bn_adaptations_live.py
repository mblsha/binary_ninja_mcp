"""Opt-in scratch verification, executed INSIDE an idle Binary Ninja GUI.

Run through the custom CLI's Python executor after GUI/doctor inspection.
Refuses to run if the plugin has registered user views. No input file is opened,
no database is saved, and only the disposable in-memory view is closed.
"""

import importlib
import json
import sys
import traceback

import binaryninja as bn


def verify():
    assert bn.core_ui_enabled(), "This verification must run inside the real GUI"
    candidates = [
        module
        for module in list(sys.modules.values())
        if getattr(getattr(getattr(module, "plugin", None), "server", None), "binary_ops", None)
        is not None
        and hasattr(module, "BinaryNinjaMCP")
    ]
    assert len(candidates) == 1, "Expected exactly one custom plugin module"
    plugin = candidates[0].plugin
    server = plugin.server
    ops = server.binary_ops
    sync = importlib.import_module(candidates[0].__name__ + ".server.view_sync")
    import binaryninjaui

    assert not sync.list_ui_views(binaryninjaui), "Refusing to test alongside open GUI views"
    metadata = server.instance_metadata()
    assert not metadata["runtime"]["reload_required"], (
        "Reload changed modules before native verification"
    )
    assert not metadata["runtime"]["unverifiable_modules"], "Loaded modules must be verifiable"
    assert not ops.list_registered_views(), "Refusing to test alongside registered user views"
    assert ops.current_view is None, "Refusing to replace a selected user view"
    root = candidates[0].__name__
    reads_module = importlib.import_module(root + ".core.analysis_operations")
    edits_module = importlib.import_module(root + ".core.annotation_edits")
    mutations = importlib.import_module(root + ".core.mutations")
    endpoints_module = importlib.import_module(root + ".api.endpoints")
    binary_ops_module = importlib.import_module(root + ".core.binary_operations")
    results = []
    raw = view = None

    def check(name, operation):
        try:
            detail = operation()
            results.append({"name": name, "success": True, "detail": detail})
        except Exception:
            results.append({"name": name, "success": False, "traceback": traceback.format_exc()})

    def require(result):
        assert result.get("success", True), json.dumps(result)
        return result

    try:
        data = bytearray(b"\x90" * 160)
        data[0:15] = bytes.fromhex("55 48 89 e5 89 7d fc 8b 45 fc 83 c0 01 5d c3")
        data[32:48] = bytes.fromhex("55 48 89 e5 bf 2a 00 00 00 e8 d2 ff ff ff 5d c3")
        data[64:70] = bytes.fromhex("b8 07 00 00 00 c3")
        data[128:136] = bytes.fromhex("01 02 03 04 00 00 00 00")
        raw = bn.BinaryView.new(bytes(data))
        assert raw is not None
        raw.file.filename = "bn-adaptation-scratch-memory-only"
        view = bn.BinaryViewType["Mapped"].create(raw)
        assert view is not None
        view.platform = bn.Platform["linux-x86_64"]
        for address in (0, 32, 64):
            assert view.add_function(address) is not None
        view.update_analysis_and_wait()
        functions = {f.start: f for f in view.functions}
        assert {0, 32, 64} <= functions.keys(), str(functions)
        assert not functions[0].analysis_skipped and not functions[32].analysis_skipped
        functions[0].name = "scratch_add"
        functions[32].name = "scratch_caller"
        functions[64].name = "scratch_skipped"
        functions[
            64
        ].analysis_skip_override = bn.FunctionAnalysisSkipOverride.AlwaysSkipFunctionAnalysis
        view.update_analysis_and_wait()
        skipped_before = sorted(f.start for f in view.functions if f.analysis_skipped)
        assert 64 in skipped_before, "Scratch skip policy did not take effect"
        reads = reads_module.AnalysisOperations(view)
        edits = edits_module.AnnotationEdits(view)
        local_ops = binary_ops_module.BinaryOperations(plugin.config.binary_ninja)
        local_ops.current_view = view
        endpoints = endpoints_module.BinaryNinjaEndpoints(local_ops)

        def transaction_failure():
            before = view.get_comment_at(0)
            try:
                with mutations.MutationTransaction(view):
                    view.set_comment_at(0, "temporary")
                    raise RuntimeError("injected apply failure")
            except RuntimeError as exc:
                assert "injected" in str(exc)
            assert view.get_comment_at(0) == before
            return "Scoped undo restored a comment after an apply-stage exception"

        check("undo-exception", transaction_failure)

        def signature_preview():
            func = view.get_function_at(0)
            before = (str(func.type), func.name, func.has_user_type)
            refused = endpoints.edit_function_signature(
                "scratch_add", "int64_t scratch_add(int32_t value);", preview=True
            )
            assert refused["error_code"] == "AUTOMATIC_SIGNATURE_PREVIEW_UNSAFE"
            assert (str(func.type), func.name, func.has_user_type) == before
            require(
                endpoints.edit_function_signature(
                    "scratch_add", "uint64_t scratch_add(int32_t arg1);"
                )
            )
            func = view.get_function_at(0)
            assert func.has_user_type
            before = (str(func.type), func.name, func.has_user_type)
            result = require(
                endpoints.edit_function_signature(
                    "scratch_add", "int64_t scratch_add(int32_t value);", preview=True
                )
            )
            view.update_analysis_and_wait()
            func = view.get_function_at(0)
            assert (str(func.type), func.name, func.has_user_type) == before, str(result)
            assert result["rolled_back"] and not result["committed"]
            assert result["restoration_verified"]
            return result

        check("signature-preview", signature_preview)

        def locals_preview():
            listed = require(edits.locals("scratch_add"))
            assert listed["variables"], str(listed)
            identifier = listed["variables"][0]["id"]
            renamed = require(
                edits.local("scratch_add", identifier, "rename", "scratch_argument", preview=True)
            )
            assert renamed["restoration_verified"]
            parameter_id = next(v["id"] for v in listed["variables"] if v["is_parameter"])
            retyped = require(
                edits.local("scratch_add", parameter_id, "retype", "uint32_t", preview=True)
            )
            assert retyped["restoration_verified"]
            widened = edits.local("scratch_add", identifier, "retype", "int64_t", preview=True)
            # Widening this stack slot can merge/remove its canonical variable.
            # Both verified success and a verified safe rollback are acceptable;
            # a retargeted edit or unverified restoration is not.
            assert widened["restoration_verified"] and not widened["committed"], str(widened)
            return {"rename": renamed, "retype": retyped, "widened_stack_slot": widened}

        check("locals-preview", locals_preview)

        def struct_edits():
            view.define_user_type(
                "ScratchPacket", view.parse_type_string("struct { uint32_t a; uint32_t b; }")[0]
            )
            results = []
            for action, kwargs in (
                ("set", {"offset": 8, "member_name": "c", "declaration": "uint64_t"}),
                ("rename", {"field": "a", "member_name": "renamed"}),
                ("delete", {"field": "b"}),
                (
                    "set",
                    {
                        "offset": 0,
                        "member_name": "wide",
                        "declaration": "uint64_t",
                        "overwrite": True,
                    },
                ),
            ):
                result = require(edits.field("ScratchPacket", action, preview=True, **kwargs))
                assert result["restoration_verified"]
                results.append(result)
            committed = require(
                edits.field("ScratchPacket", "rename", field="a", member_name="committed")
            )
            assert committed["committed"]
            return {"previews": len(results), "committed": committed}

        check("structure-edits", struct_edits)

        def auto_and_union():
            declared = view.parse_type_string("struct { uint32_t a; uint32_t b; }")[0]
            auto_name = str(view.define_type("bn-adaptation-scratch-auto", "ScratchAuto", declared))
            result = require(
                edits.field(auto_name, "rename", field="a", member_name="temporary", preview=True)
            )
            assert result["before"]["auto_defined"] and result["restoration_verified"]
            view.define_user_type(
                "ScratchUnion", view.parse_type_string("union { uint32_t a; uint64_t b; }")[0]
            )
            result = require(
                edits.field(
                    "ScratchUnion",
                    "set",
                    offset=0,
                    member_name="c",
                    declaration="uint16_t",
                    preview=True,
                )
            )
            assert result["restoration_verified"] and len(result["after"]["layout"]["members"]) == 3
            return "Auto-type override and overlapping union previews restored"

        check("auto-type-and-union", auto_and_union)

        def read_primitives():
            linear = require(reads.disasm("0x80", count=1))
            typed = require(reads.read("0x80", value_type="u32", count=1, endian="little"))
            assert typed["values"] == [{"address": "0x80", "value": 0x04030201}], str(typed)
            il_counts = {}
            for level in ("llil", "mlil", "hlil"):
                for ssa in (False, True):
                    result = require(reads.function_il("scratch_add", level=level, ssa=ssa))
                    il_counts[f"{level}:{ssa}"] = len(result["instructions"])
            require(reads.info("scratch_add", include_locals=True))
            require(reads.references("scratch_add", direction="incoming"))
            require(reads.references("scratch_caller", direction="outgoing"))
            require(
                reads.bundle(
                    ["scratch_add", "scratch_caller"], include=["comments", "disasm", "locals"]
                )
            )
            return {"linear": linear["count"], "typed": typed["values"], "il": il_counts}

        check("read-primitives", read_primitives)

        def queries():
            text = require(reads.search("return", within=["scratch_add"], level="hlil"))
            constant = require(
                reads.search(42, mode="constant", within=["scratch_caller"], level="llil")
            )
            calls = require(reads.callsites("scratch_add", within=["scratch_caller"], context=1))
            assert (
                text["result_count"] > 0
                and constant["result_count"] > 0
                and calls["result_count"] == 1
            )
            return {
                "text": text["result_count"],
                "constant": constant["result_count"],
                "calls": calls,
            }

        check("search-callsites", queries)

        def skip_guard():
            for operation in (
                lambda: reads.function_il("scratch_skipped"),
                lambda: edits.locals("scratch_skipped"),
                lambda: edits.local("scratch_skipped", "1", "rename", "never_applied"),
            ):
                try:
                    operation()
                except reads_module.AnalysisError as exc:
                    assert exc.code == "analysis_skipped"
                else:
                    raise AssertionError("Skipped analysis was not refused")
            require(reads.disasm("scratch_skipped", count=1))
            after = sorted(f.start for f in view.functions if f.analysis_skipped)
            assert after == skipped_before
            return {"before": skipped_before, "after": after}

        check("skip-policy-preserved", skip_guard)
    finally:
        if view is not None:
            view.file.close()
        elif raw is not None:
            raw.file.close()

        # Remove only our own registrations. If a user opened another view
        # concurrently, preserve that view and its current selection.
        def is_scratch(candidate):
            return (
                candidate is not None
                and candidate.file.filename == "bn-adaptation-scratch-memory-only"
            )

        if is_scratch(ops.current_view):
            ops.current_view = None
        for name in ("_views_by_path", "_views_by_basename", "_views_by_id"):
            mapping = getattr(ops, name)
            for key, reference in list(mapping.items()):
                candidate = ops._deref_view(reference)
                if candidate is None or is_scratch(candidate):
                    del mapping[key]
    return {
        "success": all(result["success"] for result in results),
        "binary_ninja": bn.core_version(),
        "results": results,
        "remaining_registered_views": len(ops.list_registered_views()),
        "saved_database": False,
    }


_result = verify()
print(json.dumps(_result, indent=2))
