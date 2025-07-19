# Binary Ninja MCP Scripts

This directory contains shared utility scripts for Binary Ninja that can be used across different plugins and reverse engineering projects.

## Available Scripts

### binja-cli.py
Command-line interface for interacting with Binary Ninja MCP server. Provides access to Binary Ninja functionality from the terminal.

**Usage:**
```bash
./binja-cli.py --help
./binja-cli.py status
./binja-cli.py decompile 0x401000
./binja-cli.py python -c "print(bv.functions)"
```

**Note:** When checking how Binary Ninja Python API functions should work, reference the `binaryninja-api` directory for the actual implementation details and correct usage patterns.

### binja-restart.py
Advanced Binary Ninja restart utility with monitoring capabilities. Gracefully restarts Binary Ninja, optionally loading specified files.

**Usage:**
```bash
./binja-restart.py --help
./binja-restart.py  # Basic restart
./binja-restart.py /path/to/binary  # Restart and load file
./binja-restart.py --force  # Force kill without graceful quit
```

## Using These Scripts in Other Projects

These scripts are designed to be self-contained and can be easily integrated into other projects via symlinks:

### For Binary Ninja Plugins
```bash
cd /path/to/your/plugin/scripts
ln -s ../../binary_ninja_mcp/scripts/binja-cli.py .
ln -s ../../binary_ninja_mcp/scripts/binja-restart.py .
```

### For External Projects
You can symlink the entire scripts directory:
```bash
cd /path/to/your/project
ln -s "/Users/mblsha/Library/Application Support/Binary Ninja/plugins/binary_ninja_mcp/scripts" .
```

Or individual scripts:
```bash
cd /path/to/your/project
ln -s "/Users/mblsha/Library/Application Support/Binary Ninja/plugins/binary_ninja_mcp/scripts/binja-cli.py" .
```

## Adding New Scripts

When adding new scripts to this collection:
1. Use the `binja-` prefix for consistency
2. Make scripts self-contained (no dependencies on parent modules)
3. Include proper shebang (`#!/usr/bin/env python3`)
4. Make executable with `chmod +x script-name.py`
5. Add documentation to this README

## Requirements

- Python 3.11+
- plumbum (for CLI utilities)
- requests (for HTTP communication with MCP server)
- Binary Ninja installation (for restart script)