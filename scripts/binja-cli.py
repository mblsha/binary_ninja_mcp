#!/usr/bin/env python3
"""Compatibility entry point for running the CLI from a source checkout."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from binja_cli import cli as _implementation  # noqa: E402


def __getattr__(name):
    """Keep existing source-loader integrations working after packaging."""
    return getattr(_implementation, name)


if __name__ == "__main__":
    _implementation.main()
