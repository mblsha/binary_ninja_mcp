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

- [ ] Common output contract: JSON/NDJSON, file output, text filtering/context,
  optional text spill, unique artifacts, optional tokens, complete pipelines.
- [ ] Linear disassembly by count/exclusive end, independent of function/IL.
- [ ] Selective multi-function bundles with one pinned target and stable envelope.
- [ ] Compact function info, optional locals; scoped offline schema and doctor.
- [ ] Explicit Python code/script sources and compatible `py` alias.
- [x] Installed `binja-cli` entry point and wheel/install smoke tests. The
  `binja-mcp` alias and source wrapper use the same `binja_cli.cli` module.
  Verified a non-editable wheel in a fresh environment outside the checkout;
  neither the plugin nor the Binary Ninja SDK is imported by the client.

Packaging checkpoint: 201 offline tests passed (19 live tests deselected,
8 subtests passed), wheel build and install smoke passed, Ruff lint passed.

## Pass 3: analysis breadth

- [ ] Ambiguity-aware identifiers and interior addresses/symbol offsets.
- [ ] IL levels/SSA, typed reads, incoming/outgoing and field references.
- [ ] Callsites and bounded text/constant searches with completeness metadata.
- [ ] Stable local-variable IDs and structure-field edits using transactions.
- [ ] Documentation and CLI ergonomics aligned with actual installed commands.

## Verification precautions

The Mac was locked during initial GUI inspection. The HTTP service is reachable
outside the sandbox and has three unrelated user databases open, one analyzing.
Do not restart it or mutate those databases for tests. Inspect the GUI once
unlocked and use isolated, disposable in-memory test views. New server modules
must be loaded before testing their behavior; old endpoints are not evidence
that the edited source is running.
