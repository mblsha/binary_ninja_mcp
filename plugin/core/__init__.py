"""Core operations, also importable through the legacy plugin-only sys.path."""

import sys
from pathlib import Path

# Binary Ninja may import the repo as a package with only its parent directory
# on sys.path. Prefer this checkout's shared protocol modules in either layout.
_repository_root = str(Path(__file__).resolve().parents[2])
if _repository_root not in sys.path:
    sys.path.insert(0, _repository_root)
