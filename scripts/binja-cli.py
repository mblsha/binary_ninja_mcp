#!/usr/bin/env python3
"""
Binary Ninja MCP CLI - Command-line interface for Binary Ninja MCP server
Uses the same HTTP API as the MCP bridge but provides a terminal interface
"""

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import requests
from plumbum import cli, colors


class BinaryNinjaCLI(cli.Application):
    """Binary Ninja MCP command-line interface"""

    PROGNAME = "binja-mcp"
    VERSION = "0.2.7"
    DESCRIPTION = "Command-line interface for Binary Ninja MCP server"

    server_url = cli.SwitchAttr(
        ["--server", "-s"], str, default="http://localhost:9009", help="MCP server URL"
    )

    json_output = cli.Flag(["--json", "-j"], help="Output raw JSON response")

    verbose = cli.Flag(["--verbose", "-v"], help="Verbose output")

    request_timeout = cli.SwitchAttr(
        ["--request-timeout", "-t"],
        float,
        default=float(os.environ.get("BINJA_CLI_TIMEOUT", "5")),
        help=(
            "HTTP request timeout in seconds (default: 5; can also set BINJA_CLI_TIMEOUT)"
        ),
    )

    def _request(self, method: str, endpoint: str, params: dict = None, data: dict = None) -> dict:
        """Make HTTP request to the server"""
        url = f"{self.server_url}/{endpoint}"

        if self.verbose:
            print(f"[{method}] {url}", file=sys.stderr)
            if params:
                print(f"Params: {params}", file=sys.stderr)
            if data:
                print(f"Data: {data}", file=sys.stderr)

        try:
            if method == "GET":
                response = requests.get(url, params=params, timeout=self.request_timeout)
            else:
                response = requests.post(url, json=data, timeout=self.request_timeout)

            response.raise_for_status()
            return response.json()

        except requests.exceptions.ConnectionError:
            print(
                colors.red | f"Error: Cannot connect to server at {self.server_url}",
                file=sys.stderr,
            )
            print("Make sure the MCP server is running in Binary Ninja", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.HTTPError as e:
            # Try to parse JSON error response for structured error info
            try:
                error_data = e.response.json()
                if isinstance(error_data, dict) and "error" in error_data:
                    # Display main error
                    print(colors.red | f"Error: {error_data['error']}", file=sys.stderr)

                    # Display additional context if available
                    if "help" in error_data:
                        print(colors.yellow | f"Help: {error_data['help']}", file=sys.stderr)

                    if "received" in error_data:
                        print(f"Received: {error_data['received']}", file=sys.stderr)

                    if "requested_name" in error_data:
                        print(f"Requested: {error_data['requested_name']}", file=sys.stderr)

                    # Show available functions if provided (e.g., for function not found errors)
                    if "available_functions" in error_data and error_data["available_functions"]:
                        funcs = error_data["available_functions"][:5]  # Show first 5
                        print("\nAvailable functions:", file=sys.stderr)
                        for func in funcs:
                            print(f"  • {func}", file=sys.stderr)
                        if len(error_data["available_functions"]) > 5:
                            remaining = len(error_data["available_functions"]) - 5
                            print(f"  ... and {remaining} more", file=sys.stderr)

                    # Show exception details if available (for debugging)
                    if "exception" in error_data and self.verbose:
                        print(f"\nException details: {error_data['exception']}", file=sys.stderr)
                else:
                    # Not a structured error, show raw response
                    print(colors.red | f"HTTP Error: {e}", file=sys.stderr)
                    if hasattr(e.response, "text"):
                        print(e.response.text, file=sys.stderr)
            except (json.JSONDecodeError, AttributeError):
                # Failed to parse JSON, fall back to showing raw error
                print(colors.red | f"HTTP Error: {e}", file=sys.stderr)
                if hasattr(e.response, "text"):
                    print(e.response.text, file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(colors.red | f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    def _server_reachable(self, timeout: float = 2.0) -> bool:
        """Check whether MCP server is reachable without exiting."""
        url = f"{self.server_url}/status"
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return True
        except Exception:
            return False

    def _launch_binary_ninja_linux_wayland(self, filepath: str = "") -> dict:
        """Best-effort Binary Ninja launch for Linux Wayland sessions."""
        if sys.platform != "linux":
            return {"ok": False, "error": "auto-launch is only supported on Linux"}

        runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        env = os.environ.copy()
        env.pop("DISPLAY", None)
        env["QT_QPA_PLATFORM"] = "wayland"
        env["WAYLAND_DISPLAY"] = env.get("WAYLAND_DISPLAY") or "wayland-0"
        env["XDG_RUNTIME_DIR"] = runtime_dir
        env["DBUS_SESSION_BUS_ADDRESS"] = env.get(
            "DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus"
        )
        env["XDG_SESSION_TYPE"] = env.get("XDG_SESSION_TYPE") or "wayland"

        candidates = [
            os.environ.get("BINJA_BINARY"),
            "/home/mblsha/src/binja/binaryninja/binaryninja",
            shutil.which("binaryninja"),
            shutil.which("BinaryNinja"),
        ]
        binary_path = None
        for candidate in candidates:
            if not candidate:
                continue
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                binary_path = candidate
                break

        if binary_path is None:
            return {
                "ok": False,
                "error": (
                    "unable to find Binary Ninja executable; set BINJA_BINARY or install "
                    "binaryninja in PATH"
                ),
            }

        # Launch regular UI mode to keep plugin loading behavior consistent.
        args = [binary_path]
        if filepath:
            args.extend(["-e", filepath])

        log_path = "/tmp/binja-cli-launch.log"
        try:
            with open(log_path, "ab") as log_fp:
                subprocess.Popen(
                    args,
                    env=env,
                    stdout=log_fp,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except Exception as exc:
            return {"ok": False, "error": f"failed to launch Binary Ninja: {exc}", "log": log_path}

        return {"ok": True, "binary": binary_path, "log": log_path}

    def _ensure_server_for_open(self, filepath: str = "") -> dict:
        """Ensure MCP server is available before running open workflow."""
        if self._server_reachable(timeout=1.0):
            return {"ok": True, "launched": False}

        # Launching with -e <file> can present modal import dialogs before MCP
        # automation has control. Start without a file, then let open() drive load.
        launch = self._launch_binary_ninja_linux_wayland(filepath="")
        if not launch.get("ok"):
            return launch

        deadline = time.time() + 25.0
        while time.time() < deadline:
            if self._server_reachable(timeout=1.0):
                out = dict(launch)
                out["ok"] = True
                out["launched"] = True
                return out
            time.sleep(0.5)

        return {
            "ok": False,
            "error": (
                "Binary Ninja started but MCP server did not come up at "
                f"{self.server_url} within 25s"
            ),
            "log": launch.get("log"),
            "binary": launch.get("binary"),
        }

    def _execute_python(self, code: str, exec_timeout: float = 30.0) -> dict:
        """Execute Python in Binary Ninja via MCP console endpoint."""
        return self._request(
            "POST",
            "console/execute",
            data={"command": code, "timeout": exec_timeout},
        )

    @staticmethod
    def _extract_last_json_line(text: str):
        """Parse the last JSON object line from text output."""
        if not isinstance(text, str):
            return None
        for raw_line in reversed(text.splitlines()):
            line = raw_line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        return None

    def _output(self, data: dict):
        """Output data in JSON or formatted text"""
        if self.json_output:
            print(json.dumps(data, indent=2))
        else:
            # Custom formatting based on data type
            if "error" in data:
                print(colors.red | f"Error: {data['error']}")
                # Display additional error context if available
                if isinstance(data, dict):
                    if "help" in data:
                        print(colors.yellow | f"Help: {data['help']}")
                    if "received" in data:
                        print(f"Received: {data['received']}")
                    if "requested_name" in data:
                        print(f"Requested: {data['requested_name']}")
                    if "available_functions" in data and data["available_functions"]:
                        funcs = data["available_functions"][:5]
                        print("\nAvailable functions:")
                        for func in funcs:
                            print(f"  • {func}")
                        if len(data["available_functions"]) > 5:
                            print(f"  ... and {len(data['available_functions']) - 5} more")
            elif "success" in data and data.get("success"):
                print(colors.green | "Success!")
                if "message" in data:
                    print(data["message"])
            else:
                # Pretty print the data
                print(json.dumps(data, indent=2))

    def main(self):
        """Show help if no subcommand is provided"""
        if len(sys.argv) == 1:
            self.help()
            return 1
        return 0


@BinaryNinjaCLI.subcommand("status")
class Status(cli.Application):
    """Check if binary is loaded and server status"""

    def main(self):
        data = self.parent._request("GET", "status")

        if self.parent.json_output:
            self.parent._output(data)
        else:
            if data.get("loaded"):
                print(colors.green | "✓ Binary loaded")
                print(f"  File: {data.get('filename', 'Unknown')}")
            else:
                print(colors.yellow | "⚠ No binary loaded")


@BinaryNinjaCLI.subcommand("open")
class Open(cli.Application):
    """Open a file and auto-resolve Binary Ninja's "Open with Options" dialog.

    Behavior:
    - If MCP is not reachable on Linux, auto-launches Binary Ninja with Wayland defaults.
    - If an "Open with Options" dialog is visible, optionally sets view type/platform and clicks "Open".
    - If no dialog is visible, falls back to `bn.load(filepath)` and updates MCP current_view.
    - Always reports inspected state/actions in JSON-like output.
    """

    platform = cli.SwitchAttr(
        ["--platform", "-p"],
        str,
        help="Platform/arch text to select in the dialog (e.g. x86_16).",
    )

    view_type = cli.SwitchAttr(
        ["--view-type", "-t"],
        str,
        help="View type to select in the dialog (e.g. Mapped, Raw).",
    )

    no_click = cli.Flag(
        ["--no-click"],
        help="Inspect/set dialog fields but do not click the Open button.",
    )

    inspect_only = cli.Flag(
        ["--inspect-only"],
        help="Inspect UI state only (do not load or click open).",
    )

    def main(self, filepath: str = ""):
        ensure = self.parent._ensure_server_for_open(filepath=filepath)
        if not ensure.get("ok"):
            print(colors.red | f"Error: {ensure.get('error', 'unable to start Binary Ninja')}")
            if ensure.get("binary"):
                print(f"Binary: {ensure['binary']}", file=sys.stderr)
            if ensure.get("log"):
                print(f"Launch log: {ensure['log']}", file=sys.stderr)
            print(
                "If Binary Ninja is already open, ensure the MCP server is running.",
                file=sys.stderr,
            )
            return 1
        if ensure.get("launched") and self.parent.verbose:
            print(
                colors.yellow
                | f"Started Binary Ninja ({ensure.get('binary')}); waiting for MCP server succeeded."
            )

        config = {
            "filepath": filepath,
            "platform": self.platform or "",
            "view_type": self.view_type or "",
            "click_open": not self.no_click,
            "inspect_only": self.inspect_only,
        }

        script = textwrap.dedent(
            """
            import json
            import time
            from pathlib import Path
            import binaryninja as bn
            from PySide6.QtWidgets import QApplication

            CONFIG = json.loads(%r)

            target_file = str(CONFIG.get("filepath") or "").strip()
            target_platform = str(CONFIG.get("platform") or "").strip()
            target_view_type = str(CONFIG.get("view_type") or "").strip()
            click_open = bool(CONFIG.get("click_open", True))
            inspect_only = bool(CONFIG.get("inspect_only", False))

            result = {
                "ok": True,
                "input": {
                    "filepath": target_file,
                    "platform": target_platform,
                    "view_type": target_view_type,
                    "click_open": click_open,
                    "inspect_only": inspect_only,
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

            def norm_text(value):
                text = str(value or "").strip().lower()
                return "".join(ch for ch in text if ch.isalnum() or ch in ("_", ".", "-"))

            def collect_visible_windows(app):
                windows = []
                if app is None:
                    return windows
                for widget in app.topLevelWidgets():
                    if not widget.isVisible():
                        continue
                    windows.append(
                        {
                            "class": type(widget).__name__,
                            "title": str(widget.windowTitle() or ""),
                        }
                    )
                return windows

            def find_item_index(combo, wanted_text):
                wanted_norm = norm_text(wanted_text)
                if not wanted_norm:
                    return -1
                partial_idx = -1
                for idx in range(combo.count()):
                    item_norm = norm_text(combo.itemText(idx))
                    if item_norm == wanted_norm:
                        return idx
                    if partial_idx < 0 and (wanted_norm in item_norm or item_norm in wanted_norm):
                        partial_idx = idx
                return partial_idx

            def find_options_dialog(app):
                if app is None:
                    return None
                for widget in app.topLevelWidgets():
                    if not widget.isVisible():
                        continue
                    title = str(widget.windowTitle() or "")
                    cls_name = type(widget).__name__.lower()
                    if "open with options" in title.lower() or "optionsdialog" in cls_name:
                        return widget
                return None

            def is_qt_object_alive(obj):
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

            def set_combo_value(combo, requested):
                idx = find_item_index(combo, requested)
                if idx < 0:
                    return {"requested": requested, "changed": False, "reason": "not-found"}
                before = str(combo.currentText() or "")
                combo.setCurrentIndex(idx)
                QApplication.processEvents()
                after = str(combo.currentText() or "")
                return {
                    "requested": requested,
                    "changed": before != after,
                    "before": before,
                    "after": after,
                    "index": idx,
                }

            def get_loaded_filename():
                try:
                    import binary_ninja_mcp.plugin as mcp_plugin
                    current = mcp_plugin.plugin.server.binary_ops.current_view
                    if current is not None and getattr(current, "file", None) is not None:
                        return str(current.file.filename)
                except Exception:
                    pass
                try:
                    current_bv = globals().get("bv")
                    if current_bv is not None and getattr(current_bv, "file", None) is not None:
                        return str(current_bv.file.filename)
                except Exception:
                    pass
                return None

            def open_with_ui_context(filepath):
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

            def handle_open_with_options_dialog(dialog, detected_action):
                if dialog is None or not is_qt_object_alive(dialog):
                    return False

                try:
                    dialog_title = str(dialog.windowTitle() or "")
                except Exception:
                    result["warnings"].append("open dialog disappeared before it could be handled")
                    return False

                result["dialog"]["present"] = True
                result["dialog"]["title"] = dialog_title
                result["actions"].append(detected_action)

                combos = []
                try:
                    dialog_children = dialog.findChildren(object)
                except Exception:
                    result["warnings"].append("unable to enumerate open dialog controls")
                    return False

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
                        idx = find_item_index(combo, target_view_type)
                        if idx < 0:
                            continue
                        count = combo.count()
                        items = [norm_text(combo.itemText(i)) for i in range(count)]
                        score = 0
                        if norm_text(combo.itemText(idx)) == norm_text(target_view_type):
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
                        items = {norm_text(combo.itemText(i)) for i in range(combo.count())}
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
                        view_set = set_combo_value(view_combo, target_view_type)
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
                        idx = find_item_index(combo, target_platform)
                        if idx < 0:
                            continue
                        count = combo.count()
                        items = [norm_text(combo.itemText(i)) for i in range(count)]
                        score = 0
                        if norm_text(combo.itemText(idx)) == norm_text(target_platform):
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

                if not inspect_only and click_open:
                    open_button = None
                    try:
                        button_children = dialog.findChildren(object)
                    except Exception:
                        button_children = []
                    for button in button_children:
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
                    elif not open_button.isEnabled():
                        result["warnings"].append("open button is disabled")
                    else:
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
                                hidden_after_accept = False
                                try:
                                    hidden_after_accept = not dialog.isVisible()
                                except Exception:
                                    hidden_after_accept = True
                                if hidden_after_accept:
                                    result["actions"].append("accepted_open_dialog")
                            except Exception as exc:
                                result["warnings"].append(
                                    f"open dialog accept() fallback failed: {exc}"
                                )

                return True

            # The MCP Python executor may run `exec` with separate globals/locals.
            # Expose helpers in globals so comprehensions and nested call paths can
            # resolve these names consistently.
            globals()["norm_text"] = norm_text
            globals()["collect_visible_windows"] = collect_visible_windows
            globals()["find_item_index"] = find_item_index
            globals()["find_options_dialog"] = find_options_dialog
            globals()["is_qt_object_alive"] = is_qt_object_alive
            globals()["set_combo_value"] = set_combo_value
            globals()["get_loaded_filename"] = get_loaded_filename
            globals()["open_with_ui_context"] = open_with_ui_context
            globals()["handle_open_with_options_dialog"] = handle_open_with_options_dialog
            globals()["time"] = time
            globals()["QApplication"] = QApplication
            globals()["Path"] = Path
            globals()["bn"] = bn
            globals()["result"] = result
            globals()["target_file"] = target_file
            globals()["target_view_type"] = target_view_type
            globals()["target_platform"] = target_platform
            globals()["inspect_only"] = inspect_only
            globals()["click_open"] = click_open

            def run_open_workflow():
                app = QApplication.instance()
                globals()["app"] = app
                result["state"]["visible_windows"] = collect_visible_windows(app)
                if app is not None and app.activeWindow() is not None:
                    result["state"]["active_window"] = str(app.activeWindow().windowTitle() or "")

                dialog = find_options_dialog(app)
                loaded_bv = None

                if dialog is not None:
                    handle_open_with_options_dialog(dialog, "detected_open_with_options_dialog")
                else:
                    result["actions"].append("no_open_with_options_dialog")
                    if inspect_only:
                        result["actions"].append("inspect_only_no_load")
                    elif not target_file:
                        result["warnings"].append("no filepath provided and no dialog to accept")
                    else:
                        ui_open = {"ok": False, "reason": "skipped"}
                        if app is not None:
                            ui_open = open_with_ui_context(target_file)
                            if ui_open.get("ok"):
                                result["actions"].append("ui_context_open_filename")
                            else:
                                result["warnings"].append(
                                    f"ui_context_open_filename: {ui_open.get('reason')}"
                                )
                        if not ui_open.get("ok"):
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
                                post_dialog = find_options_dialog(app)
                                if post_dialog is not None:
                                    handle_open_with_options_dialog(
                                        post_dialog,
                                        "detected_open_with_options_dialog_after_open",
                                    )
                                loaded_now = get_loaded_filename()
                                if loaded_now:
                                    try:
                                        if target_file:
                                            expected_now = str(Path(target_file).resolve())
                                            observed_now = str(Path(loaded_now).resolve())
                                            if observed_now == expected_now:
                                                break
                                    except Exception:
                                        break
                                time.sleep(0.05)

                # Final pass: if any options dialog is still visible, keep trying to resolve it.
                if app is not None and (not inspect_only) and click_open:
                    deadline = time.time() + 8.0
                    while time.time() < deadline:
                        lingering = find_options_dialog(app)
                        if lingering is None:
                            break
                        handle_open_with_options_dialog(
                            lingering, "resolved_open_with_options_dialog_final_pass"
                        )
                        for _ in range(12):
                            app.processEvents()
                            time.sleep(0.02)
                    if find_options_dialog(app) is not None:
                        result["warnings"].append(
                            "open dialog remained visible after final resolution pass"
                        )

                if loaded_bv is None:
                    candidate_bv = globals().get("bv")
                    if candidate_bv is not None and getattr(candidate_bv, "file", None) is not None:
                        loaded_bv = candidate_bv

                # Ensure MCP server tracks the view when we can identify one.
                try:
                    import binary_ninja_mcp.plugin as mcp_plugin
                    if loaded_bv is not None:
                        mcp_plugin.plugin.server.binary_ops.current_view = loaded_bv
                        result["actions"].append("set_current_view")
                except Exception as exc:
                    result["warnings"].append(f"unable to set MCP current_view: {exc}")

                if app is not None:
                    result["state"]["visible_windows"] = collect_visible_windows(app)
                    if app.activeWindow() is not None:
                        result["state"]["active_window"] = str(app.activeWindow().windowTitle() or "")
                    else:
                        result["state"]["active_window"] = None

                loaded_filename = get_loaded_filename()
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
                    if norm_text(result["state"]["loaded_arch"]) != norm_text(target_platform):
                        result["warnings"].append(
                            f"loaded arch ({result['state']['loaded_arch']}) differs from requested platform ({target_platform})"
                        )

                return loaded_bv

            globals()["run_open_workflow"] = run_open_workflow

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

            globals()["run_non_ui_fallback_load"] = run_non_ui_fallback_load

            loaded_bv = None
            if hasattr(bn, "execute_on_main_thread_and_wait"):
                def _run_open_workflow_main_thread():
                    globals()["__open_main_thread_started"] = True
                    globals()["__open_loaded_bv"] = globals()["run_open_workflow"]()
                    globals()["__open_main_thread_done"] = True

                globals()["_run_open_workflow_main_thread"] = _run_open_workflow_main_thread
                try:
                    globals()["__open_main_thread_started"] = False
                    globals()["__open_main_thread_done"] = False
                    bn.execute_on_main_thread_and_wait(_run_open_workflow_main_thread)
                    if globals().get("__open_main_thread_done"):
                        loaded_bv = globals().get("__open_loaded_bv")
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

            # Reconcile state after any fallback path.
            app = QApplication.instance()
            if app is not None:
                result["state"]["visible_windows"] = collect_visible_windows(app)
                if app.activeWindow() is not None:
                    result["state"]["active_window"] = str(app.activeWindow().windowTitle() or "")
                else:
                    result["state"]["active_window"] = None

            if loaded_bv is not None:
                try:
                    import binary_ninja_mcp.plugin as mcp_plugin

                    mcp_plugin.plugin.server.binary_ops.current_view = loaded_bv
                    if "set_current_view" not in result["actions"]:
                        result["actions"].append("set_current_view")
                except Exception as exc:
                    result["warnings"].append(f"unable to set MCP current_view: {exc}")

            loaded_filename = get_loaded_filename()
            if loaded_filename is None and loaded_bv is not None:
                try:
                    if getattr(loaded_bv, "file", None) is not None:
                        loaded_filename = str(loaded_bv.file.filename)
                except Exception:
                    pass
            result["state"]["loaded_filename"] = loaded_filename

            if loaded_bv is not None and not result["state"].get("loaded_arch"):
                try:
                    result["state"]["loaded_arch"] = str(
                        loaded_bv.arch.name if loaded_bv.arch is not None else None
                    )
                except Exception:
                    result["state"]["loaded_arch"] = None

            if result["errors"]:
                result["ok"] = False

            print(json.dumps(result, sort_keys=True))
            """
            % json.dumps(config)
        )

        data = self.parent._execute_python(script)

        parsed = None
        if isinstance(data, dict):
            parsed = self.parent._extract_last_json_line(data.get("stdout", ""))
            if parsed is None and isinstance(data.get("return_value"), dict):
                parsed = data["return_value"]
            stderr_text = data.get("stderr", "")
            if (
                isinstance(parsed, dict)
                and isinstance(stderr_text, str)
                and ("Traceback" in stderr_text or "Exception ignored on calling ctypes callback" in stderr_text)
            ):
                warnings = parsed.setdefault("warnings", [])
                warn_msg = "python execution emitted stderr traceback; check --json stderr output"
                if warn_msg not in warnings:
                    warnings.append(warn_msg)
                parsed["ok"] = False

        if self.parent.json_output:
            if parsed is not None and isinstance(data, dict):
                data = dict(data)
                data["open_result"] = parsed
            self.parent._output(data)
            return

        if not data.get("success"):
            error = data.get("error", {})
            if isinstance(error, dict):
                print(
                    colors.red
                    | f"Error: {error.get('type', 'Unknown')}: {error.get('message', 'Unknown error')}"
                )
            else:
                print(colors.red | f"Error: {error}")
            if data.get("stderr"):
                print(colors.red | data["stderr"], end="")
            return

        if parsed is None:
            print(colors.yellow | "Open command executed, but no structured result was returned.")
            if data.get("stdout"):
                print(data["stdout"], end="")
            return

        ok = bool(parsed.get("ok"))
        status_line = "✓ Open workflow completed" if ok else "⚠ Open workflow completed with issues"
        color = colors.green if ok else colors.yellow
        print(color | status_line)

        loaded = parsed.get("state", {}).get("loaded_filename")
        if loaded:
            print(f"  Loaded: {loaded}")
        else:
            print("  Loaded: <unknown>")

        active_window = parsed.get("state", {}).get("active_window")
        if active_window:
            print(f"  Active Window: {active_window}")

        actions = parsed.get("actions", [])
        if actions:
            print("  Actions:")
            for action in actions:
                print(f"    - {action}")

        warnings = parsed.get("warnings", [])
        if warnings:
            print(colors.yellow | "  Warnings:")
            for warning in warnings:
                print(colors.yellow | f"    - {warning}")

        errors = parsed.get("errors", [])
        if errors:
            print(colors.red | "  Errors:")
            for err in errors:
                print(colors.red | f"    - {err}")


@BinaryNinjaCLI.subcommand("quit")
class Quit(cli.Application):
    """Close Binary Ninja windows and auto-answer save confirmation dialogs.

    Default decision policy:
    - Save if currently loaded file is a `.bndb` or has a sibling `<file>.bndb`.
    - Otherwise choose Don't Save/Discard.
    """

    decision = cli.SwitchAttr(
        ["--decision"],
        str,
        default="auto",
        help="Decision policy: auto|save|dont-save|cancel",
    )

    mark_dirty = cli.Flag(
        ["--mark-dirty"],
        help="Force current BinaryView's modified flag before closing (useful for testing).",
    )

    inspect_only = cli.Flag(
        ["--inspect-only"],
        help="Inspect dialogs and policy only; do not close windows or click buttons.",
    )

    wait_ms = cli.SwitchAttr(
        ["--wait-ms"],
        int,
        default=2000,
        help="Maximum time to wait for confirmation dialogs after close (ms).",
    )

    quit_app = cli.Flag(
        ["--quit-app"],
        help="Request QApplication.quit() after dialog handling (best-effort).",
    )

    quit_delay_ms = cli.SwitchAttr(
        ["--quit-delay-ms"],
        int,
        default=300,
        help="Delay before QApplication.quit() when --quit-app is used (ms).",
    )

    def main(self):
        decision_in = (self.decision or "auto").strip().lower()
        valid = {"auto", "save", "dont-save", "dont_save", "cancel"}
        if decision_in not in valid:
            print(
                colors.red
                | f"Invalid --decision '{self.decision}'. Expected one of: auto, save, dont-save, cancel"
            )
            return 1
        if decision_in == "dont_save":
            decision_in = "dont-save"

        config = {
            "decision": decision_in,
            "mark_dirty": bool(self.mark_dirty),
            "inspect_only": bool(self.inspect_only),
            "wait_ms": int(self.wait_ms or 2000),
            "quit_app": bool(self.quit_app),
            "quit_delay_ms": int(self.quit_delay_ms or 300),
        }

        script = textwrap.dedent(
            """
            import json
            import time
            from pathlib import Path

            import binaryninja as bn
            from PySide6.QtCore import QTimer, Qt
            from PySide6.QtGui import QAction
            from PySide6.QtWidgets import QApplication, QPushButton

            CONFIG = json.loads(%r)

            decision_in = str(CONFIG.get("decision") or "auto").strip().lower()
            mark_dirty = bool(CONFIG.get("mark_dirty", False))
            inspect_only = bool(CONFIG.get("inspect_only", False))
            wait_ms = max(0, int(CONFIG.get("wait_ms", 2000)))
            quit_app = bool(CONFIG.get("quit_app", False))
            quit_delay_ms = max(0, int(CONFIG.get("quit_delay_ms", 300)))

            result = {
                "ok": True,
                "input": {
                    "decision": decision_in,
                    "mark_dirty": mark_dirty,
                    "inspect_only": inspect_only,
                    "wait_ms": wait_ms,
                },
                "policy": {
                    "resolved_decision": None,
                    "loaded_filename": None,
                    "loaded_is_bndb": False,
                    "companion_bndb_exists": False,
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

            def norm_text(value):
                text = str(value or "").replace("&", "").strip().lower()
                return " ".join(text.split())

            def collect_visible_windows(app):
                out = []
                if app is None:
                    return out
                for widget in app.topLevelWidgets():
                    if not widget.isVisible():
                        continue
                    out.append(
                        {
                            "class": type(widget).__name__,
                            "title": str(widget.windowTitle() or ""),
                        }
                    )
                return out

            def get_current_bv():
                try:
                    import binary_ninja_mcp.plugin as mcp_plugin
                    current = mcp_plugin.plugin.server.binary_ops.current_view
                    if current is not None:
                        return current
                except Exception:
                    pass
                return globals().get("bv")

            def get_loaded_filename():
                current_bv = get_current_bv()
                if current_bv is None:
                    return None
                try:
                    if getattr(current_bv, "file", None) is not None:
                        return str(current_bv.file.filename)
                except Exception:
                    pass
                return None

            def resolve_policy(loaded_filename, decision):
                loaded_is_bndb = False
                companion_exists = False
                resolved = decision
                if loaded_filename:
                    try:
                        loaded_name = str(loaded_filename).strip()
                        loaded_is_bndb = loaded_name.lower().endswith(".bndb")
                        if not loaded_is_bndb:
                            companion_exists = Path(loaded_name + ".bndb").exists()
                    except Exception:
                        pass
                if decision == "auto":
                    resolved = "save" if (loaded_is_bndb or companion_exists) else "dont-save"
                return resolved, loaded_is_bndb, companion_exists

            def collect_confirmation_dialogs(app):
                dialogs = []
                if app is None:
                    return dialogs
                for widget in app.topLevelWidgets():
                    if not widget.isVisible():
                        continue
                    buttons = []
                    try:
                        push_buttons = widget.findChildren(
                            QPushButton, options=Qt.FindDirectChildrenOnly
                        )
                    except Exception:
                        push_buttons = []
                    for button in push_buttons:
                        if not button.isVisible():
                            continue
                        text = norm_text(button.text())
                        if not text:
                            continue
                        buttons.append(
                            {
                                "text": str(button.text() or ""),
                                "norm": text,
                                "enabled": bool(button.isEnabled()),
                            }
                        )
                    if not buttons:
                        # If we cannot enumerate buttons but this still looks like a modal save prompt,
                        # keep tracking it as a confirmation dialog.
                        title_norm = norm_text(widget.windowTitle())
                        cls_norm = type(widget).__name__.lower()
                        if "messagebox" not in cls_norm and "modified" not in title_norm:
                            continue
                    tokens = {b["norm"] for b in buttons}
                    has_save_token = any("save" in t for t in tokens)
                    has_reject_token = any(
                        ("don't save" in t)
                        or ("dont save" in t)
                        or ("discard" in t)
                        or ("close without saving" in t)
                        or ("close without save" in t)
                        or (t == "no")
                        for t in tokens
                    )
                    has_cancel_token = any(("cancel" in t) for t in tokens)
                    title_norm = norm_text(widget.windowTitle())
                    cls_norm = type(widget).__name__.lower()
                    looks_modal_save_prompt = (
                        ("messagebox" in cls_norm) and ("modified" in title_norm or "save" in title_norm)
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

            def find_button_for_decision(dialog_widget, decision):
                priorities = []
                if decision == "save":
                    priorities = ["save", "save changes", "save all", "yes"]
                elif decision == "dont-save":
                    priorities = [
                        "don't save",
                        "dont save",
                        "close without saving",
                        "close without save",
                        "discard changes",
                        "discard",
                        "no",
                    ]
                elif decision == "cancel":
                    priorities = ["cancel"]

                candidates = []
                try:
                    buttons = dialog_widget.findChildren(QPushButton)
                except Exception:
                    buttons = []

                for button in buttons:
                    if not button.isVisible():
                        continue
                    label = str(button.text() or "")
                    norm = norm_text(label)
                    if not norm:
                        continue
                    candidates.append((button, label, norm))

                for wanted in priorities:
                    for button, label, norm in candidates:
                        if norm == wanted:
                            return button, label
                    for button, label, norm in candidates:
                        if wanted in norm:
                            return button, label
                return None, None

            def find_primary_main_window(app):
                if app is None:
                    return None
                for widget in app.topLevelWidgets():
                    if not widget.isVisible():
                        continue
                    if "mainwindow" in type(widget).__name__.lower():
                        return widget
                return None

            def trigger_close_tab(main_window):
                if main_window is None:
                    return False, "no_main_window"
                try:
                    actions = main_window.findChildren(QAction)
                except Exception:
                    actions = []

                # Prefer exact "Close Tab", then fallback to any action containing both terms.
                best = None
                for action in actions:
                    text = norm_text(action.text())
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
                    best.trigger()
                    QApplication.processEvents()
                    return True, "close_tab_action_triggered"
                except Exception as exc:
                    return False, f"close_tab_action_trigger_failed:{exc}"

            # The MCP Python executor may run exec with split globals/locals.
            globals()["norm_text"] = norm_text
            globals()["Path"] = Path
            globals()["QPushButton"] = QPushButton
            globals()["QApplication"] = QApplication
            globals()["QAction"] = QAction
            globals()["Qt"] = Qt
            globals()["time"] = time
            globals()["collect_visible_windows"] = collect_visible_windows
            globals()["get_current_bv"] = get_current_bv
            globals()["get_loaded_filename"] = get_loaded_filename
            globals()["resolve_policy"] = resolve_policy
            globals()["collect_confirmation_dialogs"] = collect_confirmation_dialogs
            globals()["find_button_for_decision"] = find_button_for_decision
            globals()["find_primary_main_window"] = find_primary_main_window
            globals()["trigger_close_tab"] = trigger_close_tab

            app = QApplication.instance()
            result["state"]["visible_windows_before"] = collect_visible_windows(app)
            if app is not None and app.activeWindow() is not None:
                result["state"]["active_window_before"] = str(app.activeWindow().windowTitle() or "")
            if app is not None:
                try:
                    result["state"]["quit_on_last_window_closed_before"] = bool(
                        app.quitOnLastWindowClosed()
                    )
                except Exception:
                    result["state"]["quit_on_last_window_closed_before"] = None

            loaded_filename = get_loaded_filename()
            result["policy"]["loaded_filename"] = loaded_filename
            resolved, loaded_is_bndb, companion_exists = resolve_policy(loaded_filename, decision_in)
            result["policy"]["resolved_decision"] = resolved
            result["policy"]["loaded_is_bndb"] = loaded_is_bndb
            result["policy"]["companion_bndb_exists"] = companion_exists

            if mark_dirty:
                current_bv = get_current_bv()
                if current_bv is None or getattr(current_bv, "file", None) is None:
                    result["warnings"].append("no current BinaryView available to mark dirty")
                else:
                    try:
                        current_bv.file.modified = True
                        result["actions"].append("marked_current_view_modified")
                    except Exception as exc:
                        result["warnings"].append(f"unable to mark current view modified: {exc}")

            # For explicit/auto "save" policy, proactively persist .bndb changes
            # before closing UI to avoid losing edits when close paths vary by platform.
            if (not inspect_only) and (resolved == "save"):
                current_bv = get_current_bv()
                if current_bv is None:
                    result["warnings"].append("save policy selected but no current BinaryView is available")
                elif not loaded_filename:
                    result["warnings"].append("save policy selected but no loaded filename is available")
                else:
                    try:
                        save_ok = bool(current_bv.create_database(str(loaded_filename)))
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
                    "title": d["title"],
                    "class": d["class"],
                    "buttons": d["buttons"],
                }
                for d in dialogs
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

                    # Prefer closing only the active tab (keeps app/server alive),
                    # and fall back to closing main windows when action lookup fails.
                    if not dialogs:
                        main_window = find_primary_main_window(app)
                        close_tab_ok, close_tab_reason = trigger_close_tab(main_window)
                        if close_tab_ok:
                            result["actions"].append(close_tab_reason)
                        else:
                            result["warnings"].append(close_tab_reason)
                            closed = 0
                            for widget in app.topLevelWidgets():
                                if not widget.isVisible():
                                    continue
                                cls_name = type(widget).__name__.lower()
                                if "mainwindow" not in cls_name:
                                    continue
                                try:
                                    widget.close()
                                    closed += 1
                                except Exception:
                                    pass
                            result["actions"].append(f"requested_close_main_windows:{closed}")

                    # Re-check for modal prompts after close. If a prompt already exists,
                    # keep it and respond without waiting for a new one.
                    if not dialogs:
                        deadline = time.time() + (wait_ms / 1000.0)
                        while time.time() < deadline:
                            app.processEvents()
                            dialogs = collect_confirmation_dialogs(app)
                            if dialogs:
                                break
                            time.sleep(0.05)

                    if dialogs:
                        chosen_button = None
                        chosen_label = None
                        def dialog_priority(d):
                            cls = str(d.get("class") or "").lower()
                            title = norm_text(d.get("title"))
                            if "messagebox" in cls:
                                return 0
                            if "dialog" in cls:
                                return 1
                            if "modified" in title or "save" in title:
                                return 2
                            return 9

                        dialogs = sorted(dialogs, key=dialog_priority)
                        chosen_dialog = dialogs[0]
                        chosen_button, chosen_label = find_button_for_decision(
                            chosen_dialog["_widget"], resolved
                        )
                        if chosen_button is None:
                            result["warnings"].append(
                                f"confirmation dialog detected but no matching '{resolved}' button found"
                            )
                        elif not chosen_button.isEnabled():
                            result["warnings"].append(
                                f"matched confirmation button '{chosen_label}' is disabled"
                            )
                        else:
                            chosen_button.click()
                            result["actions"].append(
                                f"clicked_confirmation_button:{str(chosen_label)}"
                            )
                            for _ in range(20):
                                app.processEvents()
                                time.sleep(0.02)
                    else:
                        result["actions"].append("no_confirmation_dialog_detected_after_close")

            dialogs_after = collect_confirmation_dialogs(app)
            result["state"]["dialogs_after_action"] = [
                {
                    "title": d["title"],
                    "class": d["class"],
                    "buttons": d["buttons"],
                }
                for d in dialogs_after
            ]
            result["state"]["stuck_confirmation"] = len(dialogs_after) > 0

            if app is not None:
                result["state"]["visible_windows_after"] = collect_visible_windows(app)
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

                if quit_app:
                    try:
                        QTimer.singleShot(quit_delay_ms, app.quit)
                        result["actions"].append(f"scheduled_app_quit:{quit_delay_ms}ms")
                    except Exception as exc:
                        result["warnings"].append(f"unable to schedule app.quit(): {exc}")

            if result["errors"]:
                result["ok"] = False

            print(json.dumps(result, sort_keys=True))
            """
            % json.dumps(config)
        )

        data = self.parent._execute_python(script)

        parsed = None
        if isinstance(data, dict):
            parsed = self.parent._extract_last_json_line(data.get("stdout", ""))
            if parsed is None and isinstance(data.get("return_value"), dict):
                parsed = data["return_value"]

        if self.parent.json_output:
            if parsed is not None and isinstance(data, dict):
                data = dict(data)
                data["quit_result"] = parsed
            self.parent._output(data)
            return

        if not data.get("success"):
            error = data.get("error", {})
            if isinstance(error, dict):
                print(
                    colors.red
                    | f"Error: {error.get('type', 'Unknown')}: {error.get('message', 'Unknown error')}"
                )
            else:
                print(colors.red | f"Error: {error}")
            if data.get("stderr"):
                print(colors.red | data["stderr"], end="")
            return

        if parsed is None:
            print(colors.yellow | "Quit command executed, but no structured result was returned.")
            if data.get("stdout"):
                print(data["stdout"], end="")
            return

        ok = bool(parsed.get("ok"))
        stuck = bool(parsed.get("state", {}).get("stuck_confirmation"))
        decision = parsed.get("policy", {}).get("resolved_decision")
        status_line = "✓ Quit workflow completed" if ok and not stuck else "⚠ Quit workflow completed with issues"
        color = colors.green if ok and not stuck else colors.yellow
        print(color | status_line)
        print(f"  Policy Decision: {decision}")
        print(f"  Loaded File: {parsed.get('policy', {}).get('loaded_filename')}")
        print(f"  Stuck On Confirmation: {stuck}")

        actions = parsed.get("actions", [])
        if actions:
            print("  Actions:")
            for action in actions:
                print(f"    - {action}")

        warnings = parsed.get("warnings", [])
        if warnings:
            print(colors.yellow | "  Warnings:")
            for warning in warnings:
                print(colors.yellow | f"    - {warning}")

        errors = parsed.get("errors", [])
        if errors:
            print(colors.red | "  Errors:")
            for err in errors:
                print(colors.red | f"    - {err}")


@BinaryNinjaCLI.subcommand("functions")
class Functions(cli.Application):
    """List functions in the binary"""

    offset = cli.SwitchAttr(
        ["--offset", "-o"], int, default=0, help="Starting offset for pagination"
    )

    limit = cli.SwitchAttr(
        ["--limit", "-l"], int, default=100, help="Maximum number of functions to return"
    )

    search = cli.SwitchAttr(["--search", "-s"], str, help="Search for functions by name")

    def main(self):
        if self.search:
            data = self.parent._request(
                "GET",
                "searchFunctions",
                {"query": self.search, "offset": self.offset, "limit": self.limit},
            )

            if self.parent.json_output:
                self.parent._output(data)
            else:
                matches = data.get("matches", [])
                if matches:
                    print(f"Found {len(matches)} matching functions:")
                    for func in matches:
                        print(f"  • {func}")
                else:
                    print("No matching functions found")
        else:
            data = self.parent._request(
                "GET", "functions", {"offset": self.offset, "limit": self.limit}
            )

            if self.parent.json_output:
                self.parent._output(data)
            else:
                functions = data.get("functions", [])
                if functions:
                    print(f"Functions ({self.offset}-{self.offset + len(functions)}):")
                    for func in functions:
                        print(f"  • {func}")
                else:
                    print("No functions found")


@BinaryNinjaCLI.subcommand("decompile")
class Decompile(cli.Application):
    """Decompile a function"""

    def main(self, function_name: str):
        data = self.parent._request("GET", "decompile", {"name": function_name})

        if self.parent.json_output:
            self.parent._output(data)
        else:
            if "error" in data:
                print(colors.red | f"Error: {data['error']}")
            else:
                print(colors.cyan | f"Decompiled code for {function_name}:")
                print(data.get("decompiled", "No decompilation available"))


@BinaryNinjaCLI.subcommand("assembly")
class Assembly(cli.Application):
    """Get assembly code for a function"""

    def main(self, function_name: str):
        data = self.parent._request("GET", "assembly", {"name": function_name})

        if self.parent.json_output:
            self.parent._output(data)
        else:
            if "error" in data:
                print(colors.red | f"Error: {data['error']}")
            else:
                print(colors.cyan | f"Assembly for {function_name}:")
                print(data.get("assembly", "No assembly available"))


@BinaryNinjaCLI.subcommand("rename")
class Rename(cli.Application):
    """Rename functions or data"""


@Rename.subcommand("function")
class RenameFunction(cli.Application):
    """Rename a function"""

    def main(self, old_name: str, new_name: str):
        data = self.parent.parent._request(
            "POST", "renameFunction", data={"oldName": old_name, "newName": new_name}
        )
        self.parent.parent._output(data)


@Rename.subcommand("data")
class RenameData(cli.Application):
    """Rename data at address"""

    def main(self, address: str, new_name: str):
        data = self.parent.parent._request(
            "POST", "renameData", data={"address": address, "newName": new_name}
        )
        self.parent.parent._output(data)


@BinaryNinjaCLI.subcommand("comment")
class Comment(cli.Application):
    """Manage comments"""

    delete = cli.Flag(["--delete", "-d"], help="Delete comment instead of setting")

    function = cli.Flag(["--function", "-f"], help="Comment on function instead of address")

    def main(self, target: str, comment: str = None):
        if self.function:
            endpoint = "comment/function"
            params = {"name": target}
        else:
            endpoint = "comment"
            params = {"address": target}

        if self.delete:
            params["_method"] = "DELETE"
            data = self.parent._request("POST", endpoint, data=params)
        elif comment is None:
            # Get comment
            data = self.parent._request("GET", endpoint, params)
        else:
            # Set comment
            params["comment"] = comment
            data = self.parent._request("POST", endpoint, data=params)

        self.parent._output(data)


@BinaryNinjaCLI.subcommand("refs")
class References(cli.Application):
    """Find code references to a function"""

    def main(self, function_name: str):
        data = self.parent._request("GET", "codeReferences", {"function": function_name})

        if self.parent.json_output:
            self.parent._output(data)
        else:
            refs = json.loads(data) if isinstance(data, str) else data
            if "error" in refs:
                print(colors.red | f"Error: {refs['error']}")
            else:
                code_refs = refs.get("code_references", [])
                if code_refs:
                    print(f"Functions that call {function_name}:")
                    for ref in code_refs:
                        print(f"  • {ref}")
                else:
                    print(f"No references found to {function_name}")


@BinaryNinjaCLI.subcommand("logs")
class Logs(cli.Application):
    """View Binary Ninja logs"""

    count = cli.SwitchAttr(["--count", "-c"], int, default=20, help="Number of log entries to show")

    level = cli.SwitchAttr(
        ["--level", "-l"], str, help="Filter by log level (DebugLog, InfoLog, WarningLog, ErrorLog)"
    )

    search = cli.SwitchAttr(["--search", "-s"], str, help="Search in log messages")

    errors = cli.Flag(["--errors", "-e"], help="Show only errors")

    warnings = cli.Flag(["--warnings", "-w"], help="Show only warnings")

    stats = cli.Flag(["--stats"], help="Show log statistics")

    clear = cli.Flag(["--clear"], help="Clear all logs")

    def main(self):
        if self.clear:
            data = self.parent._request("POST", "logs/clear")
            self.parent._output(data)
            return

        if self.stats:
            data = self.parent._request("GET", "logs/stats")
            if self.parent.json_output:
                self.parent._output(data)
            else:
                print("Log Statistics:")
                print(f"  Total logs: {data.get('total_logs', 0)}")
                print("  By level:")
                for level, count in data.get("levels", {}).items():
                    print(f"    {level}: {count}")
            return

        # Get logs
        params = {"count": self.count}
        endpoint = "logs"

        if self.errors:
            endpoint = "logs/errors"
        elif self.warnings:
            endpoint = "logs/warnings"
        else:
            if self.level:
                params["level"] = self.level
            if self.search:
                params["search"] = self.search

        data = self.parent._request("GET", endpoint, params)

        if self.parent.json_output:
            self.parent._output(data)
        else:
            logs = data.get("logs", data.get("errors", data.get("warnings", [])))
            if logs:
                for log in logs:
                    level = log.get("level", "INFO")
                    timestamp = log.get("timestamp", "")[:19]  # Trim microseconds
                    message = log.get("message", "")

                    # Color based on level
                    if "Error" in level:
                        level_color = colors.red
                    elif "Warn" in level:
                        level_color = colors.yellow
                    elif "Debug" in level:
                        level_color = colors.blue
                    else:
                        level_color = colors.white

                    print(f"{colors.dim | timestamp} {level_color | f'[{level:>8}]'} {message}")
            else:
                print("No logs found")


@BinaryNinjaCLI.subcommand("type")
class Type(cli.Application):
    """Get or define types"""

    define = cli.Flag(["--define", "-d"], help="Define types from C code")

    def main(self, type_name_or_code: str):
        if self.define:
            # Define types
            data = self.parent._request("GET", "defineTypes", {"cCode": type_name_or_code})
        else:
            # Get user-defined type
            data = self.parent._request("GET", "getUserDefinedType", {"name": type_name_or_code})

        if self.parent.json_output:
            self.parent._output(data)
        else:
            result = json.loads(data) if isinstance(data, str) else data
            if "error" in result:
                print(colors.red | f"Error: {result['error']}")
            elif "type_definition" in result:
                print(f"Type: {result.get('type_name', type_name_or_code)}")
                print(result.get("type_definition", "No definition"))
            else:
                print(result)


@BinaryNinjaCLI.subcommand("imports")
class Imports(cli.Application):
    """List imported symbols"""

    offset = cli.SwitchAttr(["--offset", "-o"], int, default=0)
    limit = cli.SwitchAttr(["--limit", "-l"], int, default=100)

    def main(self):
        data = self.parent._request("GET", "imports", {"offset": self.offset, "limit": self.limit})

        if self.parent.json_output:
            self.parent._output(data)
        else:
            imports = data.get("imports", [])
            if imports:
                print(f"Imports ({self.offset}-{self.offset + len(imports)}):")
                for imp in imports:
                    print(f"  • {imp}")
            else:
                print("No imports found")


@BinaryNinjaCLI.subcommand("exports")
class Exports(cli.Application):
    """List exported symbols"""

    offset = cli.SwitchAttr(["--offset", "-o"], int, default=0)
    limit = cli.SwitchAttr(["--limit", "-l"], int, default=100)

    def main(self):
        data = self.parent._request("GET", "exports", {"offset": self.offset, "limit": self.limit})

        if self.parent.json_output:
            self.parent._output(data)
        else:
            exports = data.get("exports", [])
            if exports:
                print(f"Exports ({self.offset}-{self.offset + len(exports)}):")
                for exp in exports:
                    print(f"  • {exp}")
            else:
                print("No exports found")


@BinaryNinjaCLI.subcommand("python")
class Python(cli.Application):
    """Execute Python code in Binary Ninja context

    Examples:
        python "print('Hello')"          # Execute inline code
        python < script.py               # Execute from stdin
        python script.py                 # Execute from file
        python -f script.py              # Execute from file (explicit)
        python -                         # Read from stdin
        python -i                        # Interactive mode
        echo "2+2" | python -            # Pipe code to execute
    """

    file = cli.SwitchAttr(["-f", "--file"], cli.ExistingFile, help="Execute Python code from file")

    interactive = cli.Flag(["-i", "--interactive"], help="Start interactive Python session")

    stdin = cli.Flag(["--stdin"], help="Read code from stdin (can also use '-' as argument)")

    complete = cli.SwitchAttr(
        ["-c", "--complete"], str, help="Get code completions for partial input"
    )
    exec_timeout = cli.SwitchAttr(
        ["--exec-timeout"],
        float,
        default=30.0,
        help="Execution timeout in seconds for /console/execute (default: 30)",
    )

    def main(self, *args):
        code = None

        # Handle completion request
        if self.complete is not None:
            data = self.parent._request(
                "GET", "console/complete", params={"partial": self.complete}
            )
            completions = data.get("completions", [])
            if self.parent.json_output:
                self.parent._output(data)
            else:
                if completions:
                    for comp in completions:
                        print(comp)
                else:
                    print(f"No completions for '{self.complete}'")
            return 0

        # Determine source of code
        if self.interactive:
            # Interactive mode
            self._interactive_mode()
            return 0

        elif self.file:
            # Explicit file flag
            try:
                code = self.file.read()
            except Exception as e:
                print(colors.red | f"Error reading file: {e}")
                return 1

        elif self.stdin or (args and args[0] == "-"):
            # Read from stdin
            try:
                import sys

                code = sys.stdin.read()
                if not code.strip():
                    print(colors.red | "No input received from stdin")
                    return 1
            except KeyboardInterrupt:
                print("\nCancelled")
                return 1
            except Exception as e:
                print(colors.red | f"Error reading stdin: {e}")
                return 1

        elif args:
            # Check if first argument is a file
            if len(args) == 1 and not args[0].startswith("-"):
                from pathlib import Path

                file_path = Path(args[0])
                if file_path.exists() and file_path.is_file():
                    # It's a file, read it
                    try:
                        code = file_path.read_text()
                    except Exception as e:
                        print(colors.red | f"Error reading file '{args[0]}': {e}")
                        return 1
                else:
                    # Not a file, treat as inline code
                    code = " ".join(args)
            else:
                # Multiple arguments or starts with -, treat as inline code
                code = " ".join(args)

        else:
            # No arguments, check if stdin is piped
            import sys

            # Check if stdin has data (works on Unix-like systems)
            if sys.stdin.isatty():
                # No piped input, show usage
                print("Usage: python [options] <code|file|->")
                print("       python script.py              # Execute file")
                print("       python 'print(42)'            # Execute inline code")
                print("       python -                      # Read from stdin")
                print("       echo 'print(42)' | python     # Pipe to stdin")
                print("       python -i                     # Interactive mode")
                print("       python -f script.py           # Explicit file")
                return 1
            else:
                # Data is piped to stdin
                try:
                    code = sys.stdin.read()
                except Exception as e:
                    print(colors.red | f"Error reading piped input: {e}")
                    return 1

        if not code:
            print(colors.red | "No code to execute")
            return 1

        # Execute the code
        data = self.parent._request(
            "POST",
            "console/execute",
            data={"command": code, "timeout": self.exec_timeout},
        )

        if self.parent.json_output:
            self.parent._output(data)
        else:
            if data.get("success"):
                # Show output
                if data.get("stdout"):
                    print(data["stdout"], end="")
                if data.get("stderr"):
                    print(colors.red | data["stderr"], end="")

                # Show return value if present
                if data.get("return_value") is not None:
                    if not data.get("stdout", "").strip().endswith(str(data["return_value"])):
                        print(colors.cyan | f"→ {data['return_value']}")

                # Show variables if any were created/modified
                if data.get("variables"):
                    print(colors.green | f"\nVariables: {', '.join(data['variables'].keys())}")

                # Show execution time if verbose
                if self.parent.verbose and "execution_time" in data:
                    print(colors.dim | f"Execution time: {data['execution_time']:.3f}s")
            else:
                # Show error
                error = data.get("error", {})
                if isinstance(error, dict):
                    print(
                        colors.red
                        | f"Error: {error.get('type', 'Unknown')}: {error.get('message', 'Unknown error')}"
                    )
                    if self.parent.verbose and error.get("traceback"):
                        print(colors.dim | error["traceback"])
                else:
                    print(colors.red | f"Error: {error}")

    def _interactive_mode(self):
        """Interactive Python session"""
        print("Binary Ninja Python Console (type 'exit()' to quit)")
        print("=" * 50)

        while True:
            try:
                # Get input
                code = input(colors.cyan | ">>> ")
                if code.strip() in ["exit()", "quit()", "exit", "quit"]:
                    break

                if not code.strip():
                    continue

                # Handle multi-line input
                if code.rstrip().endswith(":"):
                    lines = [code]
                    while True:
                        line = input(colors.cyan | "... ")
                        lines.append(line)
                        if not line.strip():
                            break
                    code = "\n".join(lines)

                # Execute
                data = self.parent._request(
                    "POST",
                    "console/execute",
                    data={"command": code, "timeout": self.exec_timeout},
                )

                # Display results
                if data.get("success"):
                    if data.get("stdout"):
                        print(data["stdout"], end="")
                    if data.get("stderr"):
                        print(colors.yellow | data["stderr"], end="")
                    if data.get("return_value") is not None:
                        # Don't duplicate if already in stdout
                        stdout = data.get("stdout", "")
                        if not stdout.strip().endswith(str(data["return_value"])):
                            print(colors.green | data["return_value"])
                else:
                    error = data.get("error", {})
                    if isinstance(error, dict):
                        print(
                            colors.red
                            | f"{error.get('type', 'Unknown')}: {error.get('message', 'Unknown error')}"
                        )
                    else:
                        print(colors.red | str(error))

            except KeyboardInterrupt:
                print("\nKeyboardInterrupt")
            except EOFError:
                print()
                break
            except Exception as e:
                print(colors.red | f"Client error: {e}")


if __name__ == "__main__":
    BinaryNinjaCLI.run()
