#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest


MODULE_PATH = Path(__file__).resolve().parent / "plugin" / "core" / "annotation_archive.py"


class _NamedValue:
    def __init__(self, name: str):
        self.name = name


class _QualifiedName(list):
    def __str__(self) -> str:
        return "::".join(self)


class _FakeType:
    def __init__(self, declaration: str):
        self.declaration = declaration

    def get_string_before_name(self) -> str:
        return self.declaration

    def get_string_after_name(self) -> str:
        return ""

    def get_lines(self, _view, name, _width):
        return [f"typedef int {name};"]


class _FakeTypeLibrary:
    def __init__(self, architecture, name: str):
        self.architecture = architecture
        self.name = name
        self.platforms = []
        self.named_types = []
        self.named_objects = []
        self.metadata = {}

    @classmethod
    def new(cls, architecture, name: str):
        return cls(architecture, name)

    def add_platform(self, platform) -> None:
        self.platforms.append(platform.name)

    def add_named_type(self, name, value) -> None:
        self.named_types.append((str(name), value.declaration))

    def add_named_object(self, name, value) -> None:
        self.named_objects.append((str(name), value.declaration))

    def store_metadata(self, name: str, value) -> None:
        self.metadata[name] = value

    def write_to_file(self, path: str) -> None:
        Path(path).write_text(
            json.dumps(
                {
                    "metadata": self.metadata,
                    "name": self.name,
                    "named_objects": self.named_objects,
                    "named_types": self.named_types,
                    "platforms": self.platforms,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )


def _load_module():
    binaryninja = types.ModuleType("binaryninja")
    binaryninja.QualifiedName = _QualifiedName
    binaryninja.TypeLibrary = _FakeTypeLibrary
    binaryninja.SymbolType = types.SimpleNamespace(FunctionSymbol=_NamedValue("FunctionSymbol"))

    spec = importlib.util.spec_from_file_location("annotation_archive_unit_target", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"binaryninja": binaryninja}):
        spec.loader.exec_module(module)
    return module


class _FakeTag:
    def __init__(self, data: str):
        self.data = data
        self.type = types.SimpleNamespace(name="Review", icon="R")


class _FakeVariable:
    def __init__(self, type_declaration: str = "uint32_t"):
        self.index = 0
        self.name = "renamed_value"
        self.source_type = _NamedValue("RegisterVariableSourceType")
        self.storage = 7
        self.type = _FakeType(type_declaration)


class _FakeFunction:
    def __init__(
        self,
        start: int,
        *,
        annotated: bool,
        architecture: str | None = "x86_64",
        platform: str | None = "linux-x86_64",
        type_declaration: str = "int32_t",
    ):
        self.start = start
        self.arch = _NamedValue(architecture) if architecture is not None else None
        self.platform = _NamedValue(platform) if platform is not None else None
        self.name = "annotated_function" if annotated else "sub_1200"
        self.comments = {start + 2: "instruction comment"} if annotated else {}
        self.comment = "function comment" if annotated else ""
        self.vars = [_FakeVariable(f"{type_declaration} *")] if annotated else []
        self.type = _FakeType(type_declaration)
        self.has_explicitly_defined_type = True
        self.has_user_type = True
        self._address_tags = {start + 4: [_FakeTag("instruction tag")]} if annotated else {}
        self._function_tags = [_FakeTag("function tag")] if annotated else []

    @property
    def tags(self):
        return [
            (self.arch, address, tag)
            for address, tags in self._address_tags.items()
            for tag in tags
        ]

    def is_var_user_defined(self, _variable) -> bool:
        return True

    def get_tags_at(self, address: int, *, arch, auto: bool):
        assert arch is self.arch
        assert auto is False
        return self._address_tags.get(address, [])

    def get_function_tags(self, *, auto: bool):
        assert auto is False
        return self._function_tags


class _FakeSymbol:
    auto = False
    address = 0x1010
    binding = _NamedValue("GlobalBinding")
    full_name = "demo::annotated_function"
    namespace = _QualifiedName(["demo"])
    ordinal = 0
    raw_name = "annotated_function"
    short_name = "annotated_function"
    type = _NamedValue("FunctionSymbol")


class _FakeDataVariable:
    auto_discovered = False
    name = "renamed_data"
    type = _FakeType("uint8_t")


class _FakeView:
    def __init__(self, source_path: Path):
        self.file = types.SimpleNamespace(filename=str(source_path))
        self.arch = _NamedValue("x86_64")
        self.platform = _NamedValue("linux-x86_64")
        self.start = 0x1000
        self.end = 0x2000
        self.view_type = "ELF"
        self.functions = [
            _FakeFunction(0x1010, annotated=True),
            _FakeFunction(0x1200, annotated=False),
        ]
        self.data_vars = {0x1100: _FakeDataVariable()}
        user_type_name = _QualifiedName(["DemoType"])
        self.user_type_container = types.SimpleNamespace(
            types={"type-id": (user_type_name, _FakeType("struct DemoType"))}
        )
        self.dependency_sorted_types = [user_type_name]
        self._data_tags = [(0x1020, _FakeTag("data tag"))]
        self.tags = [tag for _address, tag in self._data_tags]
        self.address_comments = {0x1030: "view comment"}

    def get_symbols(self):
        return [_FakeSymbol()]

    def get_tags(self, *, auto: bool):
        assert auto is False
        return self._data_tags


def test_export_writes_json_and_native_type_companion(tmp_path: Path):
    module = _load_module()
    source_path = tmp_path / "source.bin"
    source_path.write_bytes(b"binary")
    output_path = tmp_path / "source.annotations.json"

    result = module.export_user_annotations(
        _FakeView(source_path),
        output_path,
        source_id="sha256:example",
    )

    type_path = tmp_path / "source.annotations.types.bntl"
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    native_payload = json.loads(type_path.read_text(encoding="utf-8"))

    assert payload["format"] == "binary-ninja-portable-user-annotations"
    assert payload["type_library"] == type_path.name
    assert payload["source"]["source_id"] == "sha256:example"
    assert payload["source"]["span"] == "0x1000"
    assert payload["counts"] == {
        "address_tags": 2,
        "data_variables": 1,
        "explicit_function_types": 1,
        "function_comments": 1,
        "function_rows": 1,
        "function_tags": 1,
        "instruction_comments": 1,
        "user_symbols": 1,
        "user_types": 1,
        "user_variables": 1,
        "view_comments": 1,
    }
    assert payload["functions"][0]["rva"] == "0x10"
    assert payload["functions"][0]["user_variables"][0]["name"] == "renamed_value"
    assert {row["scope"] for row in payload["tags"]["address"]} == {"address", "data"}
    instruction_tag = next(
        row for row in payload["tags"]["address"] if row["scope"] == "address"
    )
    assert instruction_tag["function_address"] == "0x1010"
    assert instruction_tag["architecture"] == "x86_64"
    assert payload["tags"]["function"][0]["scope"] == "function"
    assert native_payload["named_types"] == [["DemoType", "struct DemoType"]]
    assert len(native_payload["named_objects"]) == 3
    assert result["json"]["sha256"]
    assert result["type_library"]["sha256"]


def test_unannotated_function_types_are_opt_in(tmp_path: Path):
    module = _load_module()
    source_path = tmp_path / "source.bin"
    source_path.write_bytes(b"binary")
    output_path = tmp_path / "all-types.annotations.json"

    module.export_user_annotations(
        _FakeView(source_path),
        output_path,
        include_unannotated_function_types=True,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["counts"]["function_rows"] == 2
    assert payload["counts"]["explicit_function_types"] == 2
    assert payload["options"]["include_unannotated_function_types"] is True


def test_same_address_functions_keep_distinct_platform_native_types(tmp_path: Path):
    module = _load_module()
    source_path = tmp_path / "source.bin"
    source_path.write_bytes(b"binary")
    view = _FakeView(source_path)
    view.functions = [
        _FakeFunction(
            0x1010,
            annotated=True,
            architecture=None,
            platform=None,
            type_declaration="char",
        ),
        _FakeFunction(
            0x1010,
            annotated=True,
            architecture="armv7",
            platform="linux-armv7",
            type_declaration="int32_t",
        ),
        _FakeFunction(
            0x1010,
            annotated=True,
            architecture="thumb2",
            platform="linux-thumb2",
            type_declaration="uint32_t",
        ),
    ]
    output_path = tmp_path / "multi-platform.annotations.json"

    module.export_user_annotations(view, output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    native_payload = json.loads(
        output_path.with_suffix(".types.bntl").read_text(encoding="utf-8")
    )
    assert [
        (row["platform"], row["architecture"])
        for row in payload["functions"]
    ] == [
        (None, None),
        ("linux-armv7", "armv7"),
        ("linux-thumb2", "thumb2"),
    ]
    function_type_names = [
        "::".join(row["explicit_native_type"]) for row in payload["functions"]
    ]
    variable_type_names = [
        "::".join(row["user_variables"][0]["native_type"])
        for row in payload["functions"]
    ]
    assert len(set(function_type_names)) == 3
    assert len(set(variable_type_names)) == 3
    native_object_names = [name for name, _declaration in native_payload["named_objects"]]
    assert len(native_object_names) == len(set(native_object_names))


def test_export_refuses_relative_paths_and_existing_outputs(tmp_path: Path):
    module = _load_module()
    source_path = tmp_path / "source.bin"
    source_path.write_bytes(b"binary")
    view = _FakeView(source_path)

    with pytest.raises(ValueError, match="absolute path"):
        module.export_user_annotations(view, "relative.annotations.json")

    output_path = tmp_path / "source.annotations.json"
    module.export_user_annotations(view, output_path)
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        module.export_user_annotations(view, output_path)

    result = module.export_user_annotations(view, output_path, overwrite=True)
    assert result["counts"]["function_rows"] == 1


def test_export_keeps_instruction_and_function_tags_without_data_tags(tmp_path: Path):
    module = _load_module()
    source_path = tmp_path / "source.bin"
    source_path.write_bytes(b"binary")
    view = _FakeView(source_path)
    view._data_tags = []
    view.tags = []
    output_path = tmp_path / "tag-scopes.annotations.json"

    module.export_user_annotations(view, output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert [row["data"] for row in payload["tags"]["address"]] == ["instruction tag"]
    assert [row["scope"] for row in payload["tags"]["address"]] == ["address"]
    assert [row["data"] for row in payload["tags"]["function"]] == ["function tag"]


def test_overwrite_rolls_back_both_outputs_when_second_replace_fails(tmp_path: Path):
    module = _load_module()
    source_path = tmp_path / "source.bin"
    source_path.write_bytes(b"binary")
    view = _FakeView(source_path)
    output_path = tmp_path / "source.annotations.json"
    type_path = tmp_path / "source.annotations.types.bntl"
    module.export_user_annotations(view, output_path, source_id="original")
    original_json = output_path.read_bytes()
    original_types = type_path.read_bytes()
    real_replace = module.os.replace

    def fail_json_install(source, destination):
        source_path = Path(source)
        if source_path.name.endswith(".tmp.json") and Path(destination) == output_path:
            raise OSError("simulated JSON replacement failure")
        return real_replace(source, destination)

    with (
        patch.object(module.os, "replace", side_effect=fail_json_install),
        pytest.raises(OSError, match="simulated JSON replacement failure"),
    ):
        module.export_user_annotations(view, output_path, overwrite=True, source_id="replacement")

    assert output_path.read_bytes() == original_json
    assert type_path.read_bytes() == original_types
    assert not list(tmp_path.glob("*.backup"))


def test_no_overwrite_commit_does_not_clobber_racing_output(tmp_path: Path):
    module = _load_module()
    temporary_library = tmp_path / ".types.tmp"
    temporary_json = tmp_path / ".json.tmp"
    final_library = tmp_path / "archive.types.bntl"
    final_json = tmp_path / "archive.json"
    temporary_library.write_text("new types", encoding="utf-8")
    temporary_json.write_text("new json", encoding="utf-8")
    real_link = module.os.link
    injected_racer = False

    def inject_json_racer(source, destination):
        nonlocal injected_racer
        destination_path = Path(destination)
        if destination_path == final_json and not injected_racer:
            injected_racer = True
            final_json.write_text("racing writer", encoding="utf-8")
        return real_link(source, destination)

    with (
        patch.object(module.os, "link", side_effect=inject_json_racer),
        pytest.raises(FileExistsError),
    ):
        module._commit_output_pair(
            (
                (temporary_library, final_library),
                (temporary_json, final_json),
            ),
            overwrite=False,
        )

    assert not final_library.exists()
    assert final_json.read_text(encoding="utf-8") == "racing writer"


def test_export_rejects_architectureless_views_before_writing(tmp_path: Path):
    module = _load_module()
    source_path = tmp_path / "source.bin"
    source_path.write_bytes(b"binary")
    view = _FakeView(source_path)
    view.arch = None
    output_path = tmp_path / "raw.annotations.json"

    with pytest.raises(ValueError, match="requires a BinaryView with an architecture"):
        module.export_user_annotations(view, output_path)

    assert not output_path.exists()
    assert not output_path.with_suffix(".types.bntl").exists()
