"""Binary Ninja UI status bar automation."""

from __future__ import annotations

from typing import Any

try:
    import binaryninja as bn
except Exception:
    bn = None


def _norm(value: Any) -> str:
    text = str(value or "").strip()
    return " ".join(text.split())


def _scan_status(app, all_windows: bool, include_hidden: bool) -> dict[str, Any]:
    from PySide6.QtWidgets import QWidget

    result = {
        "ok": True,
        "active_window_title": None,
        "status_source": "",
        "status_text": "",
        "status_items": [],
        "windows": [],
        "warnings": [],
        "errors": [],
    }

    active = app.activeWindow()
    if active is not None:
        result["active_window_title"] = str(active.windowTitle() or "")

    def add_item(seen: set[str], status_items: list[str], raw: Any) -> None:
        text = _norm(raw)
        if not text:
            return
        if text in seen:
            return
        seen.add(text)
        status_items.append(text)

    def scan_window(window) -> dict[str, Any]:
        entry = {
            "title": str(window.windowTitle() or ""),
            "class": type(window).__name__,
            "visible": bool(window.isVisible()),
            "status_text": "",
            "status_items": [],
            "status_source": "none",
        }

        status_items = []
        seen = set()

        status_bar = None
        try:
            if hasattr(window, "statusBar"):
                status_bar = window.statusBar()
        except Exception:
            status_bar = None

        if status_bar is not None:
            try:
                add_item(seen, status_items, status_bar.currentMessage())
            except Exception:
                pass

            try:
                for child in status_bar.findChildren(QWidget):
                    cls = type(child).__name__.lower()
                    if ("label" in cls) and hasattr(child, "text"):
                        add_item(seen, status_items, child.text())
            except Exception:
                pass

            try:
                for child in status_bar.findChildren(QWidget):
                    cls = type(child).__name__.lower()
                    if ("progress" in cls) and hasattr(child, "format"):
                        fmt = _norm(child.format())
                        if fmt:
                            add_item(seen, status_items, fmt)
                        try:
                            add_item(
                                seen, status_items, f"{int(child.value())}/{int(child.maximum())}"
                            )
                        except Exception:
                            pass
            except Exception:
                pass

        if not status_items:
            bottom_candidates = []
            try:
                for child in window.findChildren(QWidget):
                    if not child.isVisible():
                        continue
                    cls = type(child).__name__.lower()
                    if ("label" not in cls) or (not hasattr(child, "text")):
                        continue
                    text = _norm(child.text())
                    if not text:
                        continue
                    try:
                        pos = child.mapToGlobal(child.rect().topLeft())
                        y = int(pos.y())
                        x = int(pos.x())
                    except Exception:
                        y = 0
                        x = 0
                    bottom_candidates.append((y, x, text))
            except Exception:
                bottom_candidates = []

            if bottom_candidates:
                max_y = max(y for y, _x, _t in bottom_candidates)
                row = [(x, t) for y, x, t in bottom_candidates if y >= (max_y - 2)]
                for _x, text in sorted(row, key=lambda item: item[0]):
                    add_item(seen, status_items, text)
                if status_items:
                    entry["status_source"] = "bottom_row_labels"

        if status_items and entry["status_source"] == "none":
            entry["status_source"] = "status_bar"

        entry["status_items"] = status_items
        entry["status_text"] = " | ".join(status_items)
        return entry

    scanned = []
    for widget in app.topLevelWidgets():
        try:
            visible = bool(widget.isVisible())
        except Exception:
            visible = False
        if (not include_hidden) and (not visible):
            continue
        cls_norm = type(widget).__name__.lower()
        if (not all_windows) and ("mainwindow" not in cls_norm):
            continue
        scanned.append(scan_window(widget))

    result["windows"] = scanned

    selected = None
    if active is not None:
        active_title = str(active.windowTitle() or "")
        for entry in scanned:
            if entry["title"] == active_title:
                selected = entry
                break
    if selected is None and scanned:
        selected = scanned[0]

    if selected is not None:
        result["status_source"] = selected.get("status_source", "")
        result["status_text"] = selected.get("status_text", "")
        result["status_items"] = selected.get("status_items", [])

    if not result["status_items"]:
        result["warnings"].append("no status bar text found")

    return result


def read_statusbar(
    all_windows: bool = False,
    include_hidden: bool = False,
    **_unused: Any,
) -> dict[str, Any]:
    """Read status bar text from active Binary Ninja UI windows."""
    result = {
        "ok": True,
        "active_window_title": None,
        "status_source": "",
        "status_text": "",
        "status_items": [],
        "windows": [],
        "warnings": [],
        "errors": [],
    }

    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:
        result["ok"] = False
        result["errors"].append(f"PySide6 unavailable: {exc}")
        return result

    def runner():
        app = QApplication.instance()
        if app is None:
            error_result = dict(result)
            error_result["ok"] = False
            error_result["errors"] = ["QApplication instance is not available"]
            return error_result
        return _scan_status(app, bool(all_windows), bool(include_hidden))

    if bn is not None and hasattr(bn, "execute_on_main_thread_and_wait"):
        holder = {"result": None}

        def _main_thread():
            holder["result"] = runner()

        try:
            bn.execute_on_main_thread_and_wait(_main_thread)
            if isinstance(holder["result"], dict):
                return holder["result"]
        except Exception as exc:
            result["ok"] = False
            result["errors"].append(f"statusbar main-thread execution failed: {exc}")
            return result

    return runner()
