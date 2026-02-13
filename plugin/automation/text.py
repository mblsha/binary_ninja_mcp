"""Pure text helpers used by UI automation workflows."""

from typing import Iterable


def normalize_token(value) -> str:
    """Normalize into an alnum-heavy token for robust matching."""
    text = str(value or "").strip().lower()
    return "".join(ch for ch in text if ch.isalnum() or ch in ("_", "."))


def normalize_label(value) -> str:
    """Normalize visible UI labels (ampersands, spacing, case)."""
    text = str(value or "").replace("&", "").strip().lower()
    return " ".join(text.split())


def find_item_index(items: Iterable[str], wanted_text: str) -> int:
    """Find best index by exact token match, then partial token match."""
    wanted_norm = normalize_token(wanted_text)
    if not wanted_norm:
        return -1

    partial_idx = -1
    for idx, item in enumerate(items):
        item_norm = normalize_token(item)
        if item_norm == wanted_norm:
            return idx
        if partial_idx < 0 and (wanted_norm in item_norm or item_norm in wanted_norm):
            partial_idx = idx
    return partial_idx
