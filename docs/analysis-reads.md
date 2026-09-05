# Function inspection and selected analysis reads

These commands adapt daily-analysis interfaces from
[bn v0.15.0](https://github.com/banteg/bn/releases/tag/v0.15.0), retaining our
explicit multi-instance/view routing and analysis-skip guard. Update the CLI
and reload the plugin together, then check `binja-cli doctor`. The client requires
the loaded `analysis_reads_version: 1` capability before sending these requests;
restarting only the HTTP listener is not a code reload.

For stable-ID local edits and structure-field workflows, see
[Local and structure annotations](annotation-edits.md).

## CLI arguments

Examples use `VIEW_ID` from `binja-cli views`. All commands support the
[common output options](cli-output.md), including trailing `--json`,
`--format ndjson`, and atomic `--out PATH` output.

```bash
binja-cli --view-id VIEW_ID disasm 0x1000 --count 24
binja-cli --view-id VIEW_ID disasm 'main+0x10' --end 'main+0x40' --json
binja-cli --view-id VIEW_ID disasm 0x1000 --count 8 --arch thumb2
binja-cli --view-id VIEW_ID info main
binja-cli --view-id VIEW_ID info main --locals --json
binja-cli --view-id VIEW_ID bundle main helper --include decompile,comments,xrefs
binja-cli --view-id VIEW_ID bundle main --include all --time-budget 60 --out main.json
binja-cli schema bundle
```

| Command | Default | Key argument behavior |
| --- | --- | --- |
| `disasm IDENTIFIER` | Text, 32 instructions | `-n`/`--count` and exclusive `--end` are mutually exclusive; `--arch` overrides architecture selection. |
| `info IDENTIFIER` | Compact text metadata/counts | `--locals` includes canonical parameter/local records and stable IDs. |
| `bundle IDENTIFIER...` | JSON, decompile/disasm/refs_from | `--include` selects comma-separated sections; `--time-budget` is one cooperative budget for the whole request (default 30 seconds). |
| `il IDENTIFIER` | Text, HLIL | `--level`/`--view` selects hlil/mlil/llil; `--ssa` selects SSA without changing the level. |
| `read IDENTIFIER` | JSON, 16 bytes | `--type`/`-t` selects decoding, `--count`/`-n` selects elements or a string byte bound; `--endian` overrides auto endianness. |
| `xrefs IDENTIFIER` | JSON, incoming code/data references | `--field` interprets the identifier as `Type.field` or `Type.0xOFFSET`. |
| `refs-from IDENTIFIER` | JSON, outgoing function references | Accepts an interior address; does not change legacy incoming `refs` behavior. |
| `search text QUERY` | JSON, case-insensitive HLIL search | `--level`/`--view`, `--regex`, `--case-sensitive`; repeat `--within` to scope functions. |
| `search constant INTEGER` | JSON, exact LLIL constants | Decimal/hex integers; optional level and repeated `--within`. |
| `callsites IDENTIFIER` | JSON, direct ordinary calls | `--context` (default 3), `--no-hlil`, `--include-tailcalls`, repeated `--within`. |

`assembly FUNCTION` remains the existing annotated function-disassembly command.
`disasm` is a separate linear decoder: it works outside defined functions and
does not request IL. It uses a covering function's architecture when unambiguous,
otherwise the view architecture, and honors associated architecture/address
normalization (such as an ARM/Thumb address tag). With `--arch`, the explicit
architecture wins. Ambiguous architectures require an explicit choice. The
architecture selected at the start is retained for the whole linear range.

The decoder never reads past `--end` or the current segment boundary to complete
an instruction. It stops at unmapped/unreadable bytes, a failed/invalid decode,
or the 100,000-instruction safety limit. JSON preserves every decoded instruction,
`next_address`, `complete`, and `stopped_reason`. A stop before the requested
range/count is complete retains partial output and exits nonzero.

New read commands resolve integer/decimal/hex addresses, exact function or symbol
names, and `name+offset` / `name-offset` expressions. Exact names take precedence
over parsing an offset expression. Function-name case folding is a fallback
only. Duplicate names and overlapping functions report candidates instead of
choosing the first match; exact function entries take precedence over containing
functions. Function reads accept interior addresses. These rules currently apply
to the new read interfaces; legacy commands retain their identifier contracts.

## Bundle shape and selection

The response always contains `functions: [...]`, including for one input.
Identifiers resolving to the same function are combined in its `identifiers`
array; different functions are never merged just because their names match.
One strong view reference is held for the whole HTTP request. Focus changes do
not redirect later sections to another database. This pins the target, not an
immutable analysis snapshot: concurrent GUI edits or normal analysis may change
that view's contents during the read.

Sections are `decompile` (HLIL), `mlil`, `llil`, `disasm`, `locals`, `comments`,
`xrefs`, and `refs_from`; `all` selects all of them. `refs` aliases incoming
`xrefs`, preserving our existing `refs` direction. Outgoing references are named
`refs_from`, unlike bn's outgoing `refs`. No unselected IL/local/comment/reference
section is computed. Function disassembly uses analyzed address ranges; without
those ranges, use standalone `disasm` with an explicit count/end.

Each resolved function has `success`, `sections`, and `errors`. Resolution errors
have an `error` object instead. A section failure retains other successful
sections; partial decoding retains its completeness/stop metadata. Overall
`success` is false and the CLI exits 1 if any function/section fails or is partial.
Invalid section names, ranges and budgets are rejected before analysis work.
Do not interpret successful HTTP status as a completely successful bundle.

The time budget is shared across functions and sections and checked between SDK
calls and during instruction iteration. Completed/partial results survive budget
expiration. It **cannot interrupt a blocking Binary Ninja SDK call**; a single
lazy IL request may exceed the budget or HTTP timeout. Output is buffered, not
incrementally streamed. A timed-out client must not assume that the server has
already stopped processing the read.

## Skipped functions and local identities

No new read command clears `analysis_skipped`, reanalyzes, or saves a database.
On skipped functions, compact `info` returns metadata but omits parameter/local
counts (`null` plus a warning). `info --locals` and IL/local bundle sections fail
before accessing those properties. Other sections can still succeed. Lazy IL
for a non-skipped function may cause normal Binary Ninja analysis work.

Local records deduplicate parameters appearing again in `Function.vars`, and
contain the native variable `identifier` plus an `id` composed from function
address, architecture and native identifier. This ID survives renaming within
that analysis state; reanalysis/rebasing or a different database may change the
native variable identity.

## IL, typed reads and references

```bash
binja-cli --view-id VIEW_ID il main --level mlil --ssa --json
binja-cli --view-id VIEW_ID read table --type u32 --count 8
binja-cli --view-id VIEW_ID read pointer_slot --type ptr
binja-cli --view-id VIEW_ID read string_label --type cstr --count 512
binja-cli --view-id VIEW_ID xrefs table
binja-cli --view-id VIEW_ID xrefs Widget.flags --field
binja-cli --view-id VIEW_ID refs-from main
```

IL results preserve each instruction's actual index, machine address and text.
Unavailable requested IL/SSA fails instead of silently falling back to another
representation. Standalone IL and reference commands have the same cooperative
`--time-budget` semantics as bundles, with explicit partial-result metadata.
Incoming field references use Binary Ninja's type/offset reference index and
report access size and incoming type when available. Field selection is for a
declared structure/union member; ambiguous union offsets require an exact member
name. The legacy `refs FUNCTION` command keeps its existing code-only response;
`xrefs` adds address/symbol resolution, data references and field queries.

Memory formats are `bytes`, `u8/u16/u32/u64`, `i8/i16/i32/i64`, `f32/f64`, `ptr`,
and `cstr`. Count defaults to 16 for bytes, 256 for C strings and 1 otherwise.
For C strings it is a maximum byte bound, not a string count. Reads are capped
at 1,000,000 elements and 8,000,000 raw bytes and stop at unmapped/unreadable memory.
They use the view's native endianness enum (`--endian auto`) and pointer width;
unknown endianness is an error unless explicitly overridden as little/big.

Every memory result includes exact raw hex. A short scalar read returns complete
elements plus `trailing_bytes`; `next_address` points to the first incomplete
element, while `read_end_address` records how far bytes were fetched. A C string
is complete only when a NUL was found. Invalid UTF-8 uses replacement characters
with `encoding_errors: true`, retaining original bytes. Non-finite float values
use explicit `{type: "float", value: "nan"/"inf"/"-inf"}` tags so output remains
strict JSON; their original bit patterns remain in raw hex. Partial reads exit 1.

## Bounded searches and callsites

```bash
binja-cli --view-id VIEW_ID search text malloc --level hlil --max-results 50
binja-cli --view-id VIEW_ID search text 'load.*key' --regex --within decrypt
binja-cli --view-id VIEW_ID search constant 0xdeadbeef --within main --within helper
binja-cli --view-id VIEW_ID callsites malloc --context 2 --no-hlil
binja-cli --view-id VIEW_ID callsites exit --include-tailcalls --time-budget 20
```

Search covers existing function analysis, not every raw byte/string in a binary.
Use `functions --search QUERY` for function-name search. Analysis queries use
`--max-results`/`--limit` (default 100, maximum 100,000) and one `--time-budget`
(search default 5 seconds; callsites default 30). `--within` is repeatable and
uses the ambiguity-aware resolver. Without it, search visits all known functions,
while callsites visits callers indexed by Binary Ninja. Aliases are deduplicated.
Scoping does not give each function a fresh time budget.

Results include `complete`, `stopped_reason`, `incomplete_reasons`,
`functions_total`, `functions_scanned`, `skipped_functions`, `errors` and
`elapsed_seconds`. A limit/timeout, skipped analysis or per-function failure makes
the overall result incomplete and exits 1, preserving matches already found.
Hitting the result limit conservatively reports incomplete even if that match
happened to be the last one. Zero results only establish absence within a complete
reported scope. IL search checks skip state before accessing IL; disassembly
search can inspect a skipped function's existing instructions but still reports
the skip state as incomplete coverage. SDK calls remain non-preemptible.

Text results retain native instruction indices, machine addresses and a zero-based
line within that rendered instruction. Constant search traverses actual integer
constant/constant-pointer expression nodes; numeric SSA/register/operand indices
are not treated as constants. `--level disasm` matches numeric/address token kinds,
not every token's numeric metadata. Values are compared exactly as returned by
Binary Ninja, without signed/unsigned reinterpretation.

Regex mode requires the optional `regex` package in the Python environment used
by Binary Ninja. It validates the pattern and timeout API before analysis and
applies the remaining search budget to each match. There is no fallback to an
unbounded regex engine. See the package's
[timeout documentation](https://github.com/mrabarnett/mrab-regex#timeout).
`uv sync --extra search` enables it in this repository's test environment; installing
an isolated CLI extra does not necessarily install it into the GUI's embedded
Python. Literal and constant search require no additional package.

Callsites confirms direct LLIL call destinations; indirect calls/jumps are outside
its reported scope. Direct tail calls are opt-in. Each result includes the call
address, LLIL index/text, decoded instruction, nearby instructions in address
order, and optional mapped HLIL statements with enclosing structural conditions.
An `if` condition includes its true/false branch when identifiable. These are
syntax relationships, not proven runtime path predicates (especially for loops).

`static_return_address` is the sequential continuation after the decoded call
and its declared delay-slot instructions, not an observed dynamic return address.
Tail calls and unavailable/unsupported delay information return null with a
reason. Decode/context/mapping errors retain the basic callsite and report the
failed phase. `--context 0 --no-hlil` skips both optional context expansions.

## HTTP surfaces

- `GET /analysis/disasm`: `identifier`, optional `count` or `end`, optional `arch`.
- `GET /analysis/function`: `identifier`, optional `locals=true`.
- `GET /analysis/il`: `identifier`, optional `level`, `ssa`, `time_budget`.
- `GET /analysis/read`: `identifier`, optional `type`, `count`, `endian`.
- `GET /analysis/refs`: `identifier`, optional `direction` (incoming/outgoing),
  `field` (incoming only), `time_budget`.
- `POST /analysis/bundle`: JSON `identifiers` array, optional `include` array or
  comma-separated string, optional `time_budget` (finite, >0, at most 3600 seconds).
- `POST /analysis/search`: `query`, `mode` (text/constant), optional `level`,
  `within` array, `regex`, `case_sensitive`, `max_results`, `time_budget`.
- `POST /analysis/callsites`: `identifier`, optional `within` array, `context`,
  `include_tailcalls`, `hlil`, `max_results`, `time_budget`.

Requests require explicit target parameters and endpoint API version 1. Target
context is included in successful and structured validation-error responses.
The bundle input limit is 256 identifiers. These read endpoints provide no
mutation or automatic analysis-skip opt-ins.
