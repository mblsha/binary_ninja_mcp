# Python Code Execution Options for Binary Ninja MCP

## Option 1: Enhanced Direct Execution with Context Capture

### Implementation
```python
import sys
import io
import json
import traceback
import ast
from contextlib import redirect_stdout, redirect_stderr
import binaryninja as bn

class PythonExecutor:
    def __init__(self):
        self.globals_dict = {
            'binaryninja': bn,
            'bn': bn,
            'bv': None,  # Will be set to current BinaryView
            '__builtins__': __builtins__,
        }
        self.locals_dict = {}
    
    def execute(self, code: str, binary_view=None):
        """Execute Python code and return structured results"""
        # Capture stdout/stderr
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        
        # Update context with current binary view
        if binary_view:
            self.globals_dict['bv'] = binary_view
            self.globals_dict['current_function'] = binary_view.entry_function
        
        result = {
            'success': False,
            'stdout': '',
            'stderr': '',
            'return_value': None,
            'return_type': None,
            'console_logs': [],
            'variables': {},
            'error': None
        }
        
        try:
            # Parse code to check if it's an expression or statement
            tree = ast.parse(code, mode='exec')
            
            # Check if last node is an expression
            is_expression = (len(tree.body) == 1 and 
                           isinstance(tree.body[0], ast.Expr))
            
            if is_expression:
                # Evaluate as expression to get return value
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    value = eval(code, self.globals_dict, self.locals_dict)
                    result['return_value'] = self._serialize_value(value)
                    result['return_type'] = type(value).__name__
            else:
                # Execute as statements
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    exec(code, self.globals_dict, self.locals_dict)
                    
                    # Check if code assigned a special return variable
                    if '_result' in self.locals_dict:
                        value = self.locals_dict['_result']
                        result['return_value'] = self._serialize_value(value)
                        result['return_type'] = type(value).__name__
            
            result['success'] = True
            result['stdout'] = stdout_capture.getvalue()
            result['stderr'] = stderr_capture.getvalue()
            
            # Capture modified variables
            result['variables'] = self._capture_variables()
            
        except Exception as e:
            result['error'] = {
                'type': type(e).__name__,
                'message': str(e),
                'traceback': traceback.format_exc()
            }
            result['stderr'] = stderr_capture.getvalue()
        
        return result
    
    def _serialize_value(self, value):
        """Convert Python objects to JSON-serializable format"""
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        elif isinstance(value, (list, tuple)):
            return [self._serialize_value(item) for item in value]
        elif isinstance(value, dict):
            return {str(k): self._serialize_value(v) for k, v in value.items()}
        elif isinstance(value, bn.Function):
            return {
                'type': 'Function',
                'name': value.name,
                'address': hex(value.start),
                'size': value.total_bytes
            }
        elif isinstance(value, bn.BinaryView):
            return {
                'type': 'BinaryView',
                'filename': value.file.filename,
                'type_name': value.type_name,
                'functions': len(list(value.functions))
            }
        else:
            # Fallback to string representation
            return {'type': type(value).__name__, 'repr': str(value)}
    
    def _capture_variables(self):
        """Capture interesting variables from execution context"""
        captured = {}
        for name, value in self.locals_dict.items():
            if not name.startswith('_'):
                captured[name] = self._serialize_value(value)
        return captured
```

### Pros
- Full control over execution context
- Can capture return values, stdout, stderr, and variables
- JSON-serializable output
- Maintains state between executions

### Cons
- Doesn't integrate with Binary Ninja's actual console
- May miss some Binary Ninja-specific console features

## Option 2: ScriptingProvider Wrapper with Enhanced Capture

### Implementation
```python
class EnhancedScriptingProvider:
    def __init__(self):
        self.output_buffer = []
        self.initialize_provider()
    
    def initialize_provider(self):
        """Initialize with retry mechanism"""
        for attempt in range(5):
            try:
                providers = bn.ScriptingProvider.list
                self.python_provider = None
                
                for provider in providers:
                    if provider.name == "Python":
                        self.python_provider = provider
                        break
                
                if self.python_provider:
                    instance = self.python_provider.create_instance()
                    
                    # Create custom output listener
                    class OutputCapture(bn.ScriptingOutputListener):
                        def __init__(self, buffer):
                            super().__init__()
                            self.buffer = buffer
                        
                        def output(self, text):
                            self.buffer.append({'type': 'output', 'text': text})
                        
                        def error(self, text):
                            self.buffer.append({'type': 'error', 'text': text})
                        
                        def warning(self, text):
                            self.buffer.append({'type': 'warning', 'text': text})
                    
                    self.listener = OutputCapture(self.output_buffer)
                    instance.output_listener = self.listener
                    self.instance = instance
                    return True
                    
            except Exception as e:
                bn.log_warn(f"Console init attempt {attempt + 1} failed: {e}")
                time.sleep(0.5)
        
        return False
    
    def execute_with_result(self, code: str):
        """Execute code and extract results"""
        if not hasattr(self, 'instance'):
            if not self.initialize_provider():
                return {'error': 'Failed to initialize scripting provider'}
        
        # Clear buffer
        self.output_buffer.clear()
        
        # Wrap code to capture result
        wrapped_code = f"""
import json
_mcp_result = None
_mcp_error = None
try:
    # User code
{chr(10).join('    ' + line for line in code.split(chr(10)))}
    
    # Try to capture last expression
    import ast
    tree = ast.parse({repr(code)}, mode='exec')
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        _mcp_result = eval(compile(ast.Expression(tree.body[-1].value), '<mcp>', 'eval'))
except Exception as e:
    _mcp_error = str(e)
    import traceback
    _mcp_traceback = traceback.format_exc()

# Return results via print
if _mcp_result is not None:
    print("__MCP_RESULT__:", json.dumps(_mcp_result) if isinstance(_mcp_result, (dict, list, str, int, float, bool, type(None))) else str(_mcp_result))
if _mcp_error:
    print("__MCP_ERROR__:", _mcp_error)
"""
        
        # Execute
        self.instance.execute_script_input(wrapped_code)
        
        # Parse output
        result = {
            'success': True,
            'console_output': [],
            'return_value': None,
            'error': None
        }
        
        for entry in self.output_buffer:
            if entry['type'] == 'output':
                text = entry['text']
                if text.startswith("__MCP_RESULT__:"):
                    result['return_value'] = text[15:].strip()
                elif text.startswith("__MCP_ERROR__:"):
                    result['error'] = text[14:].strip()
                    result['success'] = False
                else:
                    result['console_output'].append(entry)
            else:
                result['console_output'].append(entry)
        
        return result
```

### Pros
- Uses Binary Ninja's actual scripting infrastructure
- Captures console output properly
- Can access Binary Ninja's console features

### Cons
- Depends on ScriptingProvider availability
- More complex result extraction

## Option 3: Hybrid Approach with State Management

### Implementation
```python
class HybridPythonExecutor:
    def __init__(self):
        self.execution_state = {}
        self.console_capture = None
        self.direct_executor = PythonExecutor()
        
    def execute(self, code: str, mode='auto'):
        """
        Execute Python code with automatic mode selection
        
        Modes:
        - 'auto': Try console first, fallback to direct
        - 'console': Use Binary Ninja console only
        - 'direct': Use direct execution only
        - 'analyze': Parse code and return analysis without execution
        """
        
        if mode == 'analyze':
            return self._analyze_code(code)
        
        if mode in ['auto', 'console']:
            console_result = self._try_console_execution(code)
            if console_result['success'] or mode == 'console':
                return console_result
        
        if mode in ['auto', 'direct']:
            return self.direct_executor.execute(code)
    
    def _analyze_code(self, code: str):
        """Analyze code without executing"""
        try:
            tree = ast.parse(code)
            
            # Extract imports, function defs, variable assignments
            imports = []
            functions = []
            assignments = []
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module)
                elif isinstance(node, ast.FunctionDef):
                    functions.append({
                        'name': node.name,
                        'args': [arg.arg for arg in node.args.args],
                        'lineno': node.lineno
                    })
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            assignments.append(target.id)
            
            return {
                'valid': True,
                'imports': imports,
                'functions': functions,
                'assignments': assignments,
                'is_expression': len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr)
            }
        except SyntaxError as e:
            return {
                'valid': False,
                'error': str(e),
                'line': e.lineno,
                'offset': e.offset
            }
```

### Pros
- Flexible execution modes
- Code analysis without execution
- Graceful fallbacks

### Cons
- More complex implementation
- Potential inconsistencies between modes

## Option 4: WebSocket-Based Interactive Console

### Implementation
```python
import asyncio
import websockets
import json

class InteractiveConsole:
    def __init__(self):
        self.sessions = {}
    
    async def handle_client(self, websocket, path):
        session_id = str(uuid.uuid4())
        session = {
            'executor': PythonExecutor(),
            'history': [],
            'websocket': websocket
        }
        self.sessions[session_id] = session
        
        try:
            await websocket.send(json.dumps({
                'type': 'connected',
                'session_id': session_id
            }))
            
            async for message in websocket:
                data = json.loads(message)
                
                if data['type'] == 'execute':
                    result = session['executor'].execute(data['code'])
                    session['history'].append({
                        'code': data['code'],
                        'result': result
                    })
                    
                    await websocket.send(json.dumps({
                        'type': 'result',
                        'data': result
                    }))
                
                elif data['type'] == 'get_completions':
                    completions = self._get_completions(
                        data['partial'],
                        session['executor'].locals_dict
                    )
                    await websocket.send(json.dumps({
                        'type': 'completions',
                        'data': completions
                    }))
                    
        finally:
            del self.sessions[session_id]
    
    def start_server(self, host='localhost', port=9010):
        start_server = websockets.serve(self.handle_client, host, port)
        asyncio.get_event_loop().run_until_complete(start_server)
        asyncio.get_event_loop().run_forever()
```

### Pros
- Real-time interactive console
- Supports auto-completion
- Persistent sessions
- Can integrate with web UIs

### Cons
- Requires WebSocket infrastructure
- More complex client integration

## Recommended Implementation Strategy

I recommend implementing **Option 1 (Enhanced Direct Execution)** first as it provides the most control and reliability, then gradually adding features from other options:

1. **Phase 1**: Implement basic direct execution with JSON serialization
2. **Phase 2**: Add ScriptingProvider integration when available
3. **Phase 3**: Add code analysis and validation
4. **Phase 4**: Consider WebSocket interface for advanced use cases

## Next Steps

1. Replace the current console implementation with the enhanced version
2. Add comprehensive error handling and logging
3. Create unit tests for various code execution scenarios
4. Document the API with examples
5. Add security features (code sandboxing, timeout, resource limits)