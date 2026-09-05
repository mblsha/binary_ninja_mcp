"""SDK-independent argument rules for the read-only analysis interfaces."""

from .build_info import snapshot_source
import math

LOADED_SOURCE = snapshot_source(__file__)

MAX_INSTRUCTIONS = 100_000
MAX_BUNDLE_FUNCTIONS = 256
BUNDLE_SECTIONS = (
    "decompile",
    "mlil",
    "llil",
    "disasm",
    "locals",
    "comments",
    "xrefs",
    "refs_from",
)
DEFAULT_BUNDLE_SECTIONS = ("decompile", "disasm", "refs_from")


def bundle_sections(include=None):
    if include is None:
        return list(DEFAULT_BUNDLE_SECTIONS)
    if isinstance(include, str):
        include = include.split(",")
    if not isinstance(include, (list, tuple)) or not all(isinstance(s, str) for s in include):
        raise ValueError("Bundle include must be a list or comma-separated section names")
    sections = list(dict.fromkeys("xrefs" if s.strip() == "refs" else s.strip() for s in include))
    if sections == ["all"]:
        return list(BUNDLE_SECTIONS)
    if not sections or any(s not in BUNDLE_SECTIONS for s in sections):
        raise ValueError("Bundle sections: " + ", ".join(BUNDLE_SECTIONS) + ", all (refs=xrefs)")
    return sections


def instruction_count(count):
    if isinstance(count, bool):
        raise ValueError("Instruction count must be an integer")
    try:
        value = int(str(count), 10)
    except (TypeError, ValueError):
        raise ValueError("Instruction count must be an integer") from None
    if not 1 <= value <= MAX_INSTRUCTIONS:
        raise ValueError(f"Instruction count must be between 1 and {MAX_INSTRUCTIONS}")
    return value


def analysis_time_budget(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            "Time budget must be a finite positive number (at most 3600 seconds)"
        ) from None
    if isinstance(value, bool) or not math.isfinite(result) or not 0 < result <= 3600:
        raise ValueError("Time budget must be a finite positive number (at most 3600 seconds)")
    return result
