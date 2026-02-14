#!/usr/bin/env python3
"""Fail if repository files contain forbidden hidden/bidi Unicode controls."""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

FORBIDDEN_CODEPOINTS = {
    0x061C,  # ARABIC LETTER MARK
    0x200B,  # ZERO WIDTH SPACE
    0x200C,  # ZERO WIDTH NON-JOINER
    0x200D,  # ZERO WIDTH JOINER
    0x200E,  # LEFT-TO-RIGHT MARK
    0x200F,  # RIGHT-TO-LEFT MARK
    0x2060,  # WORD JOINER
    0xFEFF,  # ZERO WIDTH NO-BREAK SPACE/BOM
    0x202A,  # LEFT-TO-RIGHT EMBEDDING
    0x202B,  # RIGHT-TO-LEFT EMBEDDING
    0x202C,  # POP DIRECTIONAL FORMATTING
    0x202D,  # LEFT-TO-RIGHT OVERRIDE
    0x202E,  # RIGHT-TO-LEFT OVERRIDE
    0x2066,  # LEFT-TO-RIGHT ISOLATE
    0x2067,  # RIGHT-TO-LEFT ISOLATE
    0x2068,  # FIRST STRONG ISOLATE
    0x2069,  # POP DIRECTIONAL ISOLATE
}

SKIP_DIRS = {".git", ".venv", "__pycache__", ".ruff_cache"}


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".", help="Path to scan (default: repo root).")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    failures = []
    for path in iter_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for line_num, line in enumerate(text.splitlines(), start=1):
            for col_num, ch in enumerate(line, start=1):
                codepoint = ord(ch)
                if codepoint not in FORBIDDEN_CODEPOINTS:
                    continue
                name = unicodedata.name(ch, "UNKNOWN")
                failures.append(
                    f"{path.relative_to(root)}:{line_num}:{col_num} "
                    f"U+{codepoint:04X} {name}"
                )

    if failures:
        print("Found forbidden hidden/bidi Unicode characters:")
        for item in failures:
            print(f"  {item}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
