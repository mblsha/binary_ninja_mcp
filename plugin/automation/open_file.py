"""Binary Ninja UI open workflow automation."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from .text import find_item_index, normalize_token

try:
    import binaryninja as bn
except Exception:
    bn = None


def _collect_visible_windows(app) -> list[dict[str, str]]:
    windows = []
    if app is None:
        return windows
    for widget in app.topLevelWidgets():
        try:
            if not widget.isVisible():
                continue
            windows.append(
                {
                    "class": type(widget).__name__,
                    "title": str(widget.windowTitle() or ""),
                }
            )
        except Exception:
            continue
    return windows


def _find_options_dialog(app):
    if app is None:
        return None
    for widget in app.topLevelWidgets():
        try:
            if not widget.isVisible():
                continue
            title = str(widget.windowTitle() or "")
            cls_name = type(widget).__name__.lower()
            if "open with options" in title.lower() or "optionsdialog" in cls_name:
                return widget
        except Exception:
            continue
    return None


def _is_qt_object_alive(obj) -> bool:
    if obj is None:
        return False
    try:
        import shiboken6

        return bool(shiboken6.isValid(obj))
    except Exception:
        pass
    try:
        obj.metaObject()
        return True
    except Exception:
        return False


def _combo_items(combo) -> list[str]:
    return [str(combo.itemText(i) or "") for i in range(combo.count())]


def _find_item_index_combo(combo, wanted_text: str) -> int:
    return find_item_index(_combo_items(combo), wanted_text)


def _set_combo_value(combo, requested: str, qapp) -> dict[str, Any]:
    idx = _find_item_index_combo(combo, requested)
    if idx < 0:
        return {"requested": requested, "changed": False, "reason": "not-found"}
    before = str(combo.currentText() or "")
    combo.setCurrentIndex(idx)
    qapp.processEvents()
    after = str(combo.currentText() or "")
    return {
        "requested": requested,
        "changed": before != after,
        "before": before,
        "after": after,
        "index": idx,
    }


def _get_mcp_current_view():
    for module_name in ("binary_ninja_mcp.plugin", "plugin"):
        try:
            plugin_module = __import__(module_name, fromlist=["plugin"])
            server = plugin_module.plugin.server
            view = server.binary_ops.current_view
            if view is not None:
                return view
        except Exception:
            continue
    if bn and getattr(bn, "current_view", None) is not None:
        return bn.current_view
    return None


def _set_mcp_current_view(view) -> bool:
    for module_name in ("binary_ninja_mcp.plugin", "plugin"):
        try:
            plugin_module = __import__(module_name, fromlist=["plugin"])
            plugin_module.plugin.server.binary_ops.current_view = view
            return True
        except Exception:
            continue
    return False


def _get_loaded_filename() -> Optional[str]:
    current = _get_mcp_current_view()
    if current is None:
        return None
    try:
        if getattr(current, "file", None) is not None:
            return str(current.file.filename)
    except Exception:
        return None
    return None


def _find_open_view_for_file(filepath: str):
    if not filepath:
        return None
    try:
        expected = str(Path(filepath).resolve())
    except Exception:
        expected = str(filepath)

    try:
        import binaryninjaui as bnui
    except Exception:
        return None

    try:
        contexts = list(bnui.UIContext.allContexts())
    except Exception:
        return None

    for ctx in contexts:
        try:
            tabs = list(ctx.getTabs())
        except Exception:
            continue
        for tab in tabs:
            try:
                view_frame = ctx.getViewFrameForTab(tab)
                if view_frame is None:
                    continue
                view = view_frame.getCurrentBinaryView()
                if view is None or getattr(view, "file", None) is None:
                    continue
                observed = str(Path(view.file.filename).resolve())
            except Exception:
                continue
            if observed == expected:
                return {"context": ctx, "tab": tab, "view": view}
    return None


def _open_with_ui_context(filepath: str) -> dict[str, Any]:
    try:
        import binaryninjaui as bnui
    except Exception:
        return {"ok": False, "reason": "binaryninjaui-unavailable"}

    try:
        contexts = list(bnui.UIContext.allContexts())
    except Exception as exc:
        return {"ok": False, "reason": f"uicontext-list-failed:{exc}"}

    if not contexts:
        return {"ok": False, "reason": "no-uicontext"}

    last_exc = None
    for ctx in contexts:
        try:
            opened = bool(ctx.openFilename(filepath))
        except Exception as exc:
            last_exc = exc
            continue
        if opened:
            return {"ok": True, "reason": "opened"}

    if last_exc is not None:
        return {"ok": False, "reason": f"openFilename-failed:{last_exc}"}
    return {"ok": False, "reason": "openFilename-returned-false"}


def _apply_platform_to_loaded_view(loaded_bv, target_platform: str, result: dict[str, Any]) -> None:
    if not target_platform or loaded_bv is None or bn is None:
        return

    try:
        wanted_arch = bn.Architecture[target_platform]
    except Exception:
        wanted_arch = None

    if wanted_arch is None:
        result["warnings"].append(f"requested platform/arch '{target_platform}' is not registered")
        return

    try:
        current_arch_name = loaded_bv.arch.name if loaded_bv.arch is not None else None
    except Exception:
        current_arch_name = None

    if normalize_token(current_arch_name) != normalize_token(target_platform):
        try:
            loaded_bv.arch = wanted_arch
            result["actions"].append("set_loaded_view_arch")
        except Exception as exc:
            result["warnings"].append(f"unable to set loaded view arch '{target_platform}': {exc}")

    platform_map = {
        "8086": "dos-8086",
    }
    mapped_platform = platform_map.get(target_platform)
    if not mapped_platform:
        return

    try:
        wanted_platform = bn.Platform[mapped_platform]
    except Exception:
        wanted_platform = None

    if wanted_platform is None:
        result["warnings"].append(f"mapped platform '{mapped_platform}' is not registered")
        return

    try:
        cur_platform_name = loaded_bv.platform.name if loaded_bv.platform is not None else None
    except Exception:
        cur_platform_name = None

    if normalize_token(cur_platform_name) != normalize_token(mapped_platform):
        try:
            loaded_bv.platform = wanted_platform
            result["actions"].append("set_loaded_view_platform")
        except Exception as exc:
            result["warnings"].append(
                f"unable to set loaded view platform '{mapped_platform}': {exc}"
            )


def open_file_workflow(
    filepath: str = "",
    platform: str = "",
    view_type: str = "",
    click_open: bool = True,
    inspect_only: bool = False,
    **_unused: Any,
) -> dict[str, Any]:
    """Open a file and automate Binary Ninja's Open With Options dialog."""
    result: dict[str, Any] = {
        "ok": True,
        "input": {
            "filepath": str(filepath or "").strip(),
            "platform": str(platform or "").strip(),
            "view_type": str(view_type or "").strip(),
            "click_open": bool(click_open),
            "inspect_only": bool(inspect_only),
        },
        "actions": [],
        "warnings": [],
        "errors": [],
        "dialog": {
            "present": False,
            "title": None,
            "view_type_set": None,
            "platform_set": None,
            "open_clicked": False,
            "open_button_found": False,
        },
        "state": {
            "active_window": None,
            "visible_windows": [],
            "loaded_filename": None,
        },
    }

    target_file = result["input"]["filepath"]
    target_platform = result["input"]["platform"]
    target_view_type = result["input"]["view_type"]

    if bn is None:
        result["ok"] = False
        result["errors"].append("binaryninja module is unavailable")
        return result

    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:
        result["ok"] = False
        result["errors"].append(f"PySide6 unavailable: {exc}")
        return result

    def handle_open_with_options_dialog(dialog, detected_action: str, app) -> bool:
        if dialog is None or not _is_qt_object_alive(dialog):
            return False

        try:
            dialog_title = str(dialog.windowTitle() or "")
        except Exception:
            result["warnings"].append("open dialog disappeared before it could be handled")
            return False

        result["dialog"]["present"] = True
        result["dialog"]["title"] = dialog_title
        result["actions"].append(detected_action)

        try:
            dialog_children = dialog.findChildren(object)
        except Exception:
            result["warnings"].append("unable to enumerate open dialog controls")
            return False

        combos = []
        for child in dialog_children:
            cls = type(child).__name__
            if cls != "QComboBox":
                continue
            if not hasattr(child, "count") or not hasattr(child, "itemText"):
                continue
            if not hasattr(child, "setCurrentIndex") or not hasattr(child, "currentText"):
                continue
            combos.append(child)

        view_combo = None
        if target_view_type:
            best_view_score = -10**9
            for combo in combos:
                idx = _find_item_index_combo(combo, target_view_type)
                if idx < 0:
                    continue
                count = combo.count()
                items = [normalize_token(combo.itemText(i)) for i in range(count)]
                score = 0
                if normalize_token(combo.itemText(idx)) == normalize_token(target_view_type):
                    score += 100
                if "raw" in items and "mapped" in items:
                    score += 80
                if count <= 6:
                    score += 20
                if count > 20:
                    score -= 60
                if any(item.startswith("analysis.") for item in items):
                    score -= 100
                if score > best_view_score:
                    view_combo = combo
                    best_view_score = score

        if view_combo is None:
            for combo in combos:
                items = {normalize_token(combo.itemText(i)) for i in range(combo.count())}
                if "raw" in items and "mapped" in items:
                    view_combo = combo
                    break

        if target_view_type:
            if view_combo is None:
                result["dialog"]["view_type_set"] = {
                    "requested": target_view_type,
                    "changed": False,
                    "reason": "view-type-control-not-present",
                }
            else:
                view_set = _set_combo_value(view_combo, target_view_type, QApplication)
                result["dialog"]["view_type_set"] = view_set
                if view_set.get("changed"):
                    result["actions"].append("set_view_type")
                elif view_set.get("reason") == "not-found":
                    result["warnings"].append(
                        f"requested view type '{target_view_type}' not available in dialog"
                    )

        if target_platform:
            platform_combo = None
            platform_idx = -1
            best_score = -10**9
            for combo in combos:
                if view_combo is not None and combo is view_combo:
                    continue
                idx = _find_item_index_combo(combo, target_platform)
                if idx < 0:
                    continue
                count = combo.count()
                items = [normalize_token(combo.itemText(i)) for i in range(count)]
                score = 0
                if normalize_token(combo.itemText(idx)) == normalize_token(target_platform):
                    score += 100
                if count >= 12:
                    score += 20
                if any(
                    tok.startswith("x86") or tok.startswith("arm") or tok.startswith("mips")
                    for tok in items
                ):
                    score += 20
                if "raw" in items and "mapped" in items:
                    score -= 200
                if score > best_score:
                    platform_combo = combo
                    platform_idx = idx
                    best_score = score

            if platform_combo is None or platform_idx < 0:
                result["dialog"]["platform_set"] = {
                    "requested": target_platform,
                    "changed": False,
                    "reason": "platform-control-not-present-or-value-missing",
                }
            else:
                before = str(platform_combo.currentText() or "")
                platform_combo.setCurrentIndex(platform_idx)
                QApplication.processEvents()
                after = str(platform_combo.currentText() or "")
                result["dialog"]["platform_set"] = {
                    "requested": target_platform,
                    "changed": before != after,
                    "before": before,
                    "after": after,
                    "index": platform_idx,
                }
                if before != after:
                    result["actions"].append("set_platform")

        if inspect_only or (not click_open):
            return True

        open_button = None
        for button in dialog_children:
            if type(button).__name__ != "QPushButton":
                continue
            if not hasattr(button, "text") or not hasattr(button, "click"):
                continue
            label = str(button.text() or "").replace("&", "").strip().lower()
            if label == "open":
                open_button = button
                break

        result["dialog"]["open_button_found"] = open_button is not None
        if open_button is None:
            result["warnings"].append("open button not found in dialog")
            return True

        if not open_button.isEnabled():
            result["warnings"].append("open button is disabled")
            return True

        try:
            open_button.click()
        except Exception:
            result["warnings"].append("open button click failed")
            return True

        if app is not None:
            for _ in range(10):
                app.processEvents()
                time.sleep(0.02)
        result["dialog"]["open_clicked"] = True
        result["actions"].append("clicked_open_button")

        dialog_still_visible = False
        try:
            dialog_still_visible = bool(dialog.isVisible())
        except Exception:
            dialog_still_visible = False
        if dialog_still_visible and hasattr(dialog, "accept"):
            try:
                dialog.accept()
                if app is not None:
                    for _ in range(10):
                        app.processEvents()
                        time.sleep(0.02)
                try:
                    hidden_after_accept = not dialog.isVisible()
                except Exception:
                    hidden_after_accept = True
                if hidden_after_accept:
                    result["actions"].append("accepted_open_dialog")
            except Exception as exc:
                result["warnings"].append(f"open dialog accept() fallback failed: {exc}")
        return True

    def run_open_workflow() -> Optional[Any]:
        app = QApplication.instance()
        result["state"]["visible_windows"] = _collect_visible_windows(app)
        if app is not None and app.activeWindow() is not None:
            result["state"]["active_window"] = str(app.activeWindow().windowTitle() or "")

        dialog = _find_options_dialog(app)
        loaded_bv = None

        if dialog is not None:
            handle_open_with_options_dialog(dialog, "detected_open_with_options_dialog", app)
        else:
            result["actions"].append("no_open_with_options_dialog")
            if inspect_only:
                result["actions"].append("inspect_only_no_load")
            elif not target_file:
                result["warnings"].append("no filepath provided and no dialog to accept")
            else:
                existing = _find_open_view_for_file(target_file)
                if existing is not None:
                    loaded_bv = existing.get("view")
                    result["actions"].append("reuse_existing_tab_for_file")
                    try:
                        existing["context"].activateTab(existing["tab"])
                        result["actions"].append("activate_existing_tab_for_file")
                    except Exception as exc:
                        result["warnings"].append(f"unable to activate existing tab: {exc}")

                ui_open = {"ok": False, "reason": "skipped"}
                if loaded_bv is None and app is not None:
                    ui_open = _open_with_ui_context(target_file)
                    if ui_open.get("ok"):
                        result["actions"].append("ui_context_open_filename")
                    else:
                        result["warnings"].append(
                            f"ui_context_open_filename: {ui_open.get('reason')}"
                        )

                if loaded_bv is None and not ui_open.get("ok"):
                    if target_platform or target_view_type:
                        result["warnings"].append(
                            "no open dialog visible; --platform/--view-type were not forced (bn.load defaults used)"
                        )
                    try:
                        loaded_bv = bn.load(target_file)
                        result["actions"].append("bn.load")
                    except Exception as exc:
                        result["errors"].append(f"bn.load failed: {exc}")

                if app is not None:
                    deadline = time.time() + 6.0
                    while time.time() < deadline:
                        app.processEvents()
                        post_dialog = _find_options_dialog(app)
                        if post_dialog is not None:
                            handle_open_with_options_dialog(
                                post_dialog,
                                "detected_open_with_options_dialog_after_open",
                                app,
                            )
                        loaded_now = _get_loaded_filename()
                        if loaded_now:
                            try:
                                expected_now = str(Path(target_file).resolve())
                                observed_now = str(Path(loaded_now).resolve())
                                if observed_now == expected_now:
                                    break
                            except Exception:
                                break
                        time.sleep(0.05)

        if app is not None and (not inspect_only) and click_open:
            deadline = time.time() + 8.0
            while time.time() < deadline:
                lingering = _find_options_dialog(app)
                if lingering is None:
                    break
                handle_open_with_options_dialog(
                    lingering,
                    "resolved_open_with_options_dialog_final_pass",
                    app,
                )
                for _ in range(12):
                    app.processEvents()
                    time.sleep(0.02)

            if _find_options_dialog(app) is not None:
                result["warnings"].append("open dialog remained visible after final resolution pass")

        if loaded_bv is None:
            loaded_bv = _get_mcp_current_view()

        _apply_platform_to_loaded_view(loaded_bv, target_platform, result)

        if loaded_bv is not None:
            if _set_mcp_current_view(loaded_bv):
                result["actions"].append("set_current_view")
            else:
                result["warnings"].append("unable to set MCP current_view")

        if app is not None:
            result["state"]["visible_windows"] = _collect_visible_windows(app)
            if app.activeWindow() is not None:
                result["state"]["active_window"] = str(app.activeWindow().windowTitle() or "")
            else:
                result["state"]["active_window"] = None

        loaded_filename = _get_loaded_filename()
        result["state"]["loaded_filename"] = loaded_filename
        if loaded_bv is not None:
            try:
                result["state"]["loaded_arch"] = str(
                    loaded_bv.arch.name if loaded_bv.arch is not None else None
                )
            except Exception:
                result["state"]["loaded_arch"] = None

        if target_file:
            try:
                expected = str(Path(target_file).resolve())
                observed = str(Path(loaded_filename).resolve()) if loaded_filename else None
            except Exception:
                expected = target_file
                observed = loaded_filename

            if observed is None:
                result["warnings"].append("no loaded filename reported by MCP")
            elif observed != expected:
                result["warnings"].append(
                    f"loaded filename differs (expected {expected}, got {observed})"
                )

        if target_platform and result["state"].get("loaded_arch"):
            if normalize_token(result["state"]["loaded_arch"]) != normalize_token(target_platform):
                result["warnings"].append(
                    "loaded arch "
                    f"({result['state']['loaded_arch']}) differs from requested platform ({target_platform})"
                )

        return loaded_bv

    def run_non_ui_fallback_load():
        fallback_bv = None
        if not target_file:
            return fallback_bv
        try:
            fallback_bv = bn.load(target_file)
            result["actions"].append("bn.load_non_ui_fallback")
        except Exception as exc:
            result["errors"].append(f"non-ui fallback load failed: {exc}")
        return fallback_bv

    loaded_bv = None
    if hasattr(bn, "execute_on_main_thread_and_wait"):
        state = {"done": False, "loaded_bv": None}

        def _main_thread_runner():
            state["loaded_bv"] = run_open_workflow()
            state["done"] = True

        try:
            bn.execute_on_main_thread_and_wait(_main_thread_runner)
            if state["done"]:
                loaded_bv = state["loaded_bv"]
                result["actions"].append("ran_open_workflow_on_main_thread")
            else:
                result["warnings"].append(
                    "main-thread open workflow did not complete; running non-ui fallback"
                )
                loaded_bv = run_non_ui_fallback_load()
        except Exception as exc:
            result["warnings"].append(
                f"main-thread open workflow failed: {exc}; running non-ui fallback"
            )
            loaded_bv = run_non_ui_fallback_load()
    else:
        loaded_bv = run_non_ui_fallback_load()

    app = QApplication.instance()
    if app is not None:
        result["state"]["visible_windows"] = _collect_visible_windows(app)
        if app.activeWindow() is not None:
            result["state"]["active_window"] = str(app.activeWindow().windowTitle() or "")
        else:
            result["state"]["active_window"] = None

    if loaded_bv is not None:
        if _set_mcp_current_view(loaded_bv) and "set_current_view" not in result["actions"]:
            result["actions"].append("set_current_view")

    loaded_filename = _get_loaded_filename()
    if loaded_filename is None and loaded_bv is not None:
        try:
            if getattr(loaded_bv, "file", None) is not None:
                loaded_filename = str(loaded_bv.file.filename)
        except Exception:
            pass
    result["state"]["loaded_filename"] = loaded_filename

    if loaded_bv is not None and not result["state"].get("loaded_arch"):
        try:
            result["state"]["loaded_arch"] = str(loaded_bv.arch.name if loaded_bv.arch else None)
        except Exception:
            result["state"]["loaded_arch"] = None

    if result["errors"]:
        result["ok"] = False

    return result
