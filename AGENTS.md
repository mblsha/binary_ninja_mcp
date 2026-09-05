# Repository Guidelines

## Project Structure & Module Organization
- `plugin/`: Binary Ninja plugin code (core operations, HTTP API handlers, server).
- `binja_cli/`: Installed SDK-free CLI; `shared/`: client/server contracts.
- `docs/`: Design and usage documentation for the executor/CLI.
- `scripts/`: CLI and local utility scripts.
- `examples/`: Sample scripts for integrations.
- `images/`: Documentation assets.
- Root `test_*.py`: pytest unit and opt-in live integration tests.

## Build, Test, and Development Commands
- Create/sync the local environment (creates `.venv/`):
  - `uv sync`
- There is no build step; the plugin loads directly from the Binary Ninja plugins directory.
- Build the separately installed CLI wheel with `uv build`; install with `uv tool install .`.
- Run offline tests with `uv run --frozen --extra search python -m pytest -q -m 'not binja'`.
- Run lint/format gates with `uv tool run ruff@0.14.10 check .` and `uv tool run ruff@0.14.10 format --check .`.
- Run `uv run --frozen python scripts/check_unicode_safety.py .`.

## Coding Style & Naming Conventions
- Python code uses 4-space indentation; follow existing formatting and module layout.
- Ruff settings are in `pyproject.toml`; keep changes minimal and consistent.
- Prefer descriptive, snake_case names for functions and variables, matching current code.

## Testing Guidelines
- Add SDK-free tests for contracts and failure paths; mark real GUI tests `binja`.
- Real GUI verification complements mocks; inspect the application before live work.
- Register endpoints in `shared/endpoints_manifest.py`, version safety-sensitive changes,
  and test loaded-capability checks. `curl http://localhost:9009/status` is a liveness
  check only, not proof that updated source is loaded; inspect `binja-cli doctor`.
- Use disposable scratch data and explicit view IDs. Do not save unrelated databases.

## Commit & Pull Request Guidelines
- No formal commit convention is documented; use clear, imperative summaries (e.g., "Add log filtering options").
- Open PRs against `mblsha/binary_ninja_mcp` (this repo) unless explicitly instructed otherwise.
- PRs should describe Binary Ninja version used, steps to reproduce, and manual test results.
- Include screenshots or GIFs for UI-facing changes (e.g., new CLI output or plugin UI).

## Agent-Specific Notes
- Live commands require the custom HTTP server in Binary Ninja (auto-start is configurable;
  manual menu: `Plugins > MCP Server > Start MCP Server`). `schema` and help work offline.
- `BinaryView.save(...)` is intentionally monkey-patched by the plugin to raise at runtime. Do not remove this guard; use `bv.save_auto_snapshot()` for existing BNDBs and `bv.create_database(path)` for new BNDBs.
- Treat macOS and Linux as required target platforms for fixes and new features; do not ship a solution that only works on one of them unless explicitly scoped that way.
- This project runs against the GUI build of Binary Ninja, not just headless APIs. If automation or startup appears stalled, inspect the live Binary Ninja UI state first and unblock the app before adding workarounds.
- Keep compatibility with macOS, Linux, and Windows; avoid platform-specific paths in core code.
