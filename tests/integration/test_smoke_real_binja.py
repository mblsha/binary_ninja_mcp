from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.binja


def test_real_binja_smoke_workflow(client, fixture_binary_path):
    # 1) Open a known fixture binary.
    open_response, open_body = client.request(
        "POST",
        "/ui/open",
        json={"filepath": fixture_binary_path, "click_open": True, "inspect_only": False},
        timeout=60.0,
    )
    assert open_response.status_code == 200, open_body
    assert open_body.get("schema_version") == 1
    assert open_body.get("endpoint") == "/ui/open"
    assert open_body.get("ok") is True

    # 2) Status should now show a loaded file.
    status_response, status_body = client.request("GET", "/status")
    assert status_response.status_code == 200, status_body
    assert status_body.get("loaded") is True
    observed_filename = status_body.get("filename")
    assert observed_filename
    assert Path(observed_filename).resolve() == Path(fixture_binary_path).resolve()

    # 3) Console execute should work with the loaded BinaryView.
    execute_response, execute_body = client.request(
        "POST",
        "/console/execute",
        json={"command": "bv is not None"},
    )
    assert execute_response.status_code == 200, execute_body
    assert execute_body.get("success") is True
    assert execute_body.get("return_value") is True

    # 4) Fetch functions and decompile at least one function.
    functions_response, functions_body = client.request("GET", "/functions", params={"limit": 20})
    assert functions_response.status_code == 200, functions_body
    raw_functions = list(functions_body.get("functions") or [])
    assert raw_functions, "expected at least one function"
    function_names = []
    for item in raw_functions:
        if isinstance(item, dict):
            name = item.get("name")
        else:
            name = item
        if isinstance(name, str) and name:
            function_names.append(name)
    assert function_names, "expected at least one function name"

    decompile_ok = False
    for func_name in function_names[:10]:
        decompile_response, decompile_body = client.request(
            "GET",
            "/decompile",
            params={"name": func_name},
            timeout=30.0,
        )
        if decompile_response.status_code == 200 and decompile_body.get("decompiled"):
            decompile_ok = True
            break
    assert decompile_ok, "expected at least one function to decompile"

    # 5) Exercise two mutators: address comment and function comment.
    entry_address_response, entry_address_body = client.request(
        "POST",
        "/console/execute",
        json={"command": "_result = hex(bv.entry_point) if bv else None"},
    )
    assert entry_address_response.status_code == 200, entry_address_body
    entry_address = entry_address_body.get("return_value")
    assert isinstance(entry_address, str) and entry_address.startswith("0x")

    comment_text = "mcp-smoke-address-comment"
    set_comment_response, set_comment_body = client.request(
        "POST",
        "/comment",
        json={"address": entry_address, "comment": comment_text},
    )
    assert set_comment_response.status_code == 200, set_comment_body
    assert set_comment_body.get("success") is True

    get_comment_response, get_comment_body = client.request(
        "GET",
        "/comment",
        params={"address": entry_address},
    )
    assert get_comment_response.status_code == 200, get_comment_body
    assert get_comment_body.get("comment") == comment_text

    function_comment = "mcp-smoke-function-comment"
    set_func_comment_response, set_func_comment_body = client.request(
        "POST",
        "/comment/function",
        json={"name": function_names[0], "comment": function_comment},
    )
    assert set_func_comment_response.status_code == 200, set_func_comment_body
    assert set_func_comment_body.get("success") is True

    get_func_comment_response, get_func_comment_body = client.request(
        "GET",
        "/comment/function",
        params={"name": function_names[0]},
    )
    assert get_func_comment_response.status_code == 200, get_func_comment_body
    assert get_func_comment_body.get("comment") == function_comment

    # 6) UI statusbar endpoint should return the stable contract shape.
    statusbar_response, statusbar_body = client.request(
        "POST",
        "/ui/statusbar",
        json={"all_windows": False, "include_hidden": False},
    )
    assert statusbar_response.status_code == 200, statusbar_body
    assert statusbar_body.get("schema_version") == 1
    assert statusbar_body.get("endpoint") == "/ui/statusbar"
    assert "result" in statusbar_body

    # 7) Quit workflow should execute and return a stable contract.
    quit_response, quit_body = client.request(
        "POST",
        "/ui/quit",
        json={"decision": "dont-save", "wait_ms": 1500, "inspect_only": True},
        timeout=30.0,
    )
    assert quit_response.status_code == 200, quit_body
    assert quit_body.get("schema_version") == 1
    assert quit_body.get("endpoint") == "/ui/quit"
    assert isinstance(quit_body.get("actions"), list)
