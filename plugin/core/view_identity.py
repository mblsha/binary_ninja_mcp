"""Helpers for public BinaryView identifiers."""

from __future__ import annotations

from itertools import count
from hashlib import sha1
from pathlib import Path
from threading import Lock
from typing import Any
from weakref import WeakKeyDictionary


_VIEW_ID_LOCK = Lock()
_VIEW_ID_SEQUENCE = count(1)
_WEAK_VIEW_IDS: "WeakKeyDictionary[object, str]" = WeakKeyDictionary()
_STRONG_VIEW_IDS: dict[int, str] = {}


def _filename_digest(raw_filename: str | None) -> str | None:
    identity = normalize_view_filename_identity(raw_filename)
    if not identity:
        return None
    return sha1(identity.encode("utf-8")).hexdigest()[:12]


def normalize_view_filename_identity(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        normalized = str(Path(text).expanduser().resolve(strict=False))
    except Exception:
        normalized = text
    return normalized.lower()


def make_logical_view_id(raw_filename: str | None) -> str | None:
    digest = _filename_digest(raw_filename)
    if not digest:
        return None
    return f"logical-view-{digest}"


def _lookup_assigned_view_id(view: Any) -> str | None:
    if view is None:
        return None

    try:
        assigned = _WEAK_VIEW_IDS.get(view)
    except TypeError:
        assigned = _STRONG_VIEW_IDS.get(id(view))

    if assigned:
        return assigned

    for attr in ("_binja_mcp_view_id", "mcp_view_id"):
        try:
            raw = getattr(view, attr, None)
        except Exception:
            raw = None
        text = str(raw).strip() if raw is not None else ""
        if text:
            return text

    return None


def make_public_view_id(view: Any, raw_filename: str | None = None) -> str | None:
    if view is None:
        return None

    existing = _lookup_assigned_view_id(view)
    if existing:
        return existing

    digest = _filename_digest(raw_filename)
    with _VIEW_ID_LOCK:
        existing = _lookup_assigned_view_id(view)
        if existing:
            return existing

        sequence = next(_VIEW_ID_SEQUENCE)
        if digest:
            public_view_id = f"view-{digest}-{sequence:x}"
        else:
            public_view_id = f"view-session-{sequence:x}"

        try:
            _WEAK_VIEW_IDS[view] = public_view_id
        except TypeError:
            _STRONG_VIEW_IDS[id(view)] = public_view_id

        for attr in ("_binja_mcp_view_id", "mcp_view_id"):
            try:
                setattr(view, attr, public_view_id)
            except Exception:
                continue

        logical_view_id = make_logical_view_id(raw_filename)
        if logical_view_id:
            try:
                setattr(view, "_binja_mcp_logical_view_id", logical_view_id)
            except Exception:
                pass

        return public_view_id


def make_target_hint(view_id: str | None) -> str | None:
    text = str(view_id or "").strip()
    if not text:
        return None
    return f"--view-id {text}"
