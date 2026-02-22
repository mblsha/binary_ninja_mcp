# Binary Ninja MCP CLI

A command-line interface for interacting with the Binary Ninja MCP server.

## IMPORTANT: Don’t call `bv.save(...)` from the CLI

Never call `bv.save(...)` from the CLI unless you have explicit user permission.
Saving writes the `.bndb` to disk and is not always safe/desirable during automation.

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

# Open a file (auto-resolve "Open with Options" dialog)
./cli.py open /path/to/binary
./cli.py open /path/to/binary --view-type Mapped --platform x86_16

# Close Binary Ninja and auto-answer save confirmation dialogs
./cli.py quit
./cli.py quit --decision auto --mark-dirty

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

# Increase HTTP timeout for expensive operations (or set BINJA_CLI_TIMEOUT)
./cli.py --request-timeout 60 decompile main

# Target a specific already-open BinaryView when multiple binaries are open
./cli.py --filename /path/to/primary.bin functions --limit 20
./cli.py --filename secondary.bin decompile process_shared_request

# Discover loaded views and pick an explicit view id
./cli.py views
./cli.py --json views | jq '.views[] | {view_id, basename, architecture, analysis_status}'
./cli.py --view-id 202 --filename secondary.bin python "print(hex(here))"

# Targeting is strict by default when --filename/--view-id is used
./cli.py --filename /path/to/primary.bin --strict-target decompile init_hardware

# Opt into legacy best-effort fallback behavior
./cli.py --filename /path/to/primary.bin --allow-target-fallback decompile init_hardware
```

### Help

```bash
# General help
./cli.py --help

# Command-specific help
./cli.py functions --help
./cli.py logs --help
./cli.py open --help
```

### Open Dialog Automation

Use `open` to make file-opening automation reproducible from the CLI. It inspects
current UI state and does the right thing:

- If an **Open with Options** dialog is visible:
  - optional `--view-type` and `--platform` are applied when matching controls are found;
  - `Open` is clicked automatically (unless `--no-click` or `--inspect-only` is set).
- If no dialog is visible:
  - uses the UI context open flow and confirms the file appears in `views`.
  - if the target is not confirmed, the command returns a structured error.
- If MCP server is not reachable on Linux:
  - automatically launches Binary Ninja with Wayland defaults and retries;
  - prints a clear startup error (and launch log path) if startup fails.
- `open` is UI-only by design:
  - non-UI open modes are intentionally unsupported.
  - use `--wait-open-target` and `--wait-analysis` for reliability.

Examples:

```bash
# Typical UI-driven open with explicit platform/view
./cli.py open /path/to/town_mcga.bin --view-type Mapped --platform x86_16

# Confirm target registration in /views
./cli.py open /path/to/town_mcga.bin --wait-open-target 8

# Wait for analysis after target confirmation
./cli.py open /path/to/town_mcga.bin --wait-analysis --analysis-timeout 180

# Inspect state only (no click/load side effects)
./cli.py open /path/to/town_mcga.bin --inspect-only

# Configure fields but don't click Open
./cli.py open /path/to/town_mcga.bin --view-type Raw --platform x86 --no-click

# JSON output for scripting
./cli.py --json open /path/to/town_mcga.bin --platform x86_16
```

### Quit Dialog Automation

Use `quit` to close Binary Ninja windows and handle save-confirmation dialogs
without getting stuck in modal prompts.

Default `--decision auto` policy:

- choose `save` when the loaded file is `.bndb` or a sibling `<file>.bndb`
  already exists
- choose `dont-save` otherwise
- when policy resolves to `save`, the CLI pre-saves the current database via
  Binary Ninja API before close to avoid losing edits on abrupt UI shutdown

Examples:

```bash
# Auto policy (recommended)
./cli.py quit

# Force specific behavior
./cli.py quit --decision dont-save
./cli.py quit --decision save

# Test dialog handling by forcing dirty state first
./cli.py quit --mark-dirty

# Inspect policy/dialog state only
./cli.py quit --inspect-only

# Ask app to exit after dialog handling (best-effort)
./cli.py quit --quit-app --quit-delay-ms 500

# Script-friendly structured output
./cli.py --json quit
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

### Working with Comments

```bash
# Add a comment at an address (works for any address, not just functions)
./cli.py python "bv.set_comment_at(0xC0074, 'Initializes IMR')"

# Gotcha: comments can be function-local.
# - `bv.get_comment_at(addr)` only returns “global” address comments.
# - For comments inside a function, Binary Ninja commonly stores them on the function:
#   `f.get_comment_at(addr)` (and `f.set_comment_at(addr, ...)`).
./cli.py python "
addr = 0xC0074
f = bv.get_functions_containing(addr)[0]
print('func comment:', f.get_comment_at(addr))
print('global comment:', bv.get_comment_at(addr))
"

# Note: for addresses that are not part of any function, Binary Ninja may not display
# the comment in views/listings until the address has a defined item (e.g., a data var,
# a symbol, or a user-created function) at that location.
./cli.py python "
from binaryninja import Symbol, SymbolType, Type
addr = 0x132
bv.define_data_var(addr, Type.int(1, False))              # define a byte
bv.define_user_symbol(Symbol(SymbolType.DataSymbol, addr, 'a'))  # name it
bv.set_comment_at(addr, 'Keyboard short-repeat reload constant')
"

# Batch apply from JSON (format: {'0xC0074': 'comment', ...})
./cli.py python "
import json
data = json.load(open('/path/to/comments.json'))
for addr_str, desc in data.items():
    if desc:
        bv.set_comment_at(int(addr_str, 16), desc)
"

# Read comment at address
./cli.py python "print(bv.get_comment_at(0xC0074))"
```

- `bv.set_comment_at(addr, text)` - Set comment at any address
- `bv.get_comment_at(addr)` - Get comment (returns None if absent)

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
