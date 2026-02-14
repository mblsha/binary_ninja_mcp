from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Generator

import pytest
import requests

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.api_versions import expected_api_version  # noqa: E402

from mcp_client import McpClient  # noqa: E402

STARTUP_FATAL_PATTERNS = (
    "could not connect to display",
    "could not load the qt platform plugin",
    "no qt platform plugin could be initialized",
    "this application failed to start because no qt platform plugin could be initialized",
    "fatal error",
)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _wait_for_server(base_url: str, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    ver = expected_api_version("/status")
    while time.time() < deadline:
        try:
            response = requests.get(
                f"{base_url.rstrip('/')}/status",
                params={"_api_version": ver},
                headers={"X-Binja-MCP-Api-Version": str(ver)},
                timeout=2,
            )
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(
        f"MCP server did not become reachable at {base_url} within {timeout_s} seconds"
    )


def _tail_log(log_path: str, max_lines: int = 60) -> str:
    path = Path(log_path)
    if not path.exists():
        return ""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except Exception:
        return ""
    if not lines:
        return ""
    tail = lines[-max_lines:]
    return "\n".join(tail)


def _detect_startup_failure(log_path: str) -> str | None:
    tail = _tail_log(log_path, max_lines=120)
    if not tail:
        return None
    lowered = tail.lower()
    for marker in STARTUP_FATAL_PATTERNS:
        if marker in lowered:
            return marker
    return None


def _kill_pid_or_group(pid: int, sig: int) -> None:
    if pid <= 1:
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


def _find_running_binja_pids(binary_path: str, include_any: bool = False) -> list[int]:
    path_hint = str(binary_path or "").strip()
    path_hint_lower = path_hint.lower()
    out: list[int] = []
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
        if path_hint and path_hint_lower in cmd_lower:
            out.append(pid)
            continue
        if include_any and ("binaryninja" in cmd_lower or "binja" in cmd_lower):
            out.append(pid)
    return sorted(set(p for p in out if p > 1))


def _kill_existing_binja_processes(binary_path: str, include_any: bool = False) -> int:
    pids = _find_running_binja_pids(binary_path=binary_path, include_any=include_any)
    killed = 0
    for pid in pids:
        try:
            _kill_pid_or_group(pid, signal.SIGTERM)
            time.sleep(0.1)
            _kill_pid_or_group(pid, signal.SIGKILL)
            killed += 1
        except Exception:
            continue
    return killed


def _terminate_process(proc: subprocess.Popen | None, grace_s: float = 8.0) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return

    _kill_pid_or_group(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=grace_s)
        return
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        return

    _kill_pid_or_group(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=2.0)
    except Exception:
        pass


def _cleanup_stale_pid_file(pid_file: Path) -> None:
    if not pid_file.exists():
        return
    try:
        raw = pid_file.read_text().strip()
        old_pid = int(raw)
    except Exception:
        old_pid = -1

    if old_pid > 1:
        _kill_pid_or_group(old_pid, signal.SIGTERM)
        time.sleep(0.5)
        _kill_pid_or_group(old_pid, signal.SIGKILL)
    try:
        pid_file.unlink()
    except Exception:
        pass


def _prepare_clean_restart(binary_path: str, pid_file: Path) -> None:
    _cleanup_stale_pid_file(pid_file)
    if _env_flag("BINJA_FORCE_RESTART", default=True):
        include_any = _env_flag("BINJA_KILL_ANY_BINJA", default=False)
        _kill_existing_binja_processes(binary_path=binary_path, include_any=include_any)


def _wait_for_server_or_fail_fast(
    base_url: str,
    timeout_s: float,
    log_path: str,
    proc: subprocess.Popen,
) -> None:
    deadline = time.time() + timeout_s
    ver = expected_api_version("/status")
    while time.time() < deadline:
        if _detect_startup_failure(log_path):
            _terminate_process(proc, grace_s=1.0)
            log_tail = _tail_log(log_path)
            raise RuntimeError(
                "Binary Ninja startup failed before MCP was reachable"
                + (f"\n--- binja log tail ---\n{log_tail}" if log_tail else "")
            )
        try:
            response = requests.get(
                f"{base_url.rstrip('/')}/status",
                params={"_api_version": ver},
                headers={"X-Binja-MCP-Api-Version": str(ver)},
                timeout=2,
            )
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(
        f"MCP server did not become reachable at {base_url} within {timeout_s} seconds"
    )


@pytest.fixture(scope="session", autouse=True)
def require_integration_mode() -> None:
    if os.environ.get("BINJA_INTEGRATION") != "1":
        pytest.skip("Set BINJA_INTEGRATION=1 to run real Binary Ninja integration tests")


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("BINJA_MCP_BASE_URL", "http://localhost:9009").rstrip("/")


@pytest.fixture(scope="session")
def fixture_binary_path() -> str:
    fixture = Path(os.environ.get("BINJA_FIXTURE_BINARY", "/bin/ls")).resolve()
    if not fixture.exists():
        pytest.skip(f"Fixture binary does not exist: {fixture}")
    return str(fixture)


@pytest.fixture(scope="session")
def binja_process(base_url: str) -> Generator[subprocess.Popen | None, None, None]:
    spawn = _env_flag("BINJA_SPAWN", default=True)
    if not spawn:
        _wait_for_server(base_url, timeout_s=10.0)
        yield None
        return

    binja_binary = os.environ.get("BINJA_BINARY")
    if not binja_binary:
        raise RuntimeError("BINJA_SPAWN=1 requires BINJA_BINARY to be set")
    if not Path(binja_binary).exists():
        raise RuntimeError(f"BINJA_BINARY does not exist: {binja_binary}")

    log_path = os.environ.get("BINJA_LOG_PATH", "/tmp/binja-integration.log")
    pid_file = Path(os.environ.get("BINJA_PID_FILE", "/tmp/binja-integration.pid"))
    _prepare_clean_restart(binary_path=binja_binary, pid_file=pid_file)
    with open(log_path, "ab") as log_fp:
        proc = subprocess.Popen(
            [binja_binary],
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=os.environ.copy(),
        )
    try:
        pid_file.write_text(str(proc.pid))
    except Exception:
        pass

    try:
        _wait_for_server_or_fail_fast(
            base_url=base_url,
            timeout_s=45.0,
            log_path=log_path,
            proc=proc,
        )
    except Exception as exc:
        _terminate_process(proc)
        log_tail = _tail_log(log_path)
        detail = f"{exc}"
        if log_tail:
            detail = f"{detail}\n--- binja log tail ---\n{log_tail}"
        raise RuntimeError(detail) from exc
    try:
        yield proc
    finally:
        _terminate_process(proc)
        try:
            pid_file.unlink()
        except Exception:
            pass


@pytest.fixture(scope="session")
def client(base_url: str, binja_process) -> Generator[McpClient, None, None]:
    session = requests.Session()
    try:
        yield McpClient(base_url=base_url, session=session)
    finally:
        session.close()


def _extract_function_names(body: dict[str, Any]) -> list[str]:
    names: list[str] = []
    raw_items = list(body.get("functions") or [])
    for item in raw_items:
        if isinstance(item, dict):
            name = item.get("name")
        else:
            name = item
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _extract_function_address(body: dict[str, Any]) -> str | None:
    raw_items = list(body.get("functions") or [])
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        address = item.get("address")
        if isinstance(address, str) and address.startswith("0x"):
            return address
    return None


@pytest.fixture(scope="session")
def destructive_ui_enabled() -> bool:
    return _env_flag("BINJA_UI_DESTRUCTIVE", default=False)


@pytest.fixture(scope="session")
def analysis_context(client: McpClient, fixture_binary_path: str) -> dict[str, str]:
    view_type = os.environ.get("BINJA_OPEN_VIEW_TYPE", "Raw")
    platform = os.environ.get("BINJA_OPEN_PLATFORM", "")

    open_response, open_body = client.request(
        "POST",
        "/ui/open",
        json={
            "filepath": fixture_binary_path,
            "view_type": view_type,
            "platform": platform,
            "click_open": True,
            "inspect_only": False,
        },
        timeout=60.0,
    )
    assert open_response.status_code == 200, open_body

    status_response, status_body = client.request("GET", "/status")
    assert status_response.status_code == 200, status_body
    assert status_body.get("loaded") is True

    functions_response, functions_body = client.request("GET", "/functions", params={"limit": 50})
    assert functions_response.status_code == 200, functions_body
    function_names = _extract_function_names(functions_body)
    assert function_names, "expected at least one function"
    function_name = function_names[0]
    function_prefix = function_name[: max(1, min(4, len(function_name)))]

    entry_address_response, entry_address_body = client.request(
        "POST",
        "/console/execute",
        json={"command": "hex(bv.entry_point) if bv else None"},
    )
    entry_address = entry_address_body.get("return_value")
    if not isinstance(entry_address, str) or not entry_address.startswith("0x"):
        fallback_address = _extract_function_address(functions_body)
        assert fallback_address, "expected a fallback function address"
        entry_address = fallback_address

    return {
        "fixture_binary_path": fixture_binary_path,
        "function_name": function_name,
        "function_prefix": function_prefix,
        "entry_address": entry_address,
        "missing_type": "mcp_missing_type_for_integration",
        "__FIXTURE_BINARY__": fixture_binary_path,
        "__FUNCTION__": function_name,
        "__FUNCTION_PREFIX__": function_prefix,
        "__ENTRY_ADDRESS__": entry_address,
        "__MISSING_TYPE__": "mcp_missing_type_for_integration",
    }
