"""
Enhanced Python Executor for Binary Ninja MCP
Provides reliable Python code execution with comprehensive result capture
"""

import sys
import io
import json
import traceback
import ast
import time
import threading
from contextlib import redirect_stdout, redirect_stderr
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

try:
    import binaryninja as bn
except ImportError:
    bn = None


class PythonExecutor:
    """Execute Python code in Binary Ninja context with result capture"""
    
    def __init__(self, binary_view=None):
        self.binary_view = binary_view
        self.execution_history = deque(maxlen=1000)
        self.globals_dict = self._create_globals()
        self.locals_dict = {}
        self._lock = threading.Lock()
        
    def _create_globals(self) -> Dict[str, Any]:
        """Create the global namespace for code execution"""
        globals_dict = {
            '__builtins__': __builtins__,
            '__name__': '__mcp_console__',
            '__doc__': 'Binary Ninja MCP Console',
        }
        
        # Add Binary Ninja imports
        if bn:
            globals_dict.update({
                'binaryninja': bn,
                'bn': bn,
                'bv': self.binary_view,
                'BinaryView': bn.BinaryView,
                'Function': bn.Function,
                'Symbol': bn.Symbol,
                'Type': bn.Type,
                'log_debug': bn.log_debug,
                'log_info': bn.log_info,
                'log_warn': bn.log_warn,
                'log_error': bn.log_error,
            })
            
            # Add current binary view and helpers
            if self.binary_view:
                globals_dict.update({
                    'current_view': self.binary_view,
                    'functions': list(self.binary_view.functions),
                    'entry_point': self.binary_view.entry_point,
                    'entry_function': self.binary_view.entry_function,
                })
        
        return globals_dict
    
    def update_binary_view(self, binary_view):
        """Update the binary view context"""
        with self._lock:
            self.binary_view = binary_view
            self.globals_dict['bv'] = binary_view
            if binary_view:
                self.globals_dict['current_view'] = binary_view
                self.globals_dict['functions'] = list(binary_view.functions)
                self.globals_dict['entry_point'] = binary_view.entry_point
                self.globals_dict['entry_function'] = binary_view.entry_function
    
    def execute(self, code: str, timeout: float = 30.0) -> Dict[str, Any]:
        """
        Execute Python code and return structured results
        
        Returns dict with:
        - success: bool
        - stdout: captured stdout
        - stderr: captured stderr  
        - return_value: JSON-serializable return value
        - return_type: type name of return value
        - variables: dict of variables created/modified
        - error: error info if execution failed
        - execution_time: time taken in seconds
        """
        start_time = time.time()
        
        # Capture stdout/stderr
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        
        result = {
            'success': False,
            'stdout': '',
            'stderr': '',
            'return_value': None,
            'return_type': None,
            'variables': {},
            'error': None,
            'execution_time': 0
        }
        
        # Thread-safe execution
        with self._lock:
            try:
                # Update globals with current binary view
                if self.binary_view:
                    self.globals_dict['bv'] = self.binary_view
                
                # Parse code to check if it's an expression or statements
                tree = ast.parse(code, mode='exec')
                
                # Check if the last statement is an expression we should return
                is_expression = False
                if tree.body:
                    last_stmt = tree.body[-1]
                    if isinstance(last_stmt, ast.Expr):
                        is_expression = True
                        # Modify AST to capture the expression value
                        expr_code = compile(ast.Expression(last_stmt.value), '<console>', 'eval')
                        # Remove the expression from statements
                        tree.body = tree.body[:-1]
                        stmt_code = compile(tree, '<console>', 'exec') if tree.body else None
                    else:
                        stmt_code = compile(tree, '<console>', 'exec')
                        expr_code = None
                else:
                    stmt_code = None
                    expr_code = None
                
                # Execute with output capture
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    # Execute statements first
                    if stmt_code:
                        exec(stmt_code, self.globals_dict, self.locals_dict)
                    
                    # Then evaluate expression if present
                    if expr_code:
                        value = eval(expr_code, self.globals_dict, self.locals_dict)
                        if value is not None:
                            result['return_value'] = self._serialize_value(value)
                            result['return_type'] = type(value).__name__
                            # Also print the value like interactive console
                            print(repr(value))
                    
                    # Check for special _result variable
                    elif '_result' in self.locals_dict:
                        value = self.locals_dict['_result']
                        result['return_value'] = self._serialize_value(value)
                        result['return_type'] = type(value).__name__
                
                result['success'] = True
                
            except Exception as e:
                result['error'] = {
                    'type': type(e).__name__,
                    'message': str(e),
                    'traceback': traceback.format_exc()
                }
                # Also capture any partial output
                result['stderr'] += traceback.format_exc()
            
            finally:
                # Always capture output
                result['stdout'] = stdout_capture.getvalue()
                result['stderr'] += stderr_capture.getvalue()
                
                # Capture variables (excluding private and built-ins)
                result['variables'] = self._capture_variables()
                
                # Calculate execution time
                result['execution_time'] = time.time() - start_time
                
                # Store in history
                self.execution_history.append({
                    'timestamp': datetime.now().isoformat(),
                    'code': code,
                    'result': result
                })
        
        return result
    
    def _serialize_value(self, value: Any) -> Any:
        """Convert Python objects to JSON-serializable format"""
        if value is None:
            return None
        elif isinstance(value, (bool, int, float, str)):
            return value
        elif isinstance(value, bytes):
            return {
                'type': 'bytes',
                'hex': value.hex(),
                'ascii': value.decode('ascii', errors='replace')
            }
        elif isinstance(value, (list, tuple)):
            return {
                'type': type(value).__name__,
                'items': [self._serialize_value(item) for item in value[:100]]  # Limit size
            }
        elif isinstance(value, dict):
            return {
                'type': 'dict',
                'items': {str(k): self._serialize_value(v) for k, v in list(value.items())[:100]}
            }
        elif isinstance(value, set):
            return {
                'type': 'set',
                'items': [self._serialize_value(item) for item in list(value)[:100]]
            }
        
        # Binary Ninja specific types
        if bn:
            if isinstance(value, bn.Function):
                return {
                    'type': 'BinaryNinja.Function',
                    'name': value.name,
                    'address': hex(value.start),
                    'size': value.total_bytes,
                    'basic_blocks': len(list(value.basic_blocks)),
                    'calling_convention': str(value.calling_convention) if value.calling_convention else None
                }
            elif isinstance(value, bn.BinaryView):
                return {
                    'type': 'BinaryNinja.BinaryView',
                    'filename': value.file.filename if value.file else None,
                    'type_name': value.view_type,
                    'arch': str(value.arch) if value.arch else None,
                    'platform': str(value.platform) if value.platform else None,
                    'functions': len(list(value.functions)),
                    'size': len(value)
                }
            elif isinstance(value, bn.Symbol):
                return {
                    'type': 'BinaryNinja.Symbol',
                    'name': value.name,
                    'address': hex(value.address),
                    'type': str(value.type)
                }
            elif isinstance(value, bn.Type):
                return {
                    'type': 'BinaryNinja.Type',
                    'string': str(value),
                    'width': value.width
                }
        
        # Generic object fallback
        try:
            # Try to get useful attributes
            attrs = {}
            for attr in ['name', 'value', 'address', 'size', 'length']:
                if hasattr(value, attr):
                    attr_value = getattr(value, attr)
                    if isinstance(attr_value, (str, int, float, bool)):
                        attrs[attr] = attr_value
            
            return {
                'type': type(value).__name__,
                'module': type(value).__module__,
                'repr': str(value)[:500],  # Limit string length
                'attributes': attrs if attrs else None
            }
        except:
            return {
                'type': type(value).__name__,
                'repr': '<serialization error>'
            }
    
    def _capture_variables(self) -> Dict[str, Any]:
        """Capture interesting variables from execution context"""
        captured = {}
        
        # Capture from locals
        for name, value in self.locals_dict.items():
            # Skip private variables and callables (unless they're interesting)
            if not name.startswith('_') and not (callable(value) and not isinstance(value, type)):
                try:
                    captured[name] = self._serialize_value(value)
                except:
                    captured[name] = {'type': type(value).__name__, 'error': 'serialization failed'}
        
        return captured
    
    def get_completions(self, partial: str) -> List[str]:
        """Get auto-completions for partial input"""
        completions = []
        
        # Split on dots to handle attribute access
        parts = partial.split('.')
        
        if len(parts) == 1:
            # Complete from globals and locals
            prefix = parts[0]
            
            # From locals
            completions.extend([name for name in self.locals_dict.keys() 
                              if name.startswith(prefix) and not name.startswith('_')])
            
            # From globals
            completions.extend([name for name in self.globals_dict.keys() 
                              if name.startswith(prefix) and not name.startswith('_')])
            
            # Built-in functions and keywords
            import keyword
            completions.extend([name for name in dir(__builtins__) 
                              if name.startswith(prefix) and not name.startswith('_')])
            completions.extend([kw for kw in keyword.kwlist if kw.startswith(prefix)])
            
        else:
            # Try to resolve the object and get its attributes
            try:
                obj_path = '.'.join(parts[:-1])
                obj = eval(obj_path, self.globals_dict, self.locals_dict)
                prefix = parts[-1]
                
                # Get attributes
                attrs = [attr for attr in dir(obj) 
                        if attr.startswith(prefix) and not attr.startswith('_')]
                completions.extend([f"{obj_path}.{attr}" for attr in attrs])
                
            except:
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
    
    def get_history(self, count: int = 100) -> List[Dict[str, Any]]:
        """Get execution history"""
        with self._lock:
            return list(self.execution_history)[-count:]


# Console capture implementation that uses PythonExecutor
class EnhancedConsoleCapture:
    """Console capture using the enhanced Python executor"""
    
    def __init__(self):
        self.executor = PythonExecutor()
        self.output_buffer = deque(maxlen=10000)
        self.initialized = True
        bn.log_info("Enhanced Python console initialized")
    
    def start(self):
        """Start console capture (no-op for this implementation)"""
        pass
    
    def stop(self):
        """Stop console capture"""
        pass
    
    def execute_command(self, command: str) -> Dict[str, Any]:
        """Execute a Python command and return results"""
        # Update binary view if available
        if bn and hasattr(bn, 'current_view') and bn.current_view:
            self.executor.update_binary_view(bn.current_view)
        
        # Execute the command
        result = self.executor.execute(command)
        
        # Store output in buffer for retrieval
        timestamp = datetime.now().isoformat()
        
        # Add stdout as output entries
        if result['stdout']:
            for line in result['stdout'].splitlines():
                self.output_buffer.append({
                    'id': len(self.output_buffer),
                    'timestamp': timestamp,
                    'type': 'output',
                    'text': line
                })
        
        # Add stderr as error entries
        if result['stderr']:
            for line in result['stderr'].splitlines():
                self.output_buffer.append({
                    'id': len(self.output_buffer),
                    'timestamp': timestamp,
                    'type': 'error',
                    'text': line
                })
        
        # Return execution result
        return result
    
    def get_output(self, count=100, type_filter=None, search_text=None, start_id=None):
        """Get console output entries"""
        entries = list(self.output_buffer)
        
        # Filter by start_id
        if start_id is not None:
            entries = [e for e in entries if e['id'] > start_id]
        
        # Filter by type
        if type_filter:
            entries = [e for e in entries if e['type'] == type_filter]
        
        # Filter by search text
        if search_text:
            search_lower = search_text.lower()
            entries = [e for e in entries if search_lower in e.get('text', '').lower()]
        
        # Return last N entries
        return entries[-count:] if count else entries
    
    def get_completions(self, partial: str) -> List[str]:
        """Get auto-completions"""
        return self.executor.get_completions(partial)
    
    def clear(self):
        """Clear console output and execution context"""
        self.output_buffer.clear()
        self.executor.clear_context()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get console statistics"""
        type_counts = {}
        for entry in self.output_buffer:
            entry_type = entry.get('type', 'unknown')
            type_counts[entry_type] = type_counts.get(entry_type, 0) + 1
        
        entries = list(self.output_buffer)
        return {
            'total_entries': len(entries),
            'types': type_counts,
            'oldest_timestamp': entries[0]['timestamp'] if entries else None,
            'newest_timestamp': entries[-1]['timestamp'] if entries else None
        }


# Create singleton instance
_console_instance = None

def get_console_capture():
    """Get the console capture instance"""
    global _console_instance
    if _console_instance is None:
        _console_instance = EnhancedConsoleCapture()
    return _console_instance