# Python Execution API Improvement Options

## Current Issues
1. `bv` is always None despite binary being loaded
2. No way to access the current binary view
3. Variable persistence might be confusing in some contexts
4. No clear session management
5. Limited discoverability of available APIs

## Option 1: Automatic Binary View Injection

### Implementation
```python
class EnhancedConsoleCapture:
    def execute_command(self, command: str, binary_view=None) -> Dict[str, Any]:
        # Auto-detect binary view if not provided
        if binary_view is None:
            binary_view = self._get_current_binary_view()
        
        # Update executor context
        self.executor.update_binary_view(binary_view)
        
        # For convenience, also inject common shortcuts
        self.executor.inject_helpers({
            'current_function': binary_view.entry_function if binary_view else None,
            'funcs': list(binary_view.functions) if binary_view else [],
            'get_func': lambda name: next((f for f in binary_view.functions if f.name == name), None) if binary_view else None
        })
        
        return self.executor.execute(command)
    
    def _get_current_binary_view(self):
        # Try multiple sources
        if hasattr(bn, 'current_view') and bn.current_view:
            return bn.current_view
        
        # Check global registry
        if hasattr(self, '_server_context') and self._server_context:
            return self._server_context.binary_view
        
        # Try to get from Binary Ninja UI
        try:
            import binaryninjaui
            if binaryninjaui.get_current_view():
                return binaryninjaui.get_current_view().data
        except:
            pass
        
        return None
```

### HTTP Handler Update
```python
# In http_server.py
elif path == "/console/execute":
    command = params.get("command")
    binary_view = self.binary_ops.current_view if self.binary_ops else None
    
    console_capture = get_console_capture()
    console_capture.set_server_context(self)  # Pass server context
    result = console_capture.execute_command(command, binary_view)
    self._send_json_response(result)
```

### Pros
- `bv` automatically available
- Works transparently
- Backward compatible

### Cons
- Hidden magic might confuse users
- Requires server modifications

---

## Option 2: Explicit Context Management

### Implementation
```python
@mcp.tool()
def create_python_session(binary_path: str = None) -> str:
    """
    Create a new Python execution session with optional binary loading
    
    Returns session_id to use in subsequent calls
    """
    session_id = str(uuid.uuid4())
    
    if binary_path:
        # Load binary into session
        bv = bn.load(binary_path)
        sessions[session_id] = {
            'executor': PythonExecutor(binary_view=bv),
            'binary_path': binary_path,
            'created': datetime.now()
        }
    else:
        # Use current binary view
        sessions[session_id] = {
            'executor': PythonExecutor(binary_view=get_current_view()),
            'binary_path': 'current',
            'created': datetime.now()
        }
    
    return session_id

@mcp.tool()
def execute_python_in_session(session_id: str, command: str) -> str:
    """Execute Python code in a specific session"""
    if session_id not in sessions:
        return json.dumps({"error": "Invalid session ID"})
    
    return sessions[session_id]['executor'].execute(command)

@mcp.tool()
def list_python_sessions() -> list:
    """List all active Python sessions"""
    return [
        {
            'id': sid,
            'binary': s['binary_path'],
            'created': s['created'].isoformat(),
            'variables': list(s['executor'].locals_dict.keys())
        }
        for sid, s in sessions.items()
    ]
```

### Usage
```python
# Create session
session = create_python_session("/path/to/binary")

# Use session
result = execute_python_in_session(session, "len(list(bv.functions))")

# Different session for different binary
session2 = create_python_session("/path/to/other/binary")
```

### Pros
- Explicit session management
- Multiple binaries simultaneously
- Clear separation of contexts

### Cons
- More complex API
- Requires session ID tracking

---

## Option 3: Functional Pipeline Approach

### Implementation
```python
class PythonPipeline:
    def __init__(self):
        self.steps = []
    
    def with_binary(self, path_or_view):
        """Set binary context"""
        self.binary = path_or_view if isinstance(path_or_view, bn.BinaryView) else bn.load(path_or_view)
        return self
    
    def with_function(self, name_or_addr):
        """Focus on specific function"""
        if isinstance(name_or_addr, str):
            self.context['func'] = next((f for f in self.binary.functions if f.name == name_or_addr), None)
        else:
            self.context['func'] = self.binary.get_function_at(name_or_addr)
        return self
    
    def execute(self, code: str):
        """Execute code with built context"""
        # Inject context
        context = {
            'bv': self.binary,
            'func': self.context.get('func'),
            'bb': self.context.get('basic_block'),
            **self.context
        }
        return execute_with_context(code, context)
    
    def map(self, code: str, items: str = 'functions'):
        """Map code over items"""
        results = []
        for item in self._get_items(items):
            self.context['_'] = item
            results.append(self.execute(code))
        return results

# Usage
@mcp.tool()
def analyze_with_pipeline(binary_path: str, pipeline_spec: dict) -> str:
    """
    Execute analysis pipeline
    
    Example spec:
    {
        "steps": [
            {"action": "with_binary", "path": "/path/to/binary"},
            {"action": "filter", "code": "len(_.name) > 10", "over": "functions"},
            {"action": "map", "code": "{'name': _.name, 'size': _.total_bytes}"},
            {"action": "sort", "key": "size", "reverse": true},
            {"action": "limit", "count": 10}
        ]
    }
    """
    pipeline = PythonPipeline()
    # ... execute steps
    return pipeline.get_results()
```

### Pros
- Composable operations
- Clear data flow
- Functional programming style

### Cons
- Learning curve
- Different paradigm

---

## Option 4: Smart Context with Autocomplete

### Implementation
```python
class SmartPythonExecutor:
    def __init__(self):
        self.context_stack = []
        
    def execute(self, command: str) -> Dict[str, Any]:
        # Parse command for context hints
        context = self._infer_context(command)
        
        # Auto-import commonly used items
        if 'bv.' in command and 'bv' not in self.locals_dict:
            self._auto_setup_bv()
        
        # Provide smart suggestions in error messages
        try:
            return super().execute(command)
        except NameError as e:
            suggestions = self._get_suggestions(str(e))
            error_response = {
                'error': {
                    'type': 'NameError',
                    'message': str(e),
                    'suggestions': suggestions,
                    'hint': f"Did you mean: {suggestions[0]}?" if suggestions else None
                }
            }
            return error_response
    
    def _get_suggestions(self, error: str) -> List[str]:
        # Extract undefined name
        import re
        match = re.search(r"name '(\w+)' is not defined", error)
        if match:
            undefined = match.group(1)
            
            # Get similar names
            all_names = list(self.globals_dict.keys()) + list(self.locals_dict.keys())
            
            # Use fuzzy matching
            from difflib import get_close_matches
            return get_close_matches(undefined, all_names, n=3, cutoff=0.6)
        
        return []
    
    def get_interactive_help(self) -> str:
        """Get context-aware help"""
        return f"""
Available objects:
  bv         - Current binary view {'✓' if self.globals_dict.get('bv') else '✗ (run: bv = get_current_view())'}
  functions  - List of all functions {'✓' if 'functions' in self.locals_dict else '✗ (run: functions = list(bv.functions))'}
  
Current context:
  Binary: {self.globals_dict.get('bv').file.filename if self.globals_dict.get('bv') else 'None'}
  Functions: {len(list(self.globals_dict.get('bv').functions)) if self.globals_dict.get('bv') else 0}
  Variables: {', '.join(self.locals_dict.keys()) or 'None'}
  
Useful snippets:
  Find function: get_func('name') or bv.get_function_at(0xaddr)
  List strings: [s.value for s in bv.strings if len(s.value) > 10]
  Find crypto: [f.name for f in bv.functions if 'crypt' in f.name.lower()]
"""

# New endpoint
@app.route('/console/help', methods=['GET'])
def get_console_help():
    """Get context-aware help and suggestions"""
    return jsonify(console_capture.get_interactive_help())

@app.route('/console/complete', methods=['POST'])
def get_completions():
    """Get autocompletions for partial code"""
    partial = request.json.get('partial', '')
    return jsonify({
        'completions': console_capture.get_completions(partial),
        'type_hints': console_capture.get_type_hints(partial)
    })
```

### Pros
- Helpful error messages
- Discoverable API
- Reduces confusion

### Cons
- More complex implementation
- Might be too "magical"

---

## Option 5: RESTful Resource-Oriented API

### Implementation
```python
# More RESTful approach
@app.route('/binaries/<binary_id>/execute', methods=['POST'])
def execute_in_binary_context(binary_id):
    """Execute Python in context of specific binary"""
    binary = get_binary_by_id(binary_id)
    if not binary:
        return jsonify({'error': 'Binary not found'}), 404
    
    executor = get_or_create_executor(binary_id)
    executor.update_binary_view(binary)
    
    return jsonify(executor.execute(request.json['code']))

@app.route('/binaries/current/execute', methods=['POST'])
def execute_in_current_context():
    """Execute Python in current binary context"""
    # ...

@app.route('/functions/<function_name>/execute', methods=['POST'])
def execute_in_function_context(function_name):
    """Execute Python in context of specific function"""
    # Sets both bv and current_function
    # ...

# CLI integration
./cli.py execute --binary /path/to/bin "code"
./cli.py execute --function main "code"
./cli.py execute --address 0x401000 "code"
```

### Pros
- RESTful design
- Clear context from URL
- Natural for web APIs

### Cons
- More endpoints to maintain
- Verbose for simple operations

---

## Option 6: Notebook-Style Cells

### Implementation
```python
class NotebookExecutor:
    def __init__(self):
        self.cells = []
        self.outputs = []
    
    def add_cell(self, code: str, cell_type: str = 'code'):
        """Add a cell to the notebook"""
        cell_id = str(uuid.uuid4())
        self.cells.append({
            'id': cell_id,
            'type': cell_type,
            'code': code,
            'created': datetime.now()
        })
        return cell_id
    
    def execute_notebook(self, until_cell: str = None):
        """Execute all cells up to specified cell"""
        results = []
        for cell in self.cells:
            if cell['type'] == 'code':
                result = self.executor.execute(cell['code'])
                results.append({
                    'cell_id': cell['id'],
                    'result': result
                })
            
            if cell['id'] == until_cell:
                break
        
        return results
    
    def to_jupyter(self):
        """Export as Jupyter notebook"""
        # ...

@mcp.tool()
def create_analysis_notebook() -> str:
    """Create a new analysis notebook"""
    # Returns notebook ID

@mcp.tool()
def add_notebook_cell(notebook_id: str, code: str) -> str:
    """Add cell to notebook"""
    # Returns cell ID

@mcp.tool()
def run_notebook(notebook_id: str) -> str:
    """Execute entire notebook"""
    # Returns all results
```

### Pros
- Familiar notebook paradigm
- Great for documentation
- Reproducible analysis

### Cons
- More complex state management
- Overkill for simple tasks

---

## Recommendation: Hybrid Approach

Combine the best aspects:

1. **Automatic Binary View Injection** (Option 1) for immediate usability
2. **Smart Context with Autocomplete** (Option 4) for discoverability
3. **Optional Session Management** (Option 2) for advanced use cases

```python
# Simple usage - automatic context
execute_python_command("len(list(bv.functions))")  # Just works

# Get help
get_python_help()  # Returns available objects and examples

# Advanced usage - explicit session
session = create_python_session(binary_path="/path/to/binary")
execute_python_in_session(session, "complex_analysis()")
```

This provides:
- Zero-friction for simple cases
- Helpful guidance for learning
- Power features when needed
- Backward compatibility