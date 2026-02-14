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
    with open(log_path, "ab") as log_fp:
        proc = subprocess.Popen(
            [binja_binary],
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=os.environ.copy(),
        )

    try:
        _wait_for_server(base_url, timeout_s=45.0)
        yield proc
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
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
