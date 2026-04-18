"""Helpers for stable public BinaryView identifiers."""

from __future__ import annotations

from hashlib import sha1
from pathlib import Path
from typing import Optional


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


def make_public_view_id(raw_filename: str | None) -> str | None:
    identity = normalize_view_filename_identity(raw_filename)
    if not identity:
        return None
    digest = sha1(identity.encode("utf-8")).hexdigest()[:12]
    return f"view-{digest}"


def make_target_hint(view_id: str | None) -> str | None:
    text = str(view_id or "").strip()
    if not text:
        return None
    return f"--view-id {text}"
