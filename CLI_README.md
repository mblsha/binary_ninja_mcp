# Binary Ninja MCP CLI

A command-line interface for interacting with the Binary Ninja MCP server.

## IMPORTANT: Don’t call `bv.save(...)` from the CLI

Never call `bv.save(...)` from the CLI unless you have explicit user permission.
The plugin monkey-patches `BinaryView.save(...)` to raise at runtime because this
API writes raw original binary bytes and can corrupt a BNDB when used as a
database save operation. Use `bv.save_auto_snapshot()` for an existing BNDB, or
`bv.create_database(path)` to create a new BNDB.

## Installation

```bash
# Install the client executable; the Binary Ninja plugin is installed separately
uv tool install .
```

Run `binja-cli` from any directory. `binja-mcp` remains an executable alias.
For source development, `uv sync` installs an editable client into `.venv`, and
`uv run binja-cli` or `uv run python scripts/binja-cli.py` runs that implementation.

## Usage

The CLI provides a convenient way to interact with the Binary Ninja MCP server from the terminal.

### Basic Commands

```bash
# Check server status
binja-cli status

# Open a file (surface database choice; auto-resolve "Open with Options")
binja-cli open /path/to/binary
binja-cli open /path/to/binary --view-type Mapped --platform x86_16

# Close Binary Ninja and auto-answer save confirmation dialogs
binja-cli quit
binja-cli quit --decision auto --mark-dirty

# List functions
binja-cli functions
binja-cli functions --limit 50
binja-cli functions --search malloc

# Decompile a function
binja-cli decompile main
binja-cli decompile 0x401000

# Get assembly for a function
binja-cli assembly main

# Rename a function
binja-cli rename function old_name new_name

# Safely change a function signature and wait for per-function reanalysis
binja-cli signature 0x401000 --file declaration.c

# Explicitly reanalyze one function
binja-cli reanalyze 0x401000

# Add a comment
binja-cli comment 0x401000 "Entry point"
binja-cli comment --function main "Main function"

# Find references to a function
binja-cli refs malloc
```

### Log Management

```bash
# View recent logs
binja-cli logs
binja-cli logs --count 50

# View only errors
binja-cli logs --errors

# View only warnings
binja-cli logs --warnings

# Search logs
binja-cli logs --search "error"

# View log statistics
binja-cli logs --stats

# Clear logs
binja-cli logs --clear
```

### Type Management

```bash
# Get a user-defined type
binja-cli type MyStruct

# Define types from C code
binja-cli type --define "struct Point { int x; int y; };"
```

### Function Signatures and Reanalysis

Use `signature` instead of raw Python type assignment. It parses a complete
declaration, applies the function type, explicitly requests per-function
reanalysis, waits for it to finish, and verifies the applied type by reading it
back. `--dry-run` performs only the parse step.

Qualified Binary Ninja names must be backtick-quoted as one identifier. Supply
these declarations via a file or a single-quoted heredoc so the shell does not
execute the backticks:

```bash
binja-cli --filename /absolute/path/to/database.bndb \
  signature 0x6c562 --stdin <<'EOF'
int32_t __convention("default")
`EGiridaOTankFamily_6ba10::state_giridao_cannon_6c562`(
    struct EGiridaOTankFamily_6ba10* this_ @ a6
);
EOF
```

Use `--apply-name` only when the declaration's parsed name should also replace
the existing function name. If Binary Ninja still presents its function-level
Reanalyze action after a manual change, run:

```bash
binja-cli --filename /absolute/path/to/database.bndb reanalyze 0x6c562
```

The corresponding HTTP operations are `POST /function/signature` and
`POST /function/reanalyze`. Signature parser errors are returned as HTTP 400
with `error_code: FUNCTION_SIGNATURE_PARSE_ERROR`.

### Import/Export Analysis

```bash
# List imports
binja-cli imports

# List exports
binja-cli exports
```

### Portable Annotation Archives

Use `annotations export` to serialize portable user annotation state from one
explicitly targeted BinaryView. It writes a readable JSON archive and a native
Binary Ninja type-library companion:

```bash
binja-cli \
  --view-id '<global-view-id>' \
  --filename '/absolute/path/to/source.bndb' \
  --request-timeout 900 \
  annotations export '/absolute/path/to/source.annotations.json' \
  --source-id 'git-sha1:<blob-oid>'
```

The default outputs are:

```text
source.annotations.json
source.annotations.types.bntl
```

The JSON records user symbols, view/function/instruction comments, named user
types, explicit annotated function prototypes, user variables, user-defined
data variables, data/address/function-scoped tags, source geometry, absolute
addresses, and RVAs. The BNTL stores exact native type objects referenced by
the JSON. Keep both files for a full-fidelity restore.

Existing outputs are rejected unless `--force` is supplied. Use
`--type-library /absolute/path/to/output.bntl` to override the companion path.
Use `--include-unannotated-function-types` only when every function type marked
explicit/user by Binary Ninja must be retained; some saved databases mark large
numbers of analyzer-derived prototypes this way, substantially increasing the
archive.

The archive deliberately excludes analysis caches, undo history, binary
patches, instruction highlights, and user segment/section changes. Export is
read-only with respect to the loaded BinaryView and never saves the BNDB.
The target BinaryView must have an architecture; architectureless Raw views
are rejected because Binary Ninja type libraries require an architecture.

The equivalent local HTTP operation is `POST /annotations/export` with an
absolute `output_path`. Optional JSON fields are `type_library_path`,
`overwrite`, `include_unannotated_function_types`, `source_id`,
`source_filename`, `source_size`, and `source_mtime_ns`.

### Global Options

```bash
# Use a different server
binja-cli --server http://localhost:8080 status

# Get raw JSON output
binja-cli --json functions

# Verbose mode
binja-cli --verbose decompile main

# Override HTTP action/read timeout or fast connection timeout
binja-cli --request-timeout 180 --connect-timeout 5 decompile main

# Target a specific already-open BinaryView when multiple binaries are open
binja-cli --filename /path/to/primary.bin functions --limit 20
binja-cli --filename secondary.bin decompile process_shared_request

# Discover loaded views and pick an explicit view id
binja-cli views
binja-cli --json views | jq '.views[] | {view_id, basename, architecture, analysis_status}'
binja-cli --view-id 202 --filename secondary.bin python "print(hex(here))"

# Targeting is strict by default when --filename/--view-id is used
binja-cli --filename /path/to/primary.bin --strict-target decompile init_hardware

# Opt into legacy best-effort fallback behavior
binja-cli --filename /path/to/primary.bin --allow-target-fallback decompile init_hardware
```

### Help

```bash
# General help
binja-cli --help

# Command-specific help
binja-cli functions --help
binja-cli logs --help
binja-cli open --help
```

### Open Dialog Automation

Use `open` to make file-opening automation reproducible from the CLI. It inspects
current UI state and does the right thing:

- If an **Open existing database?** dialog is visible:
  - the command reports the question and available choices instead of silently
    choosing for you;
  - rerun with `--existing-database yes`, `--existing-database no`, or
    `--existing-database cancel` to answer it explicitly.
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
binja-cli open /path/to/town_mcga.bin --view-type Mapped --platform x86_16

# Open the saved database when Binary Ninja finds one beside the input
binja-cli open /path/to/town_mcga.bin --existing-database yes

# Ignore the saved database and analyze the raw input
binja-cli open /path/to/town_mcga.bin --existing-database no

# Confirm target registration in /views
binja-cli open /path/to/town_mcga.bin --wait-open-target 8

# Wait for analysis after target confirmation
binja-cli open /path/to/town_mcga.bin --wait-analysis --analysis-timeout 180

# Inspect state only (no click/load side effects)
binja-cli open /path/to/town_mcga.bin --inspect-only

# Configure fields but don't click Open
binja-cli open /path/to/town_mcga.bin --view-type Raw --platform x86 --no-click

# JSON output for scripting
binja-cli --json open /path/to/town_mcga.bin --platform x86_16
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
binja-cli quit

# Force specific behavior
binja-cli quit --decision dont-save
binja-cli quit --decision save

# Test dialog handling by forcing dirty state first
binja-cli quit --mark-dirty

# Inspect policy/dialog state only
binja-cli quit --inspect-only

# Ask app to exit after dialog handling (best-effort)
binja-cli quit --quit-app --quit-delay-ms 500

# Script-friendly structured output
binja-cli --json quit
```

## Examples

### Analyzing a Binary

```bash
# Check if a binary is loaded
binja-cli status

# Search for interesting functions
binja-cli functions --search decrypt
binja-cli functions --search auth

# Decompile a function
binja-cli decompile decrypt_data

# Find who calls it
binja-cli refs decrypt_data

# Add analysis notes
binja-cli comment --function decrypt_data "XOR decryption with key at 0x404000"
```

### Debugging Issues

```bash
# Check recent errors
binja-cli logs --errors

# Search for specific issues
binja-cli logs --search "failed to"

# Get detailed log statistics
binja-cli logs --stats
```

### Python Execution

```bash
# Execute Python code - multiple input methods
binja-cli python "print('Hello')"                    # Inline code
binja-cli python script.py                           # From file (auto-detected)
binja-cli python -f script.py                        # From file (explicit)
echo "print('Hi')" | binja-cli python                # From stdin (piped)
binja-cli python --stdin < script.py                 # From stdin (redirect)

# Complex strings without escaping (use files or stdin)
cat << 'EOF' | binja-cli python
print('''No escaping needed:
- Quotes: "double" and 'single'
- Paths: C:\Windows\System32
- JSON: {"key": "value"}
''')
EOF

# Interactive Python console
binja-cli python -i

# Code completion
binja-cli python -c "find_f"                         # Shows: find_funcs, find_functions

# With JSON output for automation
binja-cli --json python "{'count': len(list(bv.functions))}"
```

See [Python CLI Guide](docs/PYTHON_CLI_GUIDE.md) for detailed examples.

### Working with Tags

Binary Ninja tags are annotations attached to addresses (e.g., warnings, notes, unimplemented instructions). Access them via the Python interface:

```bash
# Count total tags in the binary
binja-cli python "
total = sum(len(f.tags) for f in bv.functions if hasattr(f, 'tags'))
print(f'Total tags: {total}')
"

# Find tags with specific text (e.g., unimplemented instructions)
binja-cli python "
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
binja-cli python "
addr = 0xC04AE
for func in bv.functions:
    if hasattr(func, 'tags'):
        for tag_tuple in func.tags:
            if len(tag_tuple) >= 3 and tag_tuple[1] == addr:
                print(f'Tag at 0x{addr:X}: {tag_tuple[2]}')
"

# Group tags by type
binja-cli python "
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
binja-cli python "
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
binja-cli python "bv.set_comment_at(0xC0074, 'Initializes IMR')"

# Gotcha: comments can be function-local.
# - `bv.get_comment_at(addr)` only returns “global” address comments.
# - For comments inside a function, Binary Ninja commonly stores them on the function:
#   `f.get_comment_at(addr)` (and `f.set_comment_at(addr, ...)`).
binja-cli python "
addr = 0xC0074
f = bv.get_functions_containing(addr)[0]
print('func comment:', f.get_comment_at(addr))
print('global comment:', bv.get_comment_at(addr))
"

# Note: for addresses that are not part of any function, Binary Ninja may not display
# the comment in views/listings until the address has a defined item (e.g., a data var,
# a symbol, or a user-created function) at that location.
binja-cli python "
from binaryninja import Symbol, SymbolType, Type
addr = 0x132
bv.define_data_var(addr, Type.int(1, False))              # define a byte
bv.define_user_symbol(Symbol(SymbolType.DataSymbol, addr, 'a'))  # name it
bv.set_comment_at(addr, 'Keyboard short-repeat reload constant')
"

# Batch apply from JSON (format: {'0xC0074': 'comment', ...})
binja-cli python "
import json
data = json.load(open('/path/to/comments.json'))
for addr_str, desc in data.items():
    if desc:
        bv.set_comment_at(int(addr_str, 16), desc)
"

# Read comment at address
binja-cli python "print(bv.get_comment_at(0xC0074))"
```

- `bv.set_comment_at(addr, text)` - Set comment at any address
- `bv.get_comment_at(addr)` - Get comment (returns None if absent)

### Batch Operations

```bash
# Rename multiple functions (using shell)
for i in {1..10}; do
    binja-cli rename function "sub_${i}" "handler_${i}"
done

# Export all function names
binja-cli --json functions --limit 10000 | jq -r '.functions[]' > all_functions.txt

# Use Python for complex analysis
binja-cli python "
funcs = [f for f in bv.functions if 'crypt' in f.name.lower()]
for f in funcs[:5]:
    print(f'{f.name} at {hex(f.start)}')
"
```

## Server Requirements

The CLI requires the Binary Ninja MCP server to be running. Start it from Binary Ninja:
- Plugins → MCP Server → Start MCP Server

Or with auto-start enabled, it will start automatically when Binary Ninja loads.
