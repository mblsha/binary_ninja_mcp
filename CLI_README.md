# Binary Ninja MCP CLI

A command-line interface for interacting with the Binary Ninja MCP server.

## Installation

```bash
# Create/sync local environment (creates `.venv/`)
uv sync
```

## Usage

The CLI provides a convenient way to interact with the Binary Ninja MCP server from the terminal.

### Basic Commands

```bash
# Check server status
./cli.py status

# List functions
./cli.py functions
./cli.py functions --limit 50
./cli.py functions --search malloc

# Decompile a function
./cli.py decompile main
./cli.py decompile 0x401000

# Get assembly for a function
./cli.py assembly main

# Rename a function
./cli.py rename function old_name new_name

# Add a comment
./cli.py comment 0x401000 "Entry point"
./cli.py comment --function main "Main function"

# Find references to a function
./cli.py refs malloc
```

### Log Management

```bash
# View recent logs
./cli.py logs
./cli.py logs --count 50

# View only errors
./cli.py logs --errors

# View only warnings
./cli.py logs --warnings

# Search logs
./cli.py logs --search "error"

# View log statistics
./cli.py logs --stats

# Clear logs
./cli.py logs --clear
```

### Type Management

```bash
# Get a user-defined type
./cli.py type MyStruct

# Define types from C code
./cli.py type --define "struct Point { int x; int y; };"
```

### Import/Export Analysis

```bash
# List imports
./cli.py imports

# List exports
./cli.py exports
```

### Global Options

```bash
# Use a different server
./cli.py --server http://localhost:8080 status

# Get raw JSON output
./cli.py --json functions

# Verbose mode
./cli.py --verbose decompile main
```

### Help

```bash
# General help
./cli.py --help

# Command-specific help
./cli.py functions --help
./cli.py logs --help
```

## Examples

### Analyzing a Binary

```bash
# Check if a binary is loaded
./cli.py status

# Search for interesting functions
./cli.py functions --search decrypt
./cli.py functions --search auth

# Decompile a function
./cli.py decompile decrypt_data

# Find who calls it
./cli.py refs decrypt_data

# Add analysis notes
./cli.py comment --function decrypt_data "XOR decryption with key at 0x404000"
```

### Debugging Issues

```bash
# Check recent errors
./cli.py logs --errors

# Search for specific issues
./cli.py logs --search "failed to"

# Get detailed log statistics
./cli.py logs --stats
```

### Python Execution

```bash
# Execute Python code - multiple input methods
./cli.py python "print('Hello')"                    # Inline code
./cli.py python script.py                           # From file (auto-detected)
./cli.py python -f script.py                        # From file (explicit)
echo "print('Hi')" | ./cli.py python                # From stdin (piped)
./cli.py python --stdin < script.py                 # From stdin (redirect)

# Complex strings without escaping (use files or stdin)
cat << 'EOF' | ./cli.py python
print('''No escaping needed:
- Quotes: "double" and 'single'
- Paths: C:\Windows\System32
- JSON: {"key": "value"}
''')
EOF

# Interactive Python console
./cli.py python -i

# Code completion
./cli.py python -c "find_f"                         # Shows: find_funcs, find_functions

# With JSON output for automation
./cli.py --json python "{'count': len(list(bv.functions))}"
```

See [Python CLI Guide](docs/PYTHON_CLI_GUIDE.md) for detailed examples.

### Working with Tags

Binary Ninja tags are annotations attached to addresses (e.g., warnings, notes, unimplemented instructions). Access them via the Python interface:

```bash
# Count total tags in the binary
./cli.py python "
total = sum(len(f.tags) for f in bv.functions if hasattr(f, 'tags'))
print(f'Total tags: {total}')
"

# Find tags with specific text (e.g., unimplemented instructions)
./cli.py python "
unimplemented = []
for func in bv.functions:
    if hasattr(func, 'tags'):
        for tag_tuple in func.tags:
            # Tag format: (arch, address, tag_object)
            if len(tag_tuple) >= 3:
                addr, tag_obj = tag_tuple[1], tag_tuple[2]
                tag_text = str(tag_obj)
                if 'unimplemented' in tag_text.lower():
                    unimplemented.append((addr, tag_text, func.name))

print(f'Found {len(unimplemented)} unimplemented instruction tags')
for addr, text, func_name in sorted(unimplemented)[:10]:
    print(f'  0x{addr:X} in {func_name}: {text[:60]}')
"

# Get tags at a specific address
./cli.py python "
addr = 0xC04AE
for func in bv.functions:
    if hasattr(func, 'tags'):
        for tag_tuple in func.tags:
            if len(tag_tuple) >= 3 and tag_tuple[1] == addr:
                print(f'Tag at 0x{addr:X}: {tag_tuple[2]}')
"

# Group tags by type
./cli.py python "
tag_types = {}
for func in bv.functions:
    if hasattr(func, 'tags'):
        for tag_tuple in func.tags:
            if len(tag_tuple) >= 3:
                tag_obj = tag_tuple[2]
                tag_type = tag_obj.type.name if hasattr(tag_obj, 'type') else 'Unknown'
                tag_types[tag_type] = tag_types.get(tag_type, 0) + 1

for tag_type, count in sorted(tag_types.items(), key=lambda x: x[1], reverse=True):
    print(f'{tag_type}: {count}')
"

# Save tags to a file
./cli.py python "
import json
tags_list = []
for func in bv.functions:
    if hasattr(func, 'tags'):
        for tag_tuple in func.tags:
            if len(tag_tuple) >= 3:
                addr = tag_tuple[1]
                tag_text = str(tag_tuple[2])
                tags_list.append({'address': hex(addr), 'tag': tag_text, 'function': func.name})

with open('/tmp/tags.json', 'w') as f:
    json.dump(tags_list, f, indent=2)
print(f'Saved {len(tags_list)} tags to /tmp/tags.json')
"
```

**Tag Structure:**
- Tags are stored per-function in `func.tags`
- Each tag is a tuple: `(architecture, address, tag_object)`
- Access tag text via `str(tag_object)` or `tag_object.data`
- Tag types accessed via `tag_object.type.name`

### Batch Operations

```bash
# Rename multiple functions (using shell)
for i in {1..10}; do
    ./cli.py rename function "sub_${i}" "handler_${i}"
done

# Export all function names
./cli.py --json functions --limit 10000 | jq -r '.functions[]' > all_functions.txt

# Use Python for complex analysis
./cli.py python "
funcs = [f for f in bv.functions if 'crypt' in f.name.lower()]
for f in funcs[:5]:
    print(f'{f.name} at {hex(f.start)}')
"
```

## Server Requirements

The CLI requires the Binary Ninja MCP server to be running. Start it from Binary Ninja:
- Plugins → MCP Server → Start MCP Server

Or with auto-start enabled, it will start automatically when Binary Ninja loads.
