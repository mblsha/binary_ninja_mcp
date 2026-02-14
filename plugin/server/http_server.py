from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import urllib.parse
import errno
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
    normalize_endpoint_path,
    normalize_ui_contract,
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

        - If `filename` (or `file`) is provided, attempt to select a previously-seen view.
        - Otherwise (or as fallback), use `binaryninjaui.UIContext.currentBinaryView()` when available.
        """
        if not self.binary_ops:
            return

        requested_filename = None
        if params:
            requested_filename = params.get("filename") or params.get("file")

        if requested_filename:
            try:
                selected = self.binary_ops.select_view_by_filename(str(requested_filename))
                if selected:
                    return
            except Exception:
                pass

        try:
            import binaryninjaui  # type: ignore

            if not hasattr(binaryninjaui, "UIContext"):
                return

            tab_views = []
            try:
                contexts = list(binaryninjaui.UIContext.allContexts())
            except Exception:
                contexts = []

            for ctx in contexts:
                try:
                    tabs = list(ctx.getTabs())
                except Exception:
                    tabs = []
                for tab in tabs:
                    try:
                        view_frame = ctx.getViewFrameForTab(tab)
                        if view_frame is None:
                            continue
                        view = view_frame.getCurrentBinaryView()
                        if view is not None:
                            tab_views.append(view)
                    except Exception:
                        continue

            if tab_views:
                current_view = None
                if hasattr(binaryninjaui.UIContext, "currentBinaryView"):
                    try:
                        candidate = binaryninjaui.UIContext.currentBinaryView()
                        if candidate in tab_views:
                            current_view = candidate
                    except Exception:
                        current_view = None
                if current_view is None:
                    current_view = tab_views[0]
                self.binary_ops.current_view = current_view
            elif clear_if_missing:
                self.binary_ops.current_view = None
        except Exception:
            # UI not available (headless) or API mismatch; ignore.
            pass

    def do_GET(self):
        try:
            # Endpoints that don't require a binary to be loaded
            no_binary_required = ["/status", "/logs", "/console"]
            params = self._parse_query_params()
            path = urllib.parse.urlparse(self.path).path
            if not self._validate_endpoint_version(path, params):
                return
            self._maybe_refresh_current_view(params, clear_if_missing=(path == "/status"))

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
                self._send_json_response(status)

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
            no_binary_required = ["/logs", "/console", "/ui"]
            path = urllib.parse.urlparse(self.path).path

            params = self._parse_post_params()
            if not self._validate_endpoint_version(path, params):
                return
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

                # Pass binary view directly if available
                binary_view = self.binary_ops.current_view if self.binary_ops else None
                try:
                    result = console_adapter.execute_command(
                        command,
                        binary_view=binary_view,
                        timeout=timeout,
                    )
                except RuntimeError as exc:
                    self._send_json_response({"error": str(exc)}, 500)
                    return

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

            server_address = (self.config.server.host, self.config.server.port)

            # Create handler with access to binary operations
            handler_class = type(
                "MCPRequestHandlerWithOps",
                (MCPRequestHandler,),
                {"binary_ops": self.binary_ops},
            )

            try:
                self.server = HTTPServer(server_address, handler_class)
            except OSError as e:
                if e.errno == errno.EADDRINUSE:
                    bn.log_error(
                        f"[MCP] Failed to start server: http://{self.config.server.host}:{self.config.server.port} is already in use"
                    )
                else:
                    bn.log_error(
                        f"[MCP] Failed to start server on http://{self.config.server.host}:{self.config.server.port}: {e}"
                    )
                self.server = None
                self.thread = None
                return False
            except Exception as e:
                bn.log_error(
                    f"[MCP] Failed to start server on http://{self.config.server.host}:{self.config.server.port}: {e}"
                )
                self.server = None
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
