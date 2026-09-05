# Output, schemas and upgrade safety

`binja-cli` is the installed client. `binja-mcp` and
`uv run python scripts/binja-cli.py` remain compatible entry points.

## Argument placement

Common output flags work before or after the command:

```bash
binja-cli --view-id INSTANCE:VIEW functions --json
binja-cli --format ndjson --view-id INSTANCE:VIEW functions
binja-cli --view-id INSTANCE:VIEW decompile main --match 'return' --before 2 --after 2
```

These flags are `--json`/`-j`, `--format`, `--out`, `--overwrite-output`,
`--match`, `--before`, `--after`, `--spill`, `--no-spill`, and `--tokens`.
Values belonging to other options are never reinterpreted, and `--` ends option
normalization. Other root options, including `--server`, `--view-id`,
`--filename`, and timeouts, still precede the command. Command-local options
retain their meanings: `functions -s` searches; `open -t` chooses a view type;
`close --view-id` selects a tab to close.

## Complete output and artifacts

Text is the usual default; schema and doctor default to JSON. `--format text`
explicitly selects text, and `--json` aliases `--format json`.

- JSON/NDJSON goes to stdout in full by default. No tokenizer is required.
- NDJSON writes one record per top-level array item. An object containing a
  nested array remains one record; this is not an incremental event stream.
- Text exceeding 40,000 UTF-8 bytes spills to a unique file with a maximum
  20-line/2,000-character stdout preview and artifact metadata on stderr.
- `--no-spill` keeps complete text on stdout for pipelines. `--spill` explicitly
  permits artifact envelopes for large JSON/NDJSON too.
- `--out PATH` writes on the **client's** filesystem and prints path, format,
  byte count and SHA-256 metadata. The parent directory must already exist.
  Existing output is not replaced unless `--overwrite-output` is supplied.
  Completed files are installed atomically; concurrent no-clobber writes fail.
- Spills default to the system temporary directory's `binja-cli-output`
  subdirectory. Set `BINJA_CLI_OUTPUT_DIR` for a different retention location.
- `--tokens` adds optional token metadata (stderr for ordinary stdout output).
  Install `binary-ninja-mcp[tokens]` to enable it. Counting failures produce a
  warning, never discarded output.

`--match` is a client-side text regular expression; `--before`/`--after` are
text-line context counts. These flags cannot filter JSON/NDJSON. Regex, format,
context, and output-destination validation occur before command execution.
Filesystem delivery can still fail later; in that case the command result is
reproduced on stdout, the exit status is nonzero, and stderr warns that already
committed live mutations remain committed. File delivery is not an undo action.

## Python sources

```bash
binja-cli --view-id INSTANCE:VIEW py --code 'len(bv.functions)'
binja-cli --view-id INSTANCE:VIEW python --script script.py
binja-cli --view-id INSTANCE:VIEW python --stdin < script.py
```

`python` and `py` are aliases. Positional code/files, automatic piped stdin,
`--file`/`-f`, interactive `-i`, and completion `-c` remain supported. Explicit
`--code` never probes the filesystem. Choose one source or interactive/completion
mode. Code is compiled locally before sending it; if the embedded Python version
supports newer syntax, `--no-syntax-check` defers validation to that interpreter.
Code is never executed on the client. Interactive prompts remain live and do not
support structured output, filtering, artifacts, or token counting.

## Schema and doctor

```bash
binja-cli schema
binja-cli schema signature
binja-cli schema rename function
binja-cli doctor
binja-cli --server http://localhost:9001 doctor
```

Schemas are generated offline from the actual CLI parser, with shared options
defined once and command-specific defaults/positionals. Doctor inspects all
discovered instances (or an explicitly selected server), reports loaded
capabilities, Python/BN versions, and import-time source fingerprints. A disk
change or stale class binding is reported as requiring a reload. An older server
without diagnostics is reported as such, not assumed compatible.

Safety-sensitive mutation endpoints and decompilation now require endpoint API
version 2. Update the client and reload the plugin together. In particular, an
old server must reject a new signature preview request rather than ignore its
preview flag and commit. Restarting only the HTTP listener does not reload
Python modules. Preview uses an undo transaction and never saves a database;
`signature --dry-run` remains parse-only.

The client also verifies the selected server's loaded safety capability before
mutation or decompilation. This catches partial reloads where the version
registry is new but a handler or operations instance still runs old code.
