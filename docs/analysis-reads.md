# Function inspection and selected analysis reads

These commands adapt daily-analysis interfaces from
[bn v0.15.0](https://github.com/banteg/bn/releases/tag/v0.15.0), retaining our
explicit multi-instance/view routing and analysis-skip guard. Update the CLI
and reload the plugin together, then check `binja-cli doctor`. The client requires
the loaded `analysis_reads_version: 1` capability before sending these requests;
restarting only the HTTP listener is not a code reload.

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

## HTTP surfaces

- `GET /analysis/disasm`: `identifier`, optional `count` or `end`, optional `arch`.
- `GET /analysis/function`: `identifier`, optional `locals=true`.
- `POST /analysis/bundle`: JSON `identifiers` array, optional `include` array or
  comma-separated string, optional `time_budget` (finite, >0, at most 3600 seconds).

Requests require explicit target parameters and endpoint API version 1. Target
context is included in successful and structured validation-error responses.
The bundle input limit is 256 identifiers. These read endpoints provide no
mutation or automatic analysis-skip opt-ins.
