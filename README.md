# Binary Ninja MCP

A Binary Ninja GUI plugin with a local HTTP API and an installed CLI for analysis,
annotations, Python execution and GUI workflows. The CLI is the preferred
interface. Despite the historical name, this is a custom HTTP API, not the
standard MCP protocol or Binary Ninja's separate built-in MCP server.

## Install

Copy or symlink [this repository](https://github.com/mblsha/binary_ninja_mcp)
into your Binary Ninja plugins directory:

- macOS: `~/Library/Application Support/Binary Ninja/plugins/`
- Linux: `~/.binaryninja/plugins/`
- Windows: `%APPDATA%\Binary Ninja\plugins\`

Restart/reload the plugin after installing. Auto-start is configurable; the
manual command is `Plugins > MCP Server > Start MCP Server`.

From the repository directory, install the client separately:

```sh
uv tool install .
binja-cli --help
binja-cli doctor
binja-cli views
```

`binja-mcp` is an executable alias. For development use `uv sync` and
`uv run binja-cli`; `uv run python scripts/binja-cli.py` remains supported.
The client does not require the Binary Ninja SDK. The wheel does not install
the GUI plugin. Python 3.12+ is required for the client; embedded GUI Python is
independent and is reported by `doctor`.

## Analyze an explicit target

Copy a process-qualified `view_id` from `views` and replace `VIEW_ID` below.
Root target options precede the command; common output flags also work after it.

```sh
binja-cli --view-id VIEW_ID functions --search crypt --limit 50
binja-cli --view-id VIEW_ID info main --locals
binja-cli --view-id VIEW_ID decompile main --json
binja-cli --view-id VIEW_ID disasm 'main+0x10' --count 24
binja-cli --view-id VIEW_ID il main --level mlil --ssa
binja-cli --view-id VIEW_ID read 0x1000 --type u32 --count 8 --endian little
binja-cli --view-id VIEW_ID bundle main helper --include decompile,comments,xrefs
binja-cli --view-id VIEW_ID search text malloc --within main
binja-cli --view-id VIEW_ID callsites malloc --context 3
binja-cli schema struct field set
```

## Edit or automate deliberately

```sh
binja-cli --view-id VIEW_ID signature main --file declaration.c --dry-run
binja-cli --view-id VIEW_ID signature main --file declaration.c
binja-cli --view-id VIEW_ID locals list main
binja-cli --view-id VIEW_ID locals rename main VARIABLE_ID input --preview
binja-cli --view-id VIEW_ID struct field set Packet 0x10 count uint32_t --preview
binja-cli --view-id VIEW_ID python --script analysis.py
binja-cli --view-id VIEW_ID annotations export /absolute/path/annotations.json
```

Built-in edits use scoped undo and readback; previews apply temporarily and
verify rollback. Signature previews require an existing user signature because
native undo cannot fully restore an automatic signature's annotation status.
Raw Python is unrestricted in-process execution and is not transaction-wrapped.
None of these edit commands saves a database. `BinaryView.save(...)` is blocked
because it writes raw bytes, not a BNDB save. Use authorized native database
saving only; never remove the guard.

Skipped-analysis functions are not lifted implicitly. Use `disasm` for mapped
bytes without IL. Timeouts do not cancel native analysis or Python workers;
inspect state before retrying a timed-out mutation.

GUI `open`, `close`, `quit`, status-bar inspection, log capture, multi-instance
routing and portable JSON/BNTL annotation export remain available. `close` and
`quit` may save/discard data according to their decision policy—read their help
before use. Automatic launch preserves existing instances by default.

## Guides

- [CLI workflow and command map](CLI_README.md)
- [Analysis reads, searches and callsites](docs/analysis-reads.md)
- [Local and structure annotations](docs/annotation-edits.md)
- [Output, argument placement and upgrade safety](docs/cli-output.md)
- [Python execution guide](docs/PYTHON_CLI_GUIDE.md)
- [bn v0.15.0 adaptation checkpoints](docs/bn-adaptation-plan.md)
- [Original release comparison and CLI ergonomics audit](docs/bn-comparison.md)
- [Implemented adaptations and completion evidence](docs/bn-completion-audit.md)
- [Native verification and known limitations](docs/bn-native-verification.md)
- [Development and tests](AGENTS.md)

If connection fails, check `binja-cli --server http://localhost:9009 status` and
the plugin menu. If updated commands are refused, run `doctor` and reload matching
client/plugin code; a listener restart alone does not reload Python modules.
Open PRs against `mblsha/binary_ninja_mcp`.
