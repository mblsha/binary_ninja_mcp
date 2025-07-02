# Python CLI Guide

The Binary Ninja MCP CLI provides powerful Python execution capabilities with multiple input methods, making it easy to work with complex scripts without worrying about shell escaping.

## Features

- **Multiple input methods**: inline, file, stdin
- **No escaping needed**: Use files or stdin for complex strings
- **Code completion**: Get suggestions for partial code
- **Interactive mode**: REPL-like experience
- **JSON output**: Machine-readable results

## Usage Examples

### 1. Inline Code Execution
Simple one-liners can be executed directly:
```bash
./cli.py python "print('Hello, Binary Ninja!')"
./cli.py python "len(list(bv.functions))"
```

### 2. Execute Python Files
Run scripts from files - the recommended way for complex code:
```bash
# Direct file argument (auto-detected)
./cli.py python script.py

# Explicit file flag
./cli.py python -f script.py

# With path
./cli.py python /path/to/script.py
```

### 3. Standard Input (stdin)
Perfect for generated code or piping:
```bash
# Pipe from echo
echo "print('Hello')" | ./cli.py python

# Using --stdin flag
echo "2 + 2" | ./cli.py python --stdin

# Here document for multi-line code
cat << 'EOF' | ./cli.py python
for i in range(5):
    print(f"Number {i}")
EOF

# From another command
generate_code.py | ./cli.py python
```

### 4. Complex Strings Without Escaping
When using files or stdin, you don't need to escape quotes or special characters:

```python
# Save as complex_strings.py
print('''This works perfectly:
- Single quotes: 'no problem'
- Double quotes: "also fine"
- Backslashes: C:\Windows\System32
- Unicode: 🎉 ✨ 🚀
- JSON: {"key": "value"}
''')
```

Then run:
```bash
./cli.py python complex_strings.py
```

### 5. Code Completion
Get suggestions for partial code:
```bash
# Get completions
./cli.py python -c "find_f"
# Output:
# find_funcs
# find_functions

# Use with JSON for programmatic access
./cli.py --json python -c "bv.get_"
```

### 6. Interactive Mode
Start an interactive Python session:
```bash
./cli.py python -i
# Binary Ninja Python Console (type 'exit()' to quit)
# >>> bv.file.filename
# '/bin/ls'
# >>> len(list(bv.functions))
# 141
```

### 7. JSON Output Mode
Get structured output for integration with other tools:
```bash
# Use --json flag before the subcommand
./cli.py --json python "{'functions': len(list(bv.functions))}"

# Pretty print with jq
./cli.py --json python "info()" | jq .

# Extract specific fields
./cli.py --json python "2+2" | jq -r .return_value
```

## Binary Ninja Integration

### Available Objects
When executing Python code, these objects are automatically available:
- `bv` - Current BinaryView
- `bn` - Binary Ninja module
- `info()` - Get binary information
- `find_functions(pattern)` - Search functions
- `get_func(name)` - Get function by name
- `hex_dump(addr, size)` - Display hex dump

### Example: Binary Analysis Script
```python
# analysis.py
print(f"Analyzing: {bv.file.filename if bv else 'No binary'}")

if bv:
    # Count functions by type
    funcs = list(bv.functions)
    print(f"Total functions: {len(funcs)}")
    
    # Find interesting functions
    crypto = find_functions('crypt')
    print(f"Crypto functions: {len(crypto)}")
    
    # Get strings
    strings = [s for s in bv.strings if len(s.value) > 20]
    print(f"Long strings: {len(strings)}")
    
    # Check entry point
    if bv.entry_function:
        print(f"Entry: {bv.entry_function.name} @ {hex(bv.entry_point)}")
```

Run with:
```bash
./cli.py python analysis.py
```

## Tips and Tricks

### 1. Shebang for Python Scripts
Make scripts directly executable:
```python
#!/usr/bin/env binja-mcp python
# my_script.py
print("Direct execution!")
```

Then:
```bash
chmod +x my_script.py
./my_script.py  # Requires binja-mcp in PATH
```

### 2. Pipeline Integration
Chain with other Unix tools:
```bash
# Find all function names
./cli.py --json python "[f.name for f in bv.functions]" | jq -r '.return_value.items[]' | sort

# Count function sizes
./cli.py python "for f in bv.functions: print(f'{f.name},{f.total_bytes}')" | awk -F, '{sum+=$2} END {print "Average:", sum/NR}'
```

### 3. Template Scripts
Use environment variables in scripts:
```bash
PATTERN="main" ./cli.py python -f find_template.py
```

Where `find_template.py`:
```python
import os
pattern = os.environ.get('PATTERN', 'test')
results = find_functions(pattern)
for f in results:
    print(f"{f.name} @ {hex(f.start)}")
```

### 4. Error Handling
The CLI provides clear error messages:
```bash
./cli.py python "undefined_variable"
# Error: NameError: name 'undefined_variable' is not defined

# With verbose mode for full traceback
./cli.py -v python "1/0"
```

## Common Use Cases

### Quick Binary Inspection
```bash
# Function count
./cli.py python "len(list(bv.functions))"

# Entry point
./cli.py python "hex(bv.entry_point) if bv else 'No binary'"

# Architecture
./cli.py python "str(bv.arch) if bv else 'No binary'"
```

### Batch Processing
```bash
# Process multiple binaries
for binary in *.exe; do
    echo "=== $binary ==="
    # Load binary first via HTTP API, then analyze
    ./cli.py python "print(f'Functions: {len(list(bv.functions))}')"
done
```

### Integration with External Tools
```python
# export_data.py
import json

data = {
    'binary': bv.file.filename if bv else None,
    'functions': [
        {
            'name': f.name,
            'address': f.start,
            'size': f.total_bytes
        }
        for f in (bv.functions if bv else [])
    ]
}

print(json.dumps(data, indent=2))
```

Run and save:
```bash
./cli.py python export_data.py > binary_data.json
```

## Troubleshooting

### Binary Not Loaded
If `bv` is None, ensure:
1. Binary Ninja is running
2. A binary is loaded in the UI
3. The MCP server is running (`http://localhost:9009`)

### Import Errors
The execution environment includes Binary Ninja's Python environment. Standard library imports work, but external packages may not be available.

### Performance
For large operations, use files instead of inline code to avoid shell processing overhead.

## Summary

The Python CLI provides flexible ways to execute code in Binary Ninja's context:
- Use **inline** for quick one-liners
- Use **files** for complex scripts (recommended)
- Use **stdin** for generated code or pipelines
- Use **--json** for programmatic access
- No escaping needed with files or stdin!

This makes it easy to integrate Binary Ninja analysis into larger workflows and automation pipelines.