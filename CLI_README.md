# Binary Ninja CLI workflow

Install the GUI plugin and client separately using the [README](README.md).
`binja-cli` is the installed command, `binja-mcp` its compatible alias, and
`uv run python scripts/binja-cli.py` the source wrapper. Help and `schema` work
offline; live commands require the custom plugin's HTTP server.

## Select first

```sh
binja-cli doctor
binja-cli views
binja-cli --view-id VIEW_ID resolve-target
binja-cli --view-id VIEW_ID functions --limit 50 --json
```

Replace `VIEW_ID` with the returned process-qualified ID. Auto-discovery spans
local instances; scoped discovery requests require an explicit ID when views
exist. A filename/path selector is also available, and combining it with an ID
adds a consistency check. Explicit targeting is strict by default. Use
`--allow-target-fallback` only when best-effort fallback is genuinely intended.
`--server URL` selects a specific custom HTTP server, not an official MCP URL.

## Command map

All scoped commands below use `binja-cli --view-id VIEW_ID` before the shown
command. Use `binja-cli schema COMMAND...` for exact defaults/options.

| Work | Commands and key distinctions |
| --- | --- |
| Inventory | `functions --search NAME --offset 0 --limit 100`, `imports`, `exports`; `exports` is the legacy non-import/non-external symbol inventory, not a strict loader export table. |
| Function reads | `info FUNCTION [--locals]`, `decompile FUNCTION`, legacy whole-function `assembly FUNCTION`. |
| Instruction reads | `disasm IDENTIFIER --count N` or exclusive `--end ADDRESS`; `--arch` overrides architecture. No function/IL required. |
| IL | `il FUNCTION --level mlil [--ssa]`; levels are hlil/mlil/llil; `--view` is a level alias, not a target selector. |
| Memory | `read ADDRESS --type u32 --count 8 --endian little`; scalar count means elements, cstr count is a byte bound. |
| References | Legacy `refs FUNCTION` remains incoming code refs; `xrefs IDENTIFIER` adds incoming code/data, `xrefs Type.field --field` field refs, `refs-from FUNCTION` is outgoing. |
| Bundles | `bundle F1 F2 --include decompile,comments,xrefs`; default decompile/disasm/refs_from; always a functions array with partial errors. |
| Search | `search text QUERY` or `search constant VALUE`; repeat `--within FUNCTION`, choose `--level`, `--max-results`, `--time-budget`. Regex requires the optional server-side engine. |
| Calls | `callsites CALLEE --within CALLER --context 3`; direct calls only; `--include-tailcalls`, `--no-hlil` available. |
| Annotations | `rename function OLD NEW`, `rename data ADDRESS NEW`, `comment ADDRESS TEXT`, `comment FUNCTION TEXT --function`, `comment TARGET --delete`. |
| Signatures | `signature FUNCTION --file declaration.c`; `--dry-run` parses only, `--preview` verifies undo for existing user signatures, `--apply-name` explicitly changes the name. |
| Reanalysis | `reanalyze FUNCTION` explicitly requests function reanalysis; no implicit skip clearing. |
| Locals | `locals list FUNCTION`, `locals rename FUNCTION ID NAME`, `locals retype FUNCTION ID TYPE`; edits accept `--preview`. |
| Types | `type NAME`, `type --define 'struct Point { int x; int y; };'`; `struct show NAME`, `struct field set/rename/delete`; field edits accept `--preview`. |
| Python | `python`/`py --code CODE`, `--script FILE`, `--stdin`, interactive `-i`, completion `-c`. |
| Persistence export | `annotations export /absolute/path/out.json` writes paired JSON/BNTL annotations; not a BNDB save or an implemented restore command. |

See [analysis reads](docs/analysis-reads.md), [annotation edits](docs/annotation-edits.md),
and [Python](docs/PYTHON_CLI_GUIDE.md) for detailed contracts and limits.

## Arguments and output

Common output flags work before/after commands: `--json`/`-j`, `--format
text|json|ndjson`, `--out`, `--overwrite-output`, `--match`, `--before`, `--after`,
`--spill`, `--no-spill`, `--tokens`. `--` ends option parsing/normalization.
Root target/connection flags still precede the command. Short flags are scoped:
root `-s` is server, `functions -s` searches, root `-t` is HTTP timeout, `read -t`
is a value type, and `open -t` is a BinaryView type. `python -c` is completion.

JSON/NDJSON goes to stdout in full by default. Text spills above 40,000 UTF-8
bytes; `--no-spill` preserves full text stdout. NDJSON splits top-level arrays
only; responses and rendering are buffered. `--match` and before/after operate
on text lines, not instructions or JSON records. Invalid output combinations
are rejected before sending an operation. File delivery can still fail after
a committed mutation; that does not undo the change.

`--out PATH` is client-side and no-clobber unless `--overwrite-output` is given.
This is distinct from structure `--overwrite` and annotation-export `--force`.
See [output and upgrade safety](docs/cli-output.md).

Root HTTP timeout defaults to 120 seconds, connection timeout to 5; override with
`--request-timeout`, `--connect-timeout`, `BINJA_CLI_TIMEOUT` or
`BINJA_CLI_CONNECT_TIMEOUT`. Signature/local/structure workflows allow 1800
seconds for analysis. Search budgets are cooperative between SDK calls; they
cannot interrupt a blocking native call. A timeout is not cancellation.

## Signatures and saves

Use declaration files or quoted stdin for Binary Ninja backtick-qualified C++
names so shell command substitution cannot execute them. `--apply-name` is
required to adopt the declaration's name. Native inference such as unspecified
purity is normalized for verification, but explicit attributes still matter.

`--preview` is an actual temporary edit. Previously automatic signatures are
refused before mutation because the tested SDK's undo leaves their user-type
flag set; `--dry-run` remains available. All supported signature previews check
observed restoration. `state_unknown` requires inspection, not a blind retry.

Never call `bv.save(...)`; the plugin blocks that raw-byte API. A mutation commit
does not persist a BNDB. Only intentionally authorized database saving should use
`save_auto_snapshot()` for an existing BNDB or `create_database(path)` for a new
one. Raw Python does not inherit the built-in mutation/rollback guarantees.

## GUI and diagnostics

```sh
binja-cli open /path/to/input --existing-database yes
binja-cli open /path/to/input --view-type Mapped --platform x86_16
binja-cli open /path/to/input --inspect-only
binja-cli close --view-id VIEW_ID --inspect-only
binja-cli quit --inspect-only
binja-cli statusbar --all
binja-cli logs --errors --count 50
binja-cli logs --search failed --stats
```

`open` surfaces sibling-database yes/no/cancel choices explicitly, automates
Open with Options, and verifies target registration. `--wait-open-target` and
`--wait-analysis` control waiting. Automatic launch preserves existing instances;
`BINJA_FORCE_RESTART_ON_OPEN=1` deliberately changes that policy. Do not enable
it as a generic response to a slow analysis/save.

`close` selects tabs using its own command-local `--view-id`/`--filename`, or
`--all` with exclusions. `--decision save|dont-save|cancel|auto` controls dirty
state handling. `quit` uses a similar policy: auto saves an existing/sibling BNDB
and otherwise discards. Both are consequential operations, not liveness checks.
Use `--inspect-only` first when uncertain; `--mark-dirty` is a test-only mutation.

Logs support count/level/search/errors/warnings/stats and deliberate `--clear`.
Selected legacy commands also probe newly emitted errors; root `--no-auto-errors`
disables that reporting, `--fail-on-new-errors` makes detected new errors fail.

## Portable annotation archives

```sh
binja-cli --view-id VIEW_ID --request-timeout 900 annotations export \
  /absolute/path/source.annotations.json --source-id 'git-sha1:BLOB_ID'
```

The JSON archive and `.types.bntl` companion are written in the Binary Ninja
process's filesystem. Keep both. They include user symbols/comments, named and
annotated native types, user variables/data, tags, source geometry, addresses and
RVAs. They exclude analysis caches, undo history, binary patches, instruction
highlights and user segment/section changes. Export does not save the BNDB.

Existing paths are refused without `--force`; `--type-library` overrides the
companion path. `--include-unannotated-function-types` can substantially enlarge
archives. Architectureless Raw views are refused because BNTL needs an
architecture. Native annotations remain distinct from read-only function bundles.

## Troubleshooting

Use `status` for reachability, `views`/`resolve-target` for selection, and `doctor`
for loaded code/capability compatibility. Reload matching client/plugin modules
after updates; listener restart is not source reload. Skipped-analysis errors
must be resolved by an explicit policy decision; do not probe IL first.
