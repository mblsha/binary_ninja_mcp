"""Offline command schemas derived from the actual Plumbum parser definitions."""

import inspect

from .arguments import COMMON_OUTPUT_NAMES


def _switches(application):
    result = []
    for spec in application._switches_by_func.values():
        if spec.group == "Meta-switches":
            continue
        default = getattr(spec.func, "_default_value", None)
        result.append(
            {
                "names": [("-" if len(name) == 1 else "--") + name for name in spec.names],
                "takes_value": spec.argtype is not None,
                "type": getattr(
                    spec.argtype,
                    "__name__",
                    getattr(
                        getattr(spec.argtype, "func", None), "__name__", type(spec.argtype).__name__
                    ),
                )
                if spec.argtype
                else "boolean",
                "default": default,
                "choices": list(spec.argtype.values) if hasattr(spec.argtype, "values") else None,
                "required": spec.mandatory,
                "repeatable": spec.list,
                "help": spec.help,
                "placement": "anywhere before --"
                if any(name in COMMON_OUTPUT_NAMES for name in spec.names)
                else "command level",
            }
        )
    return sorted(result, key=lambda item: item["names"])


def _describe(application, path):
    signature = inspect.signature(application.main)
    positional = []
    for name, parameter in signature.parameters.items():
        if parameter.kind not in {
            parameter.POSITIONAL_ONLY,
            parameter.POSITIONAL_OR_KEYWORD,
            parameter.VAR_POSITIONAL,
        }:
            continue
        positional.append(
            {
                "name": name,
                "required": parameter.default is parameter.empty
                and parameter.kind is not parameter.VAR_POSITIONAL,
                "variadic": parameter.kind is parameter.VAR_POSITIONAL,
                "default": None if parameter.default is parameter.empty else parameter.default,
            }
        )
    return {
        "path": path,
        "description": inspect.getdoc(type(application)) or "",
        "arguments": _switches(application) if path else [],
        "positionals": positional,
        "subcommands": sorted(application._subcommands),
        "common_arguments_ref": "#/common_arguments",
        "command_defaults": {"format": getattr(type(application), "OUTPUT_FORMAT", "text")},
    }


def command_schema(root_class, path=()):
    root = root_class("binja-cli")
    selected = root
    walked = []
    for name in path:
        if name not in selected._subcommands:
            available = ", ".join(sorted(selected._subcommands))
            raise ValueError(
                f"Unknown schema command {' '.join([*walked, name])!r}; available: {available}"
            )
        selected = selected._subcommands[name].get()(name)
        walked.append(name)
    commands = []

    def visit(application, command_path):
        commands.append(_describe(application, command_path))
        for name, subcommand in sorted(application._subcommands.items()):
            visit(subcommand.get()(name), [*command_path, name])

    visit(selected, list(path))
    return {
        "schema_version": 1,
        "program": "binja-cli",
        "version": root.VERSION,
        "scope": list(path),
        "common_arguments": _switches(root),
        "commands": commands,
        "notes": [
            "Output flags work before or after commands, up to --; option values are not rewritten.",
            "Other root flags precede the command; command-local target selectors and short flags retain their meanings.",
            "NDJSON emits one record per top-level array item; nested arrays remain inside their object record.",
            "Schema generation is offline and does not require Binary Ninja.",
        ],
    }
