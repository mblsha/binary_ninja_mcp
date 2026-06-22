#!/usr/bin/env python3

from __future__ import annotations

import errno
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import patch


THIS_DIR = Path(__file__).resolve().parent


class _FakeBinaryView:
    pass


class _FakePluginCommand:
    @staticmethod
    def register(*_args, **_kwargs):
        return None


class _FakeBinaryViewType:
    @staticmethod
    def add_binaryview_initial_analysis_completion_event(*_args, **_kwargs):
        return None


class _FakeWFile:
    def __init__(self, write_exc: OSError | None = None, flush_exc: OSError | None = None):
        self.write_exc = write_exc
        self.flush_exc = flush_exc
        self.writes = []
        self.flush_calls = 0

    def write(self, data):
        self.writes.append(data)
        if self.write_exc is not None:
            raise self.write_exc

    def flush(self):
        self.flush_calls += 1
        if self.flush_exc is not None:
            raise self.flush_exc


def _import_http_server():
    if str(THIS_DIR) not in sys.path:
        sys.path.insert(0, str(THIS_DIR))

    bn_module = types.ModuleType("binaryninja")
    bn_enums = types.ModuleType("binaryninja.enums")
    bn_enums.TypeClass = object()
    bn_enums.StructureVariant = object()
    bn_module.enums = bn_enums
    bn_module.BinaryView = _FakeBinaryView
    bn_module.Function = object
    bn_module.PluginCommand = _FakePluginCommand
    bn_module.BinaryViewType = _FakeBinaryViewType
    bn_module.log_info = lambda *_args, **_kwargs: None
    bn_module.log_warn = lambda *_args, **_kwargs: None
    bn_module.log_error = lambda *_args, **_kwargs: None

    modules_to_clear = [
        name
        for name in sys.modules
        if name == "plugin" or name.startswith(("plugin.", "binaryninja"))
    ]
    for name in modules_to_clear:
        sys.modules.pop(name, None)

    with patch.dict(sys.modules, {"binaryninja": bn_module, "binaryninja.enums": bn_enums}):
        return importlib.import_module("plugin.server.http_server")


def _new_handler(
    http_server,
    *,
    write_exc: OSError | None = None,
    flush_exc: OSError | None = None,
):
    handler = http_server.MCPRequestHandler.__new__(http_server.MCPRequestHandler)
    handler.path = "/meta/instance?_api_version=1"
    handler.wfile = _FakeWFile(write_exc=write_exc, flush_exc=flush_exc)
    handler.close_connection = False
    handler.responses = []

    def _set_headers(*, content_type="application/json", status_code=200):
        handler.responses.append((content_type, status_code))

    handler._set_headers = _set_headers
    return handler


def test_send_json_response_treats_broken_pipe_as_client_disconnect():
    http_server = _import_http_server()
    handler = _new_handler(http_server, write_exc=BrokenPipeError(errno.EPIPE, "broken pipe"))

    sent = handler._send_json_response({"service": "binary_ninja_mcp"})

    assert sent is False
    assert handler.close_connection is True
    assert handler.responses == [("application/json", 200)]


def test_send_json_response_treats_posix_connection_abort_as_client_disconnect():
    http_server = _import_http_server()
    handler = _new_handler(http_server, write_exc=OSError(errno.ECONNABORTED, "aborted"))

    sent = handler._send_json_response({"service": "binary_ninja_mcp"})

    assert sent is False
    assert handler.close_connection is True


def test_send_json_response_treats_connection_reset_during_flush_as_disconnect():
    http_server = _import_http_server()
    handler = _new_handler(
        http_server,
        flush_exc=OSError(errno.ECONNRESET, "connection reset"),
    )

    sent = handler._send_json_response({"service": "binary_ninja_mcp"})

    assert sent is False
    assert handler.close_connection is True
    assert handler.wfile.flush_calls == 1


def test_do_get_does_not_attempt_error_response_after_client_disconnect():
    http_server = _import_http_server()
    handler = http_server.MCPRequestHandler.__new__(http_server.MCPRequestHandler)
    handler.close_connection = False
    handler.error_responses = []

    def _parse_query_params():
        raise ConnectionResetError(errno.ECONNRESET, "connection reset")

    def _send_json_response(*args, **kwargs):
        handler.error_responses.append((args, kwargs))

    handler._parse_query_params = _parse_query_params
    handler._send_json_response = _send_json_response

    handler.do_GET()

    assert handler.close_connection is True
    assert handler.error_responses == []


def test_send_json_response_re_raises_non_disconnect_oserror():
    http_server = _import_http_server()
    handler = _new_handler(http_server, write_exc=OSError(errno.EINVAL, "invalid"))

    try:
        handler._send_json_response({"service": "binary_ninja_mcp"})
    except OSError as exc:
        assert exc.errno == errno.EINVAL
    else:
        raise AssertionError("non-disconnect OSError should propagate")
