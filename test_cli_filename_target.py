#!/usr/bin/env python3

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_cli_injects_filename_target_into_requests() -> None:
    root = Path(__file__).resolve().parent
    script = root / "scripts" / "binja-cli.py"

    cmd = [
        sys.executable,
        str(script),
        "--server",
        "http://127.0.0.1:1",
        "--filename",
        "st2-maincpu.combined",
        "--verbose",
        "status",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)

    assert proc.returncode != 0
    assert "Params: {'filename': 'st2-maincpu.combined'" in proc.stderr
    assert "Data: {'filename': 'st2-maincpu.combined'" in proc.stderr
