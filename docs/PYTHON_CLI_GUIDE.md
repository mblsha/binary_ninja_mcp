# Python CLI guide

`python` and `py` run code inside the selected Binary Ninja GUI process, not on
the client. Install/run the client as described in the [README](../README.md).
Use a process-qualified `VIEW_ID` from `binja-cli views` for live analysis.

```sh
binja-cli --view-id VIEW_ID python --code 'len(bv.functions)'
binja-cli --view-id VIEW_ID py --script analysis.py
binja-cli --view-id VIEW_ID python --stdin < analysis.py
binja-cli --view-id VIEW_ID python -i
binja-cli --view-id VIEW_ID python -c 'bv.get_'
```

Positional code, an existing file path, `--file`/`-f`, `-` and automatically piped
stdin remain compatible. Prefer explicit `--code`/`--script` to avoid ambiguity.
`-c` means completion, not code. Choose one input source/mode. Code is
syntax-checked locally before sending; `--no-syntax-check` defers validation to
embedded Python when versions differ. No client-side execution occurs.

## Multi-line code and quoting

Use a file or quoted heredoc for code containing shell metacharacters. A quoted
heredoc delimiter preserves backticks, dollar signs and backslashes literally.

```sh
binja-cli --view-id VIEW_ID python --stdin <<'PY'
functions = [f for f in bv.functions if "crypt" in f.name.lower()]
_result = [{"name": f.name, "address": hex(f.start)} for f in functions]
PY
```

Context persists between requests to the executor. `bv` is the selected
BinaryView (or `None` for a no-view execution); `bn` is the SDK. Helpers include
`get_func`, `find_functions`, `get_strings`, `hex_dump`, `info` and
`get_current_view`. Do not assume `bv` exists or that a helper safety-checks
arbitrary operations you write. Client shell environment variables are not
automatically forwarded into the already-running GUI process.

## Results and pipelines

The final expression or `_result` supplies a return value. JSON responses include
stdout, stderr, return value, variables, context and timing. Serialization version
2 preserves full containers, represents cycles explicitly, and uses dictionary
entries for non-string keys so distinct keys cannot collide. SDK objects may
be descriptive records/reprs, not round-trippable native objects.

```sh
binja-cli --view-id VIEW_ID python --code '[f.name for f in bv.functions]' --json \
  | jq -r '.return_value.items[]'
binja-cli --view-id VIEW_ID python --script analysis.py --json --out result.json
```

JSON/NDJSON is complete by default; it is buffered, not incremental streaming.
Text may spill above 40,000 bytes; use `--no-spill` for full text stdout. Output
files live on the client and refuse overwrite without `--overwrite-output`.
Runtime exceptions preserve their worker traceback under `error.traceback` and
return a failing CLI status. Interactive mode does not support artifacts,
structured output, filtering or tokens. See [output contracts](cli-output.md).

## Safety

Arbitrary Python can mutate the live process and bypass built-in transactions.
Prefer dedicated signature/local/structure commands for edits. Check
`func.analysis_skipped` before fetching HLIL, MLIL, LLIL or locals; do not clear
it without an explicit decision. `hasattr(func, "hlil")` also invokes the property
and is not a safe precheck. Full skip-state preservation requires comparing
addresses, not counts.

The default execution timeout is 30 seconds; `--exec-timeout` controls the worker
wait (the server caps it at 3600 seconds), while root `--request-timeout` controls
HTTP. A timeout does not terminate the worker or cancel native analysis. Do not
repeat a mutation just because the request timed out.

Never call `bv.save(...)`: the plugin deliberately blocks that raw-byte API.
BNDB persistence requires explicit authorization and the correct native snapshot
or database-creation API. A mutation/undo commit is not a disk save.
