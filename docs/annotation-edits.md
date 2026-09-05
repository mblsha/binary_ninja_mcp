# Local and structure annotations

These installed commands complement the legacy `renameVariable`/`retypeVariable`
HTTP operations. They pin one resolved view, prevalidate, open a scoped undo
group, apply, wait for analysis, and verify readback before committing. No command
in this page saves a BNDB or changes `analysis_skipped`.

Use the actual explicit target from `binja-cli views` in every example:

```sh
binja-cli --view-id VIEW_ID locals list FUNCTION
binja-cli --view-id VIEW_ID locals rename FUNCTION '0x1000:armv7:VARIABLE_ID' new_name --preview
binja-cli --view-id VIEW_ID locals retype FUNCTION VARIABLE_ID 'uint32_t *'
binja-cli --view-id VIEW_ID struct show Packet
binja-cli --view-id VIEW_ID struct field set Packet 0x10 count uint32_t --preview
binja-cli --view-id VIEW_ID struct field rename Packet count item_count
binja-cli --view-id VIEW_ID struct field delete Packet item_count --preview
```

`FUNCTION` uses the new ambiguity-aware name/address/interior-address resolver.
Copy a full variable ID from `locals list`; the illustrative ID above is not a
real target. IDs combine entry address, architecture and native variable ID.
They survive a rename but are not permanent identities across binary reloads,
analysis changes, splits, or merges. If the native variable disappears during
verification, the edit fails and attempts undo; it never falls back to another
variable with the same name. Function entry/architecture/platform identity is
rechecked after analysis.

A variable selector is a full listed ID, an exact unique name, or a decimal/hex
native ID. Exact names take precedence over bare numeric IDs; use the full ID
when a variable has a numeric name. Duplicate names are an error with candidate
IDs. There is no case-insensitive local-name fallback. Local commands check
`analysis_skipped` before fetching parameters or variables. Retyping parses the
declaration before starting undo, and rejects void/function value types (use
pointers when appropriate). Explicitly assigning an unchanged name or type can
promote an automatic variable to a user annotation.

## Structure fields

`struct show` reports directly declared members, width/alignment, packing, base
structures, pointer offset, reference-propagation setting, type ID and auto/user
definition status. Field commands edit an existing structure/union; they do not
implicitly create a type or resolve inherited members as declared fields.

Field selectors are exact names, unique case-insensitive names, or unambiguous
decimal/hex offsets, in that order. An offset shared by union members is
ambiguous: use a name. `set STRUCT OFFSET NAME TYPE` uses a nonnegative byte
offset and a positive-width parsed type. Void, direct function and zero-width
fields are rejected.

Without `--overwrite`, a duplicate name or overlapping structure member is an
error. With `--overwrite`, overlapping *declared* members are removed and the
new member inserted; `removed_members` makes this destructive scope explicit.
A same-name field at another offset cannot be moved implicitly: delete it
explicitly first. Distinct union members may overlap at offset zero without
`--overwrite`; replacing an existing union name requires it and removes only
that member. Nonzero union offsets and overlap with inherited storage are
rejected even with `--overwrite`.

Builders are prepared offline before any live write. Unaffected members,
access/scope, packing, bases, pointer offsets and reference-propagation metadata
must survive unchanged. Replacing a same-name member preserves its access and
scope. Renaming/deleting do not shrink width or alignment; insertion may grow
them but cannot shrink them. An unexpected builder-side layout change fails
before undo starts. Applying a structure change creates a user type override;
preview verifies restoration of the original auto/user status as well as the
declared layout and type ID.

## Preview, errors and protocol

`--preview` is an actual temporary edit, not a parse-only dry run. It applies,
waits, verifies, reverts the scoped undo group, waits again, and verifies the
original state. Responses contain `before`, `expected`, applied `after`, and
`current` after successful undo, plus `committed`, `rolled_back`,
`restoration_verified`, `state_unknown`, `verified` and `success`. `rolled_back`
means the undo API returned; only `restoration_verified: true` confirms observed
restoration. Any rollback/readback failure is an error, never successful preview.
Inspect live state if `state_unknown` is true. Do not retry blindly.

The default output is JSON; common `--format`, `--out`, filtering and artifact
options work after nested arguments. `--overwrite` affects structure members;
`--overwrite-output` independently permits replacing an output file. Editing
requests allow at least 30 minutes for SDK analysis waits; an HTTP timeout is
not cancellation and does not prove an edit was undone. Do not retry a timed-out
mutation without inspecting state. Analysis waits are not interruptible by a
CLI time-budget flag.

`GET /analysis/locals`, `GET /analysis/struct`, and the CLI edit commands require
loaded `annotation_edits_version >= 1`. `POST /edit/local` and
`POST /edit/struct-field` use endpoint API version 2. Update/reload client and
plugin together, then check `doctor`; a reachable old server is not evidence
that these safety guarantees are loaded. The shared mutation lock coordinates
these built-in edits only, not concurrent GUI users or arbitrary Python. These
are not isolation guarantees against other writers.

Offline SDK-shaped tests cover selectors, skip-property traps, validation,
preview restoration, silent/exceptional undo failure, partial writes, union
overlaps, metadata preservation, HTTP view pinning and nested CLI parsing.
Real-GUI verification remains outstanding; these tests do not establish native
undo or reanalysis behavior.
