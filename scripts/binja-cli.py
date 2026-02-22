#!/usr/bin/env python3
"""
Binary Ninja MCP CLI - Command-line interface for Binary Ninja MCP server
Uses the same HTTP API as the MCP bridge but provides a terminal interface
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
import requests
from plumbum import cli, colors

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.api_versions import (  # noqa: E402
    SUPPORTED_UI_CONTRACT_SCHEMA_VERSIONS,
    expected_api_version,
    normalize_endpoint_path,
)
from shared.platform import (  # noqa: E402
    find_binary_ninja_pids,
    get_platform_adapter,
    prepare_log_file,
    terminate_pid_tree,
)

STARTUP_FATAL_PATTERNS = (
    "could not connect to display",
    "could not load the qt platform plugin",
    "no qt platform plugin could be initialized",
    "this application failed to start because no qt platform plugin could be initialized",
    "fatal error",
)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


class BinaryNinjaCLI(cli.Application):
    """Binary Ninja MCP command-line interface"""

    PROGNAME = "binja-mcp"
    VERSION = "0.2.7"
    DESCRIPTION = "Command-line interface for Binary Ninja MCP server"

    server_url = cli.SwitchAttr(
        ["--server", "-s"], str, default="http://localhost:9009", help="MCP server URL"
    )

    target_filename = cli.SwitchAttr(
        ["--filename", "--target-file"],
        str,
        default="",
        help=(
            "Prefer a loaded BinaryView matching this file path/name for this command. "
            "Useful when multiple binaries are open."
        ),
    )

    target_view_id = cli.SwitchAttr(
        ["--view-id"],
        str,
        default="",
        help=(
            "Prefer a loaded BinaryView matching this view id for this command. "
            "Use with --filename for deterministic per-view scripting."
        ),
    )

    strict_target = cli.Flag(
        ["--strict-target"],
        help=(
            "Force strict target validation before each command. "
            "By default, strict validation is already enabled when --filename or --view-id is provided."
        ),
    )

    allow_target_fallback = cli.Flag(
        ["--allow-target-fallback"],
        help=(
            "Disable strict target validation when using --filename/--view-id. "
            "Use only when you intentionally want best-effort fallback behavior."
        ),
    )

    json_output = cli.Flag(["--json", "-j"], help="Output raw JSON response")

    verbose = cli.Flag(["--verbose", "-v"], help="Verbose output")

    request_timeout = cli.SwitchAttr(
        ["--request-timeout", "-t"],
        float,
        default=_float_env("BINJA_CLI_TIMEOUT", 5.0),
        help=("HTTP request timeout in seconds (default: 5; can also set BINJA_CLI_TIMEOUT)"),
    )

    @staticmethod
    def _normalize_endpoint_path(endpoint: str) -> str:
        return normalize_endpoint_path(endpoint)

    def _expected_api_version(self, endpoint: str) -> int:
        return expected_api_version(endpoint)

    @staticmethod
    def _validate_ui_contract(payload: dict, endpoint: str) -> dict:
        required_keys = {
            "ok",
            "actions",
            "warnings",
            "errors",
            "state",
            "result",
            "schema_version",
            "endpoint",
        }
        missing = [key for key in required_keys if key not in payload]
        if missing:
            raise RuntimeError(
                f"invalid UI response contract for {endpoint}: missing keys {', '.join(missing)}"
            )

        try:
            schema_version = int(payload.get("schema_version"))
        except (TypeError, ValueError):
            raise RuntimeError(
                f"invalid UI response contract for {endpoint}: "
                f"schema_version={payload.get('schema_version')!r}"
            )
        if schema_version not in SUPPORTED_UI_CONTRACT_SCHEMA_VERSIONS:
            supported = sorted(SUPPORTED_UI_CONTRACT_SCHEMA_VERSIONS)
            raise RuntimeError(
                f"unsupported UI schema_version for {endpoint}: "
                f"{schema_version} (supported: {supported})"
            )

        expected_endpoint = normalize_endpoint_path(endpoint)
        actual_endpoint = normalize_endpoint_path(payload.get("endpoint"))
        if actual_endpoint != expected_endpoint:
            raise RuntimeError(
                f"invalid UI response contract endpoint: expected {expected_endpoint}, got {actual_endpoint}"
            )
        return payload

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict = None,
        data: dict = None,
        timeout: float = None,
    ) -> dict:
        """Make HTTP request to the server"""
        endpoint_path = self._normalize_endpoint_path(endpoint)
        url = f"{self.server_url}/{endpoint.lstrip('/')}"
        request_timeout = self.request_timeout if timeout is None else float(timeout)
        expected_api_version = self._expected_api_version(endpoint_path)
        strict_selected_filename = None
        strict_selected_view_id = None

        request_headers = {
            "X-Binja-MCP-Api-Version": str(expected_api_version),
        }
        request_params = dict(params or {})
        request_data = dict(data or {})
        if self.target_filename:
            request_params.setdefault("filename", self.target_filename)
            request_data.setdefault("filename", self.target_filename)
        if self.target_view_id:
            request_params.setdefault("view_id", self.target_view_id)
            request_data.setdefault("view_id", self.target_view_id)
        request_params["_api_version"] = expected_api_version
        request_data["_api_version"] = expected_api_version

        if self.verbose:
            print(f"[{method}] {url}", file=sys.stderr)
            if request_params:
                print(f"Params: {request_params}", file=sys.stderr)
            if request_data:
                print(f"Data: {request_data}", file=sys.stderr)

        try:
            targeting_requested = bool(self.target_filename or self.target_view_id)
            enforce_strict_target = bool(
                self.strict_target or (targeting_requested and not self.allow_target_fallback)
            )
            strict_requires_precheck = (
                enforce_strict_target
                and targeting_requested
                and endpoint_path not in {"/status", "/views", "/ui/open", "/load"}
            )
            if strict_requires_precheck:
                strict_selected_filename, strict_selected_view_id = self._assert_strict_target_selected(
                    timeout=request_timeout
                )

            if method == "GET":
                response = requests.get(
                    url,
                    params=request_params,
                    headers=request_headers,
                    timeout=request_timeout,
                )
            else:
                response = requests.post(
                    url,
                    json=request_data,
                    headers=request_headers,
                    timeout=request_timeout,
                )

            response.raise_for_status()
            response_data = response.json()

            header_version_raw = response.headers.get("X-Binja-MCP-Api-Version")
            if header_version_raw is None:
                raise RuntimeError(
                    f"missing X-Binja-MCP-Api-Version response header for {endpoint_path}"
                )
            try:
                header_version = int(header_version_raw)
            except (TypeError, ValueError):
                raise RuntimeError(
                    f"invalid X-Binja-MCP-Api-Version header '{header_version_raw}' for {endpoint_path}"
                )
            if header_version != expected_api_version:
                raise RuntimeError(
                    f"endpoint API version mismatch for {endpoint_path}: "
                    f"client={expected_api_version}, server_header={header_version}"
                )

            body_version_raw = (
                response_data.get("_api_version") if isinstance(response_data, dict) else None
            )
            if body_version_raw is None:
                raise RuntimeError(f"missing _api_version response field for {endpoint_path}")
            try:
                body_version = int(body_version_raw)
            except (TypeError, ValueError):
                raise RuntimeError(
                    f"invalid _api_version response field '{body_version_raw}' for {endpoint_path}"
                )
            if body_version != expected_api_version:
                raise RuntimeError(
                    f"endpoint API version mismatch for {endpoint_path}: "
                    f"client={expected_api_version}, server_body={body_version}"
                )

            if isinstance(response_data, dict):
                observed_filename = (
                    self._extract_observed_filename(response_data) or strict_selected_filename
                )
                observed_view_id = self._extract_observed_view_id(response_data) or strict_selected_view_id

                should_validate_target = (
                    enforce_strict_target and targeting_requested and endpoint_path != "/views"
                )
                if should_validate_target:
                    if self.target_view_id and not self._view_id_matches_requested(
                        observed_view_id, self.target_view_id
                    ):
                        (
                            resolved_filename,
                            resolved_view_id,
                        ) = self._resolve_target_via_views(timeout=request_timeout)
                        observed_filename = observed_filename or resolved_filename
                        observed_view_id = observed_view_id or resolved_view_id

                    if self.target_filename and not self._filename_matches_requested(
                        observed_filename, self.target_filename
                    ):
                        if self.target_view_id:
                            (
                                resolved_filename,
                                resolved_view_id,
                            ) = self._resolve_target_via_views(timeout=request_timeout)
                            observed_filename = resolved_filename or observed_filename
                            observed_view_id = observed_view_id or resolved_view_id
                        else:
                            # Some endpoints (e.g. /ui/open) may not return loaded filename immediately.
                            observed_filename = self._resolve_target_via_status(timeout=request_timeout)

                    if self.target_filename and not self._filename_matches_requested(
                        observed_filename, self.target_filename
                    ):
                        raise RuntimeError(
                            "strict target mismatch: "
                            f"requested '{self.target_filename}', observed '{observed_filename}'"
                        )
                    if self.target_view_id and not self._view_id_matches_requested(
                        observed_view_id, self.target_view_id
                    ):
                        raise RuntimeError(
                            "strict target mismatch: "
                            f"requested view_id '{self.target_view_id}', observed '{observed_view_id}'"
                        )

                response_data.setdefault("selected_view_filename", observed_filename)
                response_data.setdefault("selected_view_id", observed_view_id)

            return response_data

        except requests.exceptions.ConnectionError:
            print(
                colors.red | f"Error: Cannot connect to server at {self.server_url}",
                file=sys.stderr,
            )
            print("Make sure the MCP server is running in Binary Ninja", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.HTTPError as e:
            # Try to parse JSON error response for structured error info
            try:
                error_data = e.response.json()
                if isinstance(error_data, dict) and "error" in error_data:
                    # Display main error
                    print(colors.red | f"Error: {error_data['error']}", file=sys.stderr)

                    # Display additional context if available
                    if "help" in error_data:
                        print(colors.yellow | f"Help: {error_data['help']}", file=sys.stderr)

                    if "received" in error_data:
                        print(f"Received: {error_data['received']}", file=sys.stderr)

                    if "requested_name" in error_data:
                        print(f"Requested: {error_data['requested_name']}", file=sys.stderr)

                    # Show available functions if provided (e.g., for function not found errors)
                    if "available_functions" in error_data and error_data["available_functions"]:
                        funcs = error_data["available_functions"][:5]  # Show first 5
                        print("\nAvailable functions:", file=sys.stderr)
                        for func in funcs:
                            print(f"  • {func}", file=sys.stderr)
                        if len(error_data["available_functions"]) > 5:
                            remaining = len(error_data["available_functions"]) - 5
                            print(f"  ... and {remaining} more", file=sys.stderr)

                    # Show exception details if available (for debugging)
                    if "exception" in error_data and self.verbose:
                        print(f"\nException details: {error_data['exception']}", file=sys.stderr)
                else:
                    # Not a structured error, show raw response
                    print(colors.red | f"HTTP Error: {e}", file=sys.stderr)
                    if hasattr(e.response, "text"):
                        print(e.response.text, file=sys.stderr)
            except (json.JSONDecodeError, AttributeError):
                # Failed to parse JSON, fall back to showing raw error
                print(colors.red | f"HTTP Error: {e}", file=sys.stderr)
                if hasattr(e.response, "text"):
                    print(e.response.text, file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(colors.red | f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    @staticmethod
    def _filename_matches_requested(observed: str | None, requested: str | None) -> bool:
        if not observed or not requested:
            return False
        observed_text = str(observed).strip()
        requested_text = str(requested).strip()
        if not observed_text or not requested_text:
            return False

        requested_path = Path(requested_text).expanduser()
        observed_path = Path(observed_text).expanduser()

        # If the request contains an explicit path, require full path match.
        if any(sep in requested_text for sep in ("/", "\\")):
            try:
                observed_norm = str(observed_path.resolve(strict=False))
            except Exception:
                observed_norm = str(observed_path)
            try:
                requested_norm = str(requested_path.resolve(strict=False))
            except Exception:
                requested_norm = str(requested_path)
            if os.name == "nt":
                return observed_norm.lower() == requested_norm.lower()
            return observed_norm == requested_norm

        observed_base = observed_path.name
        requested_base = requested_path.name
        if os.name == "nt":
            return observed_base.lower() == requested_base.lower()
        return observed_base == requested_base

    def _resolve_target_via_status(self, timeout: float) -> str | None:
        endpoint_path = "/status"
        expected_api_version = self._expected_api_version(endpoint_path)
        url = f"{self.server_url}/status"
        response = requests.get(
            url,
            params={
                "_api_version": expected_api_version,
                "filename": self.target_filename,
            },
            headers={"X-Binja-MCP-Api-Version": str(expected_api_version)},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("strict target check failed: unexpected /status payload")
        return self._extract_observed_filename(payload)

    @staticmethod
    def _view_id_candidates(raw: object | None) -> set[str]:
        if raw is None:
            return set()
        text = str(raw).strip()
        if not text:
            return set()

        candidates = {text, text.lower()}
        try:
            value = int(text, 0)
            candidates.add(str(value))
            candidates.add(hex(value))
        except Exception:
            pass
        return candidates

    @classmethod
    def _view_id_matches_requested(cls, observed: object | None, requested: object | None) -> bool:
        return bool(cls._view_id_candidates(observed).intersection(cls._view_id_candidates(requested)))

    def _resolve_target_via_views(self, timeout: float) -> tuple[str | None, object | None]:
        endpoint_path = "/views"
        expected_api_version = self._expected_api_version(endpoint_path)
        url = f"{self.server_url}/views"
        params = {
            "_api_version": expected_api_version,
        }
        if self.target_filename:
            params["filename"] = self.target_filename
        if self.target_view_id:
            params["view_id"] = self.target_view_id

        response = requests.get(
            url,
            params=params,
            headers={"X-Binja-MCP-Api-Version": str(expected_api_version)},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("strict target check failed: unexpected /views payload")

        current_filename = self._extract_observed_filename(payload)
        current_view_id = payload.get("current_view_id")

        views = payload.get("views")
        if isinstance(views, list):
            target_entry = None
            for entry in views:
                if not isinstance(entry, dict):
                    continue
                if entry.get("is_current"):
                    target_entry = entry
                    break
            if target_entry is None and self.target_view_id:
                for entry in views:
                    if isinstance(entry, dict) and self._view_id_matches_requested(
                        entry.get("view_id"), self.target_view_id
                    ):
                        target_entry = entry
                        break
            if target_entry is None and self.target_filename:
                for entry in views:
                    if isinstance(entry, dict) and self._filename_matches_requested(
                        entry.get("filename"), self.target_filename
                    ):
                        target_entry = entry
                        break

            if isinstance(target_entry, dict):
                if current_filename is None:
                    current_filename = target_entry.get("filename")
                if current_view_id is None:
                    current_view_id = target_entry.get("view_id")

        return current_filename, current_view_id

    def _assert_strict_target_selected(self, timeout: float) -> tuple[str | None, object | None]:
        observed_filename = None
        observed_view_id = None

        if self.target_view_id:
            observed_filename, observed_view_id = self._resolve_target_via_views(timeout=timeout)
        elif self.target_filename:
            observed_filename = self._resolve_target_via_status(timeout=timeout)

        if self.target_filename and not self._filename_matches_requested(
            observed_filename, self.target_filename
        ):
            raise RuntimeError(
                "strict target mismatch: "
                f"requested '{self.target_filename}', observed '{observed_filename}'"
            )

        if self.target_view_id and not self._view_id_matches_requested(
            observed_view_id, self.target_view_id
        ):
            raise RuntimeError(
                "strict target mismatch: "
                f"requested view_id '{self.target_view_id}', observed '{observed_view_id}'"
            )

        return observed_filename, observed_view_id

    def _wait_for_open_target_in_views(
        self,
        requested_filename: str,
        *,
        timeout: float = 6.0,
        poll_interval: float = 0.1,
    ) -> dict:
        requested = str(requested_filename or "").strip()
        if not requested:
            return {
                "ok": False,
                "error": "missing requested filename for open target confirmation",
                "requested_filename": requested_filename,
                "observed_current_filename": None,
                "observed_current_view_id": None,
                "views": [],
            }

        try:
            timeout_s = float(timeout)
        except (TypeError, ValueError):
            timeout_s = 6.0
        if timeout_s < 0.0:
            timeout_s = 0.0

        try:
            sleep_s = float(poll_interval)
        except (TypeError, ValueError):
            sleep_s = 0.1
        if sleep_s < 0.01:
            sleep_s = 0.01

        deadline = time.monotonic() + timeout_s
        last_payload: dict = {}
        last_views: list = []
        last_current_filename = None
        last_current_view_id = None

        while True:
            remaining = max(0.0, deadline - time.monotonic())
            req_timeout = max(self.request_timeout, 1.0)
            if timeout_s > 0.0:
                req_timeout = max(1.0, min(req_timeout, remaining + 1.0))

            payload = self._request(
                "GET",
                "views",
                params={"filename": requested},
                timeout=req_timeout,
            )
            if isinstance(payload, dict):
                last_payload = payload
                raw_views = payload.get("views")
                last_views = raw_views if isinstance(raw_views, list) else []
                last_current_filename = payload.get("current_filename")
                last_current_view_id = payload.get("current_view_id")

                matched = None
                for entry in last_views:
                    if not isinstance(entry, dict):
                        continue
                    observed_filename = entry.get("filename")
                    if self._filename_matches_requested(observed_filename, requested):
                        matched = entry
                        break
                if matched is not None:
                    return {
                        "ok": True,
                        "requested_filename": requested,
                        "matched_view": matched,
                        "observed_current_filename": last_current_filename,
                        "observed_current_view_id": last_current_view_id,
                        "views": last_views,
                        "views_payload": last_payload,
                    }

            if time.monotonic() >= deadline:
                break
            if timeout_s == 0.0:
                break
            time.sleep(min(sleep_s, max(0.0, deadline - time.monotonic())))

        return {
            "ok": False,
            "error": "open target confirmation failed",
            "requested_filename": requested,
            "observed_current_filename": last_current_filename,
            "observed_current_view_id": last_current_view_id,
            "views": last_views,
            "views_payload": last_payload,
        }

    def _wait_for_analysis_on_target(
        self,
        *,
        filename: str = "",
        view_id: object | None = None,
        timeout: float = 120.0,
    ) -> dict:
        def _coerce_state_code(raw: object) -> int | None:
            if raw is None:
                return None
            if isinstance(raw, bool):
                return None
            if isinstance(raw, int):
                return raw
            try:
                return int(raw)
            except Exception:
                pass
            try:
                return int(str(raw).strip(), 0)
            except Exception:
                return None

        try:
            timeout_s = float(timeout)
        except (TypeError, ValueError):
            timeout_s = 120.0
        if timeout_s < 1.0:
            timeout_s = 1.0

        requested_filename = str(filename or "").strip()
        requested_view_id = view_id

        try:
            idle_state_value = self._resolve_idle_analysis_state_value(
                filename=requested_filename,
                view_id=requested_view_id,
                timeout=min(timeout_s, 10.0),
            )
        except Exception as exc:
            return {
                "success": False,
                "analysis_state_code": None,
                "analysis_state_name": None,
                "analysis_status": None,
                "selected_view_filename": None,
                "selected_view_id": None,
                "wait_seconds": 0.0,
                "error": {
                    "type": "RuntimeContractError",
                    "message": f"failed to resolve runtime AnalysisState.IdleState: {exc}",
                },
            }

        poll_interval = 0.1
        deadline = time.monotonic() + timeout_s
        start = time.monotonic()
        last_status = None
        last_target: dict | None = None

        while True:
            remaining = max(0.0, deadline - time.monotonic())
            req_timeout = max(1.0, min(max(self.request_timeout, 1.0), remaining + 1.0))
            params = {}
            if requested_filename:
                params["filename"] = requested_filename
            if requested_view_id is not None:
                params["view_id"] = requested_view_id

            views_payload = self._request("GET", "views", params=params, timeout=req_timeout)
            views = []
            if isinstance(views_payload, dict):
                raw_views = views_payload.get("views")
                if isinstance(raw_views, list):
                    views = raw_views

            target = None
            for entry in views:
                if not isinstance(entry, dict):
                    continue
                entry_view_id = entry.get("view_id")
                entry_filename = entry.get("filename")
                if requested_view_id is not None and self._view_id_matches_requested(
                    entry_view_id, requested_view_id
                ):
                    target = entry
                    break
                if requested_filename and self._filename_matches_requested(
                    entry_filename, requested_filename
                ):
                    target = entry
                    break

            if target is None and isinstance(views_payload, dict):
                current_name = views_payload.get("current_filename")
                current_id = views_payload.get("current_view_id")
                if (
                    requested_view_id is not None
                    and self._view_id_matches_requested(current_id, requested_view_id)
                ) or (
                    requested_filename and self._filename_matches_requested(current_name, requested_filename)
                ):
                    target = {
                        "filename": current_name,
                        "view_id": current_id,
                        "analysis_state_code": None,
                        "analysis_state_name": None,
                        "analysis_status": None,
                    }

            if isinstance(target, dict):
                last_target = target
                state_code = _coerce_state_code(target.get("analysis_state_code"))
                state_name = target.get("analysis_state_name")
                last_status = target.get("analysis_status")
                if state_code is None:
                    elapsed = time.monotonic() - start
                    return {
                        "success": False,
                        "analysis_state_code": None,
                        "analysis_state_name": state_name,
                        "analysis_status": last_status,
                        "selected_view_filename": target.get("filename"),
                        "selected_view_id": target.get("view_id"),
                        "wait_seconds": elapsed,
                        "error": {
                            "type": "RuntimeContractError",
                            "message": (
                                "views payload missing numeric analysis_state_code "
                                "for selected target; update binary_ninja_mcp plugin."
                            ),
                        },
                    }

                if state_code == idle_state_value:
                    elapsed = time.monotonic() - start
                    return {
                        "success": True,
                        "analysis_state_code": state_code,
                        "analysis_state_name": state_name,
                        "analysis_status": last_status,
                        "selected_view_filename": target.get("filename"),
                        "selected_view_id": target.get("view_id"),
                        "wait_seconds": elapsed,
                    }

            if time.monotonic() >= deadline:
                break
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

        elapsed = time.monotonic() - start
        return {
            "success": False,
            "analysis_state_code": _coerce_state_code((last_target or {}).get("analysis_state_code")),
            "analysis_state_name": (last_target or {}).get("analysis_state_name"),
            "analysis_status": last_status,
            "selected_view_filename": (last_target or {}).get("filename"),
            "selected_view_id": (last_target or {}).get("view_id"),
            "wait_seconds": elapsed,
            "error": {
                "type": "TimeoutError",
                "message": (
                    f"analysis wait timed out after {timeout_s:.1f}s "
                    f"(last_status={last_status!r})"
                ),
            },
        }

    def _resolve_idle_analysis_state_value(
        self,
        *,
        filename: str = "",
        view_id: object | None = None,
        timeout: float = 5.0,
    ) -> int:
        cached = getattr(self, "_cached_idle_analysis_state_value", None)
        if isinstance(cached, int):
            return cached

        try:
            timeout_s = float(timeout)
        except (TypeError, ValueError):
            timeout_s = 5.0
        if timeout_s < 1.0:
            timeout_s = 1.0

        request_data = {
            "command": (
                "from binaryninja.enums import AnalysisState\n"
                "print(int(AnalysisState.IdleState))\n"
            ),
            "timeout": timeout_s,
        }
        if filename:
            request_data["filename"] = filename
        if view_id is not None:
            request_data["view_id"] = view_id

        result = self._request(
            "POST",
            "console/execute",
            data=request_data,
            timeout=max(self.request_timeout, timeout_s + 2.0),
        )
        if not isinstance(result, dict):
            raise RuntimeError("unexpected /console/execute payload while resolving IdleState")

        if not bool(result.get("success", False)):
            raise RuntimeError(
                f"/console/execute failed while resolving IdleState: {result.get('error')!r}"
            )

        stdout_text = str(result.get("stdout") or "").strip()
        if not stdout_text:
            raise RuntimeError("/console/execute returned empty stdout while resolving IdleState")

        for line in reversed(stdout_text.splitlines()):
            candidate = line.strip()
            if not candidate:
                continue
            try:
                value = int(candidate, 0)
            except Exception:
                continue
            self._cached_idle_analysis_state_value = value
            return value
        raise RuntimeError(
            "could not parse integer AnalysisState.IdleState value from /console/execute output"
        )

    @staticmethod
    def _extract_observed_filename(payload: dict | None) -> str | None:
        if not isinstance(payload, dict):
            return None

        def pick_from_dict(item: dict | None) -> str | None:
            if not isinstance(item, dict):
                return None
            direct = item.get("selected_view_filename") or item.get("filename")
            if isinstance(direct, str) and direct.strip():
                return direct
            state = item.get("state")
            if isinstance(state, dict):
                loaded = state.get("loaded_filename")
                if isinstance(loaded, str) and loaded.strip():
                    return loaded
            return None

        direct = pick_from_dict(payload)
        if direct:
            return direct

        for wrapper_key in ("open_result", "quit_result", "statusbar_result"):
            wrapped = payload.get(wrapper_key)
            if not isinstance(wrapped, dict):
                continue
            wrapped_direct = pick_from_dict(wrapped)
            if wrapped_direct:
                return wrapped_direct
            nested_result = wrapped.get("result")
            wrapped_nested = pick_from_dict(nested_result if isinstance(nested_result, dict) else None)
            if wrapped_nested:
                return wrapped_nested

        nested_result = payload.get("result")
        nested = pick_from_dict(nested_result if isinstance(nested_result, dict) else None)
        if nested:
            return nested
        return None

    @staticmethod
    def _extract_observed_view_id(payload: dict | None):
        if not isinstance(payload, dict):
            return None
        direct = payload.get("selected_view_id")
        if direct is not None:
            return direct
        for wrapper_key in ("open_result", "quit_result", "statusbar_result"):
            wrapped = payload.get(wrapper_key)
            if isinstance(wrapped, dict) and wrapped.get("selected_view_id") is not None:
                return wrapped.get("selected_view_id")
        return None

    def _server_reachable(self, timeout: float = 2.0) -> bool:
        """Check whether MCP server is reachable without exiting."""
        url = f"{self.server_url}/status"
        expected_api_version = self._expected_api_version("/status")
        try:
            response = requests.get(
                url,
                params={"_api_version": expected_api_version},
                headers={"X-Binja-MCP-Api-Version": str(expected_api_version)},
                timeout=timeout,
            )
            response.raise_for_status()
            return True
        except Exception:
            return False

    @staticmethod
    def _platform_adapter():
        return get_platform_adapter()

    @staticmethod
    def _resolve_binary_path() -> str | None:
        adapter = get_platform_adapter()
        return adapter.resolve_binary_path(explicit_path=os.environ.get("BINJA_BINARY"))

    def _find_running_binja_pids(self, binary_path: str, include_any: bool = False) -> list[int]:
        return find_binary_ninja_pids(
            binary_path=binary_path,
            include_any=include_any,
            adapter=self._platform_adapter(),
        )

    def _kill_existing_binja_processes(self, binary_path: str, include_any: bool = False) -> int:
        killed = 0
        for pid in self._find_running_binja_pids(binary_path=binary_path, include_any=include_any):
            if self._terminate_launched_binary(pid):
                killed += 1
        return killed

    def _launch_binary_ninja(self, filepath: str = "", force_restart: bool = False) -> dict:
        """Best-effort Binary Ninja launch for supported desktop platforms."""
        adapter = self._platform_adapter()
        if not adapter.supports_auto_launch():
            return {
                "ok": False,
                "error": f"auto-launch is not supported on platform '{sys.platform}'",
            }

        env = adapter.prepare_gui_env(os.environ.copy())

        binary_path = self._resolve_binary_path()

        if binary_path is None:
            return {
                "ok": False,
                "error": (
                    "unable to find Binary Ninja executable; set BINJA_BINARY or install "
                    "binaryninja in PATH"
                ),
            }

        # Launch regular UI mode to keep plugin loading behavior consistent.
        should_restart = force_restart or _bool_env("BINJA_FORCE_RESTART_ON_OPEN", True)
        if should_restart:
            include_any = _bool_env("BINJA_KILL_ANY_BINJA", False)
            killed = self._kill_existing_binja_processes(
                binary_path=binary_path,
                include_any=include_any,
            )
            if killed and self.verbose:
                print(
                    colors.yellow
                    | f"Killed {killed} existing Binary Ninja process(es) before launch.",
                    file=sys.stderr,
                )

        args = [binary_path]
        if filepath:
            args.extend(["-e", filepath])

        log_path = os.environ.get("BINJA_LAUNCH_LOG_PATH", "/tmp/binja-cli-launch.log")
        try:
            prepare_log_file(log_path)
        except Exception:
            pass
        try:
            with open(log_path, "ab") as log_fp:
                proc = subprocess.Popen(
                    args,
                    env=env,
                    stdout=log_fp,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except Exception as exc:
            return {"ok": False, "error": f"failed to launch Binary Ninja: {exc}", "log": log_path}

        return {"ok": True, "binary": binary_path, "log": log_path, "pid": int(proc.pid)}

    def _terminate_launched_binary(self, pid: int) -> bool:
        return terminate_pid_tree(pid, grace_s=0.5)

    @staticmethod
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
        return "\n".join(lines[-max_lines:])

    def _detect_launch_failure(self, log_path: str) -> str | None:
        tail = self._tail_log(log_path, max_lines=120)
        if not tail:
            return None
        lowered = tail.lower()
        for marker in STARTUP_FATAL_PATTERNS:
            if marker in lowered:
                return marker
        return None

    def _ensure_server_for_open(self, filepath: str = "") -> dict:
        """Ensure MCP server is available before running open workflow."""
        if self._server_reachable(timeout=1.0):
            return {"ok": True, "launched": False}

        # Launching with -e <file> can present modal import dialogs before MCP
        # automation has control. Start without a file, then let open() drive load.
        launch = self._launch_binary_ninja(filepath="")
        if not launch.get("ok"):
            return launch

        deadline = time.time() + 25.0
        while time.time() < deadline:
            failure = self._detect_launch_failure(str(launch.get("log") or ""))
            if failure:
                killed = False
                if _bool_env("BINJA_KILL_ON_LAUNCH_TIMEOUT", True):
                    killed = self._terminate_launched_binary(int(launch.get("pid") or 0))
                return {
                    "ok": False,
                    "error": (f"Binary Ninja startup failed before MCP server came up: {failure}"),
                    "log": launch.get("log"),
                    "binary": launch.get("binary"),
                    "killed_on_timeout": killed,
                }
            if self._server_reachable(timeout=1.0):
                out = dict(launch)
                out["ok"] = True
                out["launched"] = True
                return out
            time.sleep(0.5)

        killed = False
        if _bool_env("BINJA_KILL_ON_LAUNCH_TIMEOUT", True):
            killed = self._terminate_launched_binary(int(launch.get("pid") or 0))

        return {
            "ok": False,
            "error": (
                "Binary Ninja started but MCP server did not come up at "
                f"{self.server_url} within 25s"
            ),
            "log": launch.get("log"),
            "binary": launch.get("binary"),
            "killed_on_timeout": killed,
        }

    def _execute_python(self, code: str, exec_timeout: float = 30.0) -> dict:
        """Execute Python in Binary Ninja via MCP console endpoint."""
        return self._request(
            "POST",
            "console/execute",
            data={"command": code, "timeout": exec_timeout},
        )

    def _output(self, data: dict):
        """Output data in JSON or formatted text"""
        if self.json_output:
            print(json.dumps(data, indent=2))
        else:
            # Custom formatting based on data type
            if "error" in data:
                print(colors.red | f"Error: {data['error']}")
                # Display additional error context if available
                if isinstance(data, dict):
                    if "help" in data:
                        print(colors.yellow | f"Help: {data['help']}")
                    if "received" in data:
                        print(f"Received: {data['received']}")
                    if "requested_name" in data:
                        print(f"Requested: {data['requested_name']}")
                    if "available_functions" in data and data["available_functions"]:
                        funcs = data["available_functions"][:5]
                        print("\nAvailable functions:")
                        for func in funcs:
                            print(f"  • {func}")
                        if len(data["available_functions"]) > 5:
                            print(f"  ... and {len(data['available_functions']) - 5} more")
            elif "success" in data and data.get("success"):
                print(colors.green | "Success!")
                if "message" in data:
                    print(data["message"])
            else:
                # Pretty print the data
                print(json.dumps(data, indent=2))

    def main(self):
        """Show help if no subcommand is provided"""
        if len(sys.argv) == 1:
            self.help()
            return 1
        return 0


@BinaryNinjaCLI.subcommand("status")
class Status(cli.Application):
    """Check if binary is loaded and server status"""

    def main(self):
        data = self.parent._request("GET", "status")

        if self.parent.json_output:
            self.parent._output(data)
        else:
            if data.get("loaded"):
                print(colors.green | "✓ Binary loaded")
                print(f"  File: {data.get('filename', 'Unknown')}")
            else:
                print(colors.yellow | "⚠ No binary loaded")


@BinaryNinjaCLI.subcommand("views")
class Views(cli.Application):
    """List loaded BinaryViews for explicit per-view targeting."""

    def main(self):
        data = self.parent._request("GET", "views")

        if self.parent.json_output:
            self.parent._output(data)
            return

        views = data.get("views", [])
        if not views:
            print("No BinaryViews loaded")
            return

        print(f"Loaded views ({len(views)}):")
        for view in views:
            marker = "*" if view.get("is_current") else " "
            view_id = view.get("view_id") or "?"
            basename = view.get("basename") or "<unknown>"
            filename = view.get("filename") or "<unknown>"
            view_type = view.get("view_type") or "unknown"
            arch = view.get("architecture") or "unknown"
            analysis = view.get("analysis_status") or "unknown"

            print(f"[{marker}] {view_id}  {basename}")
            print(f"    file: {filename}")
            print(f"    type: {view_type}")
            print(f"    arch: {arch}")
            print(f"    analysis: {analysis}")


@BinaryNinjaCLI.subcommand("statusbar")
class StatusBar(cli.Application):
    """Read Binary Ninja status bar text from the active UI window"""

    all_windows = cli.Flag(
        ["--all"],
        help="Return status text for all visible top-level windows (not just active/main).",
    )

    include_hidden = cli.Flag(
        ["--include-hidden"],
        help="Include hidden windows in the scan.",
    )

    exec_timeout = cli.SwitchAttr(
        ["--exec-timeout"],
        float,
        default=20.0,
        help="UI statusbar request timeout in seconds.",
    )

    def main(self):
        config = {
            "all_windows": bool(self.all_windows),
            "include_hidden": bool(self.include_hidden),
        }

        parsed = self.parent._request(
            "POST",
            "ui/statusbar",
            data=config,
            timeout=max(self.parent.request_timeout, float(self.exec_timeout or 20.0)),
        )
        if not isinstance(parsed, dict):
            print(colors.yellow | "Statusbar endpoint returned an unexpected payload.")
            return
        parsed = self.parent._validate_ui_contract(parsed, "/ui/statusbar")

        if self.parent.json_output:
            self.parent._output({"statusbar_result": parsed})
            return

        details = parsed.get("result", {}) if isinstance(parsed.get("result"), dict) else {}

        print("Active Window:", details.get("active_window_title"))
        print("Status Source:", details.get("status_source", ""))
        print("Status Text:", details.get("status_text", ""))
        items = details.get("status_items", [])
        if items:
            print("Status Items:")
            for item in items:
                print(f"  - {item}")

        warnings = parsed.get("warnings", [])
        if warnings:
            print(colors.yellow | "Warnings:")
            for warning in warnings:
                print(colors.yellow | f"  - {warning}")

        errors = parsed.get("errors", [])
        if errors:
            print(colors.red | "Errors:")
            for err in errors:
                print(colors.red | f"  - {err}")


@BinaryNinjaCLI.subcommand("open")
class Open(cli.Application):
    """Open a file and auto-resolve Binary Ninja's "Open with Options" dialog.

    Behavior:
    - If MCP is not reachable, auto-launches Binary Ninja on supported platforms.
    - If an "Open with Options" dialog is visible, optionally sets view type/platform and clicks "Open".
    - Uses UI-only open workflow for deterministic tab creation in Binary Ninja.
    - Always reports inspected state/actions in JSON-like output.
    """

    platform = cli.SwitchAttr(
        ["--platform", "-p"],
        str,
        help="Platform/arch text to select in the dialog (e.g. x86_16).",
    )

    view_type = cli.SwitchAttr(
        ["--view-type", "-t"],
        str,
        help="View type to select in the dialog (e.g. Mapped, Raw).",
    )

    no_click = cli.Flag(
        ["--no-click"],
        help="Inspect/set dialog fields but do not click the Open button.",
    )

    inspect_only = cli.Flag(
        ["--inspect-only"],
        help="Inspect UI state only (do not load or click open).",
    )

    wait_open_target = cli.SwitchAttr(
        ["--wait-open-target"],
        float,
        default=6.0,
        help=(
            "Seconds to wait for the requested file to appear in /views after open "
            "(default: 6, set 0 to disable)."
        ),
    )

    wait_analysis = cli.Flag(
        ["--wait-analysis"],
        help="After target confirmation, wait until target analysis_status becomes idle.",
    )

    analysis_timeout = cli.SwitchAttr(
        ["--analysis-timeout"],
        float,
        default=120.0,
        help="Timeout in seconds for --wait-analysis (default: 120).",
    )

    def main(self, filepath: str = ""):
        ensure = self.parent._ensure_server_for_open(filepath=filepath)
        if not ensure.get("ok"):
            print(colors.red | f"Error: {ensure.get('error', 'unable to start Binary Ninja')}")
            if ensure.get("binary"):
                print(f"Binary: {ensure['binary']}", file=sys.stderr)
            if ensure.get("log"):
                print(f"Launch log: {ensure['log']}", file=sys.stderr)
            if ensure.get("killed_on_timeout"):
                print("Killed launched Binary Ninja after MCP startup timeout.", file=sys.stderr)
            print(
                "If Binary Ninja is already open, ensure the MCP server is running.",
                file=sys.stderr,
            )
            return 1
        if ensure.get("launched") and self.parent.verbose:
            print(
                colors.yellow
                | f"Started Binary Ninja ({ensure.get('binary')}); waiting for MCP server succeeded."
            )

        # Keep UI open workflow long enough for dialog automation + initial analysis.
        open_timeout_s = max(self.parent.request_timeout, 300.0)

        config = {
            "filepath": filepath,
            "platform": self.platform or "",
            "view_type": self.view_type or "",
            "click_open": not self.no_click,
            "inspect_only": self.inspect_only,
            "timeout_s": open_timeout_s,
        }

        parsed = self.parent._request(
            "POST",
            "ui/open",
            data=config,
            # Allow slight headroom above workflow timeout for HTTP response propagation.
            timeout=open_timeout_s + 5.0,
        )
        if not isinstance(parsed, dict):
            print(colors.yellow | "Open endpoint returned an unexpected payload.")
            return
        parsed = self.parent._validate_ui_contract(parsed, "/ui/open")

        wait_open_target_s = float(self.wait_open_target or 0.0)
        target_confirm = None
        if filepath and (not self.inspect_only) and wait_open_target_s > 0.0:
            target_confirm = self.parent._wait_for_open_target_in_views(
                filepath,
                timeout=wait_open_target_s,
            )

            if not target_confirm.get("ok"):
                failure_payload = {
                    "error": "open target confirmation failed",
                    "requested_filename": filepath,
                    "observed_current_filename": target_confirm.get("observed_current_filename"),
                    "observed_current_view_id": target_confirm.get("observed_current_view_id"),
                    "views": target_confirm.get("views", []),
                    "open_result": parsed,
                }
                if self.parent.json_output:
                    self.parent._output(failure_payload)
                else:
                    print(colors.red | "✗ Open target confirmation failed")
                    print(f"  Requested: {filepath}")
                    observed = target_confirm.get("observed_current_filename")
                    if observed:
                        print(f"  Observed Current: {observed}")
                    print("  Use `views` to inspect currently loaded tabs.")
                return 1

            if isinstance(parsed.get("state"), dict):
                matched_view = target_confirm.get("matched_view", {})
                if isinstance(matched_view, dict):
                    parsed["state"]["confirmed_target_filename"] = matched_view.get("filename")
                    parsed["state"]["confirmed_target_view_id"] = matched_view.get("view_id")
            if isinstance(parsed.get("actions"), list):
                if "confirmed_target_via_views" not in parsed["actions"]:
                    parsed["actions"].append("confirmed_target_via_views")

        analysis_wait_result = None
        if self.wait_analysis and filepath and (not self.inspect_only):
            matched_view = (
                target_confirm.get("matched_view")
                if isinstance(target_confirm, dict)
                else None
            )
            matched_filename = ""
            matched_view_id = None
            if isinstance(matched_view, dict):
                matched_filename = str(matched_view.get("filename") or "")
                matched_view_id = matched_view.get("view_id")

            analysis_wait_result = self.parent._wait_for_analysis_on_target(
                filename=matched_filename or filepath,
                view_id=matched_view_id,
                timeout=float(self.analysis_timeout or 120.0),
            )
            if not isinstance(analysis_wait_result, dict) or not analysis_wait_result.get("success"):
                failure_payload = {
                    "error": "analysis wait failed after open",
                    "requested_filename": filepath,
                    "analysis_wait_result": analysis_wait_result,
                    "open_result": parsed,
                }
                if self.parent.json_output:
                    self.parent._output(failure_payload)
                else:
                    print(colors.red | "✗ Analysis wait failed after open")
                    if isinstance(analysis_wait_result, dict):
                        err_obj = analysis_wait_result.get("error")
                        if err_obj:
                            print(f"  Error: {err_obj}")
                return 1

        if self.parent.json_output:
            out = {"open_result": parsed}
            if analysis_wait_result is not None:
                out["analysis_wait_result"] = analysis_wait_result
            self.parent._output(out)
            return

        ok = bool(parsed.get("ok"))
        status_line = "✓ Open workflow completed" if ok else "⚠ Open workflow completed with issues"
        color = colors.green if ok else colors.yellow
        print(color | status_line)

        loaded = parsed.get("state", {}).get("loaded_filename")
        if loaded:
            print(f"  Loaded: {loaded}")
        else:
            print("  Loaded: <unknown>")

        active_window = parsed.get("state", {}).get("active_window")
        if active_window:
            print(f"  Active Window: {active_window}")

        actions = parsed.get("actions", [])
        if actions:
            print("  Actions:")
            for action in actions:
                print(f"    - {action}")

        warnings = parsed.get("warnings", [])
        if warnings:
            print(colors.yellow | "  Warnings:")
            for warning in warnings:
                print(colors.yellow | f"    - {warning}")

        errors = parsed.get("errors", [])
        if errors:
            print(colors.red | "  Errors:")
            for err in errors:
                print(colors.red | f"    - {err}")

        if analysis_wait_result is not None:
            if analysis_wait_result.get("success"):
                print(colors.green | "  Analysis: wait complete")
            else:
                print(colors.red | "  Analysis: wait failed")


@BinaryNinjaCLI.subcommand("quit")
class Quit(cli.Application):
    """Close Binary Ninja windows and auto-answer save confirmation dialogs.

    Default decision policy:
    - Save if currently loaded file is a `.bndb` or has a sibling `<file>.bndb`.
    - Otherwise choose Don't Save/Discard.
    """

    decision = cli.SwitchAttr(
        ["--decision"],
        str,
        default="auto",
        help="Decision policy: auto|save|dont-save|cancel",
    )

    mark_dirty = cli.Flag(
        ["--mark-dirty"],
        help="Force current BinaryView's modified flag before closing (useful for testing).",
    )

    inspect_only = cli.Flag(
        ["--inspect-only"],
        help="Inspect dialogs and policy only; do not close windows or click buttons.",
    )

    wait_ms = cli.SwitchAttr(
        ["--wait-ms"],
        int,
        default=2000,
        help="Maximum time to wait for confirmation dialogs after close (ms).",
    )

    quit_app = cli.Flag(
        ["--quit-app"],
        help="Request QApplication.quit() after dialog handling (best-effort).",
    )

    quit_delay_ms = cli.SwitchAttr(
        ["--quit-delay-ms"],
        int,
        default=300,
        help="Delay before QApplication.quit() when --quit-app is used (ms).",
    )

    exec_timeout = cli.SwitchAttr(
        ["--exec-timeout"],
        float,
        default=120.0,
        help="UI quit request timeout in seconds.",
    )

    def main(self):
        decision_in = (self.decision or "auto").strip().lower()
        valid = {"auto", "save", "dont-save", "dont_save", "cancel"}
        if decision_in not in valid:
            print(
                colors.red
                | f"Invalid --decision '{self.decision}'. Expected one of: auto, save, dont-save, cancel"
            )
            return 1
        if decision_in == "dont_save":
            decision_in = "dont-save"

        config = {
            "decision": decision_in,
            "mark_dirty": bool(self.mark_dirty),
            "inspect_only": bool(self.inspect_only),
            "wait_ms": int(self.wait_ms or 2000),
            "quit_app": bool(self.quit_app),
            "quit_delay_ms": int(self.quit_delay_ms or 300),
        }

        parsed = self.parent._request(
            "POST",
            "ui/quit",
            data=config,
            timeout=max(self.parent.request_timeout, float(self.exec_timeout or 120.0)),
        )
        if not isinstance(parsed, dict):
            print(colors.yellow | "Quit endpoint returned an unexpected payload.")
            return
        parsed = self.parent._validate_ui_contract(parsed, "/ui/quit")

        if self.parent.json_output:
            self.parent._output({"quit_result": parsed})
            return

        details = parsed.get("result", {}) if isinstance(parsed.get("result"), dict) else {}
        policy = details.get("policy", {}) if isinstance(details.get("policy"), dict) else {}

        ok = bool(parsed.get("ok"))
        stuck = bool(parsed.get("state", {}).get("stuck_confirmation"))
        decision = policy.get("resolved_decision")
        status_line = (
            "✓ Quit workflow completed"
            if ok and not stuck
            else "⚠ Quit workflow completed with issues"
        )
        color = colors.green if ok and not stuck else colors.yellow
        print(color | status_line)
        print(f"  Policy Decision: {decision}")
        print(f"  Loaded File: {policy.get('loaded_filename')}")
        print(f"  Stuck On Confirmation: {stuck}")

        actions = parsed.get("actions", [])
        if actions:
            print("  Actions:")
            for action in actions:
                print(f"    - {action}")

        warnings = parsed.get("warnings", [])
        if warnings:
            print(colors.yellow | "  Warnings:")
            for warning in warnings:
                print(colors.yellow | f"    - {warning}")

        errors = parsed.get("errors", [])
        if errors:
            print(colors.red | "  Errors:")
            for err in errors:
                print(colors.red | f"    - {err}")


@BinaryNinjaCLI.subcommand("functions")
class Functions(cli.Application):
    """List functions in the binary"""

    offset = cli.SwitchAttr(
        ["--offset", "-o"], int, default=0, help="Starting offset for pagination"
    )

    limit = cli.SwitchAttr(
        ["--limit", "-l"], int, default=100, help="Maximum number of functions to return"
    )

    search = cli.SwitchAttr(["--search", "-s"], str, help="Search for functions by name")

    def main(self):
        if self.search:
            data = self.parent._request(
                "GET",
                "searchFunctions",
                {"query": self.search, "offset": self.offset, "limit": self.limit},
            )

            if self.parent.json_output:
                self.parent._output(data)
            else:
                matches = data.get("matches", [])
                if matches:
                    print(f"Found {len(matches)} matching functions:")
                    for func in matches:
                        print(f"  • {func}")
                else:
                    print("No matching functions found")
        else:
            data = self.parent._request(
                "GET", "functions", {"offset": self.offset, "limit": self.limit}
            )

            if self.parent.json_output:
                self.parent._output(data)
            else:
                functions = data.get("functions", [])
                if functions:
                    print(f"Functions ({self.offset}-{self.offset + len(functions)}):")
                    for func in functions:
                        print(f"  • {func}")
                else:
                    print("No functions found")


@BinaryNinjaCLI.subcommand("decompile")
class Decompile(cli.Application):
    """Decompile a function"""

    def main(self, function_name: str):
        data = self.parent._request(
            "GET",
            "decompile",
            {"name": function_name},
            timeout=max(self.parent.request_timeout, 30.0),
        )

        if self.parent.json_output:
            self.parent._output(data)
        else:
            if "error" in data:
                print(colors.red | f"Error: {data['error']}")
            else:
                print(colors.cyan | f"Decompiled code for {function_name}:")
                print(data.get("decompiled", "No decompilation available"))


@BinaryNinjaCLI.subcommand("assembly")
class Assembly(cli.Application):
    """Get assembly code for a function"""

    def main(self, function_name: str):
        data = self.parent._request(
            "GET",
            "assembly",
            {"name": function_name},
            timeout=max(self.parent.request_timeout, 30.0),
        )

        if self.parent.json_output:
            self.parent._output(data)
        else:
            if "error" in data:
                print(colors.red | f"Error: {data['error']}")
            else:
                print(colors.cyan | f"Assembly for {function_name}:")
                print(data.get("assembly", "No assembly available"))


@BinaryNinjaCLI.subcommand("rename")
class Rename(cli.Application):
    """Rename functions or data"""


@Rename.subcommand("function")
class RenameFunction(cli.Application):
    """Rename a function"""

    def main(self, old_name: str, new_name: str):
        data = self.parent.parent._request(
            "POST", "renameFunction", data={"oldName": old_name, "newName": new_name}
        )
        self.parent.parent._output(data)


@Rename.subcommand("data")
class RenameData(cli.Application):
    """Rename data at address"""

    def main(self, address: str, new_name: str):
        data = self.parent.parent._request(
            "POST", "renameData", data={"address": address, "newName": new_name}
        )
        self.parent.parent._output(data)


@BinaryNinjaCLI.subcommand("comment")
class Comment(cli.Application):
    """Manage comments"""

    delete = cli.Flag(["--delete", "-d"], help="Delete comment instead of setting")

    function = cli.Flag(["--function", "-f"], help="Comment on function instead of address")

    def main(self, target: str, comment: str = None):
        if self.function:
            endpoint = "comment/function"
            params = {"name": target}
        else:
            endpoint = "comment"
            params = {"address": target}

        if self.delete:
            params["_method"] = "DELETE"
            data = self.parent._request("POST", endpoint, data=params)
        elif comment is None:
            # Get comment
            data = self.parent._request("GET", endpoint, params)
        else:
            # Set comment
            params["comment"] = comment
            data = self.parent._request("POST", endpoint, data=params)

        self.parent._output(data)


@BinaryNinjaCLI.subcommand("refs")
class References(cli.Application):
    """Find code references to a function"""

    def main(self, function_name: str):
        data = self.parent._request("GET", "codeReferences", {"function": function_name})

        if self.parent.json_output:
            self.parent._output(data)
        else:
            refs = json.loads(data) if isinstance(data, str) else data
            if "error" in refs:
                print(colors.red | f"Error: {refs['error']}")
            else:
                code_refs = refs.get("code_references", [])
                if code_refs:
                    print(f"Functions that call {function_name}:")
                    for ref in code_refs:
                        print(f"  • {ref}")
                else:
                    print(f"No references found to {function_name}")


@BinaryNinjaCLI.subcommand("logs")
class Logs(cli.Application):
    """View Binary Ninja logs"""

    count = cli.SwitchAttr(["--count", "-c"], int, default=20, help="Number of log entries to show")

    level = cli.SwitchAttr(
        ["--level", "-l"], str, help="Filter by log level (DebugLog, InfoLog, WarningLog, ErrorLog)"
    )

    search = cli.SwitchAttr(["--search", "-s"], str, help="Search in log messages")

    errors = cli.Flag(["--errors", "-e"], help="Show only errors")

    warnings = cli.Flag(["--warnings", "-w"], help="Show only warnings")

    stats = cli.Flag(["--stats"], help="Show log statistics")

    clear = cli.Flag(["--clear"], help="Clear all logs")

    def main(self):
        if self.clear:
            data = self.parent._request("POST", "logs/clear")
            self.parent._output(data)
            return

        if self.stats:
            data = self.parent._request("GET", "logs/stats")
            if self.parent.json_output:
                self.parent._output(data)
            else:
                print("Log Statistics:")
                print(f"  Total logs: {data.get('total_logs', 0)}")
                print("  By level:")
                for level, count in data.get("levels", {}).items():
                    print(f"    {level}: {count}")
            return

        # Get logs
        params = {"count": self.count}
        endpoint = "logs"

        if self.errors:
            endpoint = "logs/errors"
        elif self.warnings:
            endpoint = "logs/warnings"
        else:
            if self.level:
                params["level"] = self.level
            if self.search:
                params["search"] = self.search

        data = self.parent._request("GET", endpoint, params)

        if self.parent.json_output:
            self.parent._output(data)
        else:
            logs = data.get("logs", data.get("errors", data.get("warnings", [])))
            if logs:
                for log in logs:
                    level = log.get("level", "INFO")
                    timestamp = log.get("timestamp", "")[:19]  # Trim microseconds
                    message = log.get("message", "")

                    # Color based on level
                    if "Error" in level:
                        level_color = colors.red
                    elif "Warn" in level:
                        level_color = colors.yellow
                    elif "Debug" in level:
                        level_color = colors.blue
                    else:
                        level_color = colors.white

                    print(f"{colors.dim | timestamp} {level_color | f'[{level:>8}]'} {message}")
            else:
                print("No logs found")


@BinaryNinjaCLI.subcommand("type")
class Type(cli.Application):
    """Get or define types"""

    define = cli.Flag(["--define", "-d"], help="Define types from C code")

    def main(self, type_name_or_code: str):
        if self.define:
            # Define types
            data = self.parent._request("GET", "defineTypes", {"cCode": type_name_or_code})
        else:
            # Get user-defined type
            data = self.parent._request("GET", "getUserDefinedType", {"name": type_name_or_code})

        if self.parent.json_output:
            self.parent._output(data)
        else:
            result = json.loads(data) if isinstance(data, str) else data
            if "error" in result:
                print(colors.red | f"Error: {result['error']}")
            elif "type_definition" in result:
                print(f"Type: {result.get('type_name', type_name_or_code)}")
                print(result.get("type_definition", "No definition"))
            else:
                print(result)


@BinaryNinjaCLI.subcommand("imports")
class Imports(cli.Application):
    """List imported symbols"""

    offset = cli.SwitchAttr(["--offset", "-o"], int, default=0)
    limit = cli.SwitchAttr(["--limit", "-l"], int, default=100)

    def main(self):
        data = self.parent._request("GET", "imports", {"offset": self.offset, "limit": self.limit})

        if self.parent.json_output:
            self.parent._output(data)
        else:
            imports = data.get("imports", [])
            if imports:
                print(f"Imports ({self.offset}-{self.offset + len(imports)}):")
                for imp in imports:
                    print(f"  • {imp}")
            else:
                print("No imports found")


@BinaryNinjaCLI.subcommand("exports")
class Exports(cli.Application):
    """List exported symbols"""

    offset = cli.SwitchAttr(["--offset", "-o"], int, default=0)
    limit = cli.SwitchAttr(["--limit", "-l"], int, default=100)

    def main(self):
        data = self.parent._request("GET", "exports", {"offset": self.offset, "limit": self.limit})

        if self.parent.json_output:
            self.parent._output(data)
        else:
            exports = data.get("exports", [])
            if exports:
                print(f"Exports ({self.offset}-{self.offset + len(exports)}):")
                for exp in exports:
                    print(f"  • {exp}")
            else:
                print("No exports found")


@BinaryNinjaCLI.subcommand("python")
class Python(cli.Application):
    """Execute Python code in Binary Ninja context

    Examples:
        python "print('Hello')"          # Execute inline code
        python < script.py               # Execute from stdin
        python script.py                 # Execute from file
        python -f script.py              # Execute from file (explicit)
        python -                         # Read from stdin
        python -i                        # Interactive mode
        echo "2+2" | python -            # Pipe code to execute
    """

    file = cli.SwitchAttr(["-f", "--file"], cli.ExistingFile, help="Execute Python code from file")

    interactive = cli.Flag(["-i", "--interactive"], help="Start interactive Python session")

    stdin = cli.Flag(["--stdin"], help="Read code from stdin (can also use '-' as argument)")

    complete = cli.SwitchAttr(
        ["-c", "--complete"], str, help="Get code completions for partial input"
    )
    exec_timeout = cli.SwitchAttr(
        ["--exec-timeout"],
        float,
        default=30.0,
        help="Execution timeout in seconds for /console/execute (default: 30)",
    )

    def main(self, *args):
        code = None

        # Handle completion request
        if self.complete is not None:
            data = self.parent._request(
                "GET", "console/complete", params={"partial": self.complete}
            )
            completions = data.get("completions", [])
            if self.parent.json_output:
                self.parent._output(data)
            else:
                if completions:
                    for comp in completions:
                        print(comp)
                else:
                    print(f"No completions for '{self.complete}'")
            return 0

        # Determine source of code
        if self.interactive:
            # Interactive mode
            self._interactive_mode()
            return 0

        elif self.file:
            # Explicit file flag
            try:
                code = self.file.read()
            except Exception as e:
                print(colors.red | f"Error reading file: {e}")
                return 1

        elif self.stdin or (args and args[0] == "-"):
            # Read from stdin
            try:
                import sys

                code = sys.stdin.read()
                if not code.strip():
                    print(colors.red | "No input received from stdin")
                    return 1
            except KeyboardInterrupt:
                print("\nCancelled")
                return 1
            except Exception as e:
                print(colors.red | f"Error reading stdin: {e}")
                return 1

        elif args:
            # Check if first argument is a file
            if len(args) == 1 and not args[0].startswith("-"):
                from pathlib import Path

                file_path = Path(args[0])
                if file_path.exists() and file_path.is_file():
                    # It's a file, read it
                    try:
                        code = file_path.read_text()
                    except Exception as e:
                        print(colors.red | f"Error reading file '{args[0]}': {e}")
                        return 1
                else:
                    # Not a file, treat as inline code
                    code = " ".join(args)
            else:
                # Multiple arguments or starts with -, treat as inline code
                code = " ".join(args)

        else:
            # No arguments, check if stdin is piped
            import sys

            # Check if stdin has data (works on Unix-like systems)
            if sys.stdin.isatty():
                # No piped input, show usage
                print("Usage: python [options] <code|file|->")
                print("       python script.py              # Execute file")
                print("       python 'print(42)'            # Execute inline code")
                print("       python -                      # Read from stdin")
                print("       echo 'print(42)' | python     # Pipe to stdin")
                print("       python -i                     # Interactive mode")
                print("       python -f script.py           # Explicit file")
                return 1
            else:
                # Data is piped to stdin
                try:
                    code = sys.stdin.read()
                except Exception as e:
                    print(colors.red | f"Error reading piped input: {e}")
                    return 1

        if not code:
            print(colors.red | "No code to execute")
            return 1

        # Execute the code
        data = self.parent._request(
            "POST",
            "console/execute",
            data={"command": code, "timeout": self.exec_timeout},
        )

        if self.parent.json_output:
            self.parent._output(data)
        else:
            if data.get("success"):
                # Show output
                if data.get("stdout"):
                    print(data["stdout"], end="")
                if data.get("stderr"):
                    print(colors.red | data["stderr"], end="")

                # Show return value if present
                if data.get("return_value") is not None:
                    if not data.get("stdout", "").strip().endswith(str(data["return_value"])):
                        print(colors.cyan | f"→ {data['return_value']}")

                # Show variables if any were created/modified
                if data.get("variables"):
                    print(colors.green | f"\nVariables: {', '.join(data['variables'].keys())}")

                # Show execution time if verbose
                if self.parent.verbose and "execution_time" in data:
                    print(colors.dim | f"Execution time: {data['execution_time']:.3f}s")
            else:
                # Show error
                error = data.get("error", {})
                if isinstance(error, dict):
                    print(
                        colors.red
                        | f"Error: {error.get('type', 'Unknown')}: {error.get('message', 'Unknown error')}"
                    )
                    if self.parent.verbose and error.get("traceback"):
                        print(colors.dim | error["traceback"])
                else:
                    print(colors.red | f"Error: {error}")

    def _interactive_mode(self):
        """Interactive Python session"""
        print("Binary Ninja Python Console (type 'exit()' to quit)")
        print("=" * 50)

        while True:
            try:
                # Get input
                code = input(colors.cyan | ">>> ")
                if code.strip() in ["exit()", "quit()", "exit", "quit"]:
                    break

                if not code.strip():
                    continue

                # Handle multi-line input
                if code.rstrip().endswith(":"):
                    lines = [code]
                    while True:
                        line = input(colors.cyan | "... ")
                        lines.append(line)
                        if not line.strip():
                            break
                    code = "\n".join(lines)

                # Execute
                data = self.parent._request(
                    "POST",
                    "console/execute",
                    data={"command": code, "timeout": self.exec_timeout},
                )

                # Display results
                if data.get("success"):
                    if data.get("stdout"):
                        print(data["stdout"], end="")
                    if data.get("stderr"):
                        print(colors.yellow | data["stderr"], end="")
                    if data.get("return_value") is not None:
                        # Don't duplicate if already in stdout
                        stdout = data.get("stdout", "")
                        if not stdout.strip().endswith(str(data["return_value"])):
                            print(colors.green | data["return_value"])
                else:
                    error = data.get("error", {})
                    if isinstance(error, dict):
                        print(
                            colors.red
                            | f"{error.get('type', 'Unknown')}: {error.get('message', 'Unknown error')}"
                        )
                    else:
                        print(colors.red | str(error))

            except KeyboardInterrupt:
                print("\nKeyboardInterrupt")
            except EOFError:
                print()
                break
            except Exception as e:
                print(colors.red | f"Client error: {e}")


if __name__ == "__main__":
    BinaryNinjaCLI.run()
