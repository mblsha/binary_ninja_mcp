# bn 0.15.0 adaptation checkpoints

Goal: adapt the useful correctness, workflow and analysis features from
`banteg/bn` v0.15.0 while preserving multi-instance HTTP routing, explicit
targets, GUI workflows, analysis-skip protection, annotation archives and the
interactive Python environment. Check in tested, coherent milestones; do not
save user databases as a side effect of verification.

## Baseline

- `6d03695`: existing analysis-skip guard and long-inline-Python handling,
  committed separately with user approval. Offline baseline: 160 passed,
  19 live tests deselected, 8 subtests passed.

## Pass 1: correctness

- [x] Complete Python container serialization and real worker tracebacks.
  Existing string-key `type`/`items` envelopes remain; non-string dictionaries
  use `entries` to prevent key collisions. Cycles have explicit path references.
  Execution responses advertise `serialization_version: 2`.
- [x] Shared scoped undo boundary for signatures, renames, comments, type
  declarations and local-variable changes. Missing undo support fails closed;
  rollback errors must report unknown live state, never successful restoration.
- [x] Signature preview applies/verifies/reverts; dry-run remains parse-only.
  Invalid preview/wait/verify combinations fail before mutation.
- [x] Parse variable types before assignment; reject local edits on skipped
  functions before inspecting their variables.
- [x] Reject non-function signature declarations before assignment; align
  function-comment deletion with the storage used by setting and reading.
- [ ] Complete live GUI scratch-view verification after application inspection.

Offline correctness checkpoint: 198 tests passed, 19 live tests deselected,
8 subtests passed. Pinned Ruff lint and Unicode checks pass; changed Python
files pass formatting. The repository-wide format check reports three existing
unrelated files: `plugin/automation/open_file.py`,
`plugin/core/annotation_archive.py`, and
`test_open_file_existing_database_unit.py`. These checks do not substitute for
real-GUI or cross-platform verification.

## Pass 2: daily workflow

- [x] Common output contract: JSON/NDJSON, file output, text filtering/context,
  optional text spill, unique artifacts, optional tokens, complete pipelines.
- [x] Linear disassembly by count/exclusive end, independent of function/IL.
- [x] Selective multi-function bundles with one pinned target and stable envelope.
- [x] Compact function info, optional locals.
- [x] Scoped offline schema and doctor with loaded-source and capability checks.
- [x] Explicit Python code/script sources and compatible `py` alias.
- [x] Installed `binja-cli` entry point and wheel/install smoke tests. The
  `binja-mcp` alias and source wrapper use the same `binja_cli.cli` module.
  Verified a non-editable wheel in a fresh environment outside the checkout;
  neither the plugin nor the Binary Ninja SDK is imported by the client.

Packaging checkpoint: 201 offline tests passed (19 live tests deselected,
8 subtests passed), wheel build and install smoke passed, Ruff lint passed.

Safety-sensitive mutation/decompilation endpoint contracts are now version 2.
This prevents an older server from ignoring `preview` or analysis-skip guards.
Update/reload the client and plugin together; doctor identifies older loaded
code and import-time source fingerprints that no longer match disk.

Output/schema checkpoint: 270 offline tests passed (19 live tests deselected,
8 subtests passed). The current wheel passes installed help, aliases, offline
schema and file-output smoke checks outside the checkout. Ruff lint, changed-file
formatting and Unicode checks pass. A focused three-platform CLI CI job is now
configured; its remote jobs have not been run or claimed passing. Read-only
`doctor` against the running server correctly reports that its loaded code
predates capability diagnostics; no plugin reload or database mutation occurred.
Safety-sensitive requests additionally preflight the routed server's loaded
capability, so a partially reloaded version registry cannot make an old handler
silently accept a preview or omit analysis-skip protection.

## Pass 3: analysis breadth

Read-interface checkpoint: 307 offline tests passed (19 live tests deselected,
8 subtests passed), including decoder/bundle/identifier and scoped-schema tests.
The rebuilt wheel passes installed `disasm`, `info`, and `bundle` schema smoke
checks outside the checkout. Ruff lint, changed-file formatting, Unicode safety
and `git diff --check` pass. GUI inspection was retried and the Mac is still
locked; the running plugin has not been reloaded and no open database was changed.

- [x] Ambiguity-aware identifiers and interior addresses/symbol offsets for new
  read interfaces; legacy mutation resolution deliberately remains unchanged.
- [ ] IL levels/SSA, typed reads, incoming/outgoing and field references.
- [ ] Callsites and bounded text/constant searches with completeness metadata.
- [ ] Stable local-variable IDs and structure-field edits using transactions.
- [ ] Documentation and CLI ergonomics aligned with actual installed commands.
- [ ] Resolve the three pre-existing format-only failures separately before
  the final repository-wide CI-equivalent validation.

## Verification precautions

Read-interface details and limits are documented in [analysis-reads.md](analysis-reads.md).
The decoder/compact-info/bundle interfaces have offline coverage for ranges,
architecture selection, ambiguity, skip-state property traps, selective work,
alias deduplication, per-section failures and a shared cooperative time budget.
Real Binary Ninja verification remains outstanding; fake-SDK tests are not
presented as evidence of live integration.

The Mac was locked during initial GUI inspection. The HTTP service is reachable
outside the sandbox and has three unrelated user databases open, one analyzing.
Do not restart it or mutate those databases for tests. Inspect the GUI once
unlocked and use isolated, disposable in-memory test views. New server modules
must be loaded before testing their behavior; old endpoints are not evidence
that the edited source is running.
