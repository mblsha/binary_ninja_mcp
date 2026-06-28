"""Binary Ninja UI tab close workflow automation."""

from __future__ import annotations

import os
import platform
import subprocess
import threading
import time
from typing import Any, Optional

from .quit_app import (
    choose_decision_label,
    normalize_decision,
    resolve_policy,
)

try:
    import binaryninja as bn
except Exception:
    bn = None


MACOS_MANUAL_SAVE_SHEET_ERROR = (
    "Manual resolution required: Binary Ninja is showing a macOS save confirmation, "
    "but binja-cli could not safely read its buttons. Select Save, Don't Save, or "
    "Cancel in Binary Ninja, then retry the command."
)


def _coerce_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        text = str(value).strip()
    except Exception:
        return None
    return text or None


def _filename_matches(observed: Optional[str], requested: Optional[str]) -> bool:
    observed_text = _coerce_text(observed)
    requested_text = _coerce_text(requested)
    if not observed_text or not requested_text:
        return False
    if observed_text == requested_text:
        return True
    if observed_text.lower() == requested_text.lower():
        return True

    try:
        from pathlib import Path

        observed_path = Path(observed_text)
        requested_path = Path(requested_text)
        if observed_path.name and observed_path.name.lower() == requested_path.name.lower():
            return True
    except Exception:
        pass
    return False


def _view_id_candidates(raw: Any) -> set[str]:
    text = _coerce_text(raw)
    if not text:
        return set()
    out = {text, text.lower()}
    try:
        value = int(text, 0)
        out.add(str(value))
        out.add(hex(value))
    except Exception:
        pass
    return out


def _get_view_id(view: Any) -> Optional[str]:
    if view is None:
        return None
    for attr in ("_binja_mcp_view_id", "mcp_view_id", "view_id", "session_id", "identifier"):
        try:
            raw = getattr(view, attr, None)
        except Exception:
            raw = None
        text = _coerce_text(raw)
        if text:
            return text
    try:
        return str(id(view))
    except Exception:
        return None


def _view_matches_id(view: Any, requested_view_id: Optional[str]) -> bool:
    if not requested_view_id:
        return False
    observed = _view_id_candidates(_get_view_id(view))
    requested = _view_id_candidates(requested_view_id)
    return bool(observed.intersection(requested))


def _view_filename(view: Any) -> Optional[str]:
    try:
        file_obj = getattr(view, "file", None)
        filename = getattr(file_obj, "filename", None) if file_obj is not None else None
        return _coerce_text(filename)
    except Exception:
        return None


def _view_modified(view: Any) -> bool:
    try:
        file_obj = getattr(view, "file", None)
        return bool(getattr(file_obj, "modified", False)) if file_obj is not None else False
    except Exception:
        return False


def _get_view_from_frame(view_frame: Any) -> Any:
    if view_frame is None:
        return None
    try:
        view_iface = view_frame.getCurrentViewInterface()
        if view_iface is not None and hasattr(view_iface, "getData"):
            view = view_iface.getData()
            if view is not None:
                return view
    except Exception:
        pass
    try:
        return view_frame.getCurrentBinaryView()
    except Exception:
        return None


def list_visible_tab_records(bnui: Any) -> list[dict[str, Any]]:
    """Return visible UI tab records with context, tab, view, and filename."""
    if bnui is None:
        return []
    ui_context_cls = getattr(bnui, "UIContext", None)
    if ui_context_cls is None:
        return []

    contexts: list[Any] = []
    try:
        active = ui_context_cls.activeContext()
        if active is not None:
            contexts.append(active)
    except Exception:
        pass
    try:
        for ctx in list(ui_context_cls.allContexts()):
            if ctx not in contexts:
                contexts.append(ctx)
    except Exception:
        pass

    records: list[dict[str, Any]] = []
    for ctx_index, ctx in enumerate(contexts):
        try:
            tabs = list(ctx.getTabs())
        except Exception:
            tabs = []
        for tab_index, tab in enumerate(tabs):
            try:
                frame = ctx.getViewFrameForTab(tab)
            except Exception:
                frame = None
            view = _get_view_from_frame(frame)
            records.append(
                {
                    "context": ctx,
                    "tab": tab,
                    "view": view,
                    "view_id": _get_view_id(view),
                    "filename": _view_filename(view),
                    "modified": _view_modified(view),
                    "context_index": ctx_index,
                    "tab_index": tab_index,
                }
            )
    return records


def _select_records(
    records: list[dict[str, Any]],
    *,
    view_id: Optional[str] = None,
    filename: Optional[str] = None,
    all_tabs: bool = False,
    except_view_id: Optional[str] = None,
    except_filename: Optional[str] = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    selected: list[dict[str, Any]] = []
    excluded_count = 0

    for record in records:
        view = record.get("view")
        if except_view_id and _view_matches_id(view, except_view_id):
            excluded_count += 1
            continue
        if except_filename and _filename_matches(record.get("filename"), except_filename):
            excluded_count += 1
            continue

        if all_tabs:
            selected.append(record)
            continue
        if view_id and _view_matches_id(view, view_id):
            selected.append(record)
            continue
        if filename and _filename_matches(record.get("filename"), filename):
            selected.append(record)

    if all_tabs and (except_view_id or except_filename) and excluded_count == 0:
        warnings.append("no visible UI tab matched any exclusion selector")
        return [], warnings
    if not selected and (view_id or filename) and not all_tabs:
        warnings.append("no visible UI tab matched the requested selector")
    if not selected and all_tabs and not (except_view_id or except_filename):
        warnings.append("no visible UI tabs available to close")
    return selected, warnings


def _collect_save_dialogs(app: Any) -> list[dict[str, Any]]:
    try:
        from PySide6.QtWidgets import QPushButton
    except Exception:
        return []
    try:
        from PySide6.QtWidgets import QLabel
    except Exception:
        QLabel = None

    dialogs: list[dict[str, Any]] = []
    if app is None:
        return dialogs
    for widget in app.topLevelWidgets():
        try:
            if not widget.isVisible():
                continue
            buttons = widget.findChildren(QPushButton)
        except Exception:
            continue

        button_records = []
        for button in buttons:
            try:
                if not button.isVisible():
                    continue
                text = str(button.text() or "")
                if text.strip():
                    button_records.append({"text": text, "enabled": bool(button.isEnabled())})
            except Exception:
                continue

        labels = [item["text"] for item in button_records]
        prompt_text_parts = [str(widget.windowTitle() or "")]
        if QLabel is not None:
            try:
                for label in widget.findChildren(QLabel):
                    try:
                        text = str(label.text() or "").strip()
                    except Exception:
                        text = ""
                    if text:
                        prompt_text_parts.append(text)
            except Exception:
                pass
        prompt_text_parts.extend(labels)
        prompt_text = " ".join(prompt_text_parts)
        if _looks_like_macos_save_sheet(prompt_text) and (
            choose_decision_label(labels, "save")
            or choose_decision_label(labels, "dont-save")
            or choose_decision_label(labels, "cancel")
        ):
            dialogs.append(
                {
                    "title": str(widget.windowTitle() or ""),
                    "class": type(widget).__name__,
                    "buttons": button_records,
                    "_widget": widget,
                }
            )
    return dialogs


def _click_save_dialog(app: Any, decision: str) -> bool:
    try:
        from PySide6.QtWidgets import QPushButton
    except Exception:
        return False

    dialogs = _collect_save_dialogs(app)
    if not dialogs:
        return False
    dialog = dialogs[0]
    buttons = []
    try:
        buttons = dialog["_widget"].findChildren(QPushButton)
    except Exception:
        return False
    label = choose_decision_label([str(button.text() or "") for button in buttons], decision)
    if not label:
        return False
    for button in buttons:
        try:
            if str(button.text() or "") == label and button.isVisible() and button.isEnabled():
                button.click()
                return True
        except Exception:
            continue
    return False


def _looks_like_macos_save_prompt(text: str) -> bool:
    lowered = str(text or "").lower()
    if "save" not in lowered:
        return False
    return (
        "modified" in lowered
        or "changed" in lowered
        or "do you want to save" in lowered
        or "save it before closing" in lowered
        or "save changes" in lowered
    )


def _looks_like_macos_save_sheet(text: str) -> bool:
    lowered = str(text or "").lower()
    if not _looks_like_macos_save_prompt(lowered):
        return False
    has_discard = (
        "don't save" in lowered
        or "dont save" in lowered
        or "discard" in lowered
        or "close without saving" in lowered
        or "close without save" in lowered
    )
    has_cancel = "cancel" in lowered
    return has_discard or has_cancel


def _looks_like_macos_manual_save_sheet(text: str) -> bool:
    return _looks_like_macos_save_prompt(text) and not _looks_like_macos_save_sheet(text)


def _current_process_id() -> int:
    try:
        return int(os.getpid())
    except Exception:
        return 0


def _macos_save_sheet_texts(process_id: Optional[int] = None) -> list[str]:
    if platform.system() != "Darwin":
        return []
    pid = _current_process_id() if process_id is None else int(process_id or 0)
    if pid <= 0:
        return []
    script = (
        'tell application "System Events"\n'
        f"  set targetPid to {pid}\n"
        "  set outputLines to {}\n"
        "  set targetProcesses to every process whose unix id is targetPid\n"
        "  repeat with targetProcess in targetProcesses\n"
        "    tell targetProcess\n"
        "      repeat with w in windows\n"
        "        try\n"
        "          repeat with s in sheets of w\n"
        "            set bits to {}\n"
        "            try\n"
        "              set end of bits to (name of s as text)\n"
        "            end try\n"
        "            try\n"
        "              set end of bits to ((value of every static text of s) as text)\n"
        "            end try\n"
        "            try\n"
        "              set end of bits to ((name of every button of s) as text)\n"
        "            end try\n"
        '            set AppleScript\'s text item delimiters to " "\n'
        "            set end of outputLines to (bits as text)\n"
        "          end repeat\n"
        "        end try\n"
        "      end repeat\n"
        "    end tell\n"
        "  end repeat\n"
        "  set AppleScript's text item delimiters to linefeed\n"
        "  return outputLines as text\n"
        "end tell\n"
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=0.75,
            check=False,
        )
        if proc.returncode != 0:
            return []
        return [line.strip() for line in str(proc.stdout or "").splitlines() if line.strip()]
    except Exception:
        return []


def _macos_save_sheet_count(process_id: Optional[int] = None) -> int:
    return sum(
        1
        for text in _macos_save_sheet_texts(process_id=process_id)
        if _looks_like_macos_save_sheet(text)
    )


def _macos_manual_save_sheet_count(process_id: Optional[int] = None) -> int:
    return sum(
        1
        for text in _macos_save_sheet_texts(process_id=process_id)
        if _looks_like_macos_manual_save_sheet(text)
    )


def _macos_any_save_sheet_count(process_id: Optional[int] = None) -> int:
    texts = _macos_save_sheet_texts(process_id=process_id)
    return sum(
        1
        for text in texts
        if _looks_like_macos_save_sheet(text) or _looks_like_macos_manual_save_sheet(text)
    )


def _macos_decision_shortcut(decision: str) -> Optional[str]:
    decision_in = normalize_decision(decision)
    if decision_in == "save":
        return "key code 36"
    if decision_in == "dont-save":
        return 'keystroke "d" using command down'
    if decision_in == "cancel":
        return "key code 53"
    return None


def _click_macos_save_sheet(decision: str) -> bool:
    shortcut = _macos_decision_shortcut(decision)
    if not shortcut:
        return False
    pid = _current_process_id()
    if pid <= 0:
        return False
    if _macos_save_sheet_count(process_id=pid) <= 0:
        return False
    script = (
        'tell application "System Events"\n'
        f"  set targetPid to {pid}\n"
        "  set targetProcesses to every process whose unix id is targetPid\n"
        '  if (count of targetProcesses) is 0 then return "missing-process"\n'
        "  set frontmost of item 1 of targetProcesses to true\n"
        "  delay 0.05\n"
        f"  {shortcut}\n"
        '  return "sent"\n'
        "end tell\n"
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=0.75,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _queue_close_tab(qtimer_cls: Any, ctx: Any, tab: Any) -> bool:
    try:
        single_shot = getattr(qtimer_cls, "singleShot", None)
        if callable(single_shot):
            single_shot(0, lambda: ctx.closeTab(tab))
            return True
    except Exception:
        pass
    ctx.closeTab(tab)
    return False


def close_tabs_workflow(
    *,
    decision: str = "auto",
    view_id: str = "",
    filename: str = "",
    all_tabs: bool = False,
    except_view_id: str = "",
    except_filename: str = "",
    inspect_only: bool = False,
    wait_ms: int = 2000,
    **_unused: Any,
) -> dict[str, Any]:
    """Close visible Binary Ninja UI tabs and auto-answer dirty-save prompts."""
    decision_in = normalize_decision(decision)
    try:
        wait_ms = max(0, int(2000 if wait_ms is None else wait_ms))
    except Exception:
        wait_ms = 2000
    result: dict[str, Any] = {
        "ok": True,
        "input": {
            "decision": decision_in,
            "view_id": view_id or "",
            "filename": filename or "",
            "all": bool(all_tabs),
            "except_view_id": except_view_id or "",
            "except_filename": except_filename or "",
            "inspect_only": bool(inspect_only),
            "wait_ms": wait_ms,
        },
        "policy": {"resolved_decision": decision_in},
        "state": {
            "tabs_before": [],
            "tabs_after": [],
            "selected_tabs": [],
            "dialogs_before_action": [],
            "dialogs_after_action": [],
            "stuck_confirmation": False,
        },
        "actions": [],
        "warnings": [],
        "errors": [],
    }

    if not all_tabs and not view_id and not filename:
        result["ok"] = False
        result["errors"].append("close requires --view-id, --filename, or --all")
        return result

    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        import binaryninjaui  # type: ignore
    except Exception as exc:
        result["ok"] = False
        result["errors"].append(f"Binary Ninja UI automation unavailable: {exc}")
        return result

    def summarize(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "view_id": record.get("view_id"),
            "filename": record.get("filename"),
            "modified": bool(record.get("modified")),
            "context_index": record.get("context_index"),
            "tab_index": record.get("tab_index"),
        }

    def _runner() -> dict[str, Any]:
        app = QApplication.instance()
        records_before = list_visible_tab_records(binaryninjaui)
        selected, warnings = _select_records(
            records_before,
            view_id=view_id or None,
            filename=filename or None,
            all_tabs=bool(all_tabs),
            except_view_id=except_view_id or None,
            except_filename=except_filename or None,
        )
        result["warnings"].extend(warnings)
        result["state"]["tabs_before"] = [summarize(record) for record in records_before]
        result["state"]["selected_tabs"] = [summarize(record) for record in selected]
        result["state"]["dialogs_before_action"] = [
            {k: v for k, v in dialog.items() if k != "_widget"}
            for dialog in _collect_save_dialogs(app)
        ]

        if not selected and (view_id or filename) and not all_tabs:
            result["errors"].append("no visible UI tab matched the requested selector")
        if "no visible UI tab matched any exclusion selector" in warnings:
            result["errors"].append("no visible UI tab matched any exclusion selector")

        tab_decisions = []
        for record in selected:
            resolved_for_tab, _is_bndb, _companion = resolve_policy(
                record.get("filename"), decision_in
            )
            tab_decisions.append(
                {
                    "view_id": record.get("view_id"),
                    "filename": record.get("filename"),
                    "resolved_decision": resolved_for_tab,
                }
            )
        result["policy"]["selected_tab_decisions"] = tab_decisions
        if selected:
            unique_decisions = {
                item["resolved_decision"] for item in tab_decisions if item.get("resolved_decision")
            }
            if len(unique_decisions) == 1:
                result["policy"]["resolved_decision"] = next(iter(unique_decisions))
            elif unique_decisions:
                result["policy"]["resolved_decision"] = "mixed"
            else:
                result["policy"]["resolved_decision"] = decision_in
        else:
            result["policy"]["resolved_decision"] = decision_in

        if not inspect_only:
            click_count = {"count": 0}
            current_decision = {"value": decision_in}
            timer = QTimer()
            timer.setInterval(30)

            def _watch_dialogs() -> None:
                try:
                    resolved = current_decision["value"]
                    if _click_save_dialog(app, resolved):
                        click_count["count"] += 1
                        result["actions"].append(f"clicked_confirmation_button:{resolved}")
                    elif _click_macos_save_sheet(resolved):
                        click_count["count"] += 1
                        result["actions"].append(f"sent_macos_{resolved}_shortcut")
                except Exception:
                    return

            timer.timeout.connect(_watch_dialogs)
            timer.start()

            try:
                for index, record in enumerate(list(selected)):
                    ctx = record.get("context")
                    tab = record.get("tab")
                    if ctx is None or tab is None:
                        result["warnings"].append("selected tab missing UI context or tab handle")
                        continue
                    try:
                        current_decision["value"] = tab_decisions[index]["resolved_decision"]
                    except Exception:
                        current_decision["value"] = decision_in
                    try:
                        queued = _queue_close_tab(QTimer, ctx, tab)
                        action_prefix = "close_tab_queued" if queued else "close_tab_requested"
                        result["actions"].append(
                            f"{action_prefix}:{record.get('filename') or record.get('view_id')}"
                        )
                    except Exception as exc:
                        result["warnings"].append(f"closeTab failed: {exc}")
                        continue

                    try:
                        app.processEvents()
                    except Exception:
                        pass

                    deadline = time.time() + (wait_ms / 1000.0)
                    quiet_cycles = 0
                    while time.time() < deadline:
                        app.processEvents()
                        if _collect_save_dialogs(app) or _macos_any_save_sheet_count() > 0:
                            quiet_cycles = 0
                        else:
                            quiet_cycles += 1
                            if quiet_cycles >= 5:
                                break
                        time.sleep(0.03)

                deadline = time.time() + (wait_ms / 1000.0)
                while time.time() < deadline:
                    app.processEvents()
                    if not _collect_save_dialogs(app) and _macos_any_save_sheet_count() <= 0:
                        break
                    time.sleep(0.03)
            finally:
                timer.stop()

            if click_count["count"] == 0:
                result["actions"].append("no_confirmation_dialog_detected_after_close")

        records_after = list_visible_tab_records(binaryninjaui)
        result["state"]["tabs_after"] = [summarize(record) for record in records_after]
        dialogs_after = _collect_save_dialogs(app)
        result["state"]["dialogs_after_action"] = [
            {k: v for k, v in dialog.items() if k != "_widget"} for dialog in dialogs_after
        ]
        macos_sheets_after = _macos_save_sheet_count()
        macos_manual_sheets_after = _macos_manual_save_sheet_count()
        result["state"]["macos_sheets_after_action"] = macos_sheets_after
        result["state"]["macos_manual_sheets_after_action"] = macos_manual_sheets_after
        result["state"]["stuck_confirmation"] = (
            bool(dialogs_after) or macos_sheets_after > 0 or macos_manual_sheets_after > 0
        )
        if macos_manual_sheets_after > 0 and MACOS_MANUAL_SAVE_SHEET_ERROR not in result["errors"]:
            result["errors"].append(MACOS_MANUAL_SAVE_SHEET_ERROR)
        if result["state"]["stuck_confirmation"]:
            result["ok"] = False
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
                result["actions"].append("scheduled_close_workflow_on_main_thread")
                scheduled = True
            except Exception as exc:
                result["warnings"].append(f"failed to schedule close workflow: {exc}")

        if (not scheduled) and hasattr(bn, "execute_on_main_thread_and_wait"):

            def _worker() -> None:
                try:
                    bn.execute_on_main_thread_and_wait(_main_thread_runner)
                except Exception as exc:
                    state["exception"] = exc
                    finished.set()

            threading.Thread(
                target=_worker,
                daemon=True,
            ).start()
            result["actions"].append("scheduled_close_workflow_via_helper_thread")
            scheduled = True

        if scheduled:
            wait_timeout_s = max(30.0, (wait_ms / 1000.0) + 15.0)
            if not finished.wait(wait_timeout_s):
                result["ok"] = False
                result["errors"].append(
                    f"close workflow timed out after {wait_timeout_s:.1f}s on main thread"
                )
                return result
            if state["exception"] is not None:
                result["ok"] = False
                result["errors"].append(f"close main-thread execution failed: {state['exception']}")
                return result
            if isinstance(state["result"], dict):
                return state["result"]

    return _runner()


__all__ = ["close_tabs_workflow", "list_visible_tab_records"]
