from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import urllib.parse
import errno
import os
import time
import uuid
from typing import Dict, Any, Optional
import binaryninja as bn
import threading
from ..core.binary_operations import BinaryOperations
from ..core.console_capture_adapter import ConsoleCaptureAdapter
from ..core.config import Config
from ..api.endpoints import BinaryNinjaEndpoints
from .api_contracts import (
    as_dict,
    as_list,
    allows_missing_api_version,
    expected_api_version,
    get_endpoint_registry_json,
    normalize_endpoint_path,
    normalize_ui_contract,
)
from .view_sync import (
    annotate_view_details,
    build_logical_view_summaries,
    describe_view,
    extract_view_filename,
    extract_view_id,
    list_ui_view_records,
    list_ui_views,
    TARGET_ERROR_TARGET_AMBIGUOUS,
    TARGET_ERROR_TARGET_CONFLICT,
    TARGET_ERROR_TARGET_NOT_FOUND,
    TARGET_ERROR_TARGET_REQUIRED,
    resolve_target_view_from_candidates,
    select_preferred_view,
)
from ..utils.string_utils import parse_int_or_default

try:
    from ..core.log_capture import get_log_capture
except Exception:
    # Fallback if main log capture fails
    from ..core.log_capture_simple import SimpleLogCapture

    _simple_log_capture = None

    def get_log_capture():
        global _simple_log_capture
        if _simple_log_capture is None:
            _simple_log_capture = SimpleLogCapture()
        return _simple_log_capture


try:
    from ..core.python_executor_v2 import get_console_capture

    bn.log_info("Using enhanced Python executor V2 for console")
except Exception as e:
    bn.log_warn(f"Failed to import enhanced Python executor V2: {e}")
    # Try v1 Python executor
    try:
        from ..core.python_executor import get_console_capture

        bn.log_info("Using enhanced Python executor V1 for console")
    except Exception as e2:
        bn.log_warn(f"Failed to import enhanced Python executor: {e2}")
        # Try original console capture
        try:
            from ..core.console_capture import get_console_capture
        except Exception:
            # Fallback if console capture fails
            from ..core.console_capture_simple import SimpleConsoleCapture

            _simple_console_capture = None

            def get_console_capture():
                global _simple_console_capture
                if _simple_console_capture is None:
                    _simple_console_capture = SimpleConsoleCapture()
                return _simple_console_capture


# Global variable to track which log capture we're using
_active_log_capture = None


def get_active_log_capture():
    """Get the active log capture instance (file-based or simple)"""
    global _active_log_capture
    if _active_log_capture is None:
        try:
            # Try to use file-based capture first
            from ..core.log_capture import get_log_capture

            _active_log_capture = get_log_capture()
        except Exception as e:
            # Fall back to simple capture
            try:
                from ..core.log_capture_simple import SimpleLogCapture

                _active_log_capture = SimpleLogCapture()
                print(f"[MCP] Using simple log capture due to: {e}")
            except Exception as e2:
                print(f"[MCP] Failed to initialize any log capture: {e2}")
                _active_log_capture = None
    return _active_log_capture


class MCPRequestHandler(BaseHTTPRequestHandler):
    binary_ops = None  # Will be set by the server
    mcp_server = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @property
    def endpoints(self):
        # Create endpoints on demand to ensure binary_ops is set
        if not hasattr(self, "_endpoints"):
            if not self.binary_ops:
                raise RuntimeError("binary_ops not initialized")
            self._endpoints = BinaryNinjaEndpoints(self.binary_ops)
        return self._endpoints

    def log_message(self, format, *args):
        bn.log_info(format % args)

    def _set_headers(self, content_type="application/json", status_code=200):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        path = urllib.parse.urlparse(self.path).path
        version = self._expected_api_version(path)
        self.send_header("X-Binja-MCP-Api-Version", str(version))
        self.send_header("X-Binja-MCP-Endpoint", path)
        self.end_headers()

    def _send_json_response(self, data: Dict[str, Any], status_code: int = 200):
        self._set_headers(status_code=status_code)
        path = urllib.parse.urlparse(self.path).path
        version = self._expected_api_version(path)
        if isinstance(data, dict):
            payload = dict(data)
            payload.setdefault("_endpoint", path)
            payload.setdefault("_api_version", version)
        else:
            payload = data
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def _instance_metadata(self) -> Dict[str, Any]:
        server = getattr(self, "mcp_server", None)
        if server is None:
            return {"service": "binary_ninja_mcp"}
        return server.instance_metadata()

    def _attach_instance_fields(self, item: Dict[str, Any]) -> Dict[str, Any]:
        metadata = self._instance_metadata()
        instance_id = metadata.get("instance_id")
        if instance_id:
            item.setdefault("instance_id", instance_id)
            view_id = item.get("view_id")
            if view_id is not None:
                item.setdefault("global_view_id", f"{instance_id}:{view_id}")
                item["target_hint"] = f"--view-id {item['global_view_id']}"
        return item

    def _attach_instance_to_views(self, views: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        return [self._attach_instance_fields(dict(view)) for view in views]

    def _parse_query_params(self) -> Dict[str, str]:
        parsed_path = urllib.parse.urlparse(self.path)
        return dict(urllib.parse.parse_qsl(parsed_path.query))

    def _parse_post_params(self) -> Dict[str, Any]:
        """Parse POST request parameters from various formats.

        Supports:
        - JSON data (application/json)
        - Form data (application/x-www-form-urlencoded)
        - Raw text (text/plain)

        Returns:
            Dictionary containing the parsed parameters
        """
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}

        content_type = self.headers.get("Content-Type", "")
        post_data = self.rfile.read(content_length).decode("utf-8")

        bn.log_info(f"Received POST data: {post_data}")
        bn.log_info(f"Content-Type: {content_type}")

        # Handle JSON data
        if "application/json" in content_type.lower():
            try:
                return json.loads(post_data)
            except json.JSONDecodeError as e:
                bn.log_error(f"Failed to parse JSON: {e}")
                return {"error": "Invalid JSON format"}

        # Handle form data
        if "application/x-www-form-urlencoded" in content_type.lower():
            try:
                return dict(urllib.parse.parse_qsl(post_data))
            except Exception as e:
                bn.log_error(f"Failed to parse form data: {e}")
                return {"error": "Invalid form data format"}

        # Handle raw text
        if "text/plain" in content_type.lower() or not content_type:
            return {"name": post_data.strip()}

        # Fallback for uncommon content-types.
        return {"name": post_data.strip()}

    @staticmethod
    def _as_list(value: Any) -> list:
        return as_list(value)

    @staticmethod
    def _as_dict(value: Any) -> dict:
        return as_dict(value)

    @staticmethod
    def _normalize_endpoint_path(path: str) -> str:
        return normalize_endpoint_path(path)

    def _expected_api_version(self, path: str) -> int:
        return expected_api_version(path)

    def _validate_endpoint_version(self, path: str, params: Optional[Dict[str, Any]]) -> bool:
        endpoint_path = self._normalize_endpoint_path(path)
        expected = self._expected_api_version(endpoint_path)

        received_raw = None
        if isinstance(params, dict):
            received_raw = params.get("_api_version")
        if received_raw is None:
            received_raw = self.headers.get("X-Binja-MCP-Api-Version")
        if received_raw is None:
            if allows_missing_api_version(endpoint_path):
                return True
            self._send_json_response(
                {
                    "error": "Missing endpoint API version",
                    "endpoint": endpoint_path,
                    "expected_api_version": expected,
                    "help": "Include _api_version or X-Binja-MCP-Api-Version in requests.",
                },
                400,
            )
            return False

        try:
            received = int(received_raw)
        except (TypeError, ValueError):
            self._send_json_response(
                {
                    "error": "Invalid endpoint API version",
                    "endpoint": endpoint_path,
                    "expected_api_version": expected,
                    "received_api_version": received_raw,
                },
                400,
            )
            return False

        if received != expected:
            self._send_json_response(
                {
                    "error": "Endpoint API version mismatch",
                    "endpoint": endpoint_path,
                    "expected_api_version": expected,
                    "received_api_version": received,
                },
                409,
            )
            return False
        return True

    def _normalize_ui_contract(self, endpoint_path: str, raw_result: Any) -> Dict[str, Any]:
        return normalize_ui_contract(endpoint_path, raw_result)

    @staticmethod
    def _parse_bool(value: Any, default: bool = False) -> bool:
        """Parse booleans from JSON/form-style values."""
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return default

    def _check_binary_loaded(self):
        """Check if a binary is loaded and return appropriate error response if not"""
        if not self.binary_ops or not self.binary_ops.current_view:
            self._send_json_response({"error": "No binary loaded"}, 400)
            return False
        return True

    def _maybe_refresh_current_view(
        self, params: Optional[Dict[str, Any]] = None, clear_if_missing: bool = False
    ):
        """Best-effort refresh of current BinaryView for multi-tab UI workflows.

        - If `view_id` (or `viewId`) is provided, attempt to select a previously-seen view.
        - If `filename` (or `file`) is provided, attempt to select a previously-seen view.
        - Otherwise (or as fallback), use `binaryninjaui.UIContext.currentBinaryView()` when available.
        """
        if not self.binary_ops:
            return

        requested_view_id = None
        requested_filename = None
        if params:
            requested_view_id = params.get("view_id") or params.get("viewId")
            requested_filename = params.get("filename") or params.get("file")

        if requested_view_id:
            try:
                selected = self.binary_ops.select_view_by_id(str(requested_view_id))
                if selected:
                    return
            except Exception:
                pass

        if requested_filename:
            try:
                selected = self.binary_ops.select_view_by_filename(str(requested_filename))
                if selected:
                    return
            except Exception:
                pass

        try:
            import binaryninjaui  # type: ignore

            ui_views = list_ui_views(binaryninjaui)
            for view in ui_views:
                try:
                    self.binary_ops.register_view(view)
                except Exception:
                    continue
            chosen_view = select_preferred_view(
                ui_views,
                requested_filename=str(requested_filename or ""),
                requested_view_id=str(requested_view_id or ""),
            )

            if chosen_view is not None:
                self.binary_ops.current_view = chosen_view
                return

            if clear_if_missing:
                existing = self.binary_ops.current_view
                keep_existing = (
                    bool(extract_view_filename(existing)) if existing is not None else False
                )
                if not keep_existing:
                    self.binary_ops.current_view = None
        except Exception:
            # UI not available (headless) or API mismatch; ignore.
            pass

    def _collect_candidate_views(self) -> tuple[list[Any], dict[int, dict[str, Any]]]:
        if not self.binary_ops:
            return [], {}

        views: list[Any] = []
        metadata_by_view: dict[int, dict[str, Any]] = {}
        seen_ids: set[int] = set()

        def add_view(view: Any, *, source: str, window_title: Optional[str] = None) -> None:
            if view is None:
                return
            ident = id(view)
            metadata = {
                "source": source,
                "window_title": window_title,
            }
            if ident not in metadata_by_view:
                metadata_by_view[ident] = metadata
            elif source == "ui":
                metadata_by_view[ident].update(
                    {
                        "source": source,
                        "window_title": window_title or metadata_by_view[ident].get("window_title"),
                    }
                )

            if ident in seen_ids:
                return
            seen_ids.add(ident)
            views.append(view)

        add_view(self.binary_ops.current_view, source="current")

        try:
            import binaryninjaui  # type: ignore

            for record in list_ui_view_records(binaryninjaui):
                ui_view = record.get("view")
                try:
                    self.binary_ops.register_view(ui_view)
                except Exception:
                    pass
                add_view(
                    ui_view,
                    source=str(record.get("source") or "ui"),
                    window_title=str(record.get("window_title") or "") or None,
                )
        except Exception:
            pass

        for view in self.binary_ops.list_registered_views():
            add_view(view, source="registry")

        return views, metadata_by_view

    @staticmethod
    def _target_error_status_code(error: Optional[dict]) -> int:
        if not isinstance(error, dict):
            return 400
        code = str(error.get("error_code") or "").strip()
        if code in {
            TARGET_ERROR_TARGET_CONFLICT,
            TARGET_ERROR_TARGET_AMBIGUOUS,
            TARGET_ERROR_TARGET_REQUIRED,
        }:
            return 409
        if code == TARGET_ERROR_TARGET_NOT_FOUND:
            return 404
        return 400

    def _resolve_request_view(
        self,
        params: Optional[Dict[str, Any]],
        *,
        require_explicit_target: bool = False,
    ) -> tuple[Any, Optional[dict], list[Any], dict[int, dict[str, Any]]]:
        if not self.binary_ops:
            return None, None, [], {}

        requested_view_id = None
        requested_filename = None
        if params:
            requested_view_id = params.get("view_id") or params.get("viewId")
            requested_filename = params.get("filename") or params.get("file")

        candidates, metadata_by_view = self._collect_candidate_views()
        selected_view, target_error = resolve_target_view_from_candidates(
            candidates,
            requested_view_id=str(requested_view_id) if requested_view_id else None,
            requested_filename=str(requested_filename) if requested_filename else None,
            fallback_view=self.binary_ops.current_view,
            require_explicit_target=require_explicit_target,
            metadata_by_view=metadata_by_view,
        )
        if selected_view is not None:
            self.binary_ops.current_view = selected_view
        return selected_view, target_error, candidates, metadata_by_view

    @staticmethod
    def _view_context_fields(view: Any) -> Dict[str, Any]:
        return {
            "selected_view_filename": extract_view_filename(view),
            "selected_view_id": extract_view_id(view),
        }

    def _build_target_resolution_response(
        self,
        selected_view: Any,
        *,
        candidates: list[Any],
        metadata_by_view: dict[int, dict[str, Any]],
        requested_view_id: Optional[str] = None,
        requested_filename: Optional[str] = None,
    ) -> dict[str, Any]:
        current_view = self.binary_ops.current_view if self.binary_ops else None
        current_view_id = extract_view_id(current_view)
        open_views_raw: list[dict[str, Any]] = []
        for view in candidates:
            details = describe_view(view, metadata=metadata_by_view.get(id(view)))
            details["is_current"] = bool(current_view is not None and view is current_view)
            open_views_raw.append(details)

        logical_open_views = build_logical_view_summaries(
            candidates,
            metadata_by_view=metadata_by_view,
            current_view=current_view,
        )
        open_views = annotate_view_details(open_views_raw, logical_views=logical_open_views)
        open_views = self._attach_instance_to_views(open_views)
        for logical_view in logical_open_views:
            if isinstance(logical_view, dict):
                self._attach_instance_fields(logical_view)

        selected_details = (
            describe_view(selected_view, metadata=metadata_by_view.get(id(selected_view)))
            if selected_view is not None
            else None
        )
        if isinstance(selected_details, dict):
            selected_details["is_current"] = bool(
                current_view is not None and selected_view is current_view
            )
            selected_details = annotate_view_details(
                [selected_details],
                logical_views=logical_open_views,
            )[0]
            selected_details = self._attach_instance_fields(selected_details)

        return {
            **self._instance_metadata(),
            "resolved": selected_view is not None,
            "target": selected_details,
            "open_views": open_views,
            "open_view_count": len(open_views),
            "logical_open_views": logical_open_views,
            "logical_view_count": len(logical_open_views),
            "current_view_id": current_view_id,
            "current_filename": extract_view_filename(current_view),
            "requested_view_id": requested_view_id,
            "requested_filename": requested_filename,
        }

    def _resolve_python_view(self, params: Optional[Dict[str, Any]]) -> tuple[Any, Optional[dict]]:
        """Resolve BinaryView for /console/execute without relying only on global current_view."""
        binary_view, target_error, _candidates, _metadata = self._resolve_request_view(
            params,
            require_explicit_target=True,
        )
        return binary_view, target_error

    def do_GET(self):
        try:
            # Endpoints that don't require a binary to be loaded
            no_binary_required = ["/status", "/views", "/target", "/logs", "/console", "/meta"]
            params = self._parse_query_params()
            path = urllib.parse.urlparse(self.path).path
            if not self._validate_endpoint_version(path, params):
                return
            if any(path.startswith(prefix) for prefix in no_binary_required):
                self._maybe_refresh_current_view(params, clear_if_missing=(path == "/status"))
            else:
                _, target_error, _candidates, _metadata = self._resolve_request_view(
                    params,
                    require_explicit_target=True,
                )
                if target_error is not None:
                    self._send_json_response(
                        target_error,
                        self._target_error_status_code(target_error),
                    )
                    return

            # For most endpoints, check if binary is loaded
            if not any(path.startswith(prefix) for prefix in no_binary_required):
                if not self._check_binary_loaded():
                    return

            offset = parse_int_or_default(params.get("offset"), 0)
            limit = parse_int_or_default(params.get("limit"), 100)

            if path == "/status":
                status = {
                    "loaded": self.binary_ops and self.binary_ops.current_view is not None,
                    "filename": self.binary_ops.current_view.file.filename
                    if self.binary_ops and self.binary_ops.current_view
                    else None,
                }
                status.update(self._instance_metadata())
                self._send_json_response(status)

            elif path == "/views":
                view_payload_raw: list[dict[str, Any]] = []
                current_view = self.binary_ops.current_view if self.binary_ops else None
                current_view_id = extract_view_id(current_view)
                candidate_views, metadata_by_view = self._collect_candidate_views()
                for view in candidate_views:
                    details = describe_view(view, metadata=metadata_by_view.get(id(view)))
                    details["is_current"] = bool(current_view is not None and view is current_view)
                    view_payload_raw.append(details)

                logical_views = build_logical_view_summaries(
                    candidate_views,
                    metadata_by_view=metadata_by_view,
                    current_view=current_view,
                )
                view_payload = annotate_view_details(view_payload_raw, logical_views=logical_views)
                view_payload = self._attach_instance_to_views(view_payload)
                for logical_view in logical_views:
                    if isinstance(logical_view, dict):
                        self._attach_instance_fields(logical_view)

                self._send_json_response(
                    {
                        **self._instance_metadata(),
                        "views": view_payload,
                        "count": len(view_payload),
                        "logical_views": logical_views,
                        "logical_view_count": len(logical_views),
                        "current_view_id": current_view_id,
                        "current_filename": extract_view_filename(current_view),
                    }
                )

            elif path == "/target/resolve":
                requested_view_id = params.get("view_id") or params.get("viewId")
                requested_filename = params.get("filename") or params.get("file")
                selected_view, target_error, candidates, metadata_by_view = (
                    self._resolve_request_view(
                        params,
                        require_explicit_target=True,
                    )
                )
                if target_error is not None:
                    self._send_json_response(
                        target_error,
                        self._target_error_status_code(target_error),
                    )
                    return
                if selected_view is None:
                    self._send_json_response(
                        {
                            "error_code": TARGET_ERROR_TARGET_NOT_FOUND,
                            "error": "No BinaryViews open",
                            "help": "Open a binary in Binary Ninja before resolving a target.",
                        },
                        404,
                    )
                    return
                self._send_json_response(
                    self._build_target_resolution_response(
                        selected_view,
                        candidates=candidates,
                        metadata_by_view=metadata_by_view,
                        requested_view_id=str(requested_view_id) if requested_view_id else None,
                        requested_filename=str(requested_filename) if requested_filename else None,
                    )
                )

            elif path == "/meta/endpoints":
                self._send_json_response({"endpoints": get_endpoint_registry_json()})

            elif path == "/meta/instance":
                self._send_json_response(self._instance_metadata())

            elif path == "/functions" or path == "/methods":
                functions = self.binary_ops.get_function_names(offset, limit)
                bn.log_info(f"Found {len(functions)} functions")
                self._send_json_response({"functions": functions})

            elif path == "/classes":
                classes = self.binary_ops.get_class_names(offset, limit)
                self._send_json_response({"classes": classes})

            elif path == "/segments":
                segments = self.binary_ops.get_segments(offset, limit)
                self._send_json_response({"segments": segments})

            elif path == "/imports":
                imports = self.endpoints.get_imports(offset, limit)
                self._send_json_response({"imports": imports})

            elif path == "/exports":
                exports = self.endpoints.get_exports(offset, limit)
                self._send_json_response({"exports": exports})

            elif path == "/namespaces":
                namespaces = self.endpoints.get_namespaces(offset, limit)
                self._send_json_response({"namespaces": namespaces})

            elif path == "/data":
                try:
                    data_items = self.binary_ops.get_defined_data(offset, limit)
                    self._send_json_response({"data": data_items})
                except Exception as e:
                    bn.log_error(f"Error getting data items: {e}")
                    self._send_json_response({"error": str(e)}, 500)

            elif path == "/searchFunctions":
                search_term = params.get("query", "")
                matches = self.endpoints.search_functions(search_term, offset, limit)
                self._send_json_response({"matches": matches})

            elif path == "/decompile":
                function_name = params.get("name") or params.get("functionName")
                if not function_name:
                    self._send_json_response(
                        {
                            "error": "Missing function name parameter. Use ?name=function_name or ?functionName=function_name"
                        },
                        400,
                    )
                    return

                self._handle_decompile(function_name)

            elif path == "/assembly":
                function_name = params.get("name") or params.get("functionName")
                if not function_name:
                    self._send_json_response(
                        {
                            "error": "Missing function name parameter. Use ?name=function_name or ?functionName=function_name"
                        },
                        400,
                    )
                    return

                try:
                    func_info = self.binary_ops.get_function_info(function_name)
                    if not func_info:
                        bn.log_error(f"Function not found: {function_name}")
                        self._send_json_response(
                            {
                                "error": "Function not found",
                                "requested_name": function_name,
                                "available_functions": self.binary_ops.get_function_names(0, 10),
                            },
                            404,
                        )
                        return

                    bn.log_info(f"Found function for assembly: {func_info}")
                    assembly = self.binary_ops.get_assembly_function(function_name)

                    if assembly is None:
                        self._send_json_response(
                            {
                                "error": "Assembly retrieval failed",
                                "function": func_info,
                                "reason": "Function assembly could not be retrieved. Check the Binary Ninja log for detailed error information.",
                            },
                            500,
                        )
                    else:
                        self._send_json_response({"assembly": assembly, "function": func_info})
                except Exception as e:
                    bn.log_error(f"Error handling assembly request: {str(e)}")
                    import traceback

                    bn.log_error(traceback.format_exc())
                    self._send_json_response(
                        {
                            "error": "Assembly retrieval failed",
                            "requested_name": function_name,
                            "exception": str(e),
                        },
                        500,
                    )

            elif path == "/functionAt":
                address_str = params.get("address")
                if not address_str:
                    self._send_json_response(
                        {
                            "error": "Missing address parameter",
                            "help": "Required parameter: address (in hex format, e.g., 0x41d100) the address of an insruction",
                            "received": params,
                        },
                        400,
                    )
                    return

                try:
                    # Convert hex string to integer
                    if isinstance(address_str, str) and address_str.startswith("0x"):
                        offset = int(address_str, 16)
                    else:
                        offset = int(address_str)

                    # Add function to binary_operations.py
                    function_names = self.binary_ops.get_functions_containing_address(offset)

                    self._send_json_response({"address": hex(offset), "functions": function_names})
                except ValueError:
                    self._send_json_response(
                        {
                            "error": "Invalid address format",
                            "help": "Address must be a valid hexadecimal (0x...) or decimal number",
                            "received": address_str,
                        },
                        400,
                    )
                except Exception as e:
                    bn.log_error(f"Error handling function_at request: {e}")
                    self._send_json_response(
                        {
                            "error": str(e),
                            "address": address_str,
                        },
                        500,
                    )

            elif path == "/codeReferences":
                function_name = params.get("function")
                if not function_name:
                    self._send_json_response(
                        {
                            "error": "Missing function parameter",
                            "help": "Required parameter: function (name of the function to find references to)",
                            "received": params,
                        },
                        400,
                    )
                    return

                try:
                    # Get function information first to confirm it exists
                    func_info = self.binary_ops.get_function_info(function_name)
                    if not func_info:
                        self._send_json_response(
                            {"error": "Function not found", "requested_function": function_name},
                            404,
                        )
                        return

                    # Get all code references to this function
                    code_refs = self.binary_ops.get_function_code_references(function_name)

                    self._send_json_response(
                        {"function": function_name, "code_references": code_refs}
                    )
                except Exception as e:
                    bn.log_error(f"Error handling code_references request: {e}")
                    self._send_json_response(
                        {
                            "error": str(e),
                            "function": function_name,
                        },
                        500,
                    )

            elif path == "/getUserDefinedType":
                type_name = params.get("name")
                if not type_name:
                    self._send_json_response(
                        {
                            "error": "Missing name parameter",
                            "help": "Required parameter: name (name of the user-defined type to retrieve)",
                            "received": params,
                        },
                        400,
                    )
                    return

                try:
                    # Get the user-defined type definition
                    type_info = self.binary_ops.get_user_defined_type(type_name)

                    if type_info:
                        self._send_json_response(type_info)
                    else:
                        # If type not found, list available types for reference
                        available_types = {}

                        try:
                            if (
                                hasattr(self.binary_ops._current_view, "user_type_container")
                                and self.binary_ops._current_view.user_type_container
                            ):
                                for (
                                    type_id
                                ) in self.binary_ops._current_view.user_type_container.types.keys():
                                    current_type = (
                                        self.binary_ops._current_view.user_type_container.types[
                                            type_id
                                        ]
                                    )
                                    available_types[current_type[0]] = (
                                        str(current_type[1].type)
                                        if hasattr(current_type[1], "type")
                                        else "unknown"
                                    )
                        except Exception as e:
                            bn.log_error(f"Error listing available types: {e}")

                        self._send_json_response(
                            {
                                "error": "Type not found",
                                "requested_type": type_name,
                                "available_types": available_types,
                            },
                            404,
                        )
                except Exception as e:
                    bn.log_error(f"Error handling getUserDefinedType request: {e}")
                    self._send_json_response(
                        {
                            "error": str(e),
                            "type_name": type_name,
                        },
                        500,
                    )

            elif path == "/comment":
                if self.command == "GET":
                    address = params.get("address")
                    if not address:
                        self._send_json_response(
                            {
                                "error": "Missing address parameter",
                                "help": "Required parameter: address",
                                "received": params,
                            },
                            400,
                        )
                        return

                    try:
                        address_int = int(address, 16) if isinstance(address, str) else int(address)
                        comment = self.binary_ops.get_comment(address_int)
                        if comment is not None:
                            self._send_json_response(
                                {
                                    "success": True,
                                    "address": hex(address_int),
                                    "comment": comment,
                                }
                            )
                        else:
                            self._send_json_response(
                                {
                                    "success": True,
                                    "address": hex(address_int),
                                    "comment": None,
                                    "message": "No comment found at this address",
                                }
                            )
                    except ValueError:
                        self._send_json_response({"error": "Invalid address format"}, 400)
                elif self.command == "DELETE":
                    address = params.get("address")
                    if not address:
                        self._send_json_response(
                            {
                                "error": "Missing address parameter",
                                "help": "Required parameter: address",
                                "received": params,
                            },
                            400,
                        )
                        return

                    try:
                        address_int = int(address, 16) if isinstance(address, str) else int(address)
                        success = self.binary_ops.delete_comment(address_int)
                        if success:
                            self._send_json_response(
                                {
                                    "success": True,
                                    "message": f"Successfully deleted comment at {hex(address_int)}",
                                }
                            )
                        else:
                            self._send_json_response(
                                {
                                    "error": "Failed to delete comment",
                                    "message": "The comment could not be deleted at the specified address.",
                                },
                                500,
                            )
                    except ValueError:
                        self._send_json_response({"error": "Invalid address format"}, 400)
                else:  # POST
                    address = params.get("address")
                    comment = params.get("comment")
                    if not address or comment is None:
                        self._send_json_response(
                            {
                                "error": "Missing parameters",
                                "help": "Required parameters: address and comment",
                                "received": params,
                            },
                            400,
                        )
                        return

                    try:
                        address_int = int(address, 16) if isinstance(address, str) else int(address)
                        success = self.binary_ops.set_comment(address_int, comment)
                        if success:
                            self._send_json_response(
                                {
                                    "success": True,
                                    "message": f"Successfully set comment at {hex(address_int)}",
                                    "comment": comment,
                                }
                            )
                        else:
                            self._send_json_response(
                                {
                                    "error": "Failed to set comment",
                                    "message": "The comment could not be set at the specified address.",
                                },
                                500,
                            )
                    except ValueError:
                        self._send_json_response({"error": "Invalid address format"}, 400)

            elif path == "/comment/function":
                if self.command == "GET":
                    function_name = params.get("name") or params.get("functionName")
                    if not function_name:
                        self._send_json_response(
                            {
                                "error": "Missing function name parameter",
                                "help": "Required parameter: name (or functionName)",
                                "received": params,
                            },
                            400,
                        )
                        return

                    comment = self.binary_ops.get_function_comment(function_name)
                    if comment is not None:
                        self._send_json_response(
                            {
                                "success": True,
                                "function": function_name,
                                "comment": comment,
                            }
                        )
                    else:
                        self._send_json_response(
                            {
                                "success": True,
                                "function": function_name,
                                "comment": None,
                                "message": "No comment found for this function",
                            }
                        )
                elif self.command == "DELETE":
                    function_name = params.get("name") or params.get("functionName")
                    if not function_name:
                        self._send_json_response(
                            {
                                "error": "Missing function name parameter",
                                "help": "Required parameter: name (or functionName)",
                                "received": params,
                            },
                            400,
                        )
                        return

                    success = self.binary_ops.delete_function_comment(function_name)
                    if success:
                        self._send_json_response(
                            {
                                "success": True,
                                "message": f"Successfully deleted comment for function {function_name}",
                            }
                        )
                    else:
                        self._send_json_response(
                            {
                                "error": "Failed to delete function comment",
                                "message": "The comment could not be deleted for the specified function.",
                            },
                            500,
                        )
                else:  # POST
                    function_name = params.get("name") or params.get("functionName")
                    comment = params.get("comment")
                    if not function_name or comment is None:
                        self._send_json_response(
                            {
                                "error": "Missing parameters",
                                "help": "Required parameters: name (or functionName) and comment",
                                "received": params,
                            },
                            400,
                        )
                        return

                    success = self.binary_ops.set_function_comment(function_name, comment)
                    if success:
                        self._send_json_response(
                            {
                                "success": True,
                                "message": f"Successfully set comment for function {function_name}",
                                "comment": comment,
                            }
                        )
                    else:
                        self._send_json_response(
                            {
                                "error": "Failed to set function comment",
                                "message": "The comment could not be set for the specified function.",
                            },
                            500,
                        )

            elif path == "/getComment":
                address = params.get("address")
                if not address:
                    self._send_json_response(
                        {
                            "error": "Missing address parameter",
                            "help": "Required parameter: address",
                            "received": params,
                        },
                        400,
                    )
                    return

                try:
                    address_int = int(address, 16) if isinstance(address, str) else int(address)
                    comment = self.binary_ops.get_comment(address_int)
                    if comment is not None:
                        self._send_json_response(
                            {
                                "success": True,
                                "address": hex(address_int),
                                "comment": comment,
                            }
                        )
                    else:
                        self._send_json_response(
                            {
                                "success": True,
                                "address": hex(address_int),
                                "comment": None,
                                "message": "No comment found at this address",
                            }
                        )
                except ValueError:
                    self._send_json_response({"error": "Invalid address format"}, 400)

            elif path == "/getFunctionComment":
                function_name = params.get("name") or params.get("functionName")
                if not function_name:
                    self._send_json_response(
                        {
                            "error": "Missing function name parameter",
                            "help": "Required parameter: name (or functionName)",
                            "received": params,
                        },
                        400,
                    )
                    return

                comment = self.binary_ops.get_function_comment(function_name)
                if comment is not None:
                    self._send_json_response(
                        {
                            "success": True,
                            "function": function_name,
                            "comment": comment,
                        }
                    )
                else:
                    self._send_json_response(
                        {
                            "success": True,
                            "function": function_name,
                            "comment": None,
                            "message": "No comment found for this function",
                        }
                    )
            elif path == "/editFunctionSignature":
                function_name = params.get("functionName")
                if not function_name:
                    self._send_json_response({"error": "Missing function name parameter"}, 400)
                    return

                signature = params.get("signature")
                if not signature:
                    self._send_json_response({"error": "Missing signature parameter"}, 400)
                    return

                try:
                    self._send_json_response(
                        self.endpoints.edit_function_signature(function_name, signature)
                    )
                except Exception as e:
                    bn.log_error(f"Error handling editFunctionSignature request: {e}")
                    self._send_json_response(
                        {"error": str(e)},
                        500,
                    )
            elif path == "/retypeVariable":
                function_name = params.get("functionName")
                if not function_name:
                    self._send_json_response({"error": "Missing function name parameter"}, 400)
                    return

                variable_name = params.get("variableName")
                if not variable_name:
                    self._send_json_response({"error": "Missing variable name parameter"}, 400)
                    return

                type_str = params.get("type")
                if not type_str:
                    self._send_json_response({"error": "Missing type parameter"}, 400)
                    return

                try:
                    self._send_json_response(
                        self.endpoints.retype_variable(function_name, variable_name, type_str)
                    )
                except Exception as e:
                    bn.log_error(f"Error handling retypeVariable request: {e}")
                    self._send_json_response(
                        {"error": str(e)},
                        500,
                    )
            elif path == "/renameVariable":
                function_name = params.get("functionName")
                if not function_name:
                    self._send_json_response({"error": "Missing function name parameter"}, 400)
                    return

                variable_name = params.get("variableName")
                if not variable_name:
                    self._send_json_response({"error": "Missing variable name parameter"}, 400)
                    return

                new_name = params.get("newName")
                if not new_name:
                    self._send_json_response({"error": "Missing new name parameter"}, 400)
                    return

                try:
                    self._send_json_response(
                        self.endpoints.rename_variable(function_name, variable_name, new_name)
                    )
                except Exception as e:
                    bn.log_error(f"Error handling renameVariable request: {e}")
                    self._send_json_response(
                        {"error": str(e)},
                        500,
                    )

            elif path == "/defineTypes":
                c_code = params.get("cCode")
                if not c_code:
                    self._send_json_response({"error": "Missing cCode parameter"}, 400)
                    return

                try:
                    self._send_json_response(self.endpoints.define_types(c_code))
                except Exception as e:
                    bn.log_error(f"Error handling defineTypes request: {e}")
                    self._send_json_response(
                        {"error": str(e)},
                        500,
                    )

            elif path == "/logs":
                # Get log capture parameters
                count = parse_int_or_default(params.get("count"), 100)
                level_filter = params.get("level")
                search_text = params.get("search")
                start_id = parse_int_or_default(params.get("start_id"), None)

                log_capture = get_active_log_capture()
                if log_capture:
                    logs = log_capture.get_logs(count, level_filter, search_text, start_id)
                    self._send_json_response({"logs": logs})
                else:
                    self._send_json_response(
                        {"error": "Log capture not available", "logs": []}, 200
                    )

            elif path == "/logs/stats":
                log_capture = get_active_log_capture()
                if log_capture:
                    stats = log_capture.get_log_stats()
                    self._send_json_response(stats)
                else:
                    self._send_json_response({"total_logs": 0, "levels": {}, "loggers": {}})

            elif path == "/logs/errors":
                count = parse_int_or_default(params.get("count"), 10)
                log_capture = get_active_log_capture()
                if log_capture:
                    errors = log_capture.get_latest_errors(count)
                    self._send_json_response({"errors": errors})
                else:
                    self._send_json_response({"errors": []})

            elif path == "/logs/warnings":
                count = parse_int_or_default(params.get("count"), 10)
                log_capture = get_active_log_capture()
                if log_capture:
                    warnings = log_capture.get_latest_warnings(count)
                    self._send_json_response({"warnings": warnings})
                else:
                    self._send_json_response({"warnings": []})

            elif path == "/console":
                # Get console capture parameters
                count = parse_int_or_default(params.get("count"), 100)
                type_filter = params.get("type")
                search_text = params.get("search")
                start_id = parse_int_or_default(params.get("start_id"), None)

                console_capture = get_console_capture()
                output = console_capture.get_output(count, type_filter, search_text, start_id)
                self._send_json_response({"output": output})

            elif path == "/console/stats":
                console_capture = get_console_capture()
                stats = console_capture.get_console_stats()
                self._send_json_response(stats)

            elif path == "/console/errors":
                count = parse_int_or_default(params.get("count"), 10)
                console_capture = get_console_capture()
                errors = console_capture.get_latest_errors(count)
                self._send_json_response({"errors": errors})

            elif path == "/console/complete":
                partial = params.get("partial", "")
                console_capture = get_console_capture()
                completions = console_capture.get_completions(partial)
                self._send_json_response({"completions": completions})

            else:
                self._send_json_response({"error": "Not found"}, 404)

        except Exception as e:
            bn.log_error(f"Error handling GET request: {e}")
            self._send_json_response({"error": str(e)}, 500)

    def _handle_decompile(self, function_name: str):
        """Handle function decompilation requests.

        Args:
            function_name: Name or address of the function to decompile

        Sends JSON response with either:
        - Decompiled function code and metadata
        - Error message with available functions list
        """
        try:
            func_info = self.binary_ops.get_function_info(function_name)
            if not func_info:
                bn.log_error(f"Function not found: {function_name}")
                self._send_json_response(
                    {
                        "error": "Function not found",
                        "requested_name": function_name,
                        "available_functions": self.binary_ops.get_function_names(0, 10),
                    },
                    404,
                )
                return

            bn.log_info(f"Found function for decompilation: {func_info}")
            decompiled = self.binary_ops.decompile_function(function_name)

            if decompiled is None:
                self._send_json_response(
                    {
                        "error": "Decompilation failed",
                        "function": func_info,
                        "reason": "Function could not be decompiled. This might be due to missing debug information or unsupported function type.",
                    },
                    500,
                )
            else:
                self._send_json_response({"decompiled": decompiled, "function": func_info})
        except Exception as e:
            bn.log_error(f"Error during decompilation: {e}")
            self._send_json_response(
                {
                    "error": f"Decompilation error: {str(e)}",
                    "requested_name": function_name,
                },
                500,
            )

    def do_POST(self):
        try:
            # Endpoints that don't require a binary to be loaded
            no_binary_required = ["/logs", "/console", "/ui", "/load"]
            path = urllib.parse.urlparse(self.path).path
            query_params = self._parse_query_params()

            params = dict(query_params)
            params.update(self._parse_post_params())
            if not self._validate_endpoint_version(path, params):
                return
            if not any(path.startswith(prefix) for prefix in no_binary_required):
                _, target_error, _candidates, _metadata = self._resolve_request_view(
                    params,
                    require_explicit_target=True,
                )
                if target_error is not None:
                    self._send_json_response(
                        target_error,
                        self._target_error_status_code(target_error),
                    )
                    return
            else:
                self._maybe_refresh_current_view(params)

            # For most endpoints, check if binary is loaded
            if not any(path.startswith(prefix) for prefix in no_binary_required):
                if not self._check_binary_loaded():
                    return

            path = urllib.parse.urlparse(self.path).path

            bn.log_info(f"POST {path} with params: {params}")

            if path == "/load":
                filepath = params.get("filepath")
                if not filepath:
                    self._send_json_response({"error": "Missing filepath parameter"}, 400)
                    return

                try:
                    self.binary_ops.load_binary(filepath)
                    self._send_json_response(
                        {"success": True, "message": f"Binary loaded: {filepath}"}
                    )
                except Exception as e:
                    self._send_json_response({"error": str(e)}, 500)

            elif path == "/rename/function" or path == "/renameFunction":
                old_name = params.get("oldName") or params.get("old_name")
                new_name = params.get("newName") or params.get("new_name")

                bn.log_info(
                    f"Rename request - old_name: {old_name}, new_name: {new_name}, params: {params}"
                )

                if not old_name or not new_name:
                    self._send_json_response(
                        {
                            "error": "Missing parameters",
                            "help": "Required parameters: oldName (or old_name) and newName (or new_name)",
                            "received": params,
                        },
                        400,
                    )
                    return

                # Handle address format (both 0x... and plain number)
                if isinstance(old_name, str):
                    if old_name.startswith("0x"):
                        try:
                            old_name = int(old_name, 16)
                        except ValueError:
                            pass
                    elif old_name.isdigit():
                        old_name = int(old_name)

                bn.log_info(f"Attempting to rename function: {old_name} -> {new_name}")

                # Get function info for validation
                func_info = self.binary_ops.get_function_info(old_name)
                if func_info:
                    bn.log_info(f"Found function: {func_info}")
                    success = self.binary_ops.rename_function(old_name, new_name)
                    if success:
                        self._send_json_response(
                            {
                                "success": True,
                                "message": f"Successfully renamed function from {old_name} to {new_name}",
                                "function": func_info,
                            }
                        )
                    else:
                        self._send_json_response(
                            {
                                "error": "Failed to rename function",
                                "message": "The function was found but could not be renamed. This might be due to permissions or binary restrictions.",
                                "function": func_info,
                            },
                            500,
                        )
                else:
                    available_funcs = self.binary_ops.get_function_names(0, 10)
                    bn.log_error(f"Function not found: {old_name}")
                    self._send_json_response(
                        {
                            "error": "Function not found",
                            "requested": old_name,
                            "help": "Make sure the function exists. You can use either the function name or its address.",
                            "available_functions": available_funcs,
                        },
                        404,
                    )

            elif path == "/rename/data" or path == "/renameData":
                address = params.get("address")
                new_name = params.get("newName") or params.get("new_name")
                if not address or not new_name:
                    self._send_json_response({"error": "Missing parameters"}, 400)
                    return

                try:
                    address_int = int(address, 16) if isinstance(address, str) else int(address)
                    success = self.binary_ops.rename_data(address_int, new_name)
                    self._send_json_response({"success": success})
                except ValueError:
                    self._send_json_response({"error": "Invalid address format"}, 400)

            elif path == "/comment":
                effective_method = str(params.get("_method") or self.command).strip().upper()
                if effective_method == "GET":
                    address = params.get("address")
                    if not address:
                        self._send_json_response(
                            {
                                "error": "Missing address parameter",
                                "help": "Required parameter: address",
                                "received": params,
                            },
                            400,
                        )
                        return

                    try:
                        address_int = int(address, 16) if isinstance(address, str) else int(address)
                        comment = self.binary_ops.get_comment(address_int)
                        if comment is not None:
                            self._send_json_response(
                                {
                                    "success": True,
                                    "address": hex(address_int),
                                    "comment": comment,
                                }
                            )
                        else:
                            self._send_json_response(
                                {
                                    "success": True,
                                    "address": hex(address_int),
                                    "comment": None,
                                    "message": "No comment found at this address",
                                }
                            )
                    except ValueError:
                        self._send_json_response({"error": "Invalid address format"}, 400)
                elif effective_method == "DELETE":
                    address = params.get("address")
                    if not address:
                        self._send_json_response(
                            {
                                "error": "Missing address parameter",
                                "help": "Required parameter: address",
                                "received": params,
                            },
                            400,
                        )
                        return

                    try:
                        address_int = int(address, 16) if isinstance(address, str) else int(address)
                        success = self.binary_ops.delete_comment(address_int)
                        if success:
                            self._send_json_response(
                                {
                                    "success": True,
                                    "message": f"Successfully deleted comment at {hex(address_int)}",
                                }
                            )
                        else:
                            self._send_json_response(
                                {
                                    "error": "Failed to delete comment",
                                    "message": "The comment could not be deleted at the specified address.",
                                },
                                500,
                            )
                    except ValueError:
                        self._send_json_response({"error": "Invalid address format"}, 400)
                else:  # POST
                    address = params.get("address")
                    comment = params.get("comment")
                    if not address or comment is None:
                        self._send_json_response(
                            {
                                "error": "Missing parameters",
                                "help": "Required parameters: address and comment",
                                "received": params,
                            },
                            400,
                        )
                        return

                    try:
                        address_int = int(address, 16) if isinstance(address, str) else int(address)
                        success = self.binary_ops.set_comment(address_int, comment)
                        if success:
                            self._send_json_response(
                                {
                                    "success": True,
                                    "message": f"Successfully set comment at {hex(address_int)}",
                                    "comment": comment,
                                }
                            )
                        else:
                            self._send_json_response(
                                {
                                    "error": "Failed to set comment",
                                    "message": "The comment could not be set at the specified address.",
                                },
                                500,
                            )
                    except ValueError:
                        self._send_json_response({"error": "Invalid address format"}, 400)

            elif path == "/comment/function":
                effective_method = str(params.get("_method") or self.command).strip().upper()
                if effective_method == "GET":
                    function_name = params.get("name") or params.get("functionName")
                    if not function_name:
                        self._send_json_response(
                            {
                                "error": "Missing function name parameter",
                                "help": "Required parameter: name (or functionName)",
                                "received": params,
                            },
                            400,
                        )
                        return

                    comment = self.binary_ops.get_function_comment(function_name)
                    if comment is not None:
                        self._send_json_response(
                            {
                                "success": True,
                                "function": function_name,
                                "comment": comment,
                            }
                        )
                    else:
                        self._send_json_response(
                            {
                                "success": True,
                                "function": function_name,
                                "comment": None,
                                "message": "No comment found for this function",
                            }
                        )
                elif effective_method == "DELETE":
                    function_name = params.get("name") or params.get("functionName")
                    if not function_name:
                        self._send_json_response(
                            {
                                "error": "Missing function name parameter",
                                "help": "Required parameter: name (or functionName)",
                                "received": params,
                            },
                            400,
                        )
                        return

                    success = self.binary_ops.delete_function_comment(function_name)
                    if success:
                        self._send_json_response(
                            {
                                "success": True,
                                "message": f"Successfully deleted comment for function {function_name}",
                            }
                        )
                    else:
                        self._send_json_response(
                            {
                                "error": "Failed to delete function comment",
                                "message": "The comment could not be deleted for the specified function.",
                            },
                            500,
                        )
                else:  # POST
                    function_name = params.get("name") or params.get("functionName")
                    comment = params.get("comment")
                    if not function_name or comment is None:
                        self._send_json_response(
                            {
                                "error": "Missing parameters",
                                "help": "Required parameters: name (or functionName) and comment",
                                "received": params,
                            },
                            400,
                        )
                        return

                    success = self.binary_ops.set_function_comment(function_name, comment)
                    if success:
                        self._send_json_response(
                            {
                                "success": True,
                                "message": f"Successfully set comment for function {function_name}",
                                "comment": comment,
                            }
                        )
                    else:
                        self._send_json_response(
                            {
                                "error": "Failed to set function comment",
                                "message": "The comment could not be set for the specified function.",
                            },
                            500,
                        )

            elif path == "/getComment":
                address = params.get("address")
                if not address:
                    self._send_json_response(
                        {
                            "error": "Missing address parameter",
                            "help": "Required parameter: address",
                            "received": params,
                        },
                        400,
                    )
                    return

                try:
                    address_int = int(address, 16) if isinstance(address, str) else int(address)
                    comment = self.binary_ops.get_comment(address_int)
                    if comment is not None:
                        self._send_json_response(
                            {
                                "success": True,
                                "address": hex(address_int),
                                "comment": comment,
                            }
                        )
                    else:
                        self._send_json_response(
                            {
                                "success": True,
                                "address": hex(address_int),
                                "comment": None,
                                "message": "No comment found at this address",
                            }
                        )
                except ValueError:
                    self._send_json_response({"error": "Invalid address format"}, 400)

            elif path == "/getFunctionComment":
                function_name = params.get("functionName") or params.get("name")
                if not function_name:
                    self._send_json_response(
                        {
                            "error": "Missing function name parameter",
                            "help": "Required parameter: name (or functionName)",
                            "received": params,
                        },
                        400,
                    )
                    return

                comment = self.binary_ops.get_function_comment(function_name)
                if comment is not None:
                    self._send_json_response(
                        {
                            "success": True,
                            "function": function_name,
                            "comment": comment,
                        }
                    )
                else:
                    self._send_json_response(
                        {
                            "success": True,
                            "function": function_name,
                            "comment": None,
                            "message": "No comment found for this function",
                        }
                    )

            elif path == "/logs/clear":
                log_capture = get_active_log_capture()
                if log_capture:
                    log_capture.clear_logs()
                    self._send_json_response({"success": True, "message": "Logs cleared"})
                else:
                    self._send_json_response(
                        {"success": False, "message": "Log capture not available"}
                    )

            elif path == "/console/clear":
                console_capture = get_console_capture()
                console_capture.clear_output()
                self._send_json_response({"success": True, "message": "Console output cleared"})

            elif path == "/console/execute":
                command = params.get("command")
                if not command:
                    self._send_json_response({"error": "Missing command parameter"}, 400)
                    return

                timeout_raw = params.get("timeout")
                if timeout_raw is None:
                    timeout_raw = params.get("exec_timeout")
                try:
                    timeout = float(timeout_raw) if timeout_raw is not None else 30.0
                except (TypeError, ValueError):
                    timeout = 30.0
                # Keep bounds sane while still allowing long imports.
                if timeout < 1:
                    timeout = 1.0
                if timeout > 3600:
                    timeout = 3600.0

                console_capture = get_console_capture()
                console_adapter = ConsoleCaptureAdapter(console_capture)

                # Pass server context for binary view access if using V2
                if hasattr(console_capture, "set_server_context"):
                    console_capture.set_server_context(self)

                binary_view, target_error = self._resolve_python_view(params)
                if target_error is not None:
                    self._send_json_response(
                        target_error,
                        self._target_error_status_code(target_error),
                    )
                    return

                try:
                    result = console_adapter.execute_command(
                        command,
                        binary_view=binary_view,
                        timeout=timeout,
                    )
                except RuntimeError as exc:
                    self._send_json_response({"error": str(exc)}, 500)
                    return

                if isinstance(result, dict):
                    view_ctx = self._view_context_fields(binary_view)
                    result.setdefault("selected_view_filename", view_ctx["selected_view_filename"])
                    result.setdefault("selected_view_id", view_ctx["selected_view_id"])

                self._send_json_response(result)

            elif path == "/ui/statusbar":
                from ..automation.statusbar import read_statusbar

                raw_result = read_statusbar(
                    all_windows=self._parse_bool(params.get("all_windows"), False),
                    include_hidden=self._parse_bool(params.get("include_hidden"), False),
                )
                result = self._normalize_ui_contract(path, raw_result)
                self._send_json_response(result)

            elif path == "/ui/open":
                from ..automation.open_file import open_file_workflow

                raw_result = open_file_workflow(
                    filepath=str(params.get("filepath") or ""),
                    platform=str(params.get("platform") or ""),
                    view_type=str(params.get("view_type") or ""),
                    click_open=self._parse_bool(params.get("click_open"), True),
                    inspect_only=self._parse_bool(params.get("inspect_only"), False),
                    timeout=params.get("timeout") or params.get("timeout_s"),
                )
                result = self._normalize_ui_contract(path, raw_result)
                self._send_json_response(result)

            elif path == "/ui/quit":
                from ..automation.quit_app import quit_workflow

                raw_result = quit_workflow(
                    decision=str(params.get("decision") or "auto"),
                    mark_dirty=self._parse_bool(params.get("mark_dirty"), False),
                    inspect_only=self._parse_bool(params.get("inspect_only"), False),
                    wait_ms=parse_int_or_default(params.get("wait_ms"), 2000),
                    quit_app=self._parse_bool(params.get("quit_app"), False),
                    quit_delay_ms=parse_int_or_default(params.get("quit_delay_ms"), 300),
                )
                self._maybe_refresh_current_view(clear_if_missing=True)
                if self.binary_ops and self.binary_ops.current_view is None:
                    actions = (
                        self._as_list(raw_result.get("actions"))
                        if isinstance(raw_result, dict)
                        else []
                    )
                    if "cleared_current_view" not in actions:
                        actions.append("cleared_current_view")
                    if isinstance(raw_result, dict):
                        raw_result["actions"] = actions
                result = self._normalize_ui_contract(path, raw_result)
                self._send_json_response(result)

            else:
                self._send_json_response({"error": "Not found"}, 404)
        except Exception as e:
            bn.log_error(f"Error handling POST request: {e}")
            self._send_json_response({"error": str(e)}, 500)


class MCPServer:
    """HTTP server for Binary Ninja MCP plugin.

    Provides REST API endpoints for:
    - Binary analysis and manipulation
    - Function decompilation
    - Symbol renaming
    - Data inspection
    """

    def __init__(self, config: Config):
        self.config = config
        self.server = None
        self.thread = None
        self.binary_ops = BinaryOperations(config.binary_ninja)
        self._lock = threading.Lock()
        self.instance_id = f"bnmcp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.started_at = time.time()

    def instance_metadata(self) -> Dict[str, Any]:
        host = str(self.config.server.host)
        port = int(self.config.server.port)
        return {
            "service": "binary_ninja_mcp",
            "instance_id": self.instance_id,
            "host": host,
            "port": port,
            "base_url": f"http://{host}:{port}",
            "pid": os.getpid(),
            "started_at": self.started_at,
        }

    def is_running(self) -> bool:
        return self.server is not None and self.thread is not None and self.thread.is_alive()

    def start(self):
        """Start the HTTP server in a background thread."""
        with self._lock:
            if self.is_running():
                bn.log_info(
                    f"[MCP] Server already running on http://{self.config.server.host}:{self.config.server.port}"
                )
                return False

            # Clean up any stale state from a previous run.
            if self.server is not None:
                try:
                    self.server.server_close()
                except Exception:
                    pass
                self.server = None
                self.thread = None

            preferred_port = int(self.config.server.port)
            candidate_ports = [preferred_port]
            for port in getattr(self.config.server, "fallback_ports", ()):
                port_int = int(port)
                if port_int not in candidate_ports:
                    candidate_ports.append(port_int)

            # Create handler with access to binary operations
            handler_class = type(
                "MCPRequestHandlerWithOps",
                (MCPRequestHandler,),
                {"binary_ops": self.binary_ops, "mcp_server": self},
            )

            last_error = None
            for port in candidate_ports:
                server_address = (self.config.server.host, port)
                try:
                    self.server = HTTPServer(server_address, handler_class)
                    bound_host, bound_port = self.server.server_address[:2]
                    self.config.server.host = str(bound_host)
                    self.config.server.port = int(bound_port)
                    break
                except OSError as e:
                    last_error = e
                    if e.errno == errno.EADDRINUSE:
                        bn.log_warn(
                            f"[MCP] Port in use, trying next candidate: http://{self.config.server.host}:{port}"
                        )
                        continue
                    bn.log_error(
                        f"[MCP] Failed to start server on http://{self.config.server.host}:{port}: {e}"
                    )
                    self.server = None
                    self.thread = None
                    return False
                except Exception as e:
                    last_error = e
                    bn.log_error(
                        f"[MCP] Failed to start server on http://{self.config.server.host}:{port}: {e}"
                    )
                    self.server = None
                    self.thread = None
                    return False

            if self.server is None:
                bn.log_error(
                    "[MCP] Failed to start server: all configured loopback ports are in use "
                    f"({candidate_ports}); last error: {last_error}"
                )
                self.thread = None
                return False

            # Start log and console capture (best-effort).
            log_capture = get_active_log_capture()
            if log_capture:
                try:
                    log_capture.start()
                except Exception as e:
                    bn.log_error(f"[MCP] Failed to start log capture: {e}")

            console_capture = get_console_capture()
            if console_capture:
                try:
                    console_capture.start()
                except Exception as e:
                    bn.log_error(f"[MCP] Failed to start console capture: {e}")

            self.thread = threading.Thread(target=self.server.serve_forever)
            self.thread.daemon = True
            self.thread.start()
            bn.log_info(
                f"[MCP] Server started on http://{self.config.server.host}:{self.config.server.port}"
            )
            return True

    def stop(self):
        """Stop the HTTP server and clean up resources."""
        with self._lock:
            if self.server is None:
                bn.log_info("[MCP] Server already stopped")
                return False

            server = self.server
            thread = self.thread
            self.server = None
            self.thread = None

        try:
            server.shutdown()
        except Exception as e:
            bn.log_error(f"[MCP] Failed to shutdown server cleanly: {e}")
        try:
            server.server_close()
        except Exception as e:
            bn.log_error(f"[MCP] Failed to close server socket: {e}")

        if thread and thread.is_alive():
            thread.join(timeout=5)

        # Stop log and console capture (best-effort).
        log_capture = get_active_log_capture()
        if log_capture:
            try:
                log_capture.stop()
            except Exception as e:
                bn.log_error(f"[MCP] Failed to stop log capture: {e}")

        console_capture = get_console_capture()
        if console_capture:
            try:
                console_capture.stop()
            except Exception as e:
                bn.log_error(f"[MCP] Failed to stop console capture: {e}")

        bn.log_info("[MCP] Server stopped")
        return True
