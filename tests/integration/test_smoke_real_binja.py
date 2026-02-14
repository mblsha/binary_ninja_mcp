from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.binja


def test_real_binja_smoke_workflow(client, analysis_context):
    fixture_binary_path = analysis_context["fixture_binary_path"]
    function_name = analysis_context["function_name"]
    function_prefix = analysis_context["function_prefix"]
    entry_address = analysis_context["entry_address"]

    # 1) Open a known fixture binary with explicit view selection request.
    open_response, open_body = client.request(
        "POST",
        "/ui/open",
        json={
            "filepath": fixture_binary_path,
            "view_type": "Raw",
            "platform": "",
            "click_open": True,
            "inspect_only": False,
        },
        timeout=60.0,
    )
    assert open_response.status_code == 200, open_body
    assert open_body.get("schema_version") == 1
    assert open_body.get("endpoint") == "/ui/open"
    assert open_body.get("ok") is True
    raw_open_result = open_body.get("result", {})
    assert raw_open_result.get("input", {}).get("view_type") == "Raw"

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

    # 4) Function endpoints should be live and searchable.
    functions_response, functions_body = client.request("GET", "/functions", params={"limit": 20})
    assert functions_response.status_code == 200, functions_body
    function_names = {
        (item.get("name") if isinstance(item, dict) else item)
        for item in list(functions_body.get("functions") or [])
    }
    assert function_name in function_names

    search_response, search_body = client.request(
        "GET",
        "/searchFunctions",
        params={"query": function_prefix, "limit": 10},
    )
    assert search_response.status_code == 200, search_body
    assert isinstance(search_body.get("matches"), list)

    # 5) Decompile and assembly on a known function.
    decompile_response, decompile_body = client.request(
        "GET",
        "/decompile",
        params={"name": function_name},
        timeout=30.0,
    )
    assert decompile_response.status_code == 200, decompile_body
    assert bool(decompile_body.get("decompiled"))

    assembly_response, assembly_body = client.request(
        "GET",
        "/assembly",
        params={"name": function_name},
        timeout=30.0,
    )
    assert assembly_response.status_code == 200, assembly_body
    assert bool(assembly_body.get("assembly"))

    function_at_response, function_at_body = client.request(
        "GET",
        "/functionAt",
        params={"address": entry_address},
    )
    assert function_at_response.status_code == 200, function_at_body
    assert isinstance(function_at_body.get("functions"), list)

    refs_response, refs_body = client.request(
        "GET",
        "/codeReferences",
        params={"function": function_name},
    )
    assert refs_response.status_code == 200, refs_body
    assert "code_references" in refs_body

    # 6) Exercise mutators: comments, aliases, and type definition.
    comment_text = "mcp-smoke-address-comment-v2"
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

    get_comment_alias_response, get_comment_alias_body = client.request(
        "GET",
        "/getComment",
        params={"address": entry_address},
    )
    assert get_comment_alias_response.status_code == 200, get_comment_alias_body
    assert get_comment_alias_body.get("comment") == comment_text

    function_comment = "mcp-smoke-function-comment-v2"
    set_func_comment_response, set_func_comment_body = client.request(
        "POST",
        "/comment/function",
        json={"name": function_name, "comment": function_comment},
    )
    assert set_func_comment_response.status_code == 200, set_func_comment_body
    assert set_func_comment_body.get("success") is True

    get_func_comment_response, get_func_comment_body = client.request(
        "GET",
        "/comment/function",
        params={"name": function_name},
    )
    assert get_func_comment_response.status_code == 200, get_func_comment_body
    assert get_func_comment_body.get("comment") == function_comment

    get_func_comment_alias_response, get_func_comment_alias_body = client.request(
        "GET",
        "/getFunctionComment",
        params={"name": function_name},
    )
    assert get_func_comment_alias_response.status_code == 200, get_func_comment_alias_body
    assert get_func_comment_alias_body.get("comment") == function_comment

    define_types_response, define_types_body = client.request(
        "GET",
        "/defineTypes",
        params={"cCode": "typedef unsigned long mcp_smoke_type_t;"},
    )
    assert define_types_response.status_code == 200, define_types_body
    assert isinstance(define_types_body, dict)
    assert "mcp_smoke_type_t" in define_types_body

    # 7) UI statusbar endpoint should return the stable contract shape.
    statusbar_response, statusbar_body = client.request(
        "POST",
        "/ui/statusbar",
        json={"all_windows": False, "include_hidden": False},
    )
    assert statusbar_response.status_code == 200, statusbar_body
    assert statusbar_body.get("schema_version") == 1
    assert statusbar_body.get("endpoint") == "/ui/statusbar"
    assert "result" in statusbar_body

    # 8) Non-destructive quit workflow contract should remain stable.
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
