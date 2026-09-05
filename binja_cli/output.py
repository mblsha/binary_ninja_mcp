"""Complete stdout, optional artifacts, and validated client-side text filters."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


DEFAULT_SPILL_BYTES = 40_000
OUTPUT_FORMATS = ("text", "json", "ndjson")


def render_value(value, fmt="json"):
    if fmt == "ndjson":
        values = value if isinstance(value, list) else [value]
        return "".join(
            json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n" for item in values
        )
    if fmt == "text" and isinstance(value, str):
        return value if not value or value.endswith("\n") else value + "\n"
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


@dataclass
class OutputOptions:
    format: str = "text"
    out: str | None = None
    overwrite: bool = False
    match: str | None = None
    before: int = 0
    after: int = 0
    spill: bool | None = None
    tokens: bool = False
    spill_bytes: int = DEFAULT_SPILL_BYTES

    def validate(self):
        if self.format not in OUTPUT_FORMATS:
            raise ValueError("--format must be text, json, or ndjson")
        if self.before < 0 or self.after < 0:
            raise ValueError("--before and --after must be non-negative line counts")
        if self.match is not None:
            if self.format != "text":
                raise ValueError(
                    "--match applies to text only; structured output is never filtered"
                )
            try:
                re.compile(self.match)
            except re.error as exc:
                raise ValueError(f"Invalid --match regular expression: {exc}") from exc
        elif self.before or self.after:
            raise ValueError("--before and --after require --match")
        if self.overwrite and not self.out:
            raise ValueError("--overwrite-output requires --out")
        if self.out is not None and not self.out.strip():
            raise ValueError("--out requires a non-empty file path")
        if self.out:
            path = Path(self.out).expanduser().absolute()
            if path.is_dir():
                raise ValueError(f"Output path is a directory: {path}")
            if (path.exists() or path.is_symlink()) and not self.overwrite:
                raise ValueError(f"Output exists: {path}; use --overwrite-output explicitly")
            if not path.parent.is_dir():
                raise ValueError(f"Output directory does not exist: {path.parent}")
            # Detect unwritable destinations before sending a mutating request.
            # Delivery can still fail later (races, disk full); report that honestly.
            try:
                with tempfile.TemporaryFile(dir=path.parent):
                    pass
            except OSError as exc:
                raise ValueError(f"Output directory is not writable: {path.parent}: {exc}") from exc

    def filter_text(self, rendered):
        if self.match is None:
            return rendered
        pattern = re.compile(self.match)
        lines = rendered.splitlines(keepends=True)
        selected = set()
        for index, line in enumerate(lines):
            if pattern.search(line):
                selected.update(
                    range(max(0, index - self.before), min(len(lines), index + self.after + 1))
                )
        output = []
        previous = None
        for index in sorted(selected):
            if previous is not None and index != previous + 1:
                output.append("--\n")
            output.append(lines[index])
            previous = index
        return "".join(output)


def _token_metadata(text):
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("o200k_base")
        return {
            "tokens": len(encoding.encode(text, disallowed_special=())),
            "tokenizer": "o200k_base",
        }
    except Exception as exc:
        return {
            "token_count_warning": f"Token count unavailable: {exc}; install binary-ninja-mcp[tokens]"
        }


def _write_explicit(path, encoded, overwrite):
    """Install a completed temporary file atomically, with no-clobber by default."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        elif os.name == "nt":
            # Windows rename fails if the destination exists; POSIX replaces it.
            os.rename(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def deliver_output(rendered, options, stdout, stderr):
    """Deliver already-rendered command output without silently discarding data.

    Output is buffered, not an incremental/constant-memory streaming protocol.
    JSON and NDJSON are complete on stdout unless --out or --spill was requested.
    """
    rendered = options.filter_text(rendered)
    encoded = rendered.encode("utf-8")
    should_spill = options.spill if options.spill is not None else options.format == "text"
    spilled = not options.out and should_spill and len(encoded) > options.spill_bytes
    metadata = _token_metadata(rendered) if options.tokens else {}
    if options.out or spilled:
        if options.out:
            path = Path(options.out).expanduser().absolute()
            _write_explicit(path, encoded, options.overwrite)
        else:
            directory = Path(
                os.environ.get(
                    "BINJA_CLI_OUTPUT_DIR", Path(tempfile.gettempdir()) / "binja-cli-output"
                )
            )
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            suffix = {"text": ".txt", "json": ".json", "ndjson": ".ndjson"}[options.format]
            fd, name = tempfile.mkstemp(prefix="binja-", suffix=suffix, dir=directory)
            path = Path(name).absolute()
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(encoded)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        artifact = {
            "artifact_path": str(path),
            "format": options.format,
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "spilled": bool(spilled),
            **metadata,
        }
        if spilled and options.format == "text":
            preview = "".join(rendered.splitlines(keepends=True)[:20])[:2000]
            stdout.write(preview)
            if preview and not preview.endswith("\n"):
                stdout.write("\n")
            stderr.write("Full output artifact: " + json.dumps(artifact, ensure_ascii=False) + "\n")
        else:
            stdout.write(render_value(artifact, "ndjson" if options.format == "ndjson" else "json"))
        return artifact
    stdout.write(rendered)
    if metadata:
        stderr.write("Output metadata: " + json.dumps(metadata, ensure_ascii=False) + "\n")
    return None
