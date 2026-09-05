# Binary Ninja CLI comparison — September 5, 2026

This is the **pre-adaptation** release audit, preserved as the original scope and
recommendation baseline. Statements about our missing features describe the
baseline below, not the later check-ins. See [adaptation checkpoints](bn-adaptation-plan.md)
and [completion audit](bn-completion-audit.md) for the implemented state.

The most valuable ideas to adapt from bn 0.15.0 are complete structured output, failure rollback, linear disassembly without function analysis, selective multi-function bundles, and scoped command schemas. Preserve our multi-instance HTTP routing, GUI automation, annotation archives, explicit analysis-skip policy, and interactive Python environment.

## Scope and evidence

- Upstream: banteg/bn v0.15.0, release published September 4, 2026 at 21:22:14 UTC; commit `bd910329547303c787a68fcd8309e83ab1be6590` (also the observed master HEAD). The annotated tag resolves to this commit.
- Earlier upstream baseline: v0.14.1, `6e4e28d053ee1dedaf2beb4fc1611d603819b7ba`. The release changes ten commits, eighteen files, with 732 additions and 733 deletions.
- Ours: version 0.2.8, HEAD `eba26be5468f02287dfa9ce3aaf8e0ffbeff843a`, plus existing changes in six files. Those changes add explicit decompilation opt-in for skipped analysis and handle ENAMETOOLONG when probing inline Python as a potential path.
- Inventory: ours has 22 executable leaf commands and 54 registered HTTP method/path pairs (including compatibility aliases); bn has 41 distinct executable command paths plus the compatible `py exec` alias.
- Inspected source, the complete CLI parsers, release changes, endpoint registry, output handling, mutation handling, Python execution, target selection, packaging, and CI. Ran offline suites and isolated parser/serializer probes. No plugin was installed into Binary Ninja and no live database was modified.
- Ours: 160 tests passed, 19 live-BN tests deselected, 8 subtests passed. Upstream: initial run had 131 passes and five sandbox-denied socket tests; the entire ten-test transport module then passed outside the sandbox, covering all 136 distinct tests. Tests used the existing Python 3.12 environment and PYTHONPATH for upstream, without modifying either lockfile. These are offline checks, not evidence of GUI behavior across all supported platforms.

Primary upstream references: [release](https://github.com/banteg/bn/releases/tag/v0.15.0), [release diff](https://github.com/banteg/bn/compare/v0.14.1...v0.15.0), [CLI](https://github.com/banteg/bn/blob/bd910329547303c787a68fcd8309e83ab1be6590/src/bn/cli.py), [bridge](https://github.com/banteg/bn/blob/bd910329547303c787a68fcd8309e83ab1be6590/src/bn/assets/plugin/bn_agent_bridge/bridge.py), [output](https://github.com/banteg/bn/blob/bd910329547303c787a68fcd8309e83ab1be6590/src/bn/output.py), [Python execution](https://github.com/banteg/bn/blob/bd910329547303c787a68fcd8309e83ab1be6590/src/bn/assets/plugin/bn_agent_bridge/python_exec.py).

Primary local references: [CLI](<../binja_cli/cli.py>), [endpoint registry](<../shared/endpoints_manifest.py>), [signature implementation](<../plugin/api/endpoints.py:307>), [Python executor](<../plugin/core/python_executor_v2.py>), [annotation exporter](<../plugin/core/annotation_archive.py>).

## What changed since the first audit

| Change | bn 0.14.1 | bn 0.15.0 | Adaptation judgment |
|---|---|---|---|
| Structured output | Large JSON/NDJSON could spill automatically, leaving stdout empty | Complete JSON/NDJSON on stdout by default; `--spill` opts into artifact envelopes | Adopt the new policy, retaining our `--json` alias |
| Text spill | 10,000-token threshold, mandatory tokenizer | 40,000-byte threshold, up to 20 preview lines/2,000 characters; file metadata on stderr | Adopt optional text spill and `--no-spill` for pipelines |
| Token counting | Mandatory dependency and work for ordinary output | Optional `tokens` package extra and `--tokens`; counting failures become artifact warnings | Keep normal output independent of tokenizers |
| Spill filenames | Second-resolution names could collide | Unique mkstemp filenames | Use unique files; retain our stronger archive no-clobber/rollback behavior |
| Function info | Always expands variables | Counts by default; `--locals` expands details | Adopt compact defaults when adding function info |
| Bundles | One function, all sections, duplicated HLIL | Multiple identifiers, selectable sections, partial successes/errors, compact defaults, no duplicate HLIL | High-value addition for our repeated analysis workflows |
| Disassembly | Analyzed functions and function-relative instruction windows | Linear `--count` or exclusive `--end`, explicit stopping metadata, no function required | High-value addition for firmware, gaps, and deliberately skipped functions |
| Window flags | `--before/--after` overloaded | Explicit `--before-instructions/--after-instructions`; legacy aliases remain without `--match` | Use explicit names in our new surface |
| Python | Required `py exec` and explicit source selection | Direct `py`, automatic piped stdin, local syntax validation; `py exec` remains compatible | We already have automatic stdin; adopt explicit source aliases and validation |
| Schemas | Entire command tree, repeated common options | Scoped command paths, shared option definitions and command defaults | Adopt for discoverability and smaller agent context |
| Argument errors | More generic parser errors | Selected-command help and concrete recovery hints | Adopt hints, stable errors, and clean stderr |
| IL | `--view` | `--view` plus `--level` alias | Prefer `--level` publicly to avoid confusing IL level with BinaryView targeting |
| Mutation failures | Expected failures handled, unexpected apply-stage exceptions could miss rollback | Apply/analysis/verify stages share a rollback boundary | Highest-priority correctness pattern to adapt |
| Output validation | Some invalid output arguments discovered after an operation | Invalid regex/context/format combinations rejected before contacting the bridge | Validate before side effects |
| Typed reads | String interpretation of endianness could misdecode enum values | Handles enum name/value explicitly, errors on unsupported representations | Use enum-aware code when adding typed reads |
| Protocol | 1 | 2 | Version behavior and response changes; reject stale companions explicitly |
| Documentation | Large README and skill | Short workflow guide, separate callsite reference | Apply the documentation structure to our stale and inconsistent guides |

Our side has also changed. Annotation export is committed and hardened with paired JSON/BNTL installation and rollback around filesystem failures. `signature` is now a first-class CLI/POST endpoint with declaration file/stdin input, parse-only dry-run, optional name application, reanalysis/wait controls, and readback verification. `reanalyze` explicitly targets a function. Opening a sibling database now requires a deliberate yes/no/cancel choice; launching preserves existing instances by default. Existing working-tree changes make clearing skipped analysis an explicit decompile option.

## Shared functionality and architectural differences

Both operate on live BinaryViews inside the Binary Ninja GUI process. Both provide function listing/search, decompilation, disassembly, import inspection, function/data rename, comment read/write/delete, C type declarations, prototype and variable edits, unrestricted in-process Python, structured responses, and explicit target selection. Some of our counterparts are HTTP-only. Having arbitrary Python on both sides is not counted as equivalent first-class support for every possible feature.

| Concern | Our current repository | bn 0.15.0 |
|---|---|---|
| Interface | Versioned HTTP GET/POST routes; no standard MCP JSON-RPC implementation in the current repo | Custom JSON socket protocol; no standard MCP |
| Server lifetime | Auto-start; plugin menu start/stop | Auto-start; bridge restart command |
| Multiple processes | Port discovery over 9009, 9000–9008; server and global view IDs | Fixed registry/socket per cache directory; no built-in multi-process selector |
| Multiple views | Rich identities, logical groups, architecture/platform/analysis state, strict request verification | Basename/path/view ID/target ID selectors, sole-view default, explicit active selector |
| Target defaults | In discovery mode, a view ID is required for scoped requests when views are found, even if only one is open | Omit selector only when exactly one view is open; BN_TARGET shell default |
| Transport portability | TCP loopback using standard HTTP libraries; platform adapters include macOS/Linux/Windows | UnixStreamServer/AF_UNIX implementation; path conventions include Windows, but no Windows transport parity demonstrated |
| Concurrency | Single-threaded HTTPServer; timed-out Python worker may outlive request | Threaded socket server, operations serialized with one reentrant lock |
| Authentication | No application authentication; loopback trust boundary and permissive CORS | No application authentication; socket filesystem access controls the local boundary |
| Versioning | Per-endpoint version plus UI response schema and manifest | Global protocol 2, live operations/features, version and build diagnostics |
| GUI operations | Open/options/existing-database dialogs, wait for registration/analysis, close tabs, quit, status bar | No equivalents |
| Logs/console | Listener capture, filtering, statistics, clearing, post-command error probes on selected commands | No persistent log/console monitoring surface |
| Mutation checks | Signature dry-run/readback; most other writes direct; no shared rollback framework | Preview, undo transaction, analysis refresh, post-state verification, no-op status, rollback and sampled diffs |
| Save protection | Global BinaryView.save guard; native database saving used for relevant UI workflows | No equivalent save guard; built-in mutation commit is an undo commit, not a disk save |
| Annotation persistence | JSON plus native BNTL archive, source identity/RVAs/types/tags; export only | Function analysis bundles; no annotation archive or restore lane |
| Python context | Persistent variables, final-expression or _result return, interactive session/completion, stdout/stderr, history | Fresh scope, explicit result variable, stdout, preserved runtime traceback, typed/address helpers |
| Packaging | Source-checkout CLI using plumbum/requests; no project.scripts entry point | Installed bn entry point, packaged plugin/skill installers, no mandatory runtime dependencies |
| License | GPL-3.0 metadata | MIT |
| CI | Offline tests/Ruff/unicode checks; scheduled self-hosted real-BN tests including optional destructive UI flows | Python 3.12/3.14, release metadata validation, wheel/install smoke and PyPI publishing |

The singleton socket limitation remains: startup unlinks the fixed socket, so multiple default-configured processes compete for one discovery location. BN_CACHE_DIR can manually isolate them, but does not provide discovery comparable to ours.

## Complete CLI correspondence and arguments

In the following tables, command names omit the executable. Our actual source invocation is `uv run python scripts/binja-cli.py`; help identifies it as `binja-mcp`. The repository still lacks a packaged executable even though some documentation uses `binja-cli` or nonexistent `./cli.py`.

Our root flags are `--server/-s`, `--filename/--target-file`, `--view-id`, `--strict-target`, `--allow-target-fallback`, `--json/-j`, `--verbose/-v`, `--request-timeout/-t` (120 seconds), `--connect-timeout` (5 seconds), `--no-auto-errors`, `--fail-on-new-errors`, and `--error-probe-count` (50). They precede the subcommand. Timeout environment variables are BINJA_CLI_TIMEOUT and BINJA_CLI_CONNECT_TIMEOUT.

bn common options are `--format {text,json,ndjson}`, `--out`, `--match`, `--before`, `--after`, `--spill`, `--no-spill`, and `--tokens`; scoped commands also have `--target`. Target works anywhere and has BN_TARGET fallback. Other common options belong at the applicable command level. Text is the default for reads/Python; JSON for mutations, setup, schema and bundles. The new release has not made every option global.

| Our executable command and local options | bn counterpart and operation-specific options | Difference |
|---|---|---|
| `status` | `doctor`, `target info` | bn adds loaded-code/protocol/capability diagnostics |
| `views` | `target list` | Ours spans instances and includes richer view/analysis metadata |
| `resolve-target` | `target info` | Ours exposes strict resolution and routing results |
| `statusbar --all --include-hidden --exec-timeout` (20s) | None | Ours inspects GUI status |
| `open [path] --platform/-p --view-type/-t --existing-database yes/no/cancel --no-click --inspect-only --wait-open-target` (6s) `--wait-analysis --analysis-timeout` (120s) | None | Ours automates file opening and startup |
| `close --view-id --filename/--file --all --except-view-id --except-filename/--except-file --decision auto/save/dont-save/cancel --inspect-only --wait-ms` (2000) `--exec-timeout` (120s) | None | Ours closes selected tabs and resolves prompts |
| `quit --decision auto/save/dont-save/cancel --mark-dirty --inspect-only --wait-ms` (2000) `--quit-app --quit-delay-ms` (300) `--exec-timeout` (120s) | None | Ours manages GUI shutdown |
| `functions --search/-s --offset/-o` (0) `--limit/-l` (100) | `function list --min-address --max-address`; `function search query --regex --min-address --max-address` | Ours paginates; bn returns full matching functions sorted by address |
| `decompile function --allow-analysis-skipped` (working tree) | `decompile identifier` | Ours requires an opt-in to clear skip state; bn function reads accept interior addresses |
| `assembly function` | `disasm identifier --count N` or `--end address`, or `--before-instructions/--after-instructions` | Ours whole-function annotated assembly; bn adds linear and window modes |
| `signature function [declaration...] --file/-f --stdin --apply-name --dry-run --no-reanalyze --no-wait --no-verify --analysis-timeout` (1800s) | `proto set identifier prototype --preview`; `proto get identifier` | Our dry-run parses only, bn preview applies/verifies/reverts; ours supports file/stdin and explicit rename choice |
| `reanalyze function --no-wait --analysis-timeout` (1800s) | `refresh` | Ours reanalyzes one function; bn refresh waits for view analysis |
| `rename function old new` | `symbol rename identifier new --kind auto/function/data --preview` | bn adds shared transaction/verification/diffs |
| `rename data address new` | `symbol rename identifier new --kind data --preview` | Same safety difference |
| `comment target [text] --delete/-d --function/-f` | `comment get/set/delete --address/--function`, text positional for set, `--preview` on writes | Ours infers action; bn explicit verbs |
| `refs function` | `xrefs identifier` | Our refs is inbound; bn refs is outbound |
| `logs --count/-c` (20) `--level/-l --search/-s --errors/-e --warnings/-w --stats --clear` | None | Ours only |
| `type name_or_code --define/-d` | `types show name`; `types declare [declaration] --file --stdin --preview` | bn preserves declaration source path for relative includes |
| `imports --offset/-o --limit/-l` (0/100) | `imports` | bn returns full list; our API lists ImportedFunctionSymbol entries |
| `exports --offset/-o --limit/-l` (0/100) | None | Our implementation lists non-import/non-external symbols, broader than a strict loader export table |
| `annotations export output --type-library --source-id --source-filename --source-size --source-mtime-ns --force --include-unannotated-function-types` | No equivalent | Our absolute output paths are evaluated in the BN process; bundle --out is evaluated by bn's CLI |
| `python [code/file/-] --file/-f --stdin --interactive/-i --complete/-c --exec-timeout` (30s) | `py [exec] --code --script --stdin`, automatic piped stdin | Ours preserves interactive state; bn validates syntax locally and disambiguates explicit code/file inputs |

The separate `scripts/binja-restart.py` utility has no bn counterpart. It supports startup/restart timing, executable override, forced termination, verbose output, startup script generation, and a raw-file preference. Those are operational capabilities, not analysis primitives.

Additional bn commands and arguments are all accounted for below. An HTTP-only counterpart means the capability exists here without a matching named CLI command.

| bn command | Parameters beyond common options | Our closest capability |
|---|---|---|
| `schema [command path...]` | Optional multiword path, e.g. function info | Live `/meta/endpoints` only; no offline CLI schema |
| `plugin install` | `--dest --mode symlink/copy --force` | Manual copy/symlink or Plugin Manager |
| `skill install` | `--dest --mode symlink/copy --force` | No bundled installer |
| `function info` | `identifier --locals` | `/functionAt` and core metadata; no compact CLI |
| `function containing` | `address` | HTTP `/functionAt` and Python |
| `il` | `identifier --view/--level hlil/mlil/llil --ssa` | Python |
| `address info` | `address` | Python; limited function lookup endpoint |
| `data read` | `address --type bytes/u8/u16/u32/u64/i8/i16/i32/i64/f32/f64/ptr/cstr --count` | `/data` inventory and Python; no typed-read CLI |
| `xrefs field` | `Struct.field` | Python |
| `refs` | `identifier` | Outbound reference access through Python |
| `callsites` | `callee --within function` or `--within-file path`, `--context` (3), `--caller-static` | No dedicated lane; our code refs lack return-address and local HLIL/condition enrichment |
| `search text` | `query --view hlil/mlil/llil/disasm --regex --max-results` (200) `--timeout` (5s) | No database text search command |
| `search constant` | `value --max-results` (200) `--timeout` (5s) | Python |
| `types` | `--query --offset` (0) `--limit` (100) | Named type lookup only |
| `strings` | `--query --offset` (0) `--limit` (100) | Python get_strings helper; no strings route |
| `bundle function` | `identifiers... --include decompile,mlil,llil,disasm,locals,comments,xrefs,refs` or `all` | Python; annotation archive serves a different purpose |
| `local list` | `function` | Python |
| `local rename` | `function variable new_name --preview` | HTTP `/renameVariable` uses variable name |
| `local retype` | `function variable new_type --preview` | HTTP `/retypeVariable` uses variable name |
| `struct show` | `struct_name` | `type` returns some type/field details |
| `struct field set` | `struct offset field_name field_type --preview --no-overwrite` | Python |
| `struct field rename` | `struct old_name new_name --preview` | Python |
| `struct field delete` | `struct field_name --preview` | Python |

Our remaining HTTP-only inventory includes classes, namespaces, segments, defined data, console capture/statistics/errors/clear/completion, and aliases for comments/renames/function listing. Some legacy mutations still use GET, including defineTypes, renameVariable, retypeVariable and editFunctionSignature. Prefer POST for newly added mutations and retain compatibility deliberately.

## Ergonomic conclusions and verified limitations

1. **Option placement still matters.** An isolated execution of our `functions --json` exits 2 and writes `Error: Unknown switch --json` to stdout. `--json functions` is the supported order. bn accepts `--target` between command words, but `bn --format json function list` also fails. Adopt a clear common-option policy with tests; do not describe bn as accepting all global flags anywhere.
2. **Python stdin is now parity.** Automatic piped input was already supported here. The useful new bn ideas are local syntax checks, an explicit `--code` lane, and compatibility aliases. Our `-c` means completion, so do not reuse it for code. When CLI and embedded Python versions differ, local syntax rejection must account for that difference.
3. **Output correctness precedes formatting.** Our Python serializer wraps lists/dicts and slices them to 100 items. An offline probe of 150 items returned 100 with no truncation field. This is independent of CLI `--limit`; simply adding --no-spill would not recover discarded values. Return full structured values or explicit truncation/pagination metadata.
4. **Preserve execution tracebacks at the catch site.** Our worker catches an exception but the calling thread later calls traceback.format_exc(). A deliberate offline ValueError produced `NoneType: None\n` instead of its stack. bn captures traceback in the executing exception handler. Adapt that directly, preserving our stdout/stderr and interactive features.
5. **Dry-run and preview differ.** Our signature --dry-run validates parsing without changing the view. bn --preview temporarily applies the mutation, waits for analysis, captures sampled diffs, then reverts. Keep both concepts explicit; do not relabel our existing dry-run as equivalent preview.
6. **Verification is not rollback.** Our signature implementation compares normalized rendered type/name and reports failure, but does not undo the mutation. Unexpected errors after assignment can also leave a partial change. A common transaction wrapper is still needed.
7. **Preserve reference direction.** The first audit suggested changing our refs semantics. Revise that recommendation: keep existing inbound refs behavior; add `xrefs` as an inbound alias and a clearly named outbound command such as `refs-from`, or an explicit direction option. A silent direction flip would break scripts.
8. **Complete stdout is not incremental streaming.** bn still buffers the entire socket response and rendered output. NDJSON emits one line per top-level list item; object responses containing nested function arrays remain a single line. Adopt its output contract, but do not claim constant-memory streaming or per-function NDJSON events without implementing them.
9. **Compact function info saves output, not necessarily computation.** bn still enumerates locals to count them. Selective bundles do skip unrequested IL/locals work; that is the more direct computational saving. No latency benchmark was performed.
10. **Disassembly flags improved but have constraints.** Count/end are exclusive. Instruction windows cannot combine with linear mode, and bn rejects --match together with explicit instruction-window options. Legacy before/after instruction meanings remain without match. Our new API can keep explicit instruction/text context namespaces without carrying that historical overload.
11. **Timeouts are not cancellation.** bn still has no general user-facing request deadline; a blocking operation can hold its request lock. Our Python timeout returns while its worker may keep running. Retain configurable deadlines and consider observable job state/cooperative cancellation; moving execution to another process would not preserve direct live BinaryView access.
12. **Filesystem output can still fail after a committed mutation.** bn validates output option combinations before the request, but --out is actually written afterward. A failed file write does not imply that the BN mutation was rolled back. Our implementation should clearly report committed state if final rendering or file delivery fails.
13. **Diffs are sampled context.** bn guesses affected functions, including a limited type-dependent sample. Treat its diffs as helpful evidence, not a complete impact analysis or a proof that no other functions changed. Raw Python writes do not inherit the mutation framework.

## Recommended adaptation order

| Priority | Concrete change | Where it fits here | Completion evidence |
|---|---|---|---|
| 1 | Shared undo transaction for signature, rename, comments, type/local edits; prevalidate; verify; revert on mismatch or exception | API/core operations; preserve our skip guards and parse-only dry-run | Failure injected after assignment leaves original annotations intact; preview restores before-state; real-GUI readback test |
| 1 | Lossless structured Python results and correct traceback capture | python_executor_v2.py and output contract | A 150-item result remains 150; nested values retain shape; a worker exception retains filename/line/stack |
| 1 | Common output writer with JSON/NDJSON, --out, --match/context, optional text spill, unique artifacts, optional token counting | Extract reusable client output module; preserve --json/-j | Large JSON still pipes to jq; no-spill text is complete; concurrent artifacts differ; tokenizer absence never loses data |
| 2 | Linear `assembly`/`disasm --count/--end` without accessing IL or creating a function | New read-only core operation and versioned endpoint | Decode mapped unanalyzed bytes, report unmapped/undecodable stop and next address, preserve skipped analysis, honor exclusive end |
| 2 | Selective multi-function bundle, target pinned once, per-function results/errors | Read-only endpoint and CLI; reuse existing decompile/assembly/refs | Requested sections only, no duplicated HLIL, partial success survives, stable target across all functions |
| 2 | Offline scoped schema plus doctor/version/build/capability diagnostics | Extend shared endpoint specification and derive CLI descriptions | Schema requires no live BN; argument defaults match parser; stale loaded code produces actionable mismatch |
| 2 | Explicit Python --code/--script and py alias; syntax validation; retain completion and interactive state | Existing Python command | No file probe for explicit code; malformed code fails locally where version-compatible; piped input remains compatible |
| 2 | Real installed binja-cli entry point and package/install smoke tests | pyproject/package layout and CI | uv tool install from a wheel works outside the repo; existing wrapper integration is preserved |
| 3 | Common identifier resolver, IL levels/SSA, typed reads, inbound/outbound/field refs, direct callsites, bounded database search, local IDs and struct edits | Expand existing API incrementally | Ambiguity errors, interior-address reads, enum-correct endianness, partial-search metadata, stable variable targeting |
| 3 | Shorten user docs; correct executable examples, repository metadata and stale test/lint instructions | README, CLI_README, CLAUDE, AGENTS, plugin metadata | Every example uses an actual installed/source invocation; documentation matches current tests and endpoint semantics |

Implementation details should improve on upstream where our architecture differs. Keep process-qualified target identity in every result. Preserve the existing explicit-target policy unless a deliberate compatible selector design is chosen. Use bounded output with explicit completeness metadata for expensive queries. For a new multi-function API, prefer a consistent envelope even for one function, avoiding upstream's single-versus-multiple response-shape switch. Keep declaration-name changes explicit through --apply-name. Preserve archive protection against overwriting files; upstream --out currently overwrites a chosen path without a force flag.

The earlier recommendations that remain strongest are mutation transactions, richer analysis primitives, schemas and packaging. The recommendation to copy mandatory token-based spilling is superseded by 0.15.0. The recommendation to silently reverse refs direction should be dropped. Our dedicated signature/reanalysis commands and committed annotation exporter also close gaps that were present in the July audit.
