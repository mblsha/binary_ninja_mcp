"""Bounded, loss-aware scalar and string decoding from a pinned BinaryView."""

import math
import struct

from shared.analysis_contract import MAX_READ_BYTES, read_arguments
from shared.build_info import snapshot_source
from .identifiers import AnalysisError

LOADED_SOURCE = snapshot_source(__file__)


def byte_order(view, requested="auto"):
    if requested in {"little", "big"}:
        return requested
    # BNEndianness is an IntEnum: LittleEndian=0, BigEndian=1. In particular,
    # str(IntEnum) may be "0"/"1", so searching its string for "Big" is wrong.
    try:
        value = view.endianness
    except Exception as exc:
        raise AnalysisError(
            "unknown_endianness",
            f"Cannot read the SDK's default endianness ({exc}); supply --endian little or big explicitly",
        ) from exc
    if not isinstance(value, bool) and value in {0, 1}:
        return "big" if value == 1 else "little"
    raise AnalysisError("unknown_endianness", "View reports an unknown endianness; supply --endian")


def _read_mapped(view, address, length):
    chunks = []
    remaining = length
    while remaining:
        segment = view.get_segment_at(address)
        if segment is None or not view.is_offset_readable(address):
            break
        size = min(remaining, int(segment.end) - address, 65536)
        if size <= 0:
            break
        raw = bytes(view.read(address, size))
        if len(raw) > size:
            raise AnalysisError("invalid_read", "BinaryView returned more bytes than requested")
        chunks.append(raw)
        address += len(raw)
        remaining -= len(raw)
        if len(raw) < size:
            break
    return b"".join(chunks)


def read_memory(view, resolver, identifier, *, value_type="bytes", count=None, endian="auto"):
    count = read_arguments(value_type, count, endian)
    address = resolver.address(identifier)
    width = (
        int(view.address_size)
        if value_type == "ptr"
        else ({"bytes": 1, "cstr": 1}.get(value_type) or int(value_type[1:]) // 8)
    )
    if not 1 <= width <= 16:
        raise AnalysisError("invalid_width", f"Unsupported pointer/scalar width: {width}")
    requested_bytes = count * width
    if requested_bytes > MAX_READ_BYTES or address + requested_bytes > 0x10000000000000000:
        raise AnalysisError(
            "invalid_range", "Read exceeds the byte limit or unsigned 64-bit address space"
        )
    order = byte_order(view, endian) if value_type not in {"bytes", "cstr"} else None
    raw = _read_mapped(view, address, requested_bytes)
    result = {
        "address": hex(address),
        "type": value_type,
        "width": width,
        "byte_order": order,
        "requested_count": count,
        "requested_bytes": requested_bytes,
        "bytes_read": len(raw),
        "raw": raw.hex(),
        "read_end_address": hex(address + len(raw)),
        "next_address": hex(address + len(raw)),
        "complete": len(raw) == requested_bytes,
        "stopped_reason": None if len(raw) == requested_bytes else "short_read",
    }
    if value_type == "bytes":
        result.update(
            count=len(raw),
            value=raw.hex(),
            ascii="".join(chr(b) if 32 <= b < 127 else "." for b in raw),
        )
    elif value_type == "cstr":
        terminated = b"\0" in raw
        string_bytes = raw.split(b"\0", 1)[0]
        try:
            value = string_bytes.decode("utf-8")
            encoding_errors = False
        except UnicodeDecodeError:
            value = string_bytes.decode("utf-8", errors="replace")
            encoding_errors = True
        result.update(
            count=len(string_bytes),
            value=value,
            terminated=terminated,
            encoding="utf-8",
            encoding_errors=encoding_errors,
            complete=terminated,
            stopped_reason=None
            if terminated
            else ("short_read" if len(raw) < requested_bytes else "unterminated"),
            next_address=hex(address + len(string_bytes) + int(terminated)),
        )
    else:
        values = []
        for offset in range(0, len(raw) - width + 1, width):
            item = raw[offset : offset + width]
            if value_type in {"f32", "f64"}:
                value = struct.unpack(
                    ("<" if order == "little" else ">") + ("f" if value_type == "f32" else "d"),
                    item,
                )[0]
                if not math.isfinite(value):
                    value = {
                        "type": "float",
                        "value": "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf"),
                    }
            else:
                value = int.from_bytes(item, byteorder=order, signed=value_type.startswith("i"))
            entry = {"address": hex(address + offset), "value": value}
            if value_type == "ptr":
                entry["hex"] = hex(value)
            values.append(entry)
        result.update(
            count=len(values),
            values=values,
            trailing_bytes=raw[len(values) * width :].hex(),
            next_address=hex(address + len(values) * width),
        )
    result["success"] = result["complete"]
    return result
