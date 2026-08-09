"""Portable Binary Ninja user-annotation serialization.

The JSON document provides the address/name/comment mapping and a Binary Ninja
type library stores exact native type objects.  Neither output contains Binary
Ninja analysis caches or original binary data.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
from datetime import datetime, timezone
from typing import Any

import binaryninja as bn


FORMAT_NAME = "binary-ninja-portable-user-annotations"
SCHEMA_VERSION = 1
RESTORE_NAME = "__binja_annotation_restore"


def _hex_int(value: int) -> str:
    return f"{value:#x}"


def _parse_int(value: int | str) -> int:
    return value if isinstance(value, int) else int(value, 0)


def _address_row(address: int, image_base: int) -> dict[str, str]:
    return {"address": _hex_int(address), "rva": _hex_int(address - image_base)}


def _qualified_name_parts(name: Any) -> list[str]:
    if isinstance(name, str):
        return name.split("::")
    try:
        return [str(part) for part in name]
    except TypeError:
        return [str(name)]


def _type_declaration(value: Any, name: str = RESTORE_NAME) -> str:
    before = value.get_string_before_name().rstrip()
    after = value.get_string_after_name().lstrip()
    separator = " " if before else ""
    return f"{before}{separator}{name}{after}"


def _native_type_name(*parts: str) -> Any:
    return bn.QualifiedName(["__binja_annotations", *parts])


def _absolute_path(value: str | os.PathLike[str], label: str) -> pathlib.Path:
    path = pathlib.Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path: {path}")
    return path


def _temporary_path(final_path: pathlib.Path, suffix: str) -> pathlib.Path:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=final_path.parent,
        prefix=f".{final_path.name}.",
        suffix=suffix,
    )
    os.close(descriptor)
    temporary = pathlib.Path(temporary_name)
    temporary.unlink()
    return temporary


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_result(path: pathlib.Path, format_name: str) -> dict[str, Any]:
    return {
        "bytes": path.stat().st_size,
        "format": format_name,
        "path": str(path),
        "sha256": _sha256(path),
    }


def _commit_output_pair(
    replacements: tuple[tuple[pathlib.Path, pathlib.Path], ...],
    *,
    overwrite: bool,
) -> None:
    """Replace related outputs while preserving any previous pair on failure."""

    if not overwrite:
        installed: list[pathlib.Path] = []
        try:
            for temporary, final in replacements:
                # The temporary is created beside the final output, so a hard
                # link provides an atomic no-clobber install on each target
                # filesystem. Unlike os.replace, this cannot overwrite a file
                # that appeared after the caller's existence check.
                os.link(temporary, final)
                installed.append(final)
        except BaseException as exc:
            rollback_errors: list[str] = []
            for final in reversed(installed):
                try:
                    final.unlink(missing_ok=True)
                except OSError as rollback_exc:
                    rollback_errors.append(f"remove {final}: {rollback_exc}")
            if rollback_errors:
                details = "; ".join(rollback_errors)
                raise OSError(
                    f"annotation output commit failed and rollback was incomplete: {details}"
                ) from exc
            raise
        return

    backups: dict[pathlib.Path, pathlib.Path] = {}
    installed: list[pathlib.Path] = []
    try:
        for _temporary, final in replacements:
            if not final.exists():
                continue
            backup = _temporary_path(final, ".backup")
            os.replace(final, backup)
            backups[final] = backup

        for temporary, final in replacements:
            os.replace(temporary, final)
            installed.append(final)
    except BaseException as exc:
        rollback_errors: list[str] = []
        for final in reversed(installed):
            try:
                final.unlink(missing_ok=True)
            except OSError as rollback_exc:
                rollback_errors.append(f"remove {final}: {rollback_exc}")
        for final, backup in backups.items():
            if not backup.exists():
                continue
            try:
                os.replace(backup, final)
            except OSError as rollback_exc:
                rollback_errors.append(f"restore {final} from {backup}: {rollback_exc}")

        if rollback_errors:
            details = "; ".join(rollback_errors)
            raise OSError(
                f"annotation output commit failed and rollback was incomplete: {details}"
            ) from exc
        raise
    else:
        for backup in backups.values():
            backup.unlink(missing_ok=True)


def _export_symbols(view: Any) -> list[dict[str, Any]]:
    rows = []
    for symbol in view.get_symbols():
        if symbol.auto:
            continue
        rows.append(
            {
                **_address_row(symbol.address, view.start),
                "binding": symbol.binding.name,
                "full_name": symbol.full_name,
                "namespace": _qualified_name_parts(symbol.namespace),
                "ordinal": symbol.ordinal,
                "raw_name": symbol.raw_name,
                "short_name": symbol.short_name,
                "type": symbol.type.name,
            }
        )
    return sorted(
        rows,
        key=lambda row: (_parse_int(row["address"]), row["type"], row["raw_name"]),
    )


def _function_identity(function: Any, image_base: int) -> tuple[int, str | None, str | None]:
    platform = getattr(function, "platform", None)
    architecture = getattr(function, "arch", None)
    return (
        function.start - image_base,
        str(platform.name) if platform is not None else None,
        str(architecture.name) if architecture is not None else None,
    )


def _function_sort_key(function: Any, image_base: int) -> tuple[int, str, str]:
    rva, platform_name, architecture_name = _function_identity(function, image_base)
    return rva, platform_name or "", architecture_name or ""


def _function_native_type_name(view: Any, function: Any, kind: str, *parts: str) -> Any:
    rva, platform_name, architecture_name = _function_identity(function, view.start)
    return _native_type_name(
        kind,
        f"{rva:x}",
        f"platform={platform_name or ''}",
        f"architecture={architecture_name or ''}",
        *parts,
    )


def _export_function_rows(
    view: Any,
    native_types: list[tuple[Any, Any]],
    user_function_addresses: set[int],
    *,
    include_unannotated_function_types: bool,
) -> list[dict[str, Any]]:
    rows = []
    for function in sorted(view.functions, key=lambda item: _function_sort_key(item, view.start)):
        _rva, platform_name, architecture_name = _function_identity(function, view.start)
        comments = [
            {**_address_row(address, view.start), "text": text}
            for address, text in sorted(function.comments.items())
            if text
        ]

        variables = []
        for variable in function.vars:
            if not function.is_var_user_defined(variable):
                continue
            variable_type_name = _function_native_type_name(
                view,
                function,
                "variable",
                variable.source_type.name,
                str(variable.index),
                str(variable.storage),
            )
            native_types.append((variable_type_name, variable.type))
            variables.append(
                {
                    "index": variable.index,
                    "name": variable.name,
                    "native_type": _qualified_name_parts(variable_type_name),
                    "source_type": variable.source_type.name,
                    "storage": variable.storage,
                    "type_declaration": _type_declaration(variable.type),
                }
            )
        variables.sort(key=lambda row: (row["source_type"], row["index"], row["storage"]))

        function_comment = function.comment or None
        explicit_type = None
        explicit_native_type = None
        has_adjacent_user_annotation = bool(
            comments or variables or function_comment or function.start in user_function_addresses
        )
        has_restorable_function_type = bool(
            function.has_explicitly_defined_type or function.has_user_type
        )
        should_export_function_type = has_restorable_function_type and (
            include_unannotated_function_types or has_adjacent_user_annotation
        )
        if should_export_function_type:
            explicit_native_type = _function_native_type_name(view, function, "function")
            native_types.append((explicit_native_type, function.type))
            explicit_type = _type_declaration(function.type)

        if not (comments or variables or explicit_type or function_comment):
            continue
        rows.append(
            {
                **_address_row(function.start, view.start),
                "architecture": architecture_name,
                "comments": comments,
                "explicit_native_type": (
                    _qualified_name_parts(explicit_native_type) if explicit_native_type else None
                ),
                "explicit_type_declaration": explicit_type,
                "function_comment": function_comment,
                "name_at_export": function.name,
                "platform": platform_name,
                "user_variables": variables,
            }
        )
    return rows


def _export_data_variables(
    view: Any,
    native_types: list[tuple[Any, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for address, variable in sorted(view.data_vars.items()):
        if variable.auto_discovered:
            continue
        variable_type_name = _native_type_name("data", f"{address - view.start:x}")
        native_types.append((variable_type_name, variable.type))
        rows.append(
            {
                **_address_row(address, view.start),
                "name": variable.name,
                "native_type": _qualified_name_parts(variable_type_name),
                "type_declaration": _type_declaration(variable.type),
            }
        )
    return rows


def _tag_row(address: int, tag: Any, image_base: int) -> dict[str, Any]:
    return {
        **_address_row(address, image_base),
        "data": tag.data,
        "icon": tag.type.icon,
        "type": tag.type.name,
    }


def _tag_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    function_address = row.get("function_address")
    return (
        _parse_int(row["address"]),
        row.get("scope", ""),
        row.get("platform") or "",
        row.get("architecture") or "",
        _parse_int(function_address) if function_address is not None else -1,
        row["type"],
        row["data"],
    )


def _export_tags(view: Any) -> dict[str, Any]:
    address_tags = [
        {**_tag_row(address, tag, view.start), "scope": "data"}
        for address, tag in view.get_tags(auto=False)
    ]
    function_tags = []
    for function in view.functions:
        _rva, platform_name, _architecture_name = _function_identity(function, view.start)
        tag_locations = {}
        for architecture, address, _tag in function.tags:
            architecture_name = architecture.name if architecture is not None else None
            tag_locations[(address, architecture_name or "")] = architecture

        for (address, architecture_name), architecture in sorted(tag_locations.items()):
            for tag in function.get_tags_at(address, arch=architecture, auto=False):
                address_tags.append(
                    {
                        **_tag_row(address, tag, view.start),
                        "architecture": architecture_name or None,
                        "function_address": _hex_int(function.start),
                        "function_rva": _hex_int(function.start - view.start),
                        "platform": platform_name,
                        "scope": "address",
                    }
                )

        for tag in function.get_function_tags(auto=False):
            function_tags.append(
                {
                    **_address_row(function.start, view.start),
                    "architecture": function.arch.name if function.arch is not None else None,
                    "data": tag.data,
                    "icon": tag.type.icon,
                    "platform": platform_name,
                    "scope": "function",
                    "type": tag.type.name,
                }
            )
    address_tags.sort(key=_tag_sort_key)
    function_tags.sort(key=_tag_sort_key)
    return {"address": address_tags, "function": function_tags}


def _export_type_library(
    view: Any,
    output_path: pathlib.Path,
    final_path: pathlib.Path,
    extra_types: list[tuple[Any, Any]],
) -> list[dict[str, Any]]:
    container_rows = list(view.user_type_container.types.items())
    # Do not use BinaryView.dependency_sorted_types here. In addition to being
    # unnecessary for TypeLibrary.add_named_type, that property enters
    # BNGetAnalysisDependencySortedTypeList and can crash Binary Ninja when a
    # database contains a malformed or cyclic user-type graph. The user type
    # container already gives us every type we need; sort its qualified names
    # for stable archives and let the type library retain named references.
    by_name = {
        str(name): (type_id, name, value)
        for type_id, (name, value) in container_rows
    }
    ordered_names = sorted(by_name)

    rows = []
    named_types = []
    for order, name_text in enumerate(ordered_names):
        item = by_name.get(name_text)
        if item is None:
            continue
        type_id, name, value = item
        rows.append(
            {
                "definition_lines": [str(line) for line in value.get_lines(view, name, 240)],
                "name": _qualified_name_parts(name),
                "order": order,
                "type_id": type_id,
            }
        )
        named_types.append((name, value))

    library = bn.TypeLibrary.new(view.arch, f"annotations:{final_path.stem}")
    if view.platform is not None:
        library.add_platform(view.platform)
    for name, value in named_types:
        library.add_named_type(name, value)
    for name, value in extra_types:
        library.add_named_object(name, value)
    library.store_metadata("annotation_format", FORMAT_NAME)
    library.store_metadata("annotation_schema_version", SCHEMA_VERSION)
    # Do not finalize: exact function/variable objects can reference types that
    # are supplied by the destination view rather than by this library.
    library.write_to_file(str(output_path))
    return rows


def _source_description(
    view: Any,
    *,
    source_id: str | None,
    source_filename: str | None,
    source_size: int | None,
    source_mtime_ns: int | None,
) -> dict[str, Any]:
    filename = pathlib.Path(view.file.filename)
    try:
        stat = filename.stat()
    except OSError:
        stat = None
    return {
        "architecture": view.arch.name if view.arch else None,
        "end": _hex_int(view.end),
        "filename": source_filename or str(filename),
        "mtime_ns": source_mtime_ns
        if source_mtime_ns is not None
        else (stat.st_mtime_ns if stat else None),
        "platform": view.platform.name if view.platform else None,
        "size": source_size if source_size is not None else (stat.st_size if stat else None),
        "source_id": source_id,
        "span": _hex_int(view.end - view.start),
        "start": _hex_int(view.start),
        "view_type": view.view_type,
    }


def _count_payload(payload: dict[str, Any]) -> dict[str, int]:
    function_rows = payload["functions"]
    return {
        "address_tags": len(payload["tags"]["address"]),
        "data_variables": len(payload["data_variables"]),
        "explicit_function_types": sum(
            bool(row["explicit_type_declaration"]) for row in function_rows
        ),
        "function_comments": sum(bool(row["function_comment"]) for row in function_rows),
        "function_rows": len(function_rows),
        "function_tags": len(payload["tags"]["function"]),
        "instruction_comments": sum(len(row["comments"]) for row in function_rows),
        "user_symbols": len(payload["user_symbols"]),
        "user_types": len(payload["user_types"]),
        "user_variables": sum(len(row["user_variables"]) for row in function_rows),
        "view_comments": len(payload["view_comments"]),
    }


def export_user_annotations(
    view: Any,
    json_path: str | os.PathLike[str],
    *,
    type_library_path: str | os.PathLike[str] | None = None,
    overwrite: bool = False,
    include_unannotated_function_types: bool = False,
    source_id: str | None = None,
    source_filename: str | None = None,
    source_size: int | None = None,
    source_mtime_ns: int | None = None,
) -> dict[str, Any]:
    """Write a portable JSON annotation archive and matching native BNTL."""

    if view.arch is None:
        raise ValueError(
            "annotation export requires a BinaryView with an architecture; "
            "architectureless Raw views cannot produce a BNTL companion"
        )

    archive_path = _absolute_path(json_path, "annotation archive path")
    if archive_path.suffix.lower() != ".json":
        raise ValueError("annotation archive path must end in .json")
    library_path = (
        _absolute_path(type_library_path, "type library path")
        if type_library_path is not None
        else archive_path.with_suffix(".types.bntl")
    )
    if library_path.suffix.lower() != ".bntl":
        raise ValueError("type library path must end in .bntl")
    if archive_path == library_path:
        raise ValueError("annotation archive and type library paths must differ")

    existing = [path for path in (archive_path, library_path) if path.exists()]
    if existing and not overwrite:
        paths = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"refusing to overwrite existing annotation output: {paths}")

    native_types: list[tuple[Any, Any]] = []
    user_symbols = _export_symbols(view)
    user_function_addresses = {
        _parse_int(row["address"])
        for row in user_symbols
        if row["type"] == bn.SymbolType.FunctionSymbol.name
    }
    functions = _export_function_rows(
        view,
        native_types,
        user_function_addresses,
        include_unannotated_function_types=include_unannotated_function_types,
    )
    data_variables = _export_data_variables(view, native_types)

    temporary_library = _temporary_path(library_path, ".tmp.bntl")
    temporary_json = _temporary_path(archive_path, ".tmp.json")
    try:
        user_types = _export_type_library(
            view,
            temporary_library,
            library_path,
            native_types,
        )
        try:
            recorded_type_path = os.path.relpath(library_path, archive_path.parent)
        except ValueError:
            recorded_type_path = str(library_path)

        payload: dict[str, Any] = {
            "coverage": {
                "excluded_non_annotation_state": [
                    "analysis caches",
                    "binary patches",
                    "instruction highlights",
                    "undo history",
                    "user segments and sections",
                ],
                "included": [
                    "user symbols and function/data names",
                    "BinaryView comments",
                    "function and instruction comments",
                    "named user types",
                    "explicit function prototypes",
                    "user variables",
                    "user-defined data variables",
                    "user address and function tags",
                ],
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "data_variables": data_variables,
            "format": FORMAT_NAME,
            "functions": functions,
            "options": {
                "include_unannotated_function_types": include_unannotated_function_types,
            },
            "schema_version": SCHEMA_VERSION,
            "source": _source_description(
                view,
                source_id=source_id,
                source_filename=source_filename,
                source_size=source_size,
                source_mtime_ns=source_mtime_ns,
            ),
            "tags": _export_tags(view),
            "type_library": recorded_type_path,
            "user_symbols": user_symbols,
            "user_types": user_types,
            "view_comments": [
                {**_address_row(address, view.start), "text": text}
                for address, text in sorted(view.address_comments.items())
                if text
            ],
        }
        payload["counts"] = _count_payload(payload)
        with temporary_json.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")

        if not overwrite:
            raced = [path for path in (archive_path, library_path) if path.exists()]
            if raced:
                paths = ", ".join(str(path) for path in raced)
                raise FileExistsError(
                    f"annotation output appeared during export; refusing overwrite: {paths}"
                )

        _commit_output_pair(
            (
                (temporary_library, library_path),
                (temporary_json, archive_path),
            ),
            overwrite=overwrite,
        )
    finally:
        temporary_library.unlink(missing_ok=True)
        temporary_json.unlink(missing_ok=True)

    return {
        "counts": payload["counts"],
        "format": FORMAT_NAME,
        "json": _file_result(archive_path, FORMAT_NAME),
        "schema_version": SCHEMA_VERSION,
        "type_library": _file_result(library_path, "binary-ninja-type-library"),
    }
