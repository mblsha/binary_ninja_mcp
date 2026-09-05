# bn adaptation native verification — 2026-09-05

Environment: macOS, Binary Ninja Personal **6.1.10608-dev** (`5e1fb771`), embedded
Python 3.10.14. GUI inspection showed the launch screen; the current instance
had no open views. `doctor` confirmed matching loaded source fingerprints,
capabilities, and no stale bindings before tests. No user database was opened,
changed or saved. The earlier three-database instance was not restarted by these
tests. Changed modules were reloaded only in the later, verified idle instance.

The reviewed [scratch verifier](../scripts/verify_bn_adaptations_live.py) creates
160 bytes in memory, a mapped x86-64 view, three functions (one deliberately
skipped), and temporary native types. It refuses an instance with registered or
GUI views, requires verifiable loaded code, and closes only its own scratch
view. It removes only scratch registrations, preserving any unrelated view
that might appear concurrently. Tests run through the real Python HTTP executor
but call analysis/edit core classes directly; HTTP route pinning and CLI argument
wiring have separate SDK-shaped tests. This is not an end-to-end live test of
every new HTTP endpoint.

Final scratch result: **8/8 groups passed**, zero remaining registered views,
`saved_database: false`. Local result artifact:
`/tmp/bn-live-verification-20260905-run6.json`, SHA-256
`4ee70a2db2fef541d1635825720c5ba96918b8df4f2c78218c919f37dd81cf68`.

| Native check | Observed evidence |
| --- | --- |
| Scoped undo after exception | An injected exception after comment assignment restored the original comment. |
| Signatures | Automatic-signature preview refused before mutation; intentional apply succeeded; an existing user-signature preview restored name, rendered type, user-type status and skip state. |
| Locals | Full-ID rename and parameter retype previews restored annotations. Widening a stack local changed its identity; the edit failed and verified rollback instead of retargeting. |
| Declared structure fields | Set, rename, delete and explicit overlap replacement previews restored full recorded layout; a deliberate scratch commit succeeded. |
| Automatic types and unions | A temporary user override restored automatic-type status; adding an overlapping distinct union member preserved all members and reverted. |
| Reads | Linear bytes outside a function, explicit-endian u32 read, all three IL levels with/without SSA, compact/locals info, incoming/outgoing refs and selective multi-function bundles succeeded. |
| Queries | Text and constant searches found matches; a direct callsite included native instruction bytes, HLIL mapping and the correct static continuation. |
| Skip policy | IL/local operations refused the skipped function; raw disassembly worked; the full skipped-address set remained exactly `[64]`. |

Two additional real-executor checks passed: a `list(range(150))` response retained
all 150 items with serialization version 2; an intentional `ValueError` returned
the executing worker's filename/line traceback rather than `NoneType: None`.
Artifacts: `/tmp/bn-live-serializer-150.json` and
`/tmp/bn-live-worker-traceback.json`. The deliberate exception correctly returned
a failing CLI exit status; it was not a crash or unexpected server failure.

## Native findings incorporated into the implementation

- Reanalysis infers `__pure` even when absent from a parsed signature. Comparison
  now normalizes only unspecified (zero-confidence) purity/can-return attributes
  on a temporary type builder; it preserves the actual observed type in output.
  Explicit attributes and other signature components must still match.
- Native undo of a previously automatic function signature leaves
  `has_user_type` true. A rendered-type-only check missed this. Automatic
  signature previews now fail before mutation; no null handles or private
  clearing APIs are used. Existing user-signature previews verify restoration.
- Automatic local type confidence can become zero after otherwise successful
  undo. That analysis metadata change is surfaced, while name/type/ID/skip and
  automatic/user annotation status remain strict. User-variable confidence is
  still checked.
- This dev SDK's mapped scratch view raised `ValueError: 76 is not a valid
  Endianness` in its default-endianness accessor. We do not guess a byte order:
  the CLI now provides an actionable `--endian little|big` error, and explicit
  little-endian decoding was verified. Offline tests separately cover normal
  little/big enums and invalid SDK representations.

No claim is made of Linux/Windows GUI execution, every architecture, incremental
streaming, benchmarked latency, persisted BNDB behavior, or concurrent-writer
isolation. The three-platform client CI matrix is configured but has not been
run remotely as part of these local check-ins.
