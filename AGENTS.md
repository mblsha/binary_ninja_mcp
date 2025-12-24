# Repository Guidelines

## Project Structure & Module Organization
- `plugin/`: Binary Ninja plugin code (core operations, HTTP API handlers, server).
- `bridge/`: MCP bridge that connects clients (e.g., Claude Desktop) to the plugin server.
- `docs/`: Design and usage documentation for the executor/CLI.
- `scripts/`: Setup helpers (e.g., `scripts/setup_claude_desktop.py`).
- `examples/`: Sample scripts for integrations.
- `images/`: Documentation assets.
- Root test scripts: `test_*.py` (manual/integration helpers).

## Build, Test, and Development Commands
- Create/sync the local environment (creates `.venv/`):
  - `uv sync`
- Run the bridge (Binary Ninja must be running with the MCP server started):
  - `uv run python bridge/binja_mcp_bridge.py`
- macOS setup helper for Claude Desktop:
  - `./scripts/setup_claude_desktop.py`
- There is no build step; the plugin loads directly from the Binary Ninja plugins directory.

## Coding Style & Naming Conventions
- Python code uses 4-space indentation; follow existing formatting and module layout.
- There is no repo-wide linter or formatter configured; keep changes minimal and consistent.
- Prefer descriptive, snake_case names for functions and variables, matching current code.

## Testing Guidelines
- No automated unit test suite is configured; test manually in Binary Ninja.
- Use the root `test_*.py` scripts for ad hoc verification when relevant.
- When adding endpoints, validate with HTTP calls (e.g., `curl http://localhost:9009/status`).

## Commit & Pull Request Guidelines
- No formal commit convention is documented; use clear, imperative summaries (e.g., "Add log filtering options").
- Open PRs against `mblsha/binary_ninja_mcp` (this repo) unless explicitly instructed otherwise.
- PRs should describe Binary Ninja version used, steps to reproduce, and manual test results.
- Include screenshots or GIFs for UI-facing changes (e.g., new CLI output or plugin UI).

## Agent-Specific Notes
- The MCP server must be started from Binary Ninja (`Plugins > MCP Server > Start MCP Server`) before running the bridge.
- Keep compatibility with macOS, Linux, and Windows; avoid platform-specific paths in core code.
