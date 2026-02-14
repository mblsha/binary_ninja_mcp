from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.api_versions import expected_api_version, normalize_endpoint_path  # noqa: E402


@dataclass
class McpClient:
    base_url: str
    session: requests.Session

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        include_version: bool = True,
        version_override: int | None = None,
        timeout: float = 20.0,
    ) -> tuple[requests.Response, dict[str, Any]]:
        endpoint = normalize_endpoint_path(path)
        expected_version = expected_api_version(endpoint)
        request_version = expected_version if version_override is None else int(version_override)

        query = dict(params or {})
        payload = dict(json or {})
        headers: dict[str, str] = {}
        if include_version:
            headers["X-Binja-MCP-Api-Version"] = str(request_version)
            if method.upper() == "GET":
                query["_api_version"] = request_version
            else:
                payload["_api_version"] = request_version

        response = self.session.request(
            method=method.upper(),
            url=f"{self.base_url.rstrip('/')}{endpoint}",
            params=query,
            json=(payload if method.upper() != "GET" else None),
            headers=headers,
            timeout=timeout,
        )

        body = response.json()
        assert isinstance(body, dict), f"{method} {endpoint}: expected object JSON body"

        header_version = int(response.headers.get("X-Binja-MCP-Api-Version", "-1"))
        assert (
            header_version == expected_version
        ), f"{method} {endpoint}: header version {header_version} != {expected_version}"
        body_version = int(body.get("_api_version", -1))
        assert (
            body_version == expected_version
        ), f"{method} {endpoint}: body version {body_version} != {expected_version}"
        body_endpoint = normalize_endpoint_path(body.get("_endpoint"))
        assert (
            body_endpoint == endpoint
        ), f"{method} {endpoint}: body endpoint {body_endpoint!r} != {endpoint!r}"

        return response, body
