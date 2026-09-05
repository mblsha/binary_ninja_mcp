"""Normalize unambiguous common output options without rewriting values."""

COMMON_OUTPUT_NAMES = frozenset(
    {
        "json",
        "j",
        "format",
        "out",
        "overwrite-output",
        "match",
        "before",
        "after",
        "spill",
        "no-spill",
        "tokens",
    }
)


def normalize_output_options(application, argv):
    """Allow common output flags anywhere, respecting option values and `--`.

    Target flags retain their existing placement: close's --view-id is a tab
    selector, for example, not a root request target. Short -s/-t keep their
    existing command-local meanings.
    """
    argv = list(argv)
    root = application(argv[0])
    current = root
    common = []
    remaining = []
    is_meta = False
    index = 1
    while index < len(argv):
        token = argv[index]
        index += 1
        if token == "--":
            remaining.extend([token, *argv[index:]])
            break
        if token.startswith("-"):
            name = token.lstrip("-").split("=", 1)[0]
            spec = (
                root._switches_by_name.get(name)
                if name in COMMON_OUTPUT_NAMES
                else current._switches_by_name.get(name)
            )
            destination = common if name in COMMON_OUTPUT_NAMES and spec is not None else remaining
            destination.append(token)
            if spec is not None:
                if any(alias in {"help", "help-all", "version"} for alias in spec.names):
                    is_meta = True
                if spec.argtype is not None and "=" not in token and index < len(argv):
                    destination.append(argv[index])
                    index += 1
        else:
            remaining.append(token)
            command = current._subcommands.get(token)
            if command is not None:
                current = command.get()(token)
    return [argv[0], *common, *remaining], is_meta
