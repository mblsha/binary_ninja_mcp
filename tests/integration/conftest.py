from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Generator

import pytest
import requests

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.api_versions import expected_api_version  # noqa: E402

from mcp_client import McpClient  # noqa: E402


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
    spawn = os.environ.get("BINJA_SPAWN") == "1"
    if not spawn:
        _wait_for_server(base_url, timeout_s=6.0)
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


@pytest.fixture
def client(base_url: str, binja_process) -> Generator[McpClient, None, None]:
    session = requests.Session()
    try:
        yield McpClient(base_url=base_url, session=session)
    finally:
        session.close()
