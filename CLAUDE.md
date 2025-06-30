# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Binary Ninja plugin that exposes binary analysis functionality through MCP (Model Context Protocol). It consists of two main components:
- **Plugin** (`/plugin/`): Runs inside Binary Ninja, provides HTTP API on localhost:9009
- **Bridge** (`/bridge/`): Connects MCP clients to the Binary Ninja HTTP server

## Development Commands

```bash
# Set up development environment
python3 -m venv .venv
source .venv/bin/activate  # macOS/Linux

# Install bridge dependencies
pip install -r bridge/requirements.txt

# Run the MCP bridge (after starting server in Binary Ninja)
python bridge/binja_mcp_bridge.py
```

## Architecture

### Plugin Structure (`/plugin/`)
- `core/`: Core binary operations (`binary_operations.py`), configuration (`config.py`)
  - `log_capture.py`: Binary Ninja LogListener implementation
  - `console_capture.py`: ScriptingOutputListener for Python console
- `api/`: HTTP endpoint handlers for each operation
- `server/`: HTTP server implementation (`http_server.py`)
- `utils/`: Helper utilities

### Key Architectural Decisions
1. **Stateful HTTP Server**: Server maintains reference to current binary view
2. **RESTful API**: Simple GET/POST endpoints for all operations
3. **Bridge Pattern**: MCP bridge adapts between protocols without modifying core functionality
4. **In-Memory Log Storage**: Uses thread-safe deques with configurable size limits (default 10k entries)
5. **Listener Pattern**: Captures logs/console at the source, not from UI components

### API Endpoints
All endpoints are on `http://localhost:9009/`:
- `/status` - Check if binary is loaded
- `/methods`, `/functions` - List functions
- `/decompile` - Get decompiled code
- `/assembly` - Get assembly code
- `/renameFunction`, `/retypeVariable`, `/renameVariable` - Modify binary
- `/comment`, `/addressComment` - Manage comments
- Read operations: `/imports`, `/exports`, `/segments`, `/strings`, etc.
- **Log endpoints** (NEW):
  - `/logs` - Get Binary Ninja logs with filtering
  - `/logs/stats` - Get log statistics
  - `/logs/errors`, `/logs/warnings` - Get recent errors/warnings
  - `/logs/clear` - Clear log buffer
- **Console endpoints** (NEW):
  - `/console` - Get Python console output
  - `/console/stats` - Get console statistics
  - `/console/errors` - Get console errors
  - `/console/execute` - Execute Python commands
  - `/console/clear` - Clear console buffer

### Development Notes
- No test suite exists - when adding features, test manually in Binary Ninja
- No linting configuration - follow existing code style
- Plugin loads automatically from Binary Ninja's plugins directory
- Server must be started from Binary Ninja's plugin menu before using bridge
- Binary view state is managed by the HTTP server, not the bridge

### Important Implementation Details

**Log and Console Capture:**
- Uses Binary Ninja's `LogListener` API - do NOT try to redirect stdout/stderr
- Console capture requires `ScriptingOutputListener` - Binary Ninja already redirects Python output
- Both run on separate threads - ensure thread safety with locks
- Console runs on non-main thread - use `mainthread.execute_on_main_thread_and_wait()` for UI operations

**API Design Principles:**
- No direct UI component access - Binary Ninja enforces separation between core and UI
- Use listeners/callbacks for data capture, not UI scraping
- All operations must work in both headless and UI modes

**Error Handling Patterns:**
- Always check if binary is loaded before operations (except `/status`)
- Return available functions/types when something is not found
- Log errors to Binary Ninja console for debugging

**Parameter Handling:**
- Support multiple parameter names (e.g., `name` or `functionName`)
- Accept addresses in multiple formats (hex string `0x...` or decimal)
- Use `parse_int_or_default` for safe integer parsing

### Common Tasks

**Adding a new API endpoint:**
1. Create handler in `/plugin/api/handlers/`
2. Implement operation in `/plugin/core/binary_operations.py` if needed
3. Register endpoint in `/plugin/server/http_server.py`
4. Update bridge's tool definitions in `/bridge/binja_mcp_bridge.py`

**Debugging:**
- Binary Ninja logs: Check Binary Ninja's console/log view
- Bridge logs: Run bridge directly to see stdout/stderr
- HTTP traffic: Server logs requests to Binary Ninja's console

**Version Updates:**
- Update version in `/plugin/plugin.json`
- Update version in git tag for releases

### Testing Considerations

**Current State:**
- No unit tests exist for MCP plugin components
- Binary Ninja API tests exist only for architecture modules
- Manual testing required for all new features

**Testing Challenges:**
- Binary Ninja plugins require a licensed Binary Ninja instance
- Headless testing needs special setup
- Mock objects difficult due to complex Binary Ninja object model

**Recommended Testing Approach:**
1. Test HTTP endpoints with curl/httpie during development
2. Create test binaries for common scenarios
3. Use Binary Ninja's scripting console for component testing
4. Consider integration tests over unit tests

### Platform-Specific Notes

**macOS Users:**
- Can use Peekaboo MCP server for visual debugging (screenshot analysis)
- Combine with binary_ninja_mcp for comprehensive state inspection

**Cross-Platform:**
- All core functionality works on Windows, Linux, and macOS
- Avoid platform-specific features in core implementation
- Use Binary Ninja's APIs for file paths (handles platform differences)