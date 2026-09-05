# bn v0.15.0 adaptation: implemented state and evidence

The original scope is preserved in [the release comparison](bn-comparison.md)
and [the three-pass plan](bn-adaptation-plan.md). This is an adaptation, not a
claim of command-for-command compatibility with `bn`. Upstream is pinned to
v0.15.0, commit `bd910329547303c787a68fcd8309e83ab1be6590`; our pre-work baseline
was `eba26be5468f02287dfa9ce3aaf8e0ffbeff843a`. The separately authorized user
changes were committed as `6d03695` before implementation.

Current inventory: **41 distinct executable command paths plus the `py` alias**
(42 leaf paths), 49 schema nodes including groups/root, and **66 registered HTTP
method/path pairs**. These counts come from the current parser-derived schema
and `shared/endpoints_manifest.py`, not README headings. HTTP-only legacy
operations and arbitrary Python are not counted as named CLI parity.

## Requirement-to-evidence audit

| Requirement | Implemented behavior and authoritative evidence |
| --- | --- |
| Shared rollback | `plugin/core/mutations.py` guards built-in apply/analysis/readback with scoped undo; missing undo fails closed, commit/apply failures attempt rollback, rollback failure reports unknown state. `test_mutations_unit.py`, `test_builtin_mutations_unit.py`, and native injected-comment failure verify the boundary. |
| Signature prevalidation/preview | `plugin/api/endpoints.py` parses before writes, rejects non-function types and invalid flag combinations, preserves parse-only dry-run, explicitly reanalyzes and reads back. Supported previews verify restored name/type/user-type/skip state. Native automatic-type limitation is refused before preview mutation, not hidden. Signature unit tests and native scratch evidence cover both cases. |
| Complete Python results | `python_executor_v2.py` removes silent container/repr truncation, preserves non-string dict keys and cycles explicitly. `test_python_executor_v2_unit.py` plus the native 150-item probe confirm full results. |
| Worker tracebacks | Exceptions capture their executing stack before leaving the worker. Unit tests and a deliberate native ValueError show the source/line/stack, not `NoneType: None`. |
| Validate before side effects | Output options/regex/path conflicts and Python syntax are validated locally; signature/local/field types and selectors are validated before undo/writes. Invalid-input tests assert no request or no undo begins. Output delivery failure after a committed edit retains the result and reports that the edit remains committed. |
| Common output | `binja_cli/output.py` and `arguments.py`: full JSON/NDJSON stdout, bounded optional spill, unique files, text filtering/context, atomic no-clobber client-side output and optional tokens. `test_cli_output_unit.py` covers long output, files, absent tokenizer, context, errors and failed delivery. Output remains buffered. |
| Linear disassembly | `AnalysisOperations.disasm` decodes mapped bytes without IL or a function, respects count/exclusive end, uses explicit architecture or unambiguous native selection, and returns next-address/stopping metadata. Unit decoder boundary tests and native outside-function bytes pass. |
| Selective bundles | A strong resolved view is retained, aliases deduplicated, requested sections only, stable functions-array shape, partial errors/results and shared cooperative budget. `test_analysis_reads_unit.py` checks changed global view, skipped IL, section selection and budget behavior; native multi-function bundle passes. |
| Compact info | Counts by default, optional canonical locals/parameters with IDs; skipped functions avoid variable access and expose omitted counts. Unit property traps and native info/locals checks pass. |
| Offline schema/doctor | Schema derives actual Plumbum switch/default/positional definitions, scoped to multiword paths. Doctor reports loaded capabilities/source hashes/stale bindings, not merely installed disk files. Safety preflight refuses missing/stale modules before dispatch. Schema/safety tests, installed-wheel smoke and real doctor checks provide evidence. |
| Explicit Python sources | `--code`, `--script`, legacy file/positional/stdin, `py` alias, syntax validation and escape hatch are implemented; `-c` remains completion. `test_cli_python_sources_unit.py` and native script execution verify this; interactive context remains supported. |
| Installed CLI | `pyproject.toml` packages SDK-free `binja_cli`/`shared`, both executable aliases and the existing source wrapper. Packaging tests and non-editable wheel smoke outside the checkout verify imports/help/schema/output. The wheel deliberately does not install the GUI plugin. |
| Rich identifiers | Exact names, numeric addresses, interior-function lookup, symbol offsets and explicit ambiguity errors; legacy mutation lookup semantics are retained. Resolver tests include duplicate names, overlap, architecture, exact-name-vs-offset precedence and zero. |
| IL/SSA and memory | All LLIL/MLIL/HLIL + SSA combinations; typed signed/unsigned/float/pointer/C-string reads with explicit limits, endian validation, short-read preservation and tagged nonfinite floats. Primitive unit tests cover all formats; native IL/SSA and explicit-endian read pass. |
| References | Incoming code/data and field refs, outgoing function refs; legacy `refs` stays incoming. Native incoming/outgoing checks and SDK-shaped field-reference tests cover native-shaped records and ambiguity. |
| Callsites/search | Bounded text/constant search with partial results, optional timeout-capable regex, repeated function scope; direct callsites with disassembly, optional HLIL, structural conditions and static continuations. Query tests cover budgets, skipped functions, loops, constants, tails, delay slots and regex timeout; native text/constant/direct-callsite checks pass. |
| Local/structure edits | Full variable IDs and duplicate-name refusal; named/offset fields, explicit overwrite, metadata preservation, union handling, complete recorded layout verification. Apply/rollback failures and restoration are tested. Native local/parameter/structure/union/automatic-type checks pass, with safe rollback on changed local identity. |
| Preserve existing behavior | Source wrapper/alias, strict process/view routing, incoming `refs`, Python completion/persistence, GUI open/close/quit and annotation export remain. Existing unit suites for target IDs, UI adapters, save guard and archives continue in the full offline gate. No destructive live UI or BNDB persistence was exercised. |
| Documentation/check-ins | Current guides use installed commands and explicit targets; source wrapper remains documented. CLI parser examples, links/fences, versions/fork URLs have regression tests. Original author/license preserved. The three old formatting failures have their own AST-identical commit. |

The native verification report is [separately scoped](bn-native-verification.md):
macOS/one GUI SDK release, scratch core operations through the real Python HTTP
executor, not every native HTTP route or every architecture. This complements,
but does not replace, parser/routing/contract tests. Full remote CI and Linux/
Windows GUI execution are not claimed.

## CLI ergonomics versus bn

| Area | Our implemented choice versus bn v0.15.0 |
| --- | --- |
| Entry point | Installed `binja-cli`, alias `binja-mcp`, and source compatibility versus installed `bn`; plugin installation stays separate. |
| Targeting | Process-qualified IDs, discovery across instances and strict routing remain. Output flags normalize before/after commands; target/connection options stay at root to avoid changing `close --view-id` or short-option meanings. bn's `--target` has wider placement and `BN_TARGET`. |
| Vocabulary | Flat `info`, `read`, `bundle`; plural `locals`; `struct field ...`; compare bn's `function info`, `data read`, `bundle function`, singular `local`. Offline multiword `schema` makes either hierarchy discoverable. |
| Direction | `xrefs` is incoming, `refs-from` outgoing; legacy `refs` remains incoming. bn's `refs` is outgoing. This difference is intentional and documented. |
| IL selection | Public `--level`, compatible `--view` alias; target is always `--view-id`/`--filename`, avoiding an overloaded public term. |
| Disassembly/context | Linear `--count`/exclusive `--end`; whole-function `assembly` stays separate. Output `--before`/`--after` always means text lines. We did not copy bn's legacy instruction-window overload. |
| Output | Full structured stdout, optional tokens/spill, `--out`; ours additionally refuses existing output without `--overwrite-output`. Structure `--overwrite` is separate from output replacement. |
| Python | Direct `python`/`py`, explicit code/script and piped stdin; `-c` retains completion. bn also keeps `py exec`; we do not add that redundant nested alias. Our persistent interactive environment remains. |
| Mutations | Parse-only signature `--dry-run` remains distinct from apply/undo `--preview`. Field overlap requires opt-in `--overwrite`; bn uses opt-out `--no-overwrite`. Automatic-signature preview refusal is a native safety constraint, not parse-only preview relabeling. |
| Queries | Repeatable `--within`, explicit `--time-budget` (search default 5 seconds), default 100 results and partial metadata. bn uses 200 results and `--timeout`; its callsite scope-file/static options were not copied. |
| Errors | Unknown options/validation go to stderr with nonzero status; partial analysis results remain available with exit 1. Output path failure cannot be mistaken for mutation rollback. |

## What remains different, intentionally outside this implementation

Both still provide GUI-process analysis, functions/imports, decompilation,
comments/types/renames, Python, explicit selection and structured output. We
retain multi-instance HTTP routing, GUI lifecycle/dialog automation, logs/console
capture, JSON/BNTL annotation export and the save guard. bn retains its Unix
socket/registry transport, packaged plugin/skill installers, no mandatory runtime
dependencies, and a different command hierarchy/selection policy.

The audit did not authorize cloning every upstream command. Not added here:
standalone strings/type inventory commands, address-info/containing commands,
declaration source-directory include handling, disassembly instruction-window
mode, scope files/static-caller mode, sampled mutation decompile diffs, installers,
BN_TARGET, standard MCP, or a different concurrent transport. Existing HTTP or
Python access is not falsely described as first-class CLI parity.

Potential follow-ups worth adapting next are paginated strings/named-type
inventory and declaration source-directory handling. They improve common
workflows without altering routing. A future async job/cancellation model needs
its own design; neither this implementation nor bn's buffered synchronous
requests provide that guarantee. Avoid adopting implicit overwrites, silent
reference-direction changes or mandatory tokenizers.

## Final gates

Final observed local gates on 2026-09-05:

- `uv run --offline --frozen --extra search python -m pytest -q -m 'not binja'`:
  **476 passed, 19 deselected, 8 subtests passed** in 22.52 seconds.
- Pinned Ruff 0.14.10 lint: passed; repository-wide format check: **100 files
  already formatted** (vendored SDK checkout excluded by project configuration).
- `scripts/check_unicode_safety.py .` and `git diff --check`: passed.
- Final wheel built in `/tmp/binja-cli-final-build`; non-editable install in a
  fresh environment outside the checkout passed both executable aliases,
  SDK-free imports, file output, and scoped schemas for every new command lane.
- Native GUI scratch verification: **8/8 groups passed**, plus real 150-item
  serialization and execution-site traceback probes; no database save or
  remaining scratch registration. Detailed limitations are recorded above.
- Documented parser examples, current guide links/fences, fork URLs and matching
  plugin/package/client version metadata have 24 passing regression checks.

Local check-ins preserve the user baseline separately, then correctness,
packaging/output, reads, IL/typed primitives, queries, annotation edits, native
fixes and format-only cleanup. No push, PR, remote CI dispatch or database save
was performed. Remote platform execution is not a completion claim of this
local adaptation goal.
