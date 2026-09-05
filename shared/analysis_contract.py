"""SDK-independent argument rules for the read-only analysis interfaces."""

from .build_info import snapshot_source
import math

LOADED_SOURCE = snapshot_source(__file__)

MAX_INSTRUCTIONS = 100_000
MAX_BUNDLE_FUNCTIONS = 256
MAX_READ_BYTES = 8_000_000
MAX_READ_COUNT = 1_000_000
READ_TYPES = (
    "bytes",
    "u8",
    "u16",
    "u32",
    "u64",
    "i8",
    "i16",
    "i32",
    "i64",
    "f32",
    "f64",
    "ptr",
    "cstr",
)
IL_LEVELS = ("hlil", "mlil", "llil")
SEARCH_LEVELS = (*IL_LEVELS, "disasm")
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


def read_arguments(value_type, count=None, endian="auto"):
    if value_type not in READ_TYPES:
        raise ValueError("Read type must be one of: " + ", ".join(READ_TYPES))
    if endian not in {"auto", "little", "big"}:
        raise ValueError("Endianness must be auto, little or big")
    if count is None:
        count = 256 if value_type == "cstr" else (16 if value_type == "bytes" else 1)
    try:
        parsed_count = int(str(count), 10)
    except (TypeError, ValueError):
        raise ValueError("Read count must be an integer") from None
    if isinstance(count, bool) or not 1 <= parsed_count <= MAX_READ_COUNT:
        raise ValueError(f"Read count must be between 1 and {MAX_READ_COUNT}")
    return parsed_count


def strict_bool(value, name):
    if isinstance(value, bool):
        return value
    if str(value).lower() in {"true", "1"}:
        return True
    if str(value).lower() in {"false", "0"}:
        return False
    raise ValueError(f"{name} must be true or false")


def query_limit(value):
    try:
        result = int(str(value), 10)
    except (TypeError, ValueError):
        raise ValueError("Result limit must be an integer") from None
    if isinstance(value, bool) or not 1 <= result <= 100_000:
        raise ValueError("Result limit must be between 1 and 100000")
    return result


def query_scope(within):
    if within is None:
        return []
    if not isinstance(within, list) or len(within) > MAX_BUNDLE_FUNCTIONS:
        raise ValueError(f"Scope must be a list of at most {MAX_BUNDLE_FUNCTIONS} identifiers")
    if any(
        isinstance(item, bool) or not isinstance(item, (int, str)) or not str(item).strip()
        for item in within
    ):
        raise ValueError("Scope identifiers must be nonempty names or addresses")
    return within


def constant_value(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Constant must be a decimal or hexadecimal integer")
    try:
        text = str(value).strip()
        result = int(text, 16 if text.lower().lstrip("+-").startswith("0x") else 10)
    except ValueError:
        raise ValueError("Constant must be a decimal or hexadecimal integer") from None
    if not -(1 << 63) <= result < (1 << 64):
        raise ValueError("Constant is outside the signed/unsigned 64-bit range")
    return result


def callsite_context(value):
    try:
        result = int(str(value), 10)
    except (TypeError, ValueError):
        raise ValueError("Callsite context must be an integer") from None
    if isinstance(value, bool) or not 0 <= result <= 64:
        raise ValueError("Callsite context must be between 0 and 64 instructions")
    return result
