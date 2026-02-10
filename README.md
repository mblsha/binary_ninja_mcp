# Binary Ninja MCP <img src="images/binja.png" height="24" style="margin-left: 5px; vertical-align: middle;">

This repository provides a Binary Ninja plugin that starts a local HTTP server and a CLI to drive analysis from your terminal. The CLI is the preferred interface.

## Quickstart (CLI)

### 0) Clone into your Binary Ninja plugins directory (recommended)

```bash
cd "$HOME/Library/Application Support/Binary Ninja/plugins"  # macOS
git clone https://github.com/mblsha/binary_ninja_mcp.git
cd binary_ninja_mcp
```

On Linux/Windows, use the plugins directory paths listed below.

### 1) Install the Binary Ninja plugin

- Preferred: install via Binary Ninja Plugin Manager (`Plugins → Manage Plugins`).
- Manual: copy (or symlink) this repo into your Binary Ninja plugins directory.
  - macOS: `~/Library/Application Support/Binary Ninja/plugins/`
  - Linux: `~/.binaryninja/plugins/`
  - Windows: `%APPDATA%\\Binary Ninja\\plugins\\`

Restart Binary Ninja after installing.

### 2) Install CLI dependencies (uv)

```bash
uv sync
```

### 3) Start the server in Binary Ninja

1. Open a binary in Binary Ninja and wait for analysis to finish.
2. Start the server: `Plugins → MCP Server → Start MCP Server`

Verify from your terminal:

```bash
uv run python scripts/binja-cli.py status
```

### 4) Use the CLI

```bash
# List functions
uv run python scripts/binja-cli.py functions --limit 50

# Decompile a function
uv run python scripts/binja-cli.py decompile main

# Get annotated assembly
uv run python scripts/binja-cli.py assembly main

# Count functions (via in-process Python)
uv run python scripts/binja-cli.py python "len(list(bv.functions))"

# View recent errors from Binary Ninja logs
uv run python scripts/binja-cli.py logs --errors --count 50

# Open a file and auto-resolve "Open with Options" (set view/platform when needed)
uv run python scripts/binja-cli.py open /path/to/binary --view-type Mapped --platform x86_16

# Close Binary Ninja and auto-answer save confirmation dialogs
uv run python scripts/binja-cli.py quit
```

## Common Tasks

- Rename a function: `uv run python scripts/binja-cli.py rename function <old> <new>`
- Add a comment: `uv run python scripts/binja-cli.py comment <addr> "text"`
- Work in Python: `uv run python scripts/binja-cli.py python -i` (interactive), or `... python -f script.py`
- Open a binary robustly: `uv run python scripts/binja-cli.py open <path> [--view-type Mapped] [--platform x86_16]`
- Close safely without modal prompt stalls: `uv run python scripts/binja-cli.py quit [--decision auto|save|dont-save|cancel]` (auto pre-saves when the loaded target is `.bndb`)

## Troubleshooting

- **Cannot connect to server**: ensure Binary Ninja is running and the server is started; check `uv run python scripts/binja-cli.py --server http://localhost:9009 status`.
- **“No binary loaded”**: open a binary and wait for initial analysis; then re-run `status`.
- **Wrong file when multiple tabs are open**: click the desired Binary Ninja tab and re-run the CLI command.

## Repository Layout

```
binary_ninja_mcp/
├── plugin/    # Binary Ninja plugin (HTTP server + analysis operations)
├── scripts/   # CLI entrypoints (recommended: scripts/binja-cli.py)
├── docs/      # Additional documentation
└── examples/  # Example scripts
```

## Contributing

Open PRs against `mblsha/binary_ninja_mcp`.
