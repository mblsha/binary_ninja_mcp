"""Cross-platform Binary Ninja runtime adapters.

This module centralizes OS-specific launch environment setup and process
management for CLI and integration harnesses.
"""

from __future__ import annotations

import os
import re
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

    @staticmethod
    def _runtime_dir(env: Mapping[str, str]) -> str:
        return str(env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")

    @staticmethod
    def _parse_display_number(display_value: str | None) -> int | None:
        raw = str(display_value or "").strip()
        if not raw:
            return None
        if raw.startswith(":"):
            suffix = raw[1:]
        elif ":" in raw:
            suffix = raw.split(":", 1)[1]
        else:
            return None
        number_text = suffix.split(".", 1)[0]
        if not number_text.isdigit():
            return None
        return int(number_text)

    @staticmethod
    def _is_network_x11_display(display_value: str | None) -> bool:
        raw = str(display_value or "").strip()
        return bool(raw and not raw.startswith(":"))

    def _has_x11_socket(self, display_value: str | None) -> bool:
        number = self._parse_display_number(display_value)
        if number is None:
            return False
        return Path(f"/tmp/.X11-unix/X{number}").exists()

    @staticmethod
    def _has_wayland_socket(runtime_dir: str, display_name: str | None) -> bool:
        raw = str(display_name or "").strip()
        if not raw:
            return False
        return Path(runtime_dir, raw).exists()

    def _detect_wayland_display(self, env: Mapping[str, str], runtime_dir: str) -> str | None:
        current = str(env.get("WAYLAND_DISPLAY", "")).strip()
        candidates: list[str] = []
        if current:
            candidates.append(current)
        if "wayland-0" not in candidates:
            candidates.append("wayland-0")

        for candidate in candidates:
            if self._has_wayland_socket(runtime_dir, candidate):
                return candidate

        # Keep an explicit caller-provided display as a best-effort fallback.
        if current:
            return current
        return None

    @staticmethod
    def _tigervnc_process_running(display_value: str) -> bool:
        display = str(display_value or "").strip()
        if not display:
            return False

        pattern = LinuxAdapter._display_token_pattern(display)
        if pattern is None:
            return False

        try:
            proc = subprocess.run(
                ["ps", "-eo", "args="],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except Exception:
            return False

        if not proc.stdout:
            return False

        for line in proc.stdout.splitlines():
            lowered = line.lower()
            has_tigervnc_marker = ("xtigervnc" in lowered) or (
                "tigervnc" in lowered and "vncserver" in lowered
            )
            if has_tigervnc_marker and pattern.search(lowered):
                return True
        return False

    @staticmethod
    def _display_token_pattern(display_value: str) -> re.Pattern[str] | None:
        number = LinuxAdapter._parse_display_number(display_value)
        if number is None:
            return None
        # Match :N or :N.screen but avoid accidental matches like :10 when looking for :1.
        return re.compile(rf"(?<![0-9]):{number}(?:\.[0-9]+)?(?![0-9])")

    def _detect_tigervnc_display(self) -> str | None:
        display = ":1"
        if self._has_x11_socket(display):
            return display
        if self._tigervnc_process_running(display):
            return display
        return None

    def _detect_existing_x11_display(self, env: Mapping[str, str]) -> str | None:
        display = str(env.get("DISPLAY", "")).strip()
        if not display:
            return None
        if self._is_network_x11_display(display):
            return display
        if self._has_x11_socket(display):
            return display
        return None

    def _detect_display_backend(self, env: Mapping[str, str]) -> tuple[str | None, str | None]:
        runtime_dir = self._runtime_dir(env)

        # Priority 1: Wayland (validated by socket where possible).
        wayland_display = self._detect_wayland_display(env, runtime_dir)
        if wayland_display:
            return "wayland", wayland_display

        x11_display = self._detect_existing_x11_display(env)
        if x11_display:
            return "x11", x11_display

        # Priority 2 fallback (only when no usable DISPLAY): TigerVNC on :1.
        tigervnc_display = self._detect_tigervnc_display()
        if tigervnc_display:
            return "x11", tigervnc_display

        return None, None

    def prepare_gui_env(self, source_env: Mapping[str, str]) -> dict[str, str]:
        env = dict(source_env)
        runtime_dir = self._runtime_dir(env)
        backend, display_value = self._detect_display_backend(env)

        if backend == "wayland":
            env["WAYLAND_DISPLAY"] = str(display_value or "wayland-0")
            env["XDG_RUNTIME_DIR"] = runtime_dir
            env["DBUS_SESSION_BUS_ADDRESS"] = env.get(
                "DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus"
            )
            env["XDG_SESSION_TYPE"] = env.get("XDG_SESSION_TYPE") or "wayland"
            env.pop("DISPLAY", None)
        elif backend == "x11":
            env["DISPLAY"] = str(display_value or "")
            env.pop("WAYLAND_DISPLAY", None)

        qpa_platform = str(env.get("BINJA_QPA_PLATFORM", "")).strip().lower()
        if qpa_platform:
            env["QT_QPA_PLATFORM"] = qpa_platform
        elif backend == "wayland":
            env["QT_QPA_PLATFORM"] = "wayland"
        elif backend == "x11":
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


def _pid_exists(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, but we do not have permission.
        return True
    except Exception:
        return False
    return True


def signal_pid(pid: int, sig: int) -> bool:
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        if hasattr(os, "killpg"):
            os.killpg(pid, sig)
            return True
    except ProcessLookupError:
        # Not a group leader or group missing; try direct pid below.
        pass
    except PermissionError:
        return False
    except Exception:
        pass
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    except Exception:
        return False


def terminate_pid_tree(pid: int, grace_s: float = 0.5) -> bool:
    if not isinstance(pid, int) or pid <= 1:
        return False
    if not _pid_exists(pid):
        return False

    sent_term = signal_pid(pid, signal.SIGTERM)
    if not sent_term:
        return False
    time.sleep(max(0.0, float(grace_s)))
    if not _pid_exists(pid):
        return True

    sent_kill = signal_pid(pid, signal.SIGKILL)
    if not sent_kill:
        return False

    for _ in range(20):
        if not _pid_exists(pid):
            return True
        time.sleep(0.05)
    return not _pid_exists(pid)
