from __future__ import annotations

import pytest


pytestmark = pytest.mark.binja


def test_endpoint_registry_available(client):
    response, body = client.request("GET", "/meta/endpoints")
    assert response.status_code == 200, body
    assert "endpoints" in body
    assert isinstance(body["endpoints"], list)
    assert body["endpoints"], "endpoint registry is empty"


def test_every_endpoint_exists_and_is_versioned(client):
    _, meta = client.request("GET", "/meta/endpoints")
    endpoints = meta["endpoints"]
    for endpoint in endpoints:
        method = endpoint["method"]
        path = endpoint["path"]
        params = endpoint.get("minimal_params") or {}
        payload = endpoint.get("minimal_json") or {}
        response, _ = client.request(method, path, params=params, json=payload, timeout=30.0)
        assert response.status_code != 404, f"{method} {path} returned 404"
        assert response.status_code < 500, f"{method} {path} returned {response.status_code}"


def test_every_endpoint_rejects_mismatched_api_version(client):
    _, meta = client.request("GET", "/meta/endpoints")
    endpoints = meta["endpoints"]
    for endpoint in endpoints:
        method = endpoint["method"]
        path = endpoint["path"]
        params = endpoint.get("minimal_params") or {}
        payload = endpoint.get("minimal_json") or {}
        response, body = client.request(
            method,
            path,
            params=params,
            json=payload,
            version_override=int(endpoint["api_version"]) + 99,
            timeout=20.0,
        )
        assert response.status_code == 409, f"{method} {path} expected 409, got {response.status_code}"
        assert body.get("error") == "Endpoint API version mismatch"


def test_missing_version_policy(client):
    _, meta = client.request("GET", "/meta/endpoints")
    endpoints = meta["endpoints"]
    for endpoint in endpoints:
        method = endpoint["method"]
        path = endpoint["path"]
        params = endpoint.get("minimal_params") or {}
        payload = endpoint.get("minimal_json") or {}
        response, body = client.request(
            method,
            path,
            params=params,
            json=payload,
            include_version=False,
            timeout=20.0,
        )
        if path == "/status":
            assert response.status_code == 200, body
        else:
            assert response.status_code == 400, f"{method} {path} expected 400 without version"
            assert body.get("error") == "Missing endpoint API version"
