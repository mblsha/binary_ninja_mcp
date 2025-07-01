# Enhanced Python Executor for Binary Ninja MCP

The Binary Ninja MCP server now includes an enhanced Python executor that provides reliable code execution with comprehensive result capture and JSON serialization.

## Features

### 1. Reliable Code Execution
- Direct execution using Python's `exec()` and `eval()`
- No dependency on Binary Ninja's ScriptingProvider
- Thread-safe execution with proper locking
- Timeout support (default 30 seconds)

### 2. Comprehensive Result Capture
- **Return Values**: Automatically captures the last expression value
- **Standard Output**: All print() statements captured
- **Standard Error**: Error messages and tracebacks captured
- **Variable Tracking**: New/modified variables are tracked
- **Execution Time**: Measures how long code takes to run

### 3. Binary Ninja Integration
The executor provides direct access to Binary Ninja's Python API:

```python
# Available globals:
binaryninja, bn  # Binary Ninja module
bv              # Current BinaryView (if loaded)
current_view    # Alias for bv
functions       # List of all functions
entry_point     # Entry point address
entry_function  # Entry function object

# Logging functions
log_debug, log_info, log_warn, log_error
```

### 4. JSON Serialization
All Python objects are automatically converted to JSON-compatible format:

- **Basic Types**: int, float, str, bool, None, bytes
- **Collections**: list, tuple, dict, set
- **Binary Ninja Types**: Function, BinaryView, Symbol, Type
- **Complex Objects**: Serialized with type info and string representation

## API Endpoints

### Execute Python Code
```
POST /console/execute
Content-Type: application/json

{
    "command": "print('Hello'); 2 + 2"
}
```

Response:
```json
{
    "success": true,
    "stdout": "Hello\n4\n",
    "stderr": "",
    "return_value": 4,
    "return_type": "int",
    "variables": {},
    "error": null,
    "execution_time": 0.001
}
```

### Get Console Output
```
GET /console?count=50&type=output
```

### Get Console Statistics
```
GET /console/stats
```

### Clear Console
```
POST /console/clear
```

## Usage Examples

### Basic Expression Evaluation
```python
# Request
{"command": "2 + 2"}

# Response
{
    "success": true,
    "return_value": 4,
    "return_type": "int"
}
```

### Working with Binary View
```python
# Request
{"command": "len(list(bv.functions))"}

# Response
{
    "success": true,
    "return_value": 150,
    "return_type": "int"
}
```

### Function Analysis
```python
# Request
{
    "command": "
func = bv.get_function_at(0x401000)
{
    'name': func.name,
    'size': func.total_bytes,
    'blocks': len(list(func.basic_blocks))
}
"
}

# Response
{
    "success": true,
    "return_value": {
        "type": "dict",
        "items": {
            "name": "main",
            "size": 245,
            "blocks": 8
        }
    },
    "return_type": "dict"
}
```

### Error Handling
```python
# Request
{"command": "undefined_variable"}

# Response
{
    "success": false,
    "error": {
        "type": "NameError",
        "message": "name 'undefined_variable' is not defined",
        "traceback": "Traceback (most recent call last):\n..."
    }
}
```

### Multi-line Scripts
```python
# Request
{
    "command": "
def analyze_strings(bv):
    strings = []
    for s in bv.strings:
        if len(s.value) > 10:
            strings.append({
                'value': s.value,
                'address': hex(s.start)
            })
    return strings[:10]

_result = analyze_strings(bv) if bv else []
"
}

# Response
{
    "success": true,
    "return_value": [
        {"value": "Hello World!", "address": "0x404000"},
        ...
    ],
    "return_type": "list"
}
```

### Using Special _result Variable
If your code doesn't end with an expression, you can use `_result` to specify the return value:

```python
# Request
{
    "command": "
x = 10
y = 20
_result = x + y
"
}

# Response
{
    "success": true,
    "return_value": 30,
    "return_type": "int"
}
```

## Integration with MCP Tools

The Python executor results are designed to integrate seamlessly with MCP tools:

1. **Return values are JSON-serializable** - Can be passed directly to other tools
2. **Error information includes full context** - Useful for debugging
3. **Variable tracking** - See what was created/modified
4. **Execution time** - Monitor performance

## Security Considerations

The Python executor runs with full access to Binary Ninja's API and the Python environment. Consider:

1. **Input Validation**: Validate and sanitize user input
2. **Timeouts**: Default 30-second timeout prevents infinite loops
3. **Resource Limits**: Monitor memory and CPU usage
4. **Sandboxing**: Consider additional sandboxing for untrusted code

## Troubleshooting

### Console Not Initialized
If you see "Console capture not initialized", the enhanced executor should automatically take over.

### Binary View Not Available
If `bv` is None, ensure a binary is loaded in Binary Ninja before executing code.

### Import Errors
The executor runs in Binary Ninja's Python environment. Only packages available there can be imported.

### Serialization Errors
Complex objects that can't be JSON-serialized will show as:
```json
{
    "type": "ComplexType",
    "repr": "<string representation>",
    "error": "serialization failed"
}
```

## Future Enhancements

1. **Auto-completion API**: Endpoint for code completion suggestions
2. **Persistent Sessions**: Maintain separate execution contexts
3. **Async Execution**: Support for long-running operations
4. **Rich Object Inspection**: More detailed object introspection
5. **Execution Profiles**: Save and replay common scripts