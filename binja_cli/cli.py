"""
Installed command-line client for the Binary Ninja plugin's HTTP API.
"""

import errno
import ast
import io
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter
from contextlib import redirect_stdout
from contextvars import ContextVar
from pathlib import Path
import requests
from plumbum import cli, colors
from .arguments import normalize_output_options
from .output import OUTPUT_FORMATS, OutputOptions, deliver_output, render_value
from .schema import command_schema
from shared.build_info import TOOL_VERSION, CAPABILITY_PROTOCOL_VERSION, assess_compatibility
from shared.analysis_contract import (
    MAX_BUNDLE_FUNCTIONS,
    bundle_sections,
    instruction_count,
    analysis_time_budget,
    IL_LEVELS,
    READ_TYPES,
    read_arguments,
)

from shared.api_versions import (
    SUPPORTED_UI_CONTRACT_SCHEMA_VERSIONS,
    expected_api_version,
    normalize_endpoint_path,
)
from shared.platform import (
    find_binary_ninja_pids,
    get_platform_adapter,
    prepare_log_file,
    terminate_pid_tree,
)

_invocation = ContextVar("binja_cli_invocation", default=None)

STARTUP_FATAL_PATTERNS = (
    "could not connect to display",
    "could not load the qt platform plugin",
    "no qt platform plugin could be initialized",
    "this application failed to start because no qt platform plugin could be initialized",
    "fatal error",
)

DEFAULT_SERVER_URL = "http://localhost:9009"
DISCOVERY_HOST = "localhost"
DISCOVERY_PORTS = (9009, 9000, 9001, 9002, 9003, 9004, 9005, 9006, 9007, 9008)

MUTATION_CAPABILITY_PATHS = {
    "/rename/function",
    "/renameFunction",
    "/rename/data",
    "/renameData",
    "/defineTypes",
    "/renameVariable",
    "/retypeVariable",
}


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

    PROGNAME = "binja-cli"
    VERSION = TOOL_VERSION
    DESCRIPTION = "Command-line interface for Binary Ninja MCP server"

    def __init__(self, executable):
        super().__init__(executable)
        context = _invocation.get()
        if context is not None and context["root"] is None:
            context["root"] = self
            self._live_stdout = context["stdout"]

    @classmethod
    def run(cls, argv=None, exit=True):
        """Run one command through a common, complete output delivery layer."""
        argv, is_meta = normalize_output_options(cls, argv or sys.argv)
        stdout = sys.stdout
        captured = io.StringIO()
        context = {"root": None, "stdout": stdout, "is_meta": is_meta}
        token = _invocation.set(context)
        instance = None
        try:
            with redirect_stdout(captured):
                try:
                    instance, return_code = super().run(argv, exit=False)
                except SystemExit as exc:
                    return_code = exc.code if isinstance(exc.code, int) else 1
        finally:
            _invocation.reset(token)
        root = context["root"]
        instance = instance or root
        rendered = captured.getvalue()
        options = getattr(root, "_output_options", None)
        if getattr(root, "_command_failed", False) and not return_code:
            return_code = 1
        try:
            if return_code == 2:
                # Plumbum emits parser errors and selected-command help on stdout.
                sys.stderr.write(rendered)
            elif is_meta or options is None:
                stdout.write(rendered)
            elif rendered or (options.out and not return_code):
                try:
                    deliver_output(rendered, options, stdout, sys.stderr)
                except (OSError, ValueError) as exc:
                    # Delivery is after execution. Never imply that a committed
                    # mutation was undone because its output file could not be written.
                    stdout.write(rendered)
                    print(
                        f"Output delivery failed after command execution: {exc}. "
                        "The result is reproduced on stdout; any committed live changes remain committed.",
                        file=sys.stderr,
                    )
                    return_code = return_code or 1
        except BrokenPipeError:
            return_code = return_code or 1
        if exit:
            raise SystemExit(return_code)
        return instance, return_code

    server_url = cli.SwitchAttr(
        ["--server", "-s"], str, default=DEFAULT_SERVER_URL, help="MCP server URL"
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
    output_format = cli.SwitchAttr(
        ["--format"],
        cli.Set(*OUTPUT_FORMATS, case_sensitive=True),
        help="Output format: text, json, ndjson (default: command-specific; output flags work anywhere)",
    )
    out = cli.SwitchAttr(
        ["--out"], str, help="Write output to a client-side file and print its artifact metadata"
    )
    overwrite_output = cli.Flag(
        ["--overwrite-output"], help="Explicitly replace an existing --out file"
    )
    match = cli.SwitchAttr(
        ["--match"],
        str,
        help="Filter text output by regular expression (validated before execution)",
    )
    before = cli.SwitchAttr(["--before"], int, default=0, help="Text lines before each --match")
    after = cli.SwitchAttr(["--after"], int, default=0, help="Text lines after each --match")
    spill = cli.Flag(
        ["--spill"],
        help="Allow large output to spill to a unique artifact (including structured output)",
    )
    no_spill = cli.Flag(["--no-spill"], help="Never spill; send complete output to stdout")
    tokens = cli.Flag(
        ["--tokens"], help="Optionally report token count; requires the tokens package extra"
    )

    verbose = cli.Flag(["--verbose", "-v"], help="Verbose output")

    request_timeout = cli.SwitchAttr(
        ["--request-timeout", "-t"],
        float,
        default=_float_env("BINJA_CLI_TIMEOUT", 120.0),
        help=(
            "HTTP action/read timeout in seconds after connection succeeds "
            "(default: 120; can also set BINJA_CLI_TIMEOUT)"
        ),
    )

    connect_timeout = cli.SwitchAttr(
        ["--connect-timeout"],
        float,
        default=_float_env("BINJA_CLI_CONNECT_TIMEOUT", 5.0),
        help=(
            "HTTP connection timeout in seconds before failing fast "
            "(default: 5; can also set BINJA_CLI_CONNECT_TIMEOUT)"
        ),
    )

    no_auto_errors = cli.Flag(
        ["--no-auto-errors"],
        help=("Disable automatic post-command checks for new Binary Ninja console/log errors."),
    )

    fail_on_new_errors = cli.Flag(
        ["--fail-on-new-errors"],
        help=("Return non-zero if new Binary Ninja console/log errors appear during the command."),
    )

    error_probe_count = cli.SwitchAttr(
        ["--error-probe-count"],
        int,
        default=50,
        help=("How many recent entries to compare in each post-command error probe (default: 50)."),
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

    @staticmethod
    def _server_switch_was_explicit() -> bool:
        for arg in sys.argv[1:]:
            if arg == "--server" or arg == "-s" or arg.startswith("--server="):
                return True
        return False

    def _discovery_enabled(self) -> bool:
        if self._server_switch_was_explicit():
            return False
        return str(self.server_url or "").rstrip("/") == DEFAULT_SERVER_URL

    @staticmethod
    def _split_global_view_id(view_id: object | None) -> tuple[str | None, str | None]:
        text = str(view_id or "").strip()
        if ":" not in text:
            return None, text or None
        instance_id, local_view_id = text.split(":", 1)
        instance_id = instance_id.strip()
        local_view_id = local_view_id.strip()
        if not instance_id or not local_view_id:
            return None, text or None
        return instance_id, local_view_id

    def _discover_servers(self, timeout: float = 0.25) -> list[dict]:
        cached = getattr(self, "_cached_discovered_servers", None)
        if isinstance(cached, list):
            return cached

        endpoint = "/meta/instance"
        expected = self._expected_api_version(endpoint)
        discovered: list[dict] = []
        for port in DISCOVERY_PORTS:
            base_url = f"http://{DISCOVERY_HOST}:{port}"
            try:
                response = requests.get(
                    f"{base_url}{endpoint}",
                    params={"_api_version": expected},
                    headers={"X-Binja-MCP-Api-Version": str(expected)},
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("service") != "binary_ninja_mcp":
                continue
            instance_id = str(payload.get("instance_id") or "").strip()
            if not instance_id:
                continue
            payload = dict(payload)
            payload["host"] = DISCOVERY_HOST
            payload["base_url"] = base_url.rstrip("/")
            discovered.append(payload)

        legacy_url = DEFAULT_SERVER_URL.rstrip("/")
        discovered_urls = {str(item.get("base_url") or "").rstrip("/") for item in discovered}
        if legacy_url not in discovered_urls:
            try:
                if self._probe_server_reachable(legacy_url, timeout=timeout):
                    discovered.append(
                        {
                            "service": "binary_ninja_mcp",
                            "instance_id": "legacy-9009",
                            "host": "localhost",
                            "port": 9009,
                            "base_url": legacy_url,
                            "legacy": True,
                        }
                    )
            except Exception:
                pass

        self._cached_discovered_servers = discovered
        return discovered

    def _probe_server_reachable(self, base_url: str, timeout: float = 0.5) -> bool:
        url = f"{str(base_url).rstrip('/')}/status"
        expected_api_version = self._expected_api_version("/status")
        try:
            response = requests.get(
                url,
                params={"_api_version": expected_api_version},
                headers={"X-Binja-MCP-Api-Version": str(expected_api_version)},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            return isinstance(payload, dict)
        except Exception:
            return False

    def _get_discovered_views(self, timeout: float | None = None) -> list[dict]:
        cached = getattr(self, "_cached_discovered_views", None)
        if isinstance(cached, list):
            return cached

        views: list[dict] = []
        endpoint = "/views"
        expected = self._expected_api_version(endpoint)
        request_timeout = self.request_timeout if timeout is None else float(timeout)
        for server in self._discover_servers(timeout=min(request_timeout, 0.25)):
            base_url = str(server.get("base_url") or "").rstrip("/")
            if not base_url:
                continue
            try:
                response = requests.get(
                    f"{base_url}{endpoint}",
                    params={"_api_version": expected},
                    headers={"X-Binja-MCP-Api-Version": str(expected)},
                    timeout=request_timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception:
                continue
            raw_views = payload.get("views") if isinstance(payload, dict) else None
            if not isinstance(raw_views, list):
                continue
            instance_id = str(server.get("instance_id") or "")
            for view in raw_views:
                if not isinstance(view, dict):
                    continue
                item = dict(view)
                item.setdefault("instance_id", instance_id)
                item.setdefault("server_url", base_url)
                local_view_id = item.get("view_id")
                if instance_id and local_view_id is not None:
                    item.setdefault("global_view_id", f"{instance_id}:{local_view_id}")
                    item["target_hint"] = f"--view-id {item['global_view_id']}"
                views.append(item)

        self._cached_discovered_views = views
        return views

    def _route_discovered_target(self, timeout: float) -> str | None:
        if not self.target_view_id:
            return None

        instance_id, local_view_id = self._split_global_view_id(self.target_view_id)
        if instance_id:
            if self._discovery_enabled():
                servers = self._discover_servers(timeout=0.25)
                for server in servers:
                    if server.get("instance_id") == instance_id:
                        self.server_url = str(server.get("base_url") or self.server_url).rstrip("/")
                        return local_view_id
                raise RuntimeError(
                    f"no discovered Binary Ninja MCP instance matches {instance_id!r}"
                )
            return local_view_id

        if not self._discovery_enabled():
            return None

        views = self._get_discovered_views(timeout=timeout)
        raise RuntimeError(
            f"local --view-id {local_view_id!r} is not valid in discovery mode; "
            "use a global --view-id in the form <instance_id>:<local_view_id>.\n"
            + self._format_discovered_targets(views)
        )

    @staticmethod
    def _endpoint_requires_explicit_discovered_view(endpoint_path: str) -> bool:
        path = normalize_endpoint_path(endpoint_path)
        if path in {
            "/status",
            "/views",
            "/meta/instance",
            "/meta/endpoints",
        }:
            return False
        return True

    @staticmethod
    def _format_discovered_targets(views: list[dict]) -> str:
        if not views:
            return "No open BinaryViews were discovered."
        lines = ["Valid --view-id targets:"]
        for view in views:
            view_id = view.get("global_view_id") or view.get("view_id") or "?"
            filename = view.get("filename") or "<unknown>"
            server_url = view.get("server_url")
            lines.append(f"  {view_id}  {filename}")
            if server_url:
                lines.append(f"      server: {server_url}")
        return "\n".join(lines)

    def _require_discovered_view_id_if_needed(self, endpoint_path: str, timeout: float) -> None:
        if not self._discovery_enabled() or self.target_view_id:
            return
        if not self._endpoint_requires_explicit_discovered_view(endpoint_path):
            return
        views = self._get_discovered_views(timeout=timeout)
        if not views:
            return
        raise RuntimeError(
            "missing required --view-id for instance-scoped command in discovery mode.\n"
            + self._format_discovered_targets(views)
        )

    def _select_server_from_target_view_id(self, timeout: float = 0.5) -> str | None:
        if not self.target_view_id:
            return None
        routed_view_id = self._route_discovered_target(timeout=timeout)
        return routed_view_id

    def _http_timeout(self, read_timeout: float | None = None) -> tuple[float, float]:
        try:
            connect_timeout = float(getattr(self, "connect_timeout", 5.0))
        except (TypeError, ValueError):
            connect_timeout = 5.0
        if connect_timeout <= 0.0:
            connect_timeout = 5.0

        try:
            action_timeout = (
                float(getattr(self, "request_timeout", 120.0))
                if read_timeout is None
                else float(read_timeout)
            )
        except (TypeError, ValueError):
            action_timeout = 120.0
        if action_timeout <= 0.0:
            action_timeout = 120.0

        return connect_timeout, action_timeout

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
        request_timeout = self.request_timeout if timeout is None else float(timeout)
        http_timeout = self._http_timeout(request_timeout)
        expected_api_version = self._expected_api_version(endpoint_path)
        strict_selected_filename = None
        strict_selected_view_id = None
        outgoing_view_id = self.target_view_id

        request_headers = {
            "X-Binja-MCP-Api-Version": str(expected_api_version),
        }
        request_params = dict(params or {})
        request_data = dict(data or {})
        if self.target_filename:
            request_params.setdefault("filename", self.target_filename)
            request_data.setdefault("filename", self.target_filename)
        if outgoing_view_id:
            request_params.setdefault("view_id", outgoing_view_id)
            request_data.setdefault("view_id", outgoing_view_id)
        request_params["_api_version"] = expected_api_version
        request_data["_api_version"] = expected_api_version

        try:
            skip_discovery_view_requirement = False
            if (
                endpoint_path == "/ui/close"
                and request_data.get("all")
                and not self.target_view_id
                and self._discovery_enabled()
            ):
                servers = self._discover_servers(timeout=0.25)
                if len(servers) == 1:
                    self.server_url = str(servers[0].get("base_url") or self.server_url).rstrip("/")
                    skip_discovery_view_requirement = True
                elif len(servers) > 1:
                    raise RuntimeError(
                        "close --all found multiple Binary Ninja instances; use --server "
                        "to select one instance or --view-id to target a specific view.\n"
                        + self._format_discovered_targets(
                            self._get_discovered_views(timeout=request_timeout)
                        )
                    )

            if not skip_discovery_view_requirement:
                self._require_discovered_view_id_if_needed(endpoint_path, request_timeout)

            if not skip_discovery_view_requirement and endpoint_path not in {
                "/views",
                "/meta/instance",
                "/meta/endpoints",
            }:
                routed_view_id = self._route_discovered_target(timeout=request_timeout)
                if routed_view_id:
                    outgoing_view_id = routed_view_id
                    request_params["view_id"] = outgoing_view_id
                    request_data["view_id"] = outgoing_view_id
            url = f"{self.server_url}/{endpoint.lstrip('/')}"
            if self.verbose:
                print(f"[{method}] {url}", file=sys.stderr)
                if request_params:
                    print(f"Params: {request_params}", file=sys.stderr)
                if request_data:
                    print(f"Data: {request_data}", file=sys.stderr)

            targeting_requested = bool(self.target_filename or self.target_view_id)
            enforce_strict_target = bool(
                self.strict_target or (targeting_requested and not self.allow_target_fallback)
            )
            strict_requires_precheck = (
                enforce_strict_target
                and targeting_requested
                and endpoint_path
                not in {"/status", "/views", "/target/resolve", "/ui/open", "/load"}
            )
            if strict_requires_precheck:
                strict_selected_filename, strict_selected_view_id = (
                    self._assert_strict_target_selected(timeout=request_timeout)
                )

            capability = None
            if endpoint_path.startswith("/analysis/"):
                capability = ("analysis_reads_version", 1)
            elif endpoint_path in {"/function/signature", "/editFunctionSignature"}:
                capability = ("signature_workflow_version", 2)
            elif endpoint_path == "/decompile":
                capability = ("analysis_skip_guard_version", 1)
            elif endpoint_path in MUTATION_CAPABILITY_PATHS or (
                method != "GET" and endpoint_path in {"/comment", "/comment/function"}
            ):
                capability = ("builtin_mutations_version", 1)
            if capability is not None:
                self._verify_loaded_capability(*capability, timeout=http_timeout)

            if method == "GET":
                response = requests.get(
                    url,
                    params=request_params,
                    headers=request_headers,
                    timeout=http_timeout,
                )
            else:
                response = requests.post(
                    url,
                    json=request_data,
                    headers=request_headers,
                    timeout=http_timeout,
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
                if response_data.get("error") or response_data.get("success") is False:
                    self._command_failed = True
                observed_filename = (
                    self._extract_observed_filename(response_data) or strict_selected_filename
                )
                observed_view_id = (
                    self._extract_observed_view_id(response_data) or strict_selected_view_id
                )

                should_validate_target = (
                    enforce_strict_target
                    and targeting_requested
                    and endpoint_path not in {"/views", "/target/resolve"}
                )
                if should_validate_target:
                    needs_resolution = False
                    if outgoing_view_id and not self._view_id_matches_requested(
                        observed_view_id, outgoing_view_id
                    ):
                        needs_resolution = True
                    if self.target_filename and not self._filename_matches_requested(
                        observed_filename, self.target_filename
                    ):
                        needs_resolution = True

                    if needs_resolution:
                        resolved_filename, resolved_view_id = self._resolve_target_via_endpoint(
                            timeout=request_timeout
                        )
                        observed_filename = resolved_filename or observed_filename
                        observed_view_id = resolved_view_id or observed_view_id

                    if self.target_filename and not self._filename_matches_requested(
                        observed_filename, self.target_filename
                    ):
                        raise RuntimeError(
                            "strict target mismatch: "
                            f"requested '{self.target_filename}', observed '{observed_filename}'"
                        )
                    if outgoing_view_id and not self._view_id_matches_requested(
                        observed_view_id, outgoing_view_id
                    ):
                        raise RuntimeError(
                            "strict target mismatch: "
                            f"requested view_id '{outgoing_view_id}', observed '{observed_view_id}'"
                        )

                response_data.setdefault("selected_view_filename", observed_filename)
                response_data.setdefault("selected_view_id", observed_view_id)

            return response_data

        except requests.exceptions.ConnectTimeout:
            connect_timeout = self._http_timeout(request_timeout)[0]
            print(
                colors.red
                | (
                    f"Error: Connection to server at {self.server_url} "
                    f"timed out after {connect_timeout:g}s"
                ),
                file=sys.stderr,
            )
            print("Make sure the MCP server is running and reachable", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.ReadTimeout:
            action_timeout = self._http_timeout(request_timeout)[1]
            print(
                colors.red
                | (
                    f"Error: Request to server at {self.server_url} "
                    f"timed out after {action_timeout:g}s"
                ),
                file=sys.stderr,
            )
            sys.exit(1)
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
                    if self.json_output:
                        self._output(error_data)
                    # Display main error
                    print(colors.red | f"Error: {error_data['error']}", file=sys.stderr)

                    if "error_code" in error_data:
                        print(f"Code: {error_data['error_code']}", file=sys.stderr)
                    if "expected_api_version" in error_data:
                        print(
                            f"Endpoint versions: client={error_data.get('received_api_version')}, "
                            f"server={error_data['expected_api_version']}. Update the CLI and reload "
                            "the plugin together; restarting only its HTTP listener is insufficient.",
                            file=sys.stderr,
                        )

                    # Display additional context if available
                    if "help" in error_data:
                        print(colors.yellow | f"Help: {error_data['help']}", file=sys.stderr)

                    if "received" in error_data:
                        print(f"Received: {error_data['received']}", file=sys.stderr)

                    if "requested_name" in error_data:
                        print(f"Requested: {error_data['requested_name']}", file=sys.stderr)

                    if "filename" in error_data:
                        print(f"Filename: {error_data['filename']}", file=sys.stderr)

                    if "view_id" in error_data:
                        print(f"View ID: {error_data['view_id']}", file=sys.stderr)

                    self._print_target_views_hint(error_data)

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

    def _verify_loaded_capability(self, name, minimum, *, timeout):
        """Pin safety claims to the routed server, including partial reloads.

        Endpoint versions reject old deployments. This separate preflight also
        rejects an old handler paired with a newly reloaded version registry.
        """
        response = requests.get(
            f"{self.server_url.rstrip('/')}/meta/instance",
            params={"_api_version": 1},
            headers={"X-Binja-MCP-Api-Version": "1"},
            timeout=timeout,
        )
        response.raise_for_status()
        metadata = response.json()
        if (
            not isinstance(metadata, dict)
            or metadata.get("_api_version") != 1
            or response.headers.get("X-Binja-MCP-Api-Version") != "1"
        ):
            raise RuntimeError(
                "Invalid capability handshake; reload matching client/plugin code before this operation"
            )
        capabilities = metadata.get("capabilities")
        value = capabilities.get(name) if isinstance(capabilities, dict) else None
        runtime = metadata.get("runtime")
        runtime = runtime if isinstance(runtime, dict) else {}
        if (
            type(value) is not int
            or value < minimum
            or metadata.get("capability_protocol_version") != CAPABILITY_PROTOCOL_VERSION
        ):
            raise RuntimeError(
                f"Loaded server lacks required {name}>={minimum}; no operation was sent. Reload the plugin, not only its HTTP listener."
            )
        if runtime.get("reload_required") or runtime.get("unverifiable_modules"):
            raise RuntimeError(
                "Loaded server code is stale or unverifiable; no operation was sent. Reload the plugin before safety-sensitive operations."
            )

    @staticmethod
    def _print_target_views_hint(error_data: dict) -> None:
        if not isinstance(error_data, dict):
            return

        matched_views = error_data.get("matched_views")
        if isinstance(matched_views, list) and matched_views:
            print("\nMatching open views:", file=sys.stderr)
            for entry in matched_views:
                if not isinstance(entry, dict):
                    continue
                view_id = entry.get("view_id") or "?"
                filename = entry.get("filename") or "<unknown>"
                basename = entry.get("basename") or Path(str(filename)).name
                print(f"  {view_id}  {basename}", file=sys.stderr)
                print(f"      {filename}", file=sys.stderr)
                hint = entry.get("target_hint")
                if hint:
                    print(f"      hint: {hint}", file=sys.stderr)

        open_views = error_data.get("open_views")
        if isinstance(open_views, list) and open_views:
            print("\nCurrently open views:", file=sys.stderr)
            for entry in open_views:
                if not isinstance(entry, dict):
                    continue
                marker = "*" if entry.get("is_current") else " "
                view_id = entry.get("view_id") or "?"
                filename = entry.get("filename") or "<unknown>"
                basename = entry.get("basename") or Path(str(filename)).name
                print(f"  [{marker}] {view_id}  {basename}", file=sys.stderr)
                print(f"      {filename}", file=sys.stderr)
                source = entry.get("source")
                window_title = entry.get("window_title")
                hint = entry.get("target_hint")
                if source:
                    print(f"      source: {source}", file=sys.stderr)
                if window_title:
                    print(f"      window: {window_title}", file=sys.stderr)
                if hint:
                    print(f"      hint: {hint}", file=sys.stderr)
            print(
                "\nRe-run with `--view-id <id>` or use `views` to inspect targets.",
                file=sys.stderr,
            )

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
            timeout=self._http_timeout(timeout),
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
        return bool(
            cls._view_id_candidates(observed).intersection(cls._view_id_candidates(requested))
        )

    def _resolve_target_via_endpoint(self, timeout: float) -> tuple[str | None, object | None]:
        endpoint_path = "/target/resolve"
        expected_api_version = self._expected_api_version(endpoint_path)
        url = f"{self.server_url}/target/resolve"
        _instance_id, local_target_view_id = self._split_global_view_id(self.target_view_id)
        params = {
            "_api_version": expected_api_version,
        }
        if self.target_filename:
            params["filename"] = self.target_filename
        if local_target_view_id:
            params["view_id"] = local_target_view_id

        response = requests.get(
            url,
            params=params,
            headers={"X-Binja-MCP-Api-Version": str(expected_api_version)},
            timeout=self._http_timeout(timeout),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("strict target check failed: unexpected /target/resolve payload")

        target = payload.get("target")
        if not isinstance(target, dict):
            raise RuntimeError("strict target check failed: /target/resolve returned no target")
        return target.get("filename"), target.get("view_id")

    def _assert_strict_target_selected(self, timeout: float) -> tuple[str | None, object | None]:
        observed_filename = None
        observed_view_id = None

        if self.target_view_id or self.target_filename:
            observed_filename, observed_view_id = self._resolve_target_via_endpoint(timeout=timeout)

        if self.target_filename and not self._filename_matches_requested(
            observed_filename, self.target_filename
        ):
            raise RuntimeError(
                "strict target mismatch: "
                f"requested '{self.target_filename}', observed '{observed_filename}'"
            )

        _instance_id, local_target_view_id = self._split_global_view_id(self.target_view_id)
        if local_target_view_id and not self._view_id_matches_requested(
            observed_view_id, local_target_view_id
        ):
            raise RuntimeError(
                "strict target mismatch: "
                f"requested view_id '{local_target_view_id}', observed '{observed_view_id}'"
            )

        return observed_filename, observed_view_id

    def _wait_for_open_target_in_views(
        self,
        requested_filename: str,
        *,
        timeout: float = 6.0,
        poll_interval: float = 5.0,
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
            sleep_s = 5.0
        if sleep_s < 0.1:
            sleep_s = 0.1

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

        poll_interval = 5.0
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
                    requested_filename
                    and self._filename_matches_requested(current_name, requested_filename)
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
            "analysis_state_code": _coerce_state_code(
                (last_target or {}).get("analysis_state_code")
            ),
            "analysis_state_name": (last_target or {}).get("analysis_state_name"),
            "analysis_status": last_status,
            "selected_view_filename": (last_target or {}).get("filename"),
            "selected_view_id": (last_target or {}).get("view_id"),
            "wait_seconds": elapsed,
            "error": {
                "type": "TimeoutError",
                "message": (
                    f"analysis wait timed out after {timeout_s:.1f}s (last_status={last_status!r})"
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
                "from binaryninja.enums import AnalysisState\nprint(int(AnalysisState.IdleState))\n"
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

        for wrapper_key in ("open_result", "close_result", "quit_result", "statusbar_result"):
            wrapped = payload.get(wrapper_key)
            if not isinstance(wrapped, dict):
                continue
            wrapped_direct = pick_from_dict(wrapped)
            if wrapped_direct:
                return wrapped_direct
            nested_result = wrapped.get("result")
            wrapped_nested = pick_from_dict(
                nested_result if isinstance(nested_result, dict) else None
            )
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
        for wrapper_key in ("open_result", "close_result", "quit_result", "statusbar_result"):
            wrapped = payload.get(wrapper_key)
            if isinstance(wrapped, dict) and wrapped.get("selected_view_id") is not None:
                return wrapped.get("selected_view_id")
        return None

    def _server_reachable(self, timeout: float = 2.0) -> bool:
        """Check whether MCP server is reachable without exiting."""
        return self._probe_server_reachable(self.server_url, timeout=timeout)

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

    def _wait_for_new_binja_pid(
        self,
        *,
        binary_path: str,
        existing_pids: set[int],
        timeout: float = 5.0,
    ) -> int:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            candidates = set(
                self._find_running_binja_pids(binary_path=binary_path, include_any=False)
            )
            new_pids = sorted(candidates - existing_pids)
            if new_pids:
                return new_pids[0]
            time.sleep(0.1)
        return 0

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
        should_restart = force_restart or _bool_env("BINJA_FORCE_RESTART_ON_OPEN", False)
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

        log_path = os.environ.get("BINJA_LAUNCH_LOG_PATH", "/tmp/binja-cli-launch.log")
        args = adapter.build_launch_command(
            binary_path=binary_path,
            filepath=filepath,
            log_path=log_path,
            env=env,
        )
        launched_via_service = bool(args and args[0] == "/usr/bin/open")
        existing_pids = set(
            self._find_running_binja_pids(binary_path=binary_path, include_any=False)
        )
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

        if launched_via_service:
            try:
                launcher_returncode = proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                return {
                    "ok": False,
                    "error": "LaunchServices did not return after starting Binary Ninja",
                    "binary": binary_path,
                    "log": log_path,
                    "launcher_pid": int(proc.pid),
                }
            if launcher_returncode != 0:
                return {
                    "ok": False,
                    "error": f"LaunchServices failed with exit code {launcher_returncode}",
                    "binary": binary_path,
                    "log": log_path,
                    "launcher_pid": int(proc.pid),
                }
            binary_pid = self._wait_for_new_binja_pid(
                binary_path=binary_path,
                existing_pids=existing_pids,
            )
            if binary_pid <= 0:
                return {
                    "ok": False,
                    "error": (
                        "LaunchServices returned successfully, but the Binary Ninja "
                        "application process was not observed within 5 seconds"
                    ),
                    "binary": binary_path,
                    "log": log_path,
                    "launcher_pid": int(proc.pid),
                    "launch_method": "launchservices",
                }
            return {
                "ok": True,
                "binary": binary_path,
                "log": log_path,
                "pid": binary_pid,
                "launcher_pid": int(proc.pid),
                "launch_method": "launchservices",
            }

        return {
            "ok": True,
            "binary": binary_path,
            "log": log_path,
            "pid": int(proc.pid),
            "launch_method": "direct",
        }

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
        if self.target_view_id:
            self._select_server_from_target_view_id(timeout=1.0)
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
            if self._discovery_enabled():
                self._cached_discovered_servers = None
                servers = self._discover_servers(timeout=0.5)
                if servers:
                    self.server_url = str(servers[0].get("base_url") or self.server_url).rstrip("/")
                    out = dict(launch)
                    out["ok"] = True
                    out["launched"] = True
                    out["server_url"] = self.server_url
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

    def _probe_error_endpoint(
        self,
        endpoint: str,
        *,
        count: int,
        timeout: float,
    ) -> tuple[list, str | None]:
        endpoint_path = self._normalize_endpoint_path(endpoint)
        expected_api_version = self._expected_api_version(endpoint_path)
        url = f"{self.server_url}/{endpoint.lstrip('/')}"
        params = {
            "count": int(count),
            "_api_version": expected_api_version,
        }
        headers = {"X-Binja-MCP-Api-Version": str(expected_api_version)}

        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return [], f"{endpoint}: {exc}"

        if not isinstance(payload, dict):
            return [], f"{endpoint}: unexpected non-object payload"

        entries = payload.get("errors", [])
        if not isinstance(entries, list):
            return [], f"{endpoint}: expected list in 'errors'"
        return entries, None

    def _capture_error_snapshot(self, *, count: int | None = None) -> dict | None:
        if bool(getattr(self, "no_auto_errors", False)):
            return None

        if self.target_view_id:
            try:
                self._select_server_from_target_view_id(timeout=0.5)
            except Exception:
                pass

        raw_count = count if count is not None else getattr(self, "error_probe_count", 50)
        try:
            count_n = int(raw_count)
        except (TypeError, ValueError):
            count_n = 50
        if count_n < 1:
            count_n = 1

        try:
            timeout_s = float(getattr(self, "request_timeout", 1.0))
        except (TypeError, ValueError):
            timeout_s = 1.0
        timeout_s = max(0.25, min(timeout_s, 3.0))

        console_errors, console_probe_error = self._probe_error_endpoint(
            "console/errors",
            count=count_n,
            timeout=timeout_s,
        )
        log_errors, log_probe_error = self._probe_error_endpoint(
            "logs/errors",
            count=count_n,
            timeout=timeout_s,
        )

        warnings = [item for item in (console_probe_error, log_probe_error) if item]
        return {
            "count": count_n,
            "console_errors": console_errors,
            "log_errors": log_errors,
            "probe_warnings": warnings,
        }

    @staticmethod
    def _error_entry_signature(entry: object, source: str) -> str:
        if isinstance(entry, dict):
            normalized = {
                "source": source,
                "level": str(entry.get("level") or ""),
                "type": str(entry.get("type") or ""),
                "logger": str(entry.get("logger") or ""),
                "message": str(entry.get("message") or ""),
                "text": str(entry.get("text") or ""),
            }
            if any(normalized.get(key) for key in ("level", "type", "logger", "message", "text")):
                return json.dumps(normalized, sort_keys=True)
            try:
                return f"{source}:{json.dumps(entry, sort_keys=True, default=str)}"
            except Exception:
                return f"{source}:{entry!r}"
        return f"{source}:{entry!r}"

    @classmethod
    def _new_error_entries(
        cls,
        before_entries: list,
        after_entries: list,
        *,
        source: str,
    ) -> list:
        before_counts = Counter(
            cls._error_entry_signature(entry, source) for entry in (before_entries or [])
        )
        after_counts = Counter()
        new_entries = []
        for entry in after_entries or []:
            sig = cls._error_entry_signature(entry, source)
            after_counts[sig] += 1
            if after_counts[sig] > before_counts.get(sig, 0):
                new_entries.append(entry)
        return new_entries

    @staticmethod
    def _format_error_entry(entry: object) -> str:
        if not isinstance(entry, dict):
            return str(entry)

        timestamp = str(entry.get("timestamp") or "").strip()
        level = str(entry.get("level") or entry.get("type") or "").strip()
        text = str(entry.get("message") or entry.get("text") or "").strip()

        parts = []
        if timestamp:
            parts.append(timestamp[:19])
        if level:
            parts.append(f"[{level}]")
        if text:
            parts.append(text)

        if parts:
            return " ".join(parts)
        try:
            return json.dumps(entry, sort_keys=True, default=str)
        except Exception:
            return str(entry)

    def _apply_post_command_error_report(
        self,
        command_name: str,
        before_snapshot: dict | None,
        *,
        output_payload: dict | None = None,
    ) -> bool:
        if before_snapshot is None:
            return False

        after_snapshot = self._capture_error_snapshot(count=before_snapshot.get("count"))
        if after_snapshot is None:
            return False

        new_console_errors = self._new_error_entries(
            before_snapshot.get("console_errors", []),
            after_snapshot.get("console_errors", []),
            source="console",
        )
        new_log_errors = self._new_error_entries(
            before_snapshot.get("log_errors", []),
            after_snapshot.get("log_errors", []),
            source="log",
        )

        warnings = []
        warnings.extend(before_snapshot.get("probe_warnings", []))
        warnings.extend(after_snapshot.get("probe_warnings", []))

        report = {
            "command": command_name,
            "new_console_error_count": len(new_console_errors),
            "new_log_error_count": len(new_log_errors),
            "new_error_count": len(new_console_errors) + len(new_log_errors),
            "new_console_errors": new_console_errors,
            "new_log_errors": new_log_errors,
        }
        if warnings:
            report["probe_warnings"] = warnings

        has_new_errors = report["new_error_count"] > 0

        if isinstance(output_payload, dict) and (has_new_errors or warnings):
            output_payload["new_errors"] = report

        if has_new_errors and not self.json_output:
            print(
                colors.red
                | (
                    f"Detected {report['new_error_count']} new Binary Ninja error(s) "
                    f"after {command_name}:"
                )
            )
            if new_console_errors:
                print(colors.red | f"  Console errors: {len(new_console_errors)}")
                for entry in new_console_errors[:5]:
                    print(colors.red | f"    - {self._format_error_entry(entry)}")
            if new_log_errors:
                print(colors.red | f"  Log errors: {len(new_log_errors)}")
                for entry in new_log_errors[:5]:
                    print(colors.red | f"    - {self._format_error_entry(entry)}")
        elif warnings and self.verbose and not self.json_output:
            print(colors.yellow | f"Error probe warnings after {command_name}:")
            for warning in warnings:
                print(colors.yellow | f"  - {warning}")

        return bool(has_new_errors and bool(getattr(self, "fail_on_new_errors", False)))

    def _output(self, data: dict):
        """Output data in JSON or formatted text"""
        self._last_payload = data
        if isinstance(data, dict) and (data.get("error") or data.get("success") is False):
            self._command_failed = True
        if self.json_output:
            fmt = "ndjson" if self.output_format == "ndjson" else "json"
            print(render_value(data, fmt), end="")
        else:
            # Custom formatting based on data type
            if isinstance(data, dict) and data.get("error"):
                print(colors.red | f"Error: {data['error']}", file=sys.stderr)
                # Display additional error context if available
                if isinstance(data, dict):
                    if "help" in data:
                        print(colors.yellow | f"Help: {data['help']}", file=sys.stderr)
                    if "received" in data:
                        print(f"Received: {data['received']}", file=sys.stderr)
                    if "requested_name" in data:
                        print(f"Requested: {data['requested_name']}", file=sys.stderr)
                    if "available_functions" in data and data["available_functions"]:
                        funcs = data["available_functions"][:5]
                        print("\nAvailable functions:", file=sys.stderr)
                        for func in funcs:
                            print(f"  • {func}", file=sys.stderr)
                        if len(data["available_functions"]) > 5:
                            print(
                                f"  ... and {len(data['available_functions']) - 5} more",
                                file=sys.stderr,
                            )
            elif isinstance(data, dict) and data.get("success"):
                print(colors.green | "Success!")
                if "message" in data:
                    print(data["message"])
            else:
                # Pretty print the data
                print(json.dumps(data, indent=2))

    def main(self):
        """Validate shared arguments before any child command can perform work."""
        if not self.nested_command:
            self.help()
            return 1
        context = _invocation.get()
        if context is not None and context["is_meta"]:
            return 0
        try:
            if self.spill and self.no_spill:
                raise ValueError("Choose --spill or --no-spill, not both")
            if self.json_output and self.output_format not in {None, "json"}:
                raise ValueError("--json cannot be combined with a different --format")
            fmt = (
                "json"
                if self.json_output
                else self.output_format or getattr(self.nested_command[0], "OUTPUT_FORMAT", "text")
            )
            options = OutputOptions(
                format=fmt,
                out=self.out,
                overwrite=self.overwrite_output,
                match=self.match,
                before=self.before,
                after=self.after,
                spill=False if self.no_spill else True if self.spill else None,
                tokens=self.tokens,
            )
            for name, value in (
                ("--request-timeout", self.request_timeout),
                ("--connect-timeout", self.connect_timeout),
            ):
                if not math.isfinite(value) or value <= 0:
                    raise ValueError(f"{name} must be finite and positive")
            if self.error_probe_count < 1:
                raise ValueError("--error-probe-count must be positive")
            options.validate()
            self._output_options = options
            self.output_format = fmt
            self.json_output = fmt in {"json", "ndjson"}
        except (ValueError, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        return 0


@BinaryNinjaCLI.subcommand("schema")
class Schema(cli.Application):
    """Describe all commands or a scoped command path; works without a server."""

    OUTPUT_FORMAT = "json"

    def main(self, *command_path):
        try:
            data = command_schema(BinaryNinjaCLI, command_path)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        self.parent._output(data)
        return 0


@BinaryNinjaCLI.subcommand("doctor")
class Doctor(cli.Application):
    """Inspect loaded server versions, capabilities and reload requirements."""

    OUTPUT_FORMAT = "json"

    def main(self):
        root = self.parent
        if root.target_view_id:
            root._route_discovered_target(timeout=root.request_timeout)
        if root._discovery_enabled() and not root.target_view_id and not root.target_filename:
            servers = root._discover_servers(timeout=min(root.connect_timeout, 0.5))
        else:
            servers = [root._request("GET", "meta/instance")]
        instances = [
            {**server, "compatibility": assess_compatibility(server)} for server in servers
        ]
        success = bool(instances) and all(item["compatibility"]["compatible"] for item in instances)
        result = {
            "success": success,
            "client": {
                "version": TOOL_VERSION,
                "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
            },
            "instances": instances,
        }
        if not instances:
            result["error"] = (
                "No Binary Ninja HTTP instances found; start Plugins > MCP Server > Start MCP Server"
            )
        if root.json_output:
            root._output(result)
        else:
            print(render_value(result), end="")
        return 0 if success else 1


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
        if self.parent._discovery_enabled():
            servers = self.parent._discover_servers()
            if not servers and self.parent._server_reachable(timeout=0.5):
                data = self.parent._request("GET", "views")
            else:
                views = self.parent._get_discovered_views()
                data = {
                    "service": "binary_ninja_mcp",
                    "discovered_instance_count": len(servers),
                    "views": views,
                    "count": len(views),
                    "_api_version": self.parent._expected_api_version("/views"),
                }
        else:
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
            view_id = view.get("global_view_id") or view.get("view_id") or "?"
            basename = view.get("basename") or "<unknown>"
            filename = view.get("filename") or "<unknown>"
            view_type = view.get("view_type") or "unknown"
            arch = view.get("architecture") or "unknown"
            analysis = view.get("analysis_status") or "unknown"
            source = view.get("source") or "unknown"
            window_title = view.get("window_title") or ""
            target_hint = view.get("target_hint") or ""

            print(f"[{marker}] {view_id}  {basename}")
            print(f"    file: {filename}")
            print(f"    source: {source}")
            print(f"    type: {view_type}")
            print(f"    arch: {arch}")
            print(f"    analysis: {analysis}")
            if window_title:
                print(f"    window: {window_title}")
            if target_hint:
                print(f"    hint: {target_hint}")


@BinaryNinjaCLI.subcommand("resolve-target")
class ResolveTarget(cli.Application):
    """Resolve the effective BinaryView target for the current selectors."""

    def main(self):
        data = self.parent._request("GET", "target/resolve")

        if self.parent.json_output:
            self.parent._output(data)
            return

        target = data.get("target") if isinstance(data, dict) else None
        if not isinstance(target, dict):
            print(colors.yellow | "No target resolved")
            return

        print("Resolved Target:")
        print(f"  View ID: {target.get('view_id')}")
        print(f"  File: {target.get('filename')}")
        hint = target.get("target_hint")
        if hint:
            print(f"  Hint: {hint}")


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
    """Open a file and resolve Binary Ninja's file-opening dialogs.

    Behavior:
    - If MCP is not reachable, auto-launches Binary Ninja on supported platforms.
    - If an "Open existing database?" dialog appears, surfaces it and requires
      an explicit --existing-database yes/no/cancel answer.
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

    existing_database = cli.SwitchAttr(
        ["--existing-database"],
        str,
        help=(
            "Answer Binary Ninja's 'Open existing database?' prompt with "
            "yes, no, or cancel. The CLI never chooses implicitly."
        ),
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

    def _set_effective_open_target(
        self,
        parsed: dict,
        *,
        requested_filepath: str = "",
        matched_view: dict | None = None,
    ) -> None:
        if not isinstance(parsed, dict):
            return
        state = parsed.get("state")
        if not isinstance(state, dict):
            return

        loaded_filename_raw = state.get("loaded_filename")
        loaded_view_id_raw = parsed.get("selected_view_id")

        confirmed_filename = None
        confirmed_view_id = None
        if isinstance(matched_view, dict):
            confirmed_filename = matched_view.get("filename")
            confirmed_view_id = matched_view.get("view_id")

        if confirmed_filename:
            state["confirmed_target_filename"] = confirmed_filename
        if confirmed_view_id is not None:
            state["confirmed_target_view_id"] = confirmed_view_id

        effective_filename = (
            confirmed_filename
            or state.get("confirmed_target_filename")
            or loaded_filename_raw
            or (str(requested_filepath).strip() if requested_filepath else None)
        )
        effective_view_id = (
            confirmed_view_id
            if confirmed_view_id is not None
            else state.get("confirmed_target_view_id") or loaded_view_id_raw
        )

        if effective_filename:
            state["effective_target_filename"] = effective_filename
        if effective_view_id is not None:
            state["effective_target_view_id"] = effective_view_id

        if (
            confirmed_filename
            and loaded_filename_raw
            and not self.parent._filename_matches_requested(loaded_filename_raw, confirmed_filename)
        ):
            state["observed_loaded_filename"] = loaded_filename_raw
            # Prioritize confirmed view target for command output/state consumers.
            state["loaded_filename"] = confirmed_filename
            actions = parsed.get("actions")
            if isinstance(actions, list):
                if "effective_target_from_confirmed_view" not in actions:
                    actions.append("effective_target_from_confirmed_view")
            warnings = parsed.get("warnings")
            if isinstance(warnings, list):
                filtered = []
                for warning in warnings:
                    text = str(warning)
                    if "loaded filename differs" in text:
                        continue
                    filtered.append(warning)
                parsed["warnings"] = filtered

    def _missing_filepath_help(self) -> dict:
        usage = [
            "binja-mcp open <file>",
            "binja-mcp open --new-server <file>",
            "binja-mcp --view-id <global-view-id> open <file>",
            "binja-mcp --server http://localhost:<port> open <file>",
            "binja-mcp views",
        ]
        targets = []
        if self.parent._discovery_enabled():
            try:
                targets = self.parent._get_discovered_views(timeout=0.5)
            except Exception:
                targets = []
        return {
            "error": "missing file path for open",
            "help": (
                "Pass the file to open. When multiple Binary Ninja instances are running, "
                "choose the target instance with --view-id from `views`, use --server, "
                "or launch a fresh instance with --new-server."
            ),
            "usage": usage,
            "targets": targets,
        }

    def _open_target_selection_help(self, filepath: str) -> dict:
        target_file = str(filepath or "<file>").strip() or "<file>"
        targets = []
        if self.parent._discovery_enabled():
            try:
                targets = self.parent._get_discovered_views(timeout=0.5)
            except Exception:
                targets = []
        examples = [
            f"binja-mcp open --new-server {target_file}",
            f"binja-mcp --server http://localhost:<port> open {target_file}",
        ]
        for view in targets:
            global_view_id = view.get("global_view_id")
            if global_view_id:
                examples.insert(0, f"binja-mcp --view-id {global_view_id} open {target_file}")
        return {
            "error": "target Binary Ninja instance required for open",
            "help": (
                "Choose where to open this file. Use --new-server for a fresh Binary Ninja "
                "instance, --view-id for an existing discovered instance, or --server for a "
                "known MCP server URL."
            ),
            "file": target_file,
            "examples": examples,
            "targets": targets,
        }

    def _print_open_target_selection_help(self, filepath: str) -> int:
        payload = self._open_target_selection_help(filepath)
        if self.parent.json_output:
            self.parent._output(payload)
            return 1

        print(colors.red | f"Error: {payload['error']}", file=sys.stderr)
        print(payload["help"], file=sys.stderr)
        print("\nOptions:", file=sys.stderr)
        print(f"  binja-mcp open --new-server {payload['file']}", file=sys.stderr)
        print(
            f"  binja-mcp --server http://localhost:<port> open {payload['file']}", file=sys.stderr
        )
        targets = payload.get("targets") if isinstance(payload, dict) else None
        if isinstance(targets, list) and targets:
            print("\nExisting instances:", file=sys.stderr)
            print(self.parent._format_discovered_targets(targets), file=sys.stderr)
            first = targets[0].get("global_view_id")
            if first:
                print("\nExample:", file=sys.stderr)
                print(f"  binja-mcp --view-id {first} open {payload['file']}", file=sys.stderr)
        else:
            print("\nNo open BinaryViews were discovered.", file=sys.stderr)
        return 1

    def _print_missing_filepath_help(self) -> int:
        payload = self._missing_filepath_help()
        if self.parent.json_output:
            self.parent._output(payload)
            return 1

        print(colors.red | f"Error: {payload['error']}", file=sys.stderr)
        print(payload["help"], file=sys.stderr)
        print("\nUsage:", file=sys.stderr)
        for line in payload["usage"]:
            print(f"  {line}", file=sys.stderr)
        targets = payload.get("targets") if isinstance(payload, dict) else None
        if isinstance(targets, list) and targets:
            print("\nAvailable target instances:", file=sys.stderr)
            print(self.parent._format_discovered_targets(targets), file=sys.stderr)
        else:
            print("\nRun `binja-mcp views` to list open instances and views.", file=sys.stderr)
        return 1

    def main(self, filepath: str = ""):
        if not str(filepath or "").strip() and not self.inspect_only:
            return self._print_missing_filepath_help()

        existing_database_choice = str(getattr(self, "existing_database", "") or "").strip().lower()
        if existing_database_choice not in {"", "yes", "no", "cancel"}:
            message = "--existing-database must be one of: yes, no, cancel"
            if self.parent.json_output:
                self.parent._output({"error": message})
            else:
                print(colors.red | f"Error: {message}", file=sys.stderr)
            return 2

        if (
            str(filepath or "").strip()
            and not self.inspect_only
            and self.parent._discovery_enabled()
            and not self.parent.target_view_id
        ):
            return self._print_open_target_selection_help(str(filepath))

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
        error_snapshot = self.parent._capture_error_snapshot()
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
            "existing_database": existing_database_choice,
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

        raw_result = parsed.get("result") if isinstance(parsed.get("result"), dict) else {}
        if raw_result.get("requires_input"):
            required_input = raw_result.get("required_input")
            if not isinstance(required_input, dict):
                required_input = {
                    "name": "existing_database",
                    "question": "Open existing database?",
                    "options": ["yes", "no", "cancel"],
                }
            failure_payload = {
                "error": "existing database decision required",
                "required_input": required_input,
                "open_result": parsed,
            }
            if self.parent.json_output:
                self.parent._apply_post_command_error_report(
                    "open",
                    error_snapshot,
                    output_payload=failure_payload,
                )
                self.parent._output(failure_payload)
            else:
                print(colors.yellow | "⚠ Existing database decision required")
                print(f"  {required_input.get('question', 'Open existing database?')}")
                print(
                    "  Re-run with --existing-database yes, "
                    "--existing-database no, or --existing-database cancel."
                )
                self.parent._apply_post_command_error_report(
                    "open",
                    error_snapshot,
                )
            return 2

        raw_state = raw_result.get("state") if isinstance(raw_result.get("state"), dict) else {}
        if raw_state.get("cancelled"):
            if self.parent.json_output:
                self.parent._output({"open_result": parsed, "cancelled": True})
            else:
                print(colors.green | "✓ Open cancelled")
            return

        confirmation_filepath = filepath
        if filepath and existing_database_choice == "yes":
            raw_path = Path(filepath)
            database_candidates = [
                Path(f"{raw_path}.bndb"),
                raw_path.with_suffix(".bndb"),
            ]
            confirmation_filepath = str(
                next(
                    (candidate for candidate in database_candidates if candidate.exists()),
                    database_candidates[0],
                )
            )

        wait_open_target_s = float(self.wait_open_target or 0.0)
        target_confirm = None
        matched_view = None
        if confirmation_filepath and (not self.inspect_only) and wait_open_target_s > 0.0:
            target_confirm = self.parent._wait_for_open_target_in_views(
                confirmation_filepath,
                timeout=wait_open_target_s,
            )

            if not target_confirm.get("ok"):
                failure_payload = {
                    "error": "open target confirmation failed",
                    "requested_filename": confirmation_filepath,
                    "observed_current_filename": target_confirm.get("observed_current_filename"),
                    "observed_current_view_id": target_confirm.get("observed_current_view_id"),
                    "views": target_confirm.get("views", []),
                    "open_result": parsed,
                }
                if self.parent.json_output:
                    self.parent._apply_post_command_error_report(
                        "open",
                        error_snapshot,
                        output_payload=failure_payload,
                    )
                    self.parent._output(failure_payload)
                else:
                    print(colors.red | "✗ Open target confirmation failed")
                    print(f"  Requested: {confirmation_filepath}")
                    observed = target_confirm.get("observed_current_filename")
                    if observed:
                        print(f"  Observed Current: {observed}")
                    print("  Use `views` to inspect currently loaded tabs.")
                    self.parent._apply_post_command_error_report(
                        "open",
                        error_snapshot,
                    )
                return 1

            matched_view_obj = target_confirm.get("matched_view", {})
            if isinstance(matched_view_obj, dict):
                matched_view = matched_view_obj
            if isinstance(parsed.get("actions"), list):
                if "confirmed_target_via_views" not in parsed["actions"]:
                    parsed["actions"].append("confirmed_target_via_views")

        self._set_effective_open_target(
            parsed,
            requested_filepath=confirmation_filepath,
            matched_view=matched_view,
        )

        analysis_wait_result = None
        if self.wait_analysis and filepath and (not self.inspect_only):
            matched_filename = ""
            matched_view_id = None
            if isinstance(matched_view, dict):
                matched_filename = str(matched_view.get("filename") or "")
                matched_view_id = matched_view.get("view_id")

            analysis_wait_result = self.parent._wait_for_analysis_on_target(
                filename=matched_filename or confirmation_filepath,
                view_id=matched_view_id,
                timeout=float(self.analysis_timeout or 120.0),
            )
            if not isinstance(analysis_wait_result, dict) or not analysis_wait_result.get(
                "success"
            ):
                failure_payload = {
                    "error": "analysis wait failed after open",
                    "requested_filename": filepath,
                    "analysis_wait_result": analysis_wait_result,
                    "open_result": parsed,
                }
                if self.parent.json_output:
                    self.parent._apply_post_command_error_report(
                        "open",
                        error_snapshot,
                        output_payload=failure_payload,
                    )
                    self.parent._output(failure_payload)
                else:
                    print(colors.red | "✗ Analysis wait failed after open")
                    if isinstance(analysis_wait_result, dict):
                        err_obj = analysis_wait_result.get("error")
                        if err_obj:
                            print(f"  Error: {err_obj}")
                    self.parent._apply_post_command_error_report(
                        "open",
                        error_snapshot,
                    )
                return 1

        if self.parent.json_output:
            out = {"open_result": parsed}
            if isinstance(parsed.get("state"), dict):
                out["effective_target_filename"] = parsed["state"].get("effective_target_filename")
                out["effective_target_view_id"] = parsed["state"].get("effective_target_view_id")
            if analysis_wait_result is not None:
                out["analysis_wait_result"] = analysis_wait_result
            should_fail = self.parent._apply_post_command_error_report(
                "open",
                error_snapshot,
                output_payload=out,
            )
            self.parent._output(out)
            if should_fail:
                return 1
            return

        ok = bool(parsed.get("ok"))
        status_line = "✓ Open workflow completed" if ok else "⚠ Open workflow completed with issues"
        color = colors.green if ok else colors.yellow
        print(color | status_line)

        state = parsed.get("state", {}) if isinstance(parsed.get("state"), dict) else {}
        effective_target = state.get("effective_target_filename")
        if effective_target:
            print(f"  Target: {effective_target}")
        else:
            print("  Target: <unknown>")

        effective_target_view_id = state.get("effective_target_view_id")
        if effective_target_view_id is not None:
            print(f"  Target View ID: {effective_target_view_id}")

        observed_loaded = state.get("observed_loaded_filename")
        if observed_loaded:
            print(f"  Observed Loaded (raw): {observed_loaded}")

        active_window = state.get("active_window")
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

        should_fail = self.parent._apply_post_command_error_report(
            "open",
            error_snapshot,
        )
        if should_fail:
            return 1


@BinaryNinjaCLI.subcommand("close")
class Close(cli.Application):
    """Close visible Binary Ninja UI tabs.

    Examples:
      close --view-id <id> --decision dont-save
      close --filename /path/to/file.bndb --decision save
      close --all --except-view-id <id> --decision dont-save
    """

    decision = cli.SwitchAttr(
        ["--decision"],
        str,
        default="auto",
        help="Dirty-file decision: auto, save, dont-save, or cancel.",
    )

    view_id = cli.SwitchAttr(
        ["--view-id"],
        str,
        default="",
        help="Close the visible tab matching this BinaryView id.",
    )

    filename = cli.SwitchAttr(
        ["--filename", "--file"],
        str,
        default="",
        help="Close the visible tab matching this path or basename.",
    )

    all_tabs = cli.Flag(
        ["--all"],
        help="Close all visible UI tabs, optionally excluding one target.",
    )

    except_view_id = cli.SwitchAttr(
        ["--except-view-id"],
        str,
        default="",
        help="When --all is used, keep the visible tab matching this view id.",
    )

    except_filename = cli.SwitchAttr(
        ["--except-filename", "--except-file"],
        str,
        default="",
        help="When --all is used, keep the visible tab matching this filename.",
    )

    inspect_only = cli.Flag(
        ["--inspect-only"],
        help="Inspect selected tabs and dialogs only; do not close tabs or click buttons.",
    )

    wait_ms = cli.SwitchAttr(
        ["--wait-ms"],
        int,
        default=2000,
        help="Maximum time to wait for each confirmation dialog after close (ms).",
    )

    exec_timeout = cli.SwitchAttr(
        ["--exec-timeout"],
        float,
        default=120.0,
        help="UI close request timeout in seconds.",
    )

    def _print_selector_help(self) -> None:
        print(colors.yellow | "Close needs a target selector.")
        print("Use one of:")
        print("  binja-mcp close --view-id <id> [--decision dont-save]")
        print("  binja-mcp close --filename <path-or-name> [--decision save]")
        print("  binja-mcp close --all --except-view-id <id> [--decision dont-save]")
        print("  binja-mcp --server http://localhost:<port> close --filename <path-or-name>")
        try:
            targets = self.parent._get_discovered_views(timeout=0.5)
        except Exception:
            targets = []
        if targets:
            print("\nVisible views:")
            for view in targets:
                view_id = view.get("global_view_id") or view.get("view_id") or "?"
                filename = view.get("filename") or "<unknown>"
                print(f"  --view-id {view_id}")
                print(f"      file: {filename}")
        else:
            print("\nNo visible Binary Ninja views were discovered.")

    def _route_view_id_for_filename(self, filename: str) -> str:
        if not filename or not self.parent._discovery_enabled():
            return ""
        try:
            views = self.parent._get_discovered_views(timeout=0.5)
        except Exception:
            return ""

        matches = []
        for view in views:
            if not isinstance(view, dict):
                continue
            if self.parent._filename_matches_requested(view.get("filename"), filename):
                matches.append(view)

        if not matches:
            return ""

        unique_view_ids = []
        seen = set()
        for view in matches:
            global_view_id = view.get("global_view_id") or view.get("view_id")
            if not global_view_id:
                continue
            key = str(global_view_id)
            if key in seen:
                continue
            seen.add(key)
            unique_view_ids.append(key)

        if len(unique_view_ids) == 1:
            return unique_view_ids[0]

        raise RuntimeError(
            f"filename selector {filename!r} matches multiple discovered views; use --view-id.\n"
            + self.parent._format_discovered_targets(matches)
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

        view_id = self.view_id or self.parent.target_view_id or ""
        filename = self.filename or self.parent.target_filename or ""
        except_view_id = self.except_view_id or ""
        view_instance_id, _local_view_id = self.parent._split_global_view_id(view_id)
        except_instance_id, local_except_view_id = self.parent._split_global_view_id(except_view_id)
        if view_instance_id and except_instance_id and view_instance_id != except_instance_id:
            print(
                colors.red
                | "Conflicting close targets: --view-id and --except-view-id refer to different instances."
            )
            return 2

        route_view_id = view_id
        if self.all_tabs and not route_view_id and except_view_id:
            route_view_id = except_view_id
        if not route_view_id and filename:
            try:
                route_view_id = self._route_view_id_for_filename(filename)
            except RuntimeError as exc:
                if self.parent.json_output:
                    self.parent._output({"error": str(exc)})
                else:
                    print(colors.red | f"Error: {exc}", file=sys.stderr)
                return 2
        except_view_id = local_except_view_id or except_view_id

        if not self.all_tabs and not view_id and not filename:
            if self.parent.json_output:
                self.parent._output(
                    {
                        "error": "close requires --view-id, --filename, or --all",
                        "views": self.parent._get_discovered_views(timeout=0.5),
                    }
                )
            else:
                self._print_selector_help()
            return 2

        config = {
            "decision": decision_in,
            "view_id": view_id,
            "filename": filename,
            "all": bool(self.all_tabs),
            "except_view_id": except_view_id,
            "except_filename": self.except_filename or "",
            "inspect_only": bool(self.inspect_only),
            "wait_ms": int(2000 if self.wait_ms is None else self.wait_ms),
        }

        previous_parent_view_id = self.parent.target_view_id
        previous_parent_filename = self.parent.target_filename
        if route_view_id:
            self.parent.target_view_id = route_view_id
        if filename:
            self.parent.target_filename = filename
        try:
            parsed = self.parent._request(
                "POST",
                "ui/close",
                data=config,
                timeout=max(self.parent.request_timeout, float(self.exec_timeout or 120.0)),
            )
        finally:
            self.parent.target_view_id = previous_parent_view_id
            self.parent.target_filename = previous_parent_filename
        if not isinstance(parsed, dict):
            print(colors.yellow | "Close endpoint returned an unexpected payload.")
            return 1
        parsed = self.parent._validate_ui_contract(parsed, "/ui/close")
        state_for_status = parsed.get("state", {}) if isinstance(parsed.get("state"), dict) else {}
        ok_for_status = bool(parsed.get("ok"))
        stuck_for_status = bool(state_for_status.get("stuck_confirmation"))

        if self.parent.json_output:
            self.parent._output({"close_result": parsed})
            if not ok_for_status or stuck_for_status:
                return 1
            return

        details = parsed.get("result", {}) if isinstance(parsed.get("result"), dict) else {}
        policy = details.get("policy", {}) if isinstance(details.get("policy"), dict) else {}
        state = details.get("state", {}) if isinstance(details.get("state"), dict) else {}
        selected = (
            state.get("selected_tabs") if isinstance(state.get("selected_tabs"), list) else []
        )
        remaining = state.get("tabs_after") if isinstance(state.get("tabs_after"), list) else []

        ok = bool(parsed.get("ok"))
        stuck = bool(state.get("stuck_confirmation"))
        status_line = (
            "✓ Close workflow completed"
            if ok and not stuck
            else "⚠ Close workflow completed with issues"
        )
        color = colors.green if ok and not stuck else colors.yellow
        print(color | status_line)
        print(f"  Policy Decision: {policy.get('resolved_decision')}")
        print(f"  Selected Tabs: {len(selected)}")
        print(f"  Remaining Tabs: {len(remaining)}")
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
            return 1
        if not ok or stuck:
            return 1


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
            "wait_ms": int(2000 if self.wait_ms is None else self.wait_ms),
            "quit_app": bool(self.quit_app),
            "quit_delay_ms": int(300 if self.quit_delay_ms is None else self.quit_delay_ms),
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

    allow_analysis_skipped = cli.Flag(
        ["--allow-analysis-skipped"],
        help=(
            "Explicitly clear an analysis-skipped function's skip state so it can "
            "be decompiled. This is a persistent mutation."
        ),
    )

    def main(self, function_name: str):
        error_snapshot = self.parent._capture_error_snapshot()
        data = self.parent._request(
            "GET",
            "decompile",
            {
                "name": function_name,
                "allow_analysis_skipped": bool(self.allow_analysis_skipped),
            },
            timeout=max(self.parent.request_timeout, 30.0),
        )

        if self.parent.json_output:
            should_fail = self.parent._apply_post_command_error_report(
                "decompile",
                error_snapshot,
                output_payload=data if isinstance(data, dict) else None,
            )
            self.parent._output(data)
            if should_fail:
                return 1
        else:
            if "error" in data:
                print(colors.red | f"Error: {data['error']}")
            else:
                print(colors.cyan | f"Decompiled code for {function_name}:")
                print(data.get("decompiled", "No decompilation available"))
            should_fail = self.parent._apply_post_command_error_report(
                "decompile",
                error_snapshot,
            )
            if should_fail:
                return 1


@BinaryNinjaCLI.subcommand("assembly")
class Assembly(cli.Application):
    """Get assembly code for a function"""

    def main(self, function_name: str):
        error_snapshot = self.parent._capture_error_snapshot()
        data = self.parent._request(
            "GET",
            "assembly",
            {"name": function_name},
            timeout=max(self.parent.request_timeout, 30.0),
        )

        if self.parent.json_output:
            should_fail = self.parent._apply_post_command_error_report(
                "assembly",
                error_snapshot,
                output_payload=data if isinstance(data, dict) else None,
            )
            self.parent._output(data)
            if should_fail:
                return 1
        else:
            if "error" in data:
                print(colors.red | f"Error: {data['error']}")
            else:
                print(colors.cyan | f"Assembly for {function_name}:")
                print(data.get("assembly", "No assembly available"))
            should_fail = self.parent._apply_post_command_error_report(
                "assembly",
                error_snapshot,
            )
            if should_fail:
                return 1


class _AnalysisCommand(cli.Application):
    def _emit(self, data, text=None):
        if self.parent.json_output or data.get("error"):
            self.parent._output(data)
        else:
            print(text if text is not None else json.dumps(data, indent=2))
            for warning in data.get("warnings", []):
                print(f"Warning: {warning}", file=sys.stderr)
        return 1 if data.get("success") is False or data.get("error") else 0


@BinaryNinjaCLI.subcommand("disasm")
class Disasm(_AnalysisCommand):
    """Linear disassembly of mapped bytes; no function or IL required.

    Names, interior addresses and symbol+offset expressions are accepted.
    Defaults to 32 instructions. --end is exclusive; partial output exits 1.
    Use assembly FUNCTION for the existing annotated function presentation.
    """

    count = cli.SwitchAttr(["--count", "-n"], int, help="Decode this many instructions (1-100000)")
    end = cli.SwitchAttr(["--end"], str, help="Decode up to this exclusive address or symbol")
    arch = cli.SwitchAttr(["--arch"], str, help="Explicit Binary Ninja architecture (e.g. thumb2)")

    def main(self, identifier: str):
        try:
            if self.count is not None and self.end is not None:
                raise ValueError("Choose --count or --end, not both")
            count = (
                instruction_count(self.count)
                if self.count is not None
                else (32 if self.end is None else None)
            )
            if self.end is not None and not self.end.strip():
                raise ValueError("Exclusive end must not be empty")
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        params = {"identifier": identifier}
        params.update(
            {
                key: value
                for key, value in {"count": count, "end": self.end, "arch": self.arch}.items()
                if value is not None
            }
        )
        data = self.parent._request("GET", "analysis/disasm", params)
        text = data.get("text", "")
        if data.get("stopped_reason"):
            text += f"\nStopped at {data.get('next_address')}: {data['stopped_reason']}"
        return self._emit(data, text)


@BinaryNinjaCLI.subcommand("info")
class FunctionInfo(_AnalysisCommand):
    """Compact function metadata and counts; add --locals for variable details."""

    include_locals = cli.Flag(["--locals"], help="Include parameter/local details with stable IDs")

    def main(self, identifier: str):
        data = self.parent._request(
            "GET",
            "analysis/function",
            {"identifier": identifier, "locals": bool(self.include_locals)},
        )
        function = data.get("function", {})
        text = None
        if function and not self.include_locals:
            text = (
                f"{function['address']} {function['name']} ({function['architecture']})\n"
                f"{data.get('prototype', '')}\n"
                f"Size: {data.get('size')} bytes; parameters: {data.get('parameter_count')}; locals: {data.get('local_count')}\n"
                f"Analysis skipped: {data.get('analysis_skipped')}"
            )
        return self._emit(data, text)


@BinaryNinjaCLI.subcommand("bundle")
class Bundle(_AnalysisCommand):
    """Read selected sections for one or more functions in one pinned view.

    Always returns a functions array. Aliases resolving to the same function
    share one entry. Section/identifier failures preserve other results and exit 1.
    Sections: decompile, mlil, llil, disasm, locals, comments, xrefs, refs_from, all.
    refs aliases incoming xrefs; refs_from is outgoing.
    """

    OUTPUT_FORMAT = "json"
    include = cli.SwitchAttr(
        ["--include"], str, help="Comma-separated sections; default: decompile,disasm,refs_from"
    )
    time_budget = cli.SwitchAttr(
        ["--time-budget"],
        float,
        default=30.0,
        help="Cooperative budget for the whole bundle in seconds; cannot interrupt an SDK call",
    )

    def main(self, identifier: str, *more_identifiers):
        try:
            sections = bundle_sections(self.include)
            budget = analysis_time_budget(self.time_budget)
            identifiers = [identifier, *more_identifiers]
            if len(identifiers) > MAX_BUNDLE_FUNCTIONS or any(not i.strip() for i in identifiers):
                raise ValueError(
                    f"Provide 1 to {MAX_BUNDLE_FUNCTIONS} nonempty function identifiers"
                )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        data = self.parent._request(
            "POST",
            "analysis/bundle",
            data={"identifiers": identifiers, "include": sections, "time_budget": budget},
            timeout=max(self.parent.request_timeout, budget + 5.0),
        )
        return self._emit(data)


class _BudgetedAnalysisCommand(_AnalysisCommand):
    time_budget = cli.SwitchAttr(
        ["--time-budget"],
        float,
        default=30.0,
        help="Cooperative budget in seconds; cannot interrupt an SDK call",
    )

    def _read(self, endpoint, params):
        try:
            budget = analysis_time_budget(self.time_budget)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return None
        return self.parent._request(
            "GET",
            endpoint,
            {**params, "time_budget": budget},
            timeout=max(self.parent.request_timeout, budget + 5.0),
        )


@BinaryNinjaCLI.subcommand("il")
class IntermediateLanguage(_BudgetedAnalysisCommand):
    """Read HLIL/MLIL/LLIL, optionally SSA, without clearing analysis-skip state."""

    level = cli.SwitchAttr(
        ["--level", "--view"],
        cli.Set(*IL_LEVELS),
        default="hlil",
        help="Requested IL level (no fallback to another level)",
    )
    ssa = cli.Flag(["--ssa"], help="Read the selected IL's SSA form")

    def main(self, identifier: str):
        data = self._read(
            "analysis/il", {"identifier": identifier, "level": self.level, "ssa": bool(self.ssa)}
        )
        if data is None:
            return 2
        text = data.get("text", "")
        if data.get("stopped_reason"):
            text += f"\nStopped: {data['stopped_reason']}"
        return self._emit(data, text)


@BinaryNinjaCLI.subcommand("read")
class ReadMemory(_AnalysisCommand):
    """Read typed memory. Count means elements, bytes, or a C-string byte bound.

    Defaults: bytes=16, cstr=256, all scalar/pointer types=1.
    Short reads keep complete elements and raw trailing bytes, and exit 1.
    """

    OUTPUT_FORMAT = "json"
    value_type = cli.SwitchAttr(
        ["--type", "-t"],
        cli.Set(*READ_TYPES),
        default="bytes",
        help="Scalar, pointer, byte or C-string representation",
    )
    count = cli.SwitchAttr(
        ["--count", "-n"], int, help="Number of elements; for cstr, maximum bytes to inspect"
    )
    endian = cli.SwitchAttr(
        ["--endian"],
        cli.Set("auto", "little", "big"),
        default="auto",
        help="Default uses the view's endianness",
    )

    def main(self, identifier: str):
        try:
            count = read_arguments(self.value_type, self.count, self.endian)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        data = self.parent._request(
            "GET",
            "analysis/read",
            {
                "identifier": identifier,
                "type": self.value_type,
                "count": count,
                "endian": self.endian,
            },
        )
        return self._emit(data)


@BinaryNinjaCLI.subcommand("xrefs")
class CrossReferences(_BudgetedAnalysisCommand):
    """Incoming code/data references to an address/symbol, or Type.field with --field.

    The existing refs FUNCTION command retains its legacy incoming-code output.
    """

    OUTPUT_FORMAT = "json"
    field = cli.Flag(["--field"], help="Interpret the identifier as Type.field or Type.0xOFFSET")

    def main(self, identifier: str):
        if self.field and ("." not in identifier or not all(identifier.rsplit(".", 1))):
            print("Error: Field selector must be Type.field or Type.0xOFFSET", file=sys.stderr)
            return 2
        data = self._read(
            "analysis/refs",
            {"identifier": identifier, "direction": "incoming", "field": bool(self.field)},
        )
        return 2 if data is None else self._emit(data)


@BinaryNinjaCLI.subcommand("refs-from")
class ReferencesFrom(_BudgetedAnalysisCommand):
    """Outgoing code/data references from a function (including interior-address lookup)."""

    OUTPUT_FORMAT = "json"

    def main(self, identifier: str):
        data = self._read("analysis/refs", {"identifier": identifier, "direction": "outgoing"})
        return 2 if data is None else self._emit(data)


@BinaryNinjaCLI.subcommand("signature")
class Signature(cli.Application):
    """Safely set and verify a function signature.

    Use stdin or --file for declarations containing a backtick-qualified name.
    Backticks make the complete Class::method spelling one Binary Ninja
    identifier; unquoted backticks are command substitution in most shells.

    Examples:
        binja-cli signature 0x6c562 --file declaration.c
        binja-cli signature 0x6c562 --stdin < declaration.c
        binja-cli signature 0x6c562 --dry-run --file declaration.c
    """

    file = cli.SwitchAttr(
        ["--file", "-f"],
        cli.ExistingFile,
        help="Read the complete function declaration from a file",
    )
    stdin = cli.Flag(
        ["--stdin"],
        help="Read the complete function declaration from stdin",
    )
    apply_name = cli.Flag(
        ["--apply-name"],
        help="Also replace the function name with the declaration's parsed name",
    )
    dry_run = cli.Flag(
        ["--dry-run"],
        help="Parse and report the declaration without changing the BinaryView",
    )
    preview = cli.Flag(
        ["--preview"],
        help="Apply, analyze and verify the signature, then revert without committing",
    )
    no_reanalyze = cli.Flag(
        ["--no-reanalyze"],
        help="Set the user type without explicitly reanalyzing the function",
    )
    no_wait = cli.Flag(
        ["--no-wait"],
        help="Queue reanalysis without waiting; also disables deterministic verification",
    )
    no_verify = cli.Flag(
        ["--no-verify"],
        help="Do not compare the applied type and name with their readback values",
    )
    analysis_timeout = cli.SwitchAttr(
        ["--analysis-timeout"],
        float,
        default=1800.0,
        help="HTTP timeout while Binary Ninja waits for analysis (default: 1800 seconds)",
    )

    def _read_signature(self, signature_parts: tuple[str, ...]) -> str | None:
        use_stdin = self.stdin or (signature_parts and signature_parts[0] == "-")
        sources = int(bool(self.file)) + int(bool(use_stdin)) + int(bool(signature_parts))
        if use_stdin and signature_parts == ("-",):
            sources -= 1
        if sources > 1:
            print(colors.red | "Choose only one signature source: arguments, --file, or stdin")
            return None

        try:
            if self.file:
                signature = self.file.read()
            elif use_stdin:
                signature = sys.stdin.read()
            elif signature_parts:
                signature = " ".join(signature_parts)
            elif not sys.stdin.isatty():
                signature = sys.stdin.read()
            else:
                print("Usage: binja-cli signature [options] FUNCTION DECLARATION")
                print("       binja-cli signature FUNCTION --file declaration.c")
                print("       binja-cli signature FUNCTION --stdin < declaration.c")
                return None
        except (OSError, UnicodeError) as exc:
            print(colors.red | f"Error reading signature: {exc}")
            return None

        signature = str(signature).strip()
        if not signature:
            print(colors.red | "No function declaration received")
            return None
        return signature

    def main(self, function_name: str, *signature_parts: str):
        if self.preview and (self.dry_run or self.no_wait or self.no_verify):
            print(
                "--preview cannot be combined with --dry-run, --no-wait or --no-verify",
                file=sys.stderr,
            )
            return 2
        if not math.isfinite(self.analysis_timeout) or self.analysis_timeout <= 0:
            print("--analysis-timeout must be finite and positive", file=sys.stderr)
            return 2
        signature = self._read_signature(signature_parts)
        if signature is None:
            return 1

        should_wait = not self.no_wait
        should_verify = not self.no_verify and should_wait
        payload = {
            "function": function_name,
            "signature": signature,
            "apply_name": bool(self.apply_name),
            "dry_run": bool(self.dry_run),
            "reanalyze": not self.no_reanalyze,
            "wait": should_wait,
            "verify": should_verify,
        }
        if self.preview:
            payload["preview"] = True
        timeout = self.parent.request_timeout
        if should_wait and not self.dry_run:
            timeout = max(timeout, float(self.analysis_timeout))

        error_snapshot = self.parent._capture_error_snapshot()
        data = self.parent._request(
            "POST",
            "function/signature",
            data=payload,
            timeout=timeout,
        )
        failed = not isinstance(data, dict) or bool(data.get("error")) or not data.get("success")

        if self.parent.json_output:
            error_failure = self.parent._apply_post_command_error_report(
                "signature",
                error_snapshot,
                output_payload=data if isinstance(data, dict) else None,
            )
            self.parent._output(data)
            return 1 if failed or error_failure else 0

        if failed:
            self.parent._output(data if isinstance(data, dict) else {"error": str(data)})
        else:
            state = (
                "parsed"
                if data.get("dry_run")
                else "previewed and reverted"
                if data.get("preview")
                else "applied"
            )
            if data.get("verified"):
                state += " and verified"
            print(colors.green | f"Signature {state} for {function_name}")
            if data.get("function_start"):
                print(f"  Address: {data['function_start']}")
            if data.get("before_name") != data.get("after_name"):
                print(f"  Name: {data.get('before_name')} -> {data.get('after_name')}")
            print(f"  Before: {data.get('before_type')}")
            print(f"  After:  {data.get('after_type')}")
            if self.no_wait and not self.no_verify:
                print(colors.yellow | "  Verification skipped because --no-wait was requested")

        error_failure = self.parent._apply_post_command_error_report("signature", error_snapshot)
        return 1 if failed or error_failure else 0


@BinaryNinjaCLI.subcommand("reanalyze")
class Reanalyze(cli.Application):
    """Explicitly reanalyze one function and optionally wait for completion."""

    no_wait = cli.Flag(["--no-wait"], help="Queue reanalysis without waiting for completion")
    analysis_timeout = cli.SwitchAttr(
        ["--analysis-timeout"],
        float,
        default=1800.0,
        help="HTTP timeout while Binary Ninja waits for analysis (default: 1800 seconds)",
    )

    def main(self, function_name: str):
        should_wait = not self.no_wait
        timeout = self.parent.request_timeout
        if should_wait:
            timeout = max(timeout, float(self.analysis_timeout))

        error_snapshot = self.parent._capture_error_snapshot()
        data = self.parent._request(
            "POST",
            "function/reanalyze",
            data={"function": function_name, "wait": should_wait},
            timeout=timeout,
        )
        failed = not isinstance(data, dict) or bool(data.get("error")) or not data.get("success")
        error_failure = self.parent._apply_post_command_error_report(
            "reanalyze",
            error_snapshot,
            output_payload=data if self.parent.json_output and isinstance(data, dict) else None,
        )
        self.parent._output(data if isinstance(data, dict) else {"error": str(data)})
        return 1 if failed or error_failure else 0


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


@BinaryNinjaCLI.subcommand("annotations")
class Annotations(cli.Application):
    """Export portable user annotations and native type information"""


@Annotations.subcommand("export")
class AnnotationsExport(cli.Application):
    """Write a portable JSON annotation archive and matching BNTL

    The output path is interpreted by the Binary Ninja process and must be
    absolute. The matching type-library path defaults to replacing the final
    `.json` suffix with `.types.bntl`.
    """

    type_library_path = cli.SwitchAttr(
        ["--type-library"],
        str,
        default=None,
        help="Absolute path for the native .bntl companion",
    )
    source_id = cli.SwitchAttr(
        ["--source-id"],
        str,
        default=None,
        help="Stable source identity such as git-sha1:<oid> or sha256:<digest>",
    )
    source_filename = cli.SwitchAttr(
        ["--source-filename"],
        str,
        default=None,
        help="Canonical source filename to record instead of the loaded path",
    )
    source_size = cli.SwitchAttr(
        ["--source-size"],
        int,
        default=None,
        help="Canonical source size to record",
    )
    source_mtime_ns = cli.SwitchAttr(
        ["--source-mtime-ns"],
        int,
        default=None,
        help="Canonical source modification time in nanoseconds",
    )
    force = cli.Flag(
        ["--force"],
        help="Replace existing JSON/BNTL outputs",
    )
    include_unannotated_function_types = cli.Flag(
        ["--include-unannotated-function-types"],
        help=(
            "Include every function type Binary Ninja marks explicit/user, even without "
            "an adjacent user name, variable, or comment"
        ),
    )

    def main(self, output_path: str):
        root = self.parent.parent
        payload = {
            "output_path": output_path,
            "overwrite": bool(self.force),
            "include_unannotated_function_types": bool(self.include_unannotated_function_types),
        }
        optional_values = {
            "type_library_path": self.type_library_path,
            "source_id": self.source_id,
            "source_filename": self.source_filename,
            "source_size": self.source_size,
            "source_mtime_ns": self.source_mtime_ns,
        }
        payload.update(
            {name: value for name, value in optional_values.items() if value is not None}
        )

        data = root._request("POST", "annotations/export", data=payload)
        if root.json_output:
            root._output(data)
            return

        json_info = data.get("json", {})
        type_info = data.get("type_library", {})
        print(colors.green | "Exported portable Binary Ninja annotations")
        print(f"  JSON: {json_info.get('path', output_path)}")
        print(f"  BNTL: {type_info.get('path', '<unknown>')}")
        counts = data.get("counts", {})
        nonzero_counts = {name: value for name, value in counts.items() if value}
        if nonzero_counts:
            print("  Counts:")
            for name, value in sorted(nonzero_counts.items()):
                print(f"    {name}: {value}")
        else:
            print("  Counts: no user annotations")


@BinaryNinjaCLI.subcommand("py")
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

    file = cli.SwitchAttr(
        ["-f", "--file", "--script"], cli.ExistingFile, help="Execute Python code from file"
    )
    source_code = cli.SwitchAttr(
        ["--code"], str, help="Execute explicit inline Python, without probing the filesystem"
    )
    no_syntax_check = cli.Flag(
        ["--no-syntax-check"],
        help="Defer syntax validation to embedded Python (for differing Python versions)",
    )

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
        source_filename = "<console>"
        positional_stdin = args == ("-",)
        source_count = (
            int(self.source_code is not None)
            + int(bool(self.file))
            + int(bool(self.stdin or positional_stdin))
            + int(bool(args) and not positional_stdin)
        )
        if (
            source_count > 1
            or (self.interactive and source_count)
            or (self.complete is not None and (source_count or self.interactive))
        ):
            print(
                "Choose one Python mode/source: --code, --script/--file, stdin, positional input, --interactive, or --complete",
                file=sys.stderr,
            )
            return 2
        if not math.isfinite(self.exec_timeout) or self.exec_timeout <= 0:
            print("--exec-timeout must be finite and positive", file=sys.stderr)
            return 2

        # Handle completion request
        if self.complete is not None:
            error_snapshot = self.parent._capture_error_snapshot()
            data = self.parent._request(
                "GET", "console/complete", params={"partial": self.complete}
            )
            completions = data.get("completions", [])
            if self.parent.json_output:
                should_fail = self.parent._apply_post_command_error_report(
                    "python.complete",
                    error_snapshot,
                    output_payload=data if isinstance(data, dict) else None,
                )
                self.parent._output(data)
                if should_fail:
                    return 1
            else:
                if completions:
                    for comp in completions:
                        print(comp)
                else:
                    print(f"No completions for '{self.complete}'")
                should_fail = self.parent._apply_post_command_error_report(
                    "python.complete",
                    error_snapshot,
                )
                if should_fail:
                    return 1
            return 0

        # Determine source of code
        if self.interactive:
            options = getattr(self.parent, "_output_options", None)
            if options and (
                options.format != "text"
                or options.out
                or options.match is not None
                or options.spill is True
                or options.tokens
            ):
                print(
                    "Interactive Python requires text stdout without filtering, artifacts, or token counting",
                    file=sys.stderr,
                )
                return 2
            # Prompts must remain live instead of entering the buffered output layer.
            with redirect_stdout(getattr(self.parent, "_live_stdout", sys.stdout)):
                self._interactive_mode()
            return 0

        elif self.source_code is not None:
            code = self.source_code
        elif self.file:
            # Explicit file flag
            try:
                code = self.file.read()
                source_filename = str(self.file)
            except Exception as e:
                print(colors.red | f"Error reading file: {e}", file=sys.stderr)
                return 1

        elif self.stdin or (args and args[0] == "-"):
            # Read from stdin
            try:
                code = sys.stdin.read()
                if not code.strip():
                    print(colors.red | "No input received from stdin", file=sys.stderr)
                    return 1
            except KeyboardInterrupt:
                print("\nCancelled")
                return 1
            except Exception as e:
                print(colors.red | f"Error reading stdin: {e}", file=sys.stderr)
                return 1

        elif args:
            # Check if first argument is a file
            if len(args) == 1 and not args[0].startswith("-"):
                from pathlib import Path

                file_path = Path(args[0])
                try:
                    is_file = file_path.exists() and file_path.is_file()
                except OSError as exc:
                    if exc.errno != errno.ENAMETOOLONG:
                        raise
                    # A long inline program is not a plausible filesystem path.
                    # macOS raises ENAMETOOLONG before ``exists`` can return false.
                    is_file = False
                if is_file:
                    # It's a file, read it
                    try:
                        code = file_path.read_text()
                        source_filename = str(file_path)
                    except Exception as e:
                        print(colors.red | f"Error reading file '{args[0]}': {e}", file=sys.stderr)
                        return 1
                else:
                    # Not a file, treat as inline code
                    code = " ".join(args)
            else:
                # Multiple arguments or starts with -, treat as inline code
                code = " ".join(args)

        else:
            # No arguments, check if stdin is piped
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
                    print(colors.red | f"Error reading piped input: {e}", file=sys.stderr)
                    return 1

        if not code:
            print("No code to execute", file=sys.stderr)
            return 1

        if not self.no_syntax_check:
            try:
                # Compile only: never execute client-side. Match the executor's
                # parse/compile boundary so bad syntax cannot reach side effects.
                tree = ast.parse(code, filename=source_filename, mode="exec")
                compile(tree, source_filename, "exec")
            except (SyntaxError, ValueError) as exc:
                print(
                    f"Python syntax error: {source_filename}:{getattr(exc, 'lineno', '?')}: "
                    f"{getattr(exc, 'msg', str(exc))}. If embedded Python is newer, use --no-syntax-check.",
                    file=sys.stderr,
                )
                return 2

        # Execute the code
        error_snapshot = self.parent._capture_error_snapshot()
        data = self.parent._request(
            "POST",
            "console/execute",
            data={"command": code, "timeout": self.exec_timeout},
        )

        if self.parent.json_output:
            should_fail = self.parent._apply_post_command_error_report(
                "python.execute",
                error_snapshot,
                output_payload=data if isinstance(data, dict) else None,
            )
            self.parent._output(data)
            if should_fail:
                return 1
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

            should_fail = self.parent._apply_post_command_error_report(
                "python.execute",
                error_snapshot,
            )
            if should_fail:
                return 1

        return 0 if data.get("success") else 1

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


def main():
    """Console-script entry point (does not import the Binary Ninja SDK)."""
    BinaryNinjaCLI.run()


if __name__ == "__main__":
    main()
