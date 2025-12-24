"""
Enhanced Python Executor V2 - With automatic binary view injection
"""

import io
import traceback
import ast
import time
import threading
import queue
from contextlib import redirect_stdout, redirect_stderr
from collections import deque
from datetime import datetime
from typing import Any, Dict, List
import weakref

try:
    import binaryninja as bn
except ImportError:
    bn = None


class BinaryViewRegistry:
    """Global registry for binary views"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance.views = weakref.WeakValueDictionary()
                    cls._instance.current_view_ref = None
        return cls._instance

    def register_view(self, view_id: str, binary_view):
        """Register a binary view"""
        self.views[view_id] = binary_view
        self.current_view_ref = view_id

    def get_current_view(self):
        """Get the current binary view"""
        if self.current_view_ref and self.current_view_ref in self.views:
            return self.views[self.current_view_ref]

        # Try to get from Binary Ninja UI
        if bn:
            try:
                # Check if we're in UI context
                import binaryninjaui

                if hasattr(binaryninjaui, "UIContext") and hasattr(
                    binaryninjaui.UIContext, "currentBinaryView"
                ):
                    view = binaryninjaui.UIContext.currentBinaryView()
                    if view:
                        return view
            except (ImportError, AttributeError):
                pass

            # Check for any loaded views
            for view in self.views.values():
                if view:
                    return view

        return None

    def clear(self):
        """Clear all registered views"""
        self.views.clear()
        self.current_view_ref = None


# Global registry instance
_registry = BinaryViewRegistry()


class SmartPythonExecutor:
    """Python executor with automatic context injection and helpful features"""

    def __init__(self, binary_view=None):
        self.binary_view = binary_view
        self.execution_history = deque(maxlen=1000)
        self.locals_dict = {}
        self._lock = threading.Lock()
        self._helper_functions = self._create_helpers()
        self.globals_dict = self._create_globals()

    def _create_helpers(self) -> Dict[str, Any]:
        """Create helper functions for common tasks"""
        helpers = {}

        def get_current_view():
            """Get the current binary view"""
            if self.binary_view:
                return self.binary_view
            return _registry.get_current_view()

        def get_func(name_or_addr):
            """Get function by name or address"""
            bv = get_current_view()
            if not bv:
                return None

            if isinstance(name_or_addr, str):
                return next((f for f in bv.functions if f.name == name_or_addr), None)
            else:
                return bv.get_function_at(name_or_addr)

        def find_functions(pattern: str) -> List:
            """Find functions matching pattern"""
            bv = get_current_view()
            if not bv:
                return []

            pattern_lower = pattern.lower()
            return [f for f in bv.functions if pattern_lower in f.name.lower()]

        def get_strings(min_length: int = 4) -> List:
            """Get strings from binary"""
            bv = get_current_view()
            if not bv:
                return []

            return [s for s in bv.strings if len(s.value) >= min_length]

        def hex_dump(addr: int, size: int = 16) -> str:
            """Get hex dump at address"""
            bv = get_current_view()
            if not bv:
                return "No binary view"

            data = bv.read(addr, size)
            if not data:
                return f"Cannot read {size} bytes at {hex(addr)}"

            lines = []
            for i in range(0, len(data), 16):
                chunk = data[i : i + 16]
                hex_part = " ".join(f"{b:02x}" for b in chunk)
                ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                lines.append(f"{addr + i:08x}: {hex_part:<48} {ascii_part}")

            return "\n".join(lines)

        def quick_info():
            """Get quick info about current binary"""
            bv = get_current_view()
            if not bv:
                return "No binary loaded"

            info = {
                "file": bv.file.filename if bv.file else "Unknown",
                "type": getattr(bv, "view_type", "Unknown"),
                "arch": str(bv.arch) if bv.arch else "Unknown",
                "functions": len(list(bv.functions)),
                "entry": hex(bv.entry_point) if bv.entry_point else None,
                "size": bv.length if hasattr(bv, "length") else "Unknown",
            }

            return "\n".join(f"{k}: {v}" for k, v in info.items())

        helpers.update(
            {
                "get_current_view": get_current_view,
                "get_func": get_func,
                "find_functions": find_functions,
                "find_funcs": find_functions,  # Alias
                "get_strings": get_strings,
                "hex_dump": hex_dump,
                "hexdump": hex_dump,  # Alias
                "quick_info": quick_info,
                "info": quick_info,  # Alias
            }
        )

        return helpers

    def _create_globals(self) -> Dict[str, Any]:
        """Create the global namespace for code execution"""
        globals_dict = {
            "__builtins__": __builtins__,
            "__name__": "__mcp_console__",
            "__doc__": "Binary Ninja MCP Console - Type help() for more info",
        }

        # Add Binary Ninja imports
        if bn:
            globals_dict.update(
                {
                    "binaryninja": bn,
                    "bn": bn,
                    "BinaryView": bn.BinaryView,
                    "Function": bn.Function,
                    "BasicBlock": bn.BasicBlock,
                    "Symbol": bn.Symbol,
                    "Type": bn.Type,
                    "log_debug": bn.log_debug,
                    "log_info": bn.log_info,
                    "log_warn": bn.log_warn,
                    "log_error": bn.log_error,
                }
            )

        # Add helper functions
        globals_dict.update(self._helper_functions)

        # Custom help function
        original_help = globals_dict.get("help", help)

        def enhanced_help(obj=None):
            if obj is None:
                return self._get_console_help()
            return original_help(obj)

        globals_dict["help"] = enhanced_help

        return globals_dict

    def _get_console_help(self) -> str:
        """Get context-aware help"""
        bv = self.binary_view or _registry.get_current_view()

        help_text = """
Binary Ninja MCP Python Console
==============================

Quick Start:
  bv                    - Get current binary view
  info()                - Show binary info
  get_func('name')      - Get function by name
  find_funcs('pattern') - Find functions matching pattern
  get_strings(10)       - Get strings longer than 10 chars
  hex_dump(0x401000)    - Show hex dump at address

Current Context:"""

        if bv:
            help_text += f"""
  Binary: {bv.file.filename if bv.file else "Unknown"}
  Type: {getattr(bv, "view_type", "Unknown")}
  Functions: {len(list(bv.functions))}
  Entry: {hex(bv.entry_point) if bv.entry_point else "None"}"""
        else:
            help_text += "\n  No binary loaded"

        if self.locals_dict:
            help_text += f"\n\nDefined Variables:\n  {', '.join(self.locals_dict.keys())}"

        help_text += """

Examples:
  # List all functions
  for f in bv.functions:
      print(f"{f.name} at {hex(f.start)}")
  
  # Find crypto functions
  crypto = find_funcs('crypt')
  
  # Analyze specific function
  main = get_func('main')
  if main:
      print(f"Basic blocks: {len(list(main.basic_blocks))}")
"""

        return help_text

    def execute(self, code: str, timeout: float = 30.0) -> Dict[str, Any]:
        """Execute Python code with automatic context injection and timeout"""
        start_time = time.time()

        # Auto-inject binary view
        bv = self.binary_view or _registry.get_current_view()

        # Update globals with current context
        self.globals_dict["bv"] = bv
        if bv:
            self.globals_dict.update(
                {
                    "current_view": bv,
                    "functions": list(bv.functions),
                    "entry_point": bv.entry_point,
                    "entry_function": bv.entry_function,
                }
            )

        # Rest of execution logic remains the same as original...
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        result = {
            "success": False,
            "stdout": "",
            "stderr": "",
            "return_value": None,
            "return_type": None,
            "variables": {},
            "error": None,
            "execution_time": 0,
            "context": {
                "binary_loaded": bv is not None,
                "binary_name": bv.file.filename if bv and bv.file else None,
            },
        }

        # Use a queue to communicate between threads
        result_queue = queue.Queue()
        exception_queue = queue.Queue()

        def execute_code():
            """Execute the code in a separate thread"""
            with self._lock:
                try:
                    # Parse and execute code
                    tree = ast.parse(code, mode="exec")

                    if tree.body:
                        last_stmt = tree.body[-1]
                        if isinstance(last_stmt, ast.Expr):
                            expr_code = compile(
                                ast.Expression(last_stmt.value), "<console>", "eval"
                            )
                            tree.body = tree.body[:-1]
                            stmt_code = compile(tree, "<console>", "exec") if tree.body else None
                        else:
                            stmt_code = compile(tree, "<console>", "exec")
                            expr_code = None
                    else:
                        stmt_code = None
                        expr_code = None

                    with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                        if stmt_code:
                            exec(stmt_code, self.globals_dict, self.locals_dict)

                        if expr_code:
                            value = eval(expr_code, self.globals_dict, self.locals_dict)
                            if value is not None:
                                result["return_value"] = self._serialize_value(value)
                                result["return_type"] = type(value).__name__
                                print(repr(value))

                        elif "_result" in self.locals_dict:
                            value = self.locals_dict["_result"]
                            result["return_value"] = self._serialize_value(value)
                            result["return_type"] = type(value).__name__

                    result["success"] = True

                except Exception as e:
                    exception_queue.put(e)

                finally:
                    result["stdout"] = stdout_capture.getvalue()
                    result["stderr"] += stderr_capture.getvalue()
                    result["variables"] = self._capture_variables()
                    result_queue.put(result)

        # Execute in a separate thread
        exec_thread = threading.Thread(target=execute_code, daemon=True)
        exec_thread.start()

        # Wait for completion or timeout
        exec_thread.join(timeout=timeout)

        if exec_thread.is_alive():
            # Timeout occurred
            result["error"] = {
                "type": "TimeoutError",
                "message": f"Execution timed out after {timeout} seconds",
                "traceback": "",
            }
            result["stderr"] = f"TimeoutError: Execution timed out after {timeout} seconds\n"
            # Note: We can't forcefully stop the thread in Python, it will continue running
        else:
            # Get the result from the queue
            try:
                result = result_queue.get_nowait()
                # Check if there was an exception
                if not exception_queue.empty():
                    e = exception_queue.get_nowait()
                    result["error"] = {
                        "type": type(e).__name__,
                        "message": str(e),
                        "traceback": traceback.format_exc(),
                    }

                    # Add helpful suggestions for common errors
                    if isinstance(e, NameError):
                        result["error"]["suggestions"] = self._get_name_suggestions(str(e))
                    elif isinstance(e, AttributeError) and "'NoneType'" in str(e):
                        result["error"]["hint"] = (
                            "Binary view is None. Make sure a binary is loaded."
                        )

                    result["stderr"] += traceback.format_exc()
            except queue.Empty:
                # This shouldn't happen, but handle it gracefully
                result["error"] = {
                    "type": "InternalError",
                    "message": "Failed to retrieve execution result",
                    "traceback": "",
                }

        result["execution_time"] = time.time() - start_time

        self.execution_history.append(
            {"timestamp": datetime.now().isoformat(), "code": code, "result": result}
        )

        return result

    def _serialize_value(self, value: Any) -> Any:
        """Convert Python objects to JSON-serializable format"""
        # (Same implementation as before)
        if value is None:
            return None
        elif isinstance(value, (bool, int, float, str)):
            return value
        elif isinstance(value, bytes):
            return {
                "type": "bytes",
                "hex": value.hex(),
                "ascii": value.decode("ascii", errors="replace"),
            }
        elif isinstance(value, (list, tuple)):
            return {
                "type": type(value).__name__,
                "items": [self._serialize_value(item) for item in value[:100]],
            }
        elif isinstance(value, dict):
            return {
                "type": "dict",
                "items": {str(k): self._serialize_value(v) for k, v in list(value.items())[:100]},
            }

        if bn:
            if isinstance(value, bn.Function):
                return {
                    "type": "BinaryNinja.Function",
                    "name": value.name,
                    "address": hex(value.start),
                    "size": value.total_bytes,
                    "basic_blocks": len(list(value.basic_blocks)),
                }
            elif isinstance(value, bn.BinaryView):
                return {
                    "type": "BinaryNinja.BinaryView",
                    "filename": value.file.filename if value.file else None,
                    "type_name": getattr(value, "view_type", "Unknown"),
                    "arch": str(value.arch) if value.arch else None,
                    "functions": len(list(value.functions)),
                }

        try:
            return {"type": type(value).__name__, "repr": str(value)[:500]}
        except Exception:
            return {"type": type(value).__name__, "repr": "<serialization error>"}

    def _capture_variables(self) -> Dict[str, Any]:
        """Capture interesting variables from execution context"""
        captured = {}

        skip_names = {
            "__builtins__",
            "__name__",
            "__doc__",
            "bn",
            "binaryninja",
            "bv",
            "functions",
            "current_view",
            "entry_point",
            "entry_function",
        }

        for name, value in self.locals_dict.items():
            if not name.startswith("_") and name not in skip_names:
                try:
                    captured[name] = self._serialize_value(value)
                except Exception:
                    captured[name] = {"type": type(value).__name__, "error": "serialization failed"}

        return captured

    def _get_name_suggestions(self, error: str) -> List[str]:
        """Get suggestions for undefined names"""
        import re
        from difflib import get_close_matches

        match = re.search(r"name '(\w+)' is not defined", error)
        if match:
            undefined = match.group(1)
            all_names = list(self.globals_dict.keys()) + list(self.locals_dict.keys())
            suggestions = get_close_matches(undefined, all_names, n=3, cutoff=0.6)

            # Add common mistakes
            if undefined == "function":
                suggestions.append('get_func("name")')
            elif undefined == "functions":
                suggestions.append("bv.functions")

            return suggestions

        return []

    def get_completions(self, partial: str) -> List[str]:
        """Get auto-completions for partial input"""
        completions = []

        # Split on dots to handle attribute access
        parts = partial.split(".")

        if len(parts) == 1:
            # Complete from globals and locals
            prefix = parts[0]

            # From locals
            completions.extend(
                [
                    name
                    for name in self.locals_dict.keys()
                    if name.startswith(prefix) and not name.startswith("_")
                ]
            )

            # From globals
            completions.extend(
                [
                    name
                    for name in self.globals_dict.keys()
                    if name.startswith(prefix) and not name.startswith("_")
                ]
            )

            # Built-in functions and keywords
            import keyword

            completions.extend(
                [
                    name
                    for name in dir(__builtins__)
                    if name.startswith(prefix) and not name.startswith("_")
                ]
            )
            completions.extend([kw for kw in keyword.kwlist if kw.startswith(prefix)])

        else:
            # Try to resolve the object and get its attributes
            try:
                obj_path = ".".join(parts[:-1])
                obj = eval(obj_path, self.globals_dict, self.locals_dict)
                prefix = parts[-1]

                # Get attributes
                attrs = [
                    attr
                    for attr in dir(obj)
                    if attr.startswith(prefix) and not attr.startswith("_")
                ]
                completions.extend([f"{obj_path}.{attr}" for attr in attrs])

            except Exception:
                pass

        # Remove duplicates and sort
        return sorted(list(set(completions)))

    def clear_context(self):
        """Clear the execution context"""
        with self._lock:
            self.locals_dict.clear()
            self.execution_history.clear()
            # Recreate globals to reset any modifications
            self.globals_dict = self._create_globals()


# Enhanced console capture that uses SmartPythonExecutor
class SmartConsoleCapture:
    """Console capture using the smart Python executor"""

    def __init__(self):
        self.executor = SmartPythonExecutor()
        self.output_buffer = deque(maxlen=10000)
        self.initialized = True
        self._server_context = None

        if bn:
            bn.log_info("Smart Python console initialized")

    def set_server_context(self, server):
        """Set server context for binary view access"""
        self._server_context = server

        # Register current binary view if available
        if hasattr(server, "binary_ops") and server.binary_ops:
            if hasattr(server.binary_ops, "current_view") and server.binary_ops.current_view:
                _registry.register_view("server_view", server.binary_ops.current_view)

    def execute_command(self, command: str, binary_view=None) -> Dict[str, Any]:
        """Execute a Python command with automatic context"""
        # Update binary view if provided
        if binary_view:
            self.executor.binary_view = binary_view
            _registry.register_view("command_view", binary_view)
        elif self._server_context:
            # Try to get from server context
            if hasattr(self._server_context, "binary_ops") and self._server_context.binary_ops:
                if hasattr(self._server_context.binary_ops, "current_view"):
                    binary_view = self._server_context.binary_ops.current_view
                    if binary_view:
                        self.executor.binary_view = binary_view
                        _registry.register_view("server_view", binary_view)

        # Execute the command
        result = self.executor.execute(command)

        # Store output in buffer
        timestamp = datetime.now().isoformat()

        if result["stdout"]:
            for line in result["stdout"].splitlines():
                self.output_buffer.append(
                    {
                        "id": len(self.output_buffer),
                        "timestamp": timestamp,
                        "type": "output",
                        "text": line,
                    }
                )

        if result["stderr"]:
            for line in result["stderr"].splitlines():
                self.output_buffer.append(
                    {
                        "id": len(self.output_buffer),
                        "timestamp": timestamp,
                        "type": "error",
                        "text": line,
                    }
                )

        return result

    def get_help(self) -> str:
        """Get interactive help"""
        return self.executor._get_console_help()

    # Rest of methods same as before...
    def start(self):
        pass

    def stop(self):
        pass

    def get_output(self, count=100, type_filter=None, search_text=None, start_id=None):
        entries = list(self.output_buffer)

        if start_id is not None:
            entries = [e for e in entries if e["id"] > start_id]

        if type_filter:
            entries = [e for e in entries if e["type"] == type_filter]

        if search_text:
            search_lower = search_text.lower()
            entries = [e for e in entries if search_lower in e.get("text", "").lower()]

        return entries[-count:] if count else entries

    def get_completions(self, partial: str) -> List[str]:
        return self.executor.get_completions(partial)

    def clear(self):
        self.output_buffer.clear()
        self.executor.clear_context()

    def clear_output(self):
        """Clear console output (alias for clear)"""
        self.clear()

    def get_stats(self) -> Dict[str, Any]:
        type_counts = {}
        for entry in self.output_buffer:
            entry_type = entry.get("type", "unknown")
            type_counts[entry_type] = type_counts.get(entry_type, 0) + 1

        entries = list(self.output_buffer)
        return {
            "total_entries": len(entries),
            "types": type_counts,
            "oldest_timestamp": entries[0]["timestamp"] if entries else None,
            "newest_timestamp": entries[-1]["timestamp"] if entries else None,
        }

    def get_console_stats(self):
        """Get console statistics (alias for get_stats)"""
        return self.get_stats()

    def get_latest_errors(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get latest error entries"""
        error_entries = [e for e in self.output_buffer if e.get("type") == "error"]
        return error_entries[-count:]


# Create singleton instance
_console_instance = None


def get_console_capture():
    """Get the console capture instance"""
    global _console_instance
    if _console_instance is None:
        _console_instance = SmartConsoleCapture()
    return _console_instance
