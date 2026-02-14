"""Cross-platform Binary Ninja runtime adapters.

This module centralizes OS-specific launch environment setup and process
management for CLI and integration harnesses.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping, Protocol, Sequence


class BinaryNinjaPlatformAdapter(Protocol):
    """Adapter contract for platform-specific runtime behavior."""

    platform_key: str

    def supports_auto_launch(self) -> bool: ...

    def normalize_binary_path(self, path: str) -> str: ...

    def resolve_binary_path(
        self,
        *,
        explicit_path: str | None = None,
        extra_candidates: Sequence[str | None] | None = None,
    ) -> str | None: ...

    def prepare_gui_env(self, source_env: Mapping[str, str]) -> dict[str, str]: ...

    def process_name_tokens(self) -> tuple[str, ...]: ...


class _BaseAdapter:
    platform_key = "generic"

    def supports_auto_launch(self) -> bool:
        return False

    def normalize_binary_path(self, path: str) -> str:
        return str(path or "").strip()

    def _default_binary_candidates(self) -> list[str]:
        return []

    def process_name_tokens(self) -> tuple[str, ...]:
        return ("binaryninja", "binja")

    def prepare_gui_env(self, source_env: Mapping[str, str]) -> dict[str, str]:
        return dict(source_env)

    def resolve_binary_path(
        self,
        *,
        explicit_path: str | None = None,
        extra_candidates: Sequence[str | None] | None = None,
    ) -> str | None:
        candidates: list[str | None] = [explicit_path]
        if extra_candidates:
            candidates.extend(extra_candidates)
        candidates.extend(self._default_binary_candidates())
        candidates.extend(["binaryninja", "BinaryNinja"])

        seen: set[str] = set()
        for candidate in candidates:
            if not candidate:
                continue
            normalized = self.normalize_binary_path(str(candidate))
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)

            if os.path.sep not in normalized:
                resolved = shutil.which(normalized)
                if resolved and os.path.isfile(resolved) and os.access(resolved, os.X_OK):
                    return resolved
                continue

            expanded = os.path.expanduser(normalized)
            if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
                return expanded
        return None


class LinuxAdapter(_BaseAdapter):
    platform_key = "linux"

    def supports_auto_launch(self) -> bool:
        return True

    def _default_binary_candidates(self) -> list[str]:
        home = Path.home()
        return [
            str(home / "src" / "binja" / "binaryninja" / "binaryninja"),
            str(home / "binaryninja" / "binaryninja"),
            str(home / ".binaryninja" / "binaryninja"),
            "/opt/binaryninja/binaryninja",
        ]

    def prepare_gui_env(self, source_env: Mapping[str, str]) -> dict[str, str]:
        env = dict(source_env)
        runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        has_wayland = bool(env.get("WAYLAND_DISPLAY"))
        has_x11 = bool(env.get("DISPLAY"))

        if not has_wayland and not has_x11:
            # Linux default for non-login shells where display vars are missing.
            env["WAYLAND_DISPLAY"] = "wayland-0"
            env["XDG_RUNTIME_DIR"] = runtime_dir
            env["DBUS_SESSION_BUS_ADDRESS"] = env.get(
                "DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus"
            )
            env["XDG_SESSION_TYPE"] = env.get("XDG_SESSION_TYPE") or "wayland"
            has_wayland = True

        qpa_platform = str(env.get("BINJA_QPA_PLATFORM", "")).strip().lower()
        if qpa_platform:
            env["QT_QPA_PLATFORM"] = qpa_platform
        elif has_wayland:
            env["QT_QPA_PLATFORM"] = "wayland"
            env["WAYLAND_DISPLAY"] = env.get("WAYLAND_DISPLAY") or "wayland-0"
            env["XDG_RUNTIME_DIR"] = runtime_dir
            env["DBUS_SESSION_BUS_ADDRESS"] = env.get(
                "DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus"
            )
            env["XDG_SESSION_TYPE"] = env.get("XDG_SESSION_TYPE") or "wayland"
        elif has_x11:
            env.pop("QT_QPA_PLATFORM", None)

        return env


class MacOSAdapter(_BaseAdapter):
    platform_key = "darwin"

    def supports_auto_launch(self) -> bool:
        return True

    def normalize_binary_path(self, path: str) -> str:
        raw = str(path or "").strip()
        if not raw:
            return ""
        expanded = str(Path(raw).expanduser())
        lowered = expanded.lower()
        if lowered.endswith(".app"):
            return str(Path(expanded) / "Contents" / "MacOS" / "binaryninja")
        return expanded

    def _default_binary_candidates(self) -> list[str]:
        return [
            "/Applications/Binary Ninja.app",
            "~/Applications/Binary Ninja.app",
            "/Applications/Binary Ninja.app/Contents/MacOS/binaryninja",
            "~/Applications/Binary Ninja.app/Contents/MacOS/binaryninja",
        ]

    def process_name_tokens(self) -> tuple[str, ...]:
        return (
            "binary ninja.app/contents/macos/binaryninja",
            "binaryninja",
            "binja",
        )

    def prepare_gui_env(self, source_env: Mapping[str, str]) -> dict[str, str]:
        env = dict(source_env)
        qpa_platform = str(env.get("BINJA_QPA_PLATFORM", "")).strip().lower()
        if qpa_platform:
            env["QT_QPA_PLATFORM"] = qpa_platform
        return env


def get_platform_adapter(platform_name: str | None = None) -> BinaryNinjaPlatformAdapter:
    key = (platform_name or sys.platform or "").lower()
    if key.startswith("linux"):
        return LinuxAdapter()
    if key.startswith("darwin"):
        return MacOSAdapter()
    return _BaseAdapter()


def prepare_log_file(log_path: str) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")


def find_binary_ninja_pids(
    *,
    binary_path: str,
    include_any: bool = False,
    adapter: BinaryNinjaPlatformAdapter | None = None,
) -> list[int]:
    out: list[int] = []
    runtime = adapter or get_platform_adapter()
    path_hint = runtime.normalize_binary_path(binary_path).lower()
    tokens = tuple(token.lower() for token in runtime.process_name_tokens())

    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return out

    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid_text, cmd = parts
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        cmd_lower = cmd.lower()
        if path_hint and path_hint in cmd_lower:
            out.append(pid)
            continue
        if include_any and any(token in cmd_lower for token in tokens):
            out.append(pid)
    return sorted(set(pid for pid in out if pid > 1))


def signal_pid(pid: int, sig: int) -> None:
    if not isinstance(pid, int) or pid <= 1:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except ProcessLookupError:
        return
    except Exception:
        return


def terminate_pid_tree(pid: int, grace_s: float = 0.5) -> bool:
    if not isinstance(pid, int) or pid <= 1:
        return False
    terminated = False
    try:
        signal_pid(pid, signal.SIGTERM)
        terminated = True
    except Exception:
        return False
    time.sleep(max(0.0, float(grace_s)))
    signal_pid(pid, signal.SIGKILL)
    return terminated
