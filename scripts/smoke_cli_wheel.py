#!/usr/bin/env python3
"""Install a built wheel in a disposable environment and test outside the repo."""

import argparse
import glob
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    matches = glob.glob(str(args.wheel))
    if len(matches) != 1:
        parser.error("Expected exactly one wheel file")
    wheel = Path(matches[0]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="binja-wheel-smoke-") as directory:
        root = Path(directory)
        environment = root / "venv"
        subprocess.run(["uv", "venv", "--python", sys.executable, str(environment)], check=True)
        binaries = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binaries / ("python.exe" if os.name == "nt" else "python")
        subprocess.run(["uv", "pip", "install", "--python", str(python), str(wheel)], check=True)
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        for name in ("binja-cli", "binja-mcp"):
            executable = binaries / (f"{name}.exe" if os.name == "nt" else name)
            result = subprocess.run(
                [str(executable), "--help"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            assert "binja-cli" in result.stdout and "Sub-commands:" in result.stdout, result.stdout
        subprocess.run(
            [
                str(python),
                "-c",
                "import sys, binja_cli.cli; from pathlib import Path; assert Path(binja_cli.cli.__file__).is_relative_to(sys.prefix); assert 'binaryninja' not in sys.modules; assert 'plugin' not in sys.modules",
            ],
            cwd=root,
            env=env,
            check=True,
        )
        result = subprocess.run(
            [
                str(python),
                "-m",
                "binja_cli",
                "schema",
                "python",
                "--out",
                str(root / "schema.json"),
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        import json

        schema = json.loads((root / "schema.json").read_text())
        assert schema["scope"] == ["python"]
        assert json.loads(result.stdout)["artifact_path"] == str(root / "schema.json")
        print("Wheel smoke passed: both entry points run outside the checkout without the BN SDK")


if __name__ == "__main__":
    main()
