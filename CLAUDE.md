# Contributor notes

Read [AGENTS.md](AGENTS.md) for repository rules and safety requirements.
This project provides a GUI-process Binary Ninja plugin with a custom HTTP API,
plus an installed terminal client. It is not a standard MCP JSON-RPC server and
is separate from Binary Ninja's built-in MCP server.

## Development

```sh
uv sync
uv run binja-cli --help
uv run binja-cli schema struct field set
uv run --frozen --extra search python -m pytest -q -m 'not binja'
uv tool run ruff@0.14.10 check .
uv tool run ruff@0.14.10 format --check .
uv run --frozen python scripts/check_unicode_safety.py .
uv build
uv run --frozen python scripts/smoke_cli_wheel.py 'dist/*.whl'
```

The wheel contains `binja_cli` and `shared`, not the GUI plugin or SDK.
Install the plugin separately. `uv tool install .` installs `binja-cli` and
`binja-mcp`; the source wrapper `scripts/binja-cli.py` remains supported.

## Architecture and changes

- `plugin/server/http_server.py` dispatches GET/POST requests; the server uses a
  single HTTP request thread. New analysis/edit handlers retain a strong resolved
  view instead of consulting a changing global target during the operation.
- `plugin/core/analysis_operations.py` composes identifier resolution, disassembly,
  IL/SSA, typed memory, references, bundles, searches and callsites.
- `plugin/core/annotation_edits.py` implements stable-ID local and declared-field
  edits. `mutations.py` provides scoped undo coordination for built-in writes.
- `plugin/api/endpoints.py` contains signature and legacy endpoint operations.
- `plugin/core/python_executor_v2.py` provides persistent Python context,
  complete structured serialization and execution-site tracebacks. Arbitrary
  Python does not inherit built-in mutation transactions or cancellation.
- `plugin/automation/` handles GUI workflows. Use main-thread calls for UI work;
  do not run blocking analysis waits on the UI thread.
- `shared/endpoints_manifest.py` is the endpoint inventory; `api_versions.py`
  and `build_info.py` define version/capability checks and loaded-source evidence.
- `binja_cli/cli.py` defines commands; `arguments.py`, `output.py` and `schema.py`
  provide common argument normalization, output delivery and offline discovery.

When adding a command, implement/test its core operation, register its route and
manifest entry, add capability checks where needed, then update CLI/help/schema
tests and installed-wheel smoke coverage. Keep new writes POST-only; existing
GET mutation aliases remain compatible with their version contracts. Do not
invent endpoints from stale documentation: use the manifest and live metadata.

## Safety and verification

Use `views` and a returned process-qualified ID before live scoped operations.
The custom server uses local HTTP (default 9009 plus discovery ports), not the
official Binary Ninja `/mcp` endpoint. Help/schema need no running application.
Run `doctor` after changes; restarting a listener is not a Python module reload.

Never bypass the `BinaryView.save` guard. Database persistence requires explicit
authorization; a successful mutation or undo commit does not save a BNDB.
Do not clear skipped analysis implicitly or fetch IL/variables before checking it.
Preserve the full skipped-address set, not merely a count. Signature preview on
an automatic function is refused due to native undo's user-type promotion;
`--dry-run` is parse-only. See [native verification](docs/bn-native-verification.md).

Offline tests cover mocks/contracts; GUI tests require a licensed running app.
The opt-in `scripts/verify_bn_adaptations_live.py` refuses open user views and
uses disposable in-memory data. Test failures and HTTP timeouts are not authority
to restart a busy application. Do not retry timed-out mutations blindly.

Keep macOS/Linux/Windows compatibility. Remote client CI is configured separately
from live Binary Ninja tests; do not claim unexecuted platform checks passed.
Update version metadata in root `plugin.json`, `pyproject.toml`, and
`shared/build_info.py` together. Preserve original license/author attribution.
PRs target `mblsha/binary_ninja_mcp` unless explicitly directed otherwise.
