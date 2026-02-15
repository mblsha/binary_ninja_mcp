"""Binary Ninja UI quit workflow automation."""

from __future__ import annotations

import time
import threading
from pathlib import Path
from typing import Any, Optional

from .text import normalize_label

try:
    import binaryninja as bn
except Exception:
    bn = None


def _decision_priorities(decision: str) -> list[str]:
    if decision == "save":
        return ["save", "save changes", "save all", "yes"]
    if decision == "dont-save":
        return [
            "don't save",
            "dont save",
            "close without saving",
            "close without save",
            "discard changes",
            "discard",
            "no",
        ]
    if decision == "cancel":
        return ["cancel"]
    return []


def normalize_decision(value: str) -> str:
    decision = str(value or "auto").strip().lower()
    if decision == "dont_save":
        return "dont-save"
    if decision in {"auto", "save", "dont-save", "cancel"}:
        return decision
    return "auto"


def resolve_policy(loaded_filename: Optional[str], decision: str) -> tuple[str, bool, bool]:
    loaded_is_bndb = False
    companion_exists = False
    resolved = normalize_decision(decision)

    if loaded_filename:
        try:
            loaded_name = str(loaded_filename).strip()
            loaded_is_bndb = loaded_name.lower().endswith(".bndb")
            if not loaded_is_bndb:
                companion_exists = Path(loaded_name + ".bndb").exists()
        except Exception:
            pass

    if resolved == "auto":
        resolved = "save" if (loaded_is_bndb or companion_exists) else "dont-save"
    return resolved, loaded_is_bndb, companion_exists


def compute_database_save_target(
    loaded_filename: Optional[str],
    loaded_is_bndb: bool,
    companion_exists: bool,
) -> Optional[str]:
    if not loaded_filename:
        return None

    loaded_name = str(loaded_filename).strip()
    if not loaded_name:
        return None
    if loaded_is_bndb:
        return loaded_name

    # Always target a .bndb sibling for non-bndb inputs; this prevents
    # accidental writes to the original binary path.
    companion = loaded_name + ".bndb"
    if companion_exists:
        return companion
    return companion


def choose_decision_label(labels: list[str], decision: str) -> Optional[str]:
    priorities = _decision_priorities(normalize_decision(decision))
    candidates = [(label, normalize_label(label)) for label in labels if normalize_label(label)]

    for wanted in priorities:
        for raw, norm in candidates:
            if norm == wanted:
                return raw
        for raw, norm in candidates:
            if wanted in norm:
                return raw
    return None


def _collect_visible_windows(app) -> list[dict[str, str]]:
    out = []
    if app is None:
        return out
    for widget in app.topLevelWidgets():
        try:
            if not widget.isVisible():
                continue
            out.append({"class": type(widget).__name__, "title": str(widget.windowTitle() or "")})
        except Exception:
            continue
    return out


def _get_current_bv():
    for module_name in ("binary_ninja_mcp.plugin", "plugin"):
        try:
            plugin_module = __import__(module_name, fromlist=["plugin"])
            current = plugin_module.plugin.server.binary_ops.current_view
            if current is not None:
                return current
        except Exception:
            continue
    if bn is not None and getattr(bn, "current_view", None) is not None:
        return bn.current_view
    return None


def _get_loaded_filename() -> Optional[str]:
    current_bv = _get_current_bv()
    if current_bv is None:
        return None
    try:
        if getattr(current_bv, "file", None) is not None:
            return str(current_bv.file.filename)
    except Exception:
        return None
    return None


def quit_workflow(
    decision: str = "auto",
    mark_dirty: bool = False,
    inspect_only: bool = False,
    wait_ms: int = 2000,
    quit_app: bool = False,
    quit_delay_ms: int = 300,
    **_unused: Any,
) -> dict[str, Any]:
    """Close windows/tabs and auto-answer save dialogs."""

    decision_in = normalize_decision(decision)
    wait_ms = max(0, int(wait_ms or 2000))
    quit_delay_ms = max(0, int(quit_delay_ms or 300))
    # Optional override for main-thread quit workflow completion timeout.
    # Kept separate from dialog wait_ms because pre-save can be significantly slower.
    timeout_override_raw = _unused.get("workflow_timeout_s")
    if timeout_override_raw is None:
        timeout_override_raw = _unused.get("main_thread_timeout_s")
    try:
        workflow_timeout_override_s = (
            float(timeout_override_raw) if timeout_override_raw is not None else None
        )
    except Exception:
        workflow_timeout_override_s = None

    result: dict[str, Any] = {
        "ok": True,
        "input": {
            "decision": decision_in,
            "mark_dirty": bool(mark_dirty),
            "inspect_only": bool(inspect_only),
            "wait_ms": wait_ms,
        },
        "policy": {
            "resolved_decision": None,
            "loaded_filename": None,
            "loaded_is_bndb": False,
            "companion_bndb_exists": False,
            "save_target": None,
        },
        "state": {
            "active_window_before": None,
            "active_window_after": None,
            "visible_windows_before": [],
            "visible_windows_after": [],
            "dialogs_before_action": [],
            "dialogs_after_action": [],
            "stuck_confirmation": False,
            "quit_on_last_window_closed_before": None,
            "quit_on_last_window_closed_after": None,
            "pre_saved_database": None,
        },
        "actions": [],
        "warnings": [],
        "errors": [],
    }

    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QApplication, QPushButton
    except Exception as exc:
        result["ok"] = False
        result["errors"].append(f"PySide6 unavailable: {exc}")
        return result

    def collect_confirmation_dialogs(app) -> list[dict[str, Any]]:
        dialogs = []
        if app is None:
            return dialogs
        for widget in app.topLevelWidgets():
            try:
                if not widget.isVisible():
                    continue
            except Exception:
                continue

            try:
                # Search recursively; some Qt dialogs nest buttons in a button-box.
                push_buttons = widget.findChildren(QPushButton)
            except Exception:
                push_buttons = []

            buttons = []
            for button in push_buttons:
                try:
                    if not button.isVisible():
                        continue
                    text = normalize_label(button.text())
                    if not text:
                        continue
                    buttons.append(
                        {
                            "text": str(button.text() or ""),
                            "norm": text,
                            "enabled": bool(button.isEnabled()),
                        }
                    )
                except Exception:
                    continue

            if not buttons:
                title_norm = normalize_label(widget.windowTitle())
                cls_norm = type(widget).__name__.lower()
                is_modal = False
                try:
                    is_modal = bool(widget.isModal())
                except Exception:
                    is_modal = False
                if (
                    ("messagebox" not in cls_norm)
                    and ("modified" not in title_norm)
                    and ("dialog" not in cls_norm or not is_modal)
                ):
                    continue

            tokens = {b["norm"] for b in buttons}
            has_save_token = any("save" in token for token in tokens)
            has_reject_token = any(
                ("don't save" in token)
                or ("dont save" in token)
                or ("discard" in token)
                or ("close without saving" in token)
                or ("close without save" in token)
                or (token == "no")
                for token in tokens
            )
            has_cancel_token = any("cancel" in token for token in tokens)
            title_norm = normalize_label(widget.windowTitle())
            cls_norm = type(widget).__name__.lower()
            looks_modal_save_prompt = ("messagebox" in cls_norm) and (
                "modified" in title_norm or "save" in title_norm
            )
            if has_save_token or has_reject_token or has_cancel_token or looks_modal_save_prompt:
                dialogs.append(
                    {
                        "title": str(widget.windowTitle() or ""),
                        "class": type(widget).__name__,
                        "buttons": buttons,
                        "_widget": widget,
                    }
                )
        return dialogs

    def find_button_for_decision(dialog_widget, resolved_decision: str):
        priorities = _decision_priorities(resolved_decision)
        candidates = []
        try:
            buttons = dialog_widget.findChildren(QPushButton)
        except Exception:
            buttons = []

        for button in buttons:
            try:
                if not button.isVisible():
                    continue
                label = str(button.text() or "")
                norm = normalize_label(label)
                if not norm:
                    continue
                candidates.append((button, label, norm))
            except Exception:
                continue

        for wanted in priorities:
            for button, label, norm in candidates:
                if norm == wanted:
                    return button, label
            for button, label, norm in candidates:
                if wanted in norm:
                    return button, label
        return None, None

    def dialog_priority(dialog: dict[str, Any]) -> int:
        cls = str(dialog.get("class") or "").lower()
        title = normalize_label(dialog.get("title"))
        if "messagebox" in cls:
            return 0
        if "dialog" in cls:
            return 1
        if "modified" in title or "save" in title:
            return 2
        return 9

    def click_confirmation_dialog(app, resolved_decision: str) -> bool:
        dialogs_local = collect_confirmation_dialogs(app)
        if not dialogs_local:
            return False
        dialogs_local = sorted(dialogs_local, key=dialog_priority)
        chosen_dialog = dialogs_local[0]
        chosen_button, chosen_label = find_button_for_decision(
            chosen_dialog["_widget"], resolved_decision
        )
        if chosen_button is None:
            warn = (
                f"confirmation dialog detected but no matching '{resolved_decision}' button found"
            )
            if warn not in result["warnings"]:
                result["warnings"].append(warn)
            return False
        if not chosen_button.isEnabled():
            warn = f"matched confirmation button '{chosen_label}' is disabled"
            if warn not in result["warnings"]:
                result["warnings"].append(warn)
            return False
        chosen_button.click()
        result["actions"].append(f"clicked_confirmation_button:{str(chosen_label)}")
        return True

    def find_primary_main_window(app):
        if app is None:
            return None
        for widget in app.topLevelWidgets():
            try:
                if not widget.isVisible():
                    continue
                if "mainwindow" in type(widget).__name__.lower():
                    return widget
            except Exception:
                continue
        return None

    def trigger_close_tab(main_window):
        if main_window is None:
            return False, "no_main_window"
        try:
            actions = main_window.findChildren(QAction)
        except Exception:
            actions = []

        best = None
        for action in actions:
            try:
                text = normalize_label(action.text())
            except Exception:
                continue
            if not text:
                continue
            if text == "close tab":
                best = action
                break
            if ("close" in text) and ("tab" in text) and best is None:
                best = action
        if best is None:
            return False, "close_tab_action_not_found"
        if not best.isEnabled():
            return False, "close_tab_action_disabled"
        try:
            QTimer.singleShot(0, best.trigger)
            return True, "close_tab_action_queued"
        except Exception as exc:
            return False, f"close_tab_action_trigger_failed:{exc}"

    def _runner() -> dict[str, Any]:
        app = QApplication.instance()

        result["state"]["visible_windows_before"] = _collect_visible_windows(app)
        if app is not None and app.activeWindow() is not None:
            result["state"]["active_window_before"] = str(app.activeWindow().windowTitle() or "")
        if app is not None:
            try:
                result["state"]["quit_on_last_window_closed_before"] = bool(
                    app.quitOnLastWindowClosed()
                )
            except Exception:
                result["state"]["quit_on_last_window_closed_before"] = None

        loaded_filename = _get_loaded_filename()
        result["policy"]["loaded_filename"] = loaded_filename
        resolved, loaded_is_bndb, companion_exists = resolve_policy(loaded_filename, decision_in)
        result["policy"]["resolved_decision"] = resolved
        result["policy"]["loaded_is_bndb"] = loaded_is_bndb
        result["policy"]["companion_bndb_exists"] = companion_exists

        if mark_dirty:
            current_bv = _get_current_bv()
            if current_bv is None or getattr(current_bv, "file", None) is None:
                result["warnings"].append("no current BinaryView available to mark dirty")
            else:
                try:
                    current_bv.file.modified = True
                    result["actions"].append("marked_current_view_modified")
                except Exception as exc:
                    result["warnings"].append(f"unable to mark current view modified: {exc}")

        if (not inspect_only) and (resolved == "save"):
            current_bv = _get_current_bv()
            save_target = compute_database_save_target(
                loaded_filename=loaded_filename,
                loaded_is_bndb=loaded_is_bndb,
                companion_exists=companion_exists,
            )
            result["policy"]["save_target"] = save_target
            if current_bv is None:
                result["warnings"].append(
                    "save policy selected but no current BinaryView is available"
                )
            elif not save_target:
                result["warnings"].append(
                    "save policy selected but no save target could be resolved"
                )
            else:
                try:
                    save_ok = bool(current_bv.create_database(str(save_target)))
                    result["state"]["pre_saved_database"] = save_ok
                    result["actions"].append(f"pre_saved_database:{save_ok}")
                    if save_ok and getattr(current_bv, "file", None) is not None:
                        try:
                            current_bv.file.modified = False
                            result["actions"].append("cleared_modified_after_pre_save")
                        except Exception:
                            pass
                except Exception as exc:
                    result["warnings"].append(f"pre-save failed: {exc}")

        dialogs = collect_confirmation_dialogs(app)
        result["state"]["dialogs_before_action"] = [
            {
                "title": dialog["title"],
                "class": dialog["class"],
                "buttons": dialog["buttons"],
            }
            for dialog in dialogs
        ]

        if not inspect_only:
            if app is None:
                result["errors"].append("QApplication instance is not available")
            else:
                try:
                    app.setQuitOnLastWindowClosed(False)
                    result["actions"].append("set_quit_on_last_window_closed:false")
                except Exception as exc:
                    result["warnings"].append(
                        f"unable to disable quitOnLastWindowClosed before close: {exc}"
                    )

                close_requested = False
                if not dialogs:
                    main_window = find_primary_main_window(app)
                    close_tab_ok, close_tab_reason = trigger_close_tab(main_window)
                    if close_tab_ok:
                        result["actions"].append(close_tab_reason)
                        close_requested = True
                    else:
                        result["warnings"].append(close_tab_reason)
                        if quit_app:
                            queued = 0
                            for widget in app.topLevelWidgets():
                                try:
                                    if not widget.isVisible():
                                        continue
                                    cls_name = type(widget).__name__.lower()
                                    if "mainwindow" not in cls_name:
                                        continue
                                    QTimer.singleShot(0, widget.close)
                                    queued += 1
                                except Exception:
                                    continue
                            result["actions"].append(f"queued_close_main_windows:{queued}")
                            close_requested = queued > 0
                        else:
                            result["actions"].append("skipped_close_main_windows_without_quit_app")

                click_count_holder = {"count": 0}
                dialog_watch_timer = QTimer()
                dialog_watch_timer.setInterval(30)

                def _watch_dialogs() -> None:
                    try:
                        if click_confirmation_dialog(app, resolved):
                            click_count_holder["count"] += 1
                    except Exception:
                        # Keep the watcher alive even if one click attempt fails.
                        return

                dialog_watch_timer.timeout.connect(_watch_dialogs)
                dialog_watch_timer.start()

                deadline = time.time() + (wait_ms / 1000.0)
                quiet_cycles = 0
                try:
                    while time.time() < deadline:
                        app.processEvents()

                        dialogs = collect_confirmation_dialogs(app)
                        if dialogs:
                            quiet_cycles = 0
                        else:
                            quiet_cycles += 1
                            if close_requested and quiet_cycles >= 5:
                                break
                        time.sleep(0.03)
                finally:
                    dialog_watch_timer.stop()

                dialogs = collect_confirmation_dialogs(app)
                if click_count_holder["count"] == 0 and not dialogs:
                    result["actions"].append("no_confirmation_dialog_detected_after_close")

        dialogs_after = collect_confirmation_dialogs(app)
        result["state"]["dialogs_after_action"] = [
            {
                "title": dialog["title"],
                "class": dialog["class"],
                "buttons": dialog["buttons"],
            }
            for dialog in dialogs_after
        ]
        result["state"]["stuck_confirmation"] = len(dialogs_after) > 0

        if app is not None:
            result["state"]["visible_windows_after"] = _collect_visible_windows(app)
            if app.activeWindow() is not None:
                result["state"]["active_window_after"] = str(app.activeWindow().windowTitle() or "")
            else:
                result["state"]["active_window_after"] = None
            try:
                result["state"]["quit_on_last_window_closed_after"] = bool(
                    app.quitOnLastWindowClosed()
                )
            except Exception:
                result["state"]["quit_on_last_window_closed_after"] = None

            if quit_app and (not inspect_only):
                try:
                    QTimer.singleShot(quit_delay_ms, app.quit)
                    result["actions"].append(f"scheduled_app_quit:{quit_delay_ms}ms")
                except Exception as exc:
                    result["warnings"].append(f"unable to schedule app.quit(): {exc}")
            elif quit_app and inspect_only:
                result["actions"].append("skipped_app_quit_inspect_only")

        if result["errors"]:
            result["ok"] = False
        return result

    if bn is not None and (
        hasattr(bn, "execute_on_main_thread") or hasattr(bn, "execute_on_main_thread_and_wait")
    ):
        state = {"result": None, "exception": None}
        finished = threading.Event()

        def _main_thread_runner() -> None:
            try:
                state["result"] = _runner()
            except Exception as exc:
                state["exception"] = exc
            finally:
                finished.set()

        scheduled = False
        if hasattr(bn, "execute_on_main_thread"):
            try:
                bn.execute_on_main_thread(_main_thread_runner)
                result["actions"].append("scheduled_quit_workflow_on_main_thread")
                scheduled = True
            except Exception as exc:
                result["warnings"].append(f"failed to schedule quit workflow on main thread: {exc}")

        if (not scheduled) and hasattr(bn, "execute_on_main_thread_and_wait"):

            def _worker() -> None:
                try:
                    bn.execute_on_main_thread_and_wait(_main_thread_runner)
                except Exception as exc:
                    state["exception"] = exc
                    finished.set()

            threading.Thread(target=_worker, daemon=True).start()
            result["actions"].append("scheduled_quit_workflow_via_helper_thread")
            scheduled = True

        if scheduled:
            # Keep a bounded wait to avoid hanging clients indefinitely, but allow
            # significantly longer runtime for save flows where create_database()
            # can legitimately take much longer than dialog wait_ms.
            if (workflow_timeout_override_s is not None) and (workflow_timeout_override_s > 0):
                wait_timeout_s = float(workflow_timeout_override_s)
            elif inspect_only:
                wait_timeout_s = max(30.0, (wait_ms / 1000.0) + 15.0)
            elif decision_in in {"save", "auto"}:
                wait_timeout_s = max(600.0, (wait_ms / 1000.0) + 30.0)
            else:
                wait_timeout_s = max(120.0, (wait_ms / 1000.0) + 30.0)
            if not finished.wait(wait_timeout_s):
                result["ok"] = False
                result["errors"].append(
                    f"quit workflow timed out after {wait_timeout_s:.1f}s on main thread"
                )
                return result

            if state["exception"] is not None:
                result["ok"] = False
                result["errors"].append(f"quit main-thread execution failed: {state['exception']}")
                return result

            if isinstance(state["result"], dict):
                return state["result"]

    return _runner()
