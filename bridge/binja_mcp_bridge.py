import json

import requests
from mcp.server.fastmcp import FastMCP


binja_server_url = "http://localhost:9009"
mcp = FastMCP("binja-mcp")


def safe_get(endpoint: str, params: dict = None) -> list:
    """
    Perform a GET request. If 'params' is given, we convert it to a query string.
    """
    if params is None:
        params = {}
    qs = [f"{k}={v}" for k, v in params.items()]
    query_string = "&".join(qs)
    url = f"{binja_server_url}/{endpoint}"
    if query_string:
        url += "?" + query_string

    try:
        response = requests.get(url, timeout=5)
        response.encoding = "utf-8"
        if response.ok:
            return response.text.splitlines()
        else:
            return [f"Error {response.status_code}: {response.text.strip()}"]
    except Exception as e:
        return [f"Request failed: {str(e)}"]


def safe_get_json(endpoint: str, params: dict = None) -> str:
    """
    Perform a GET request and return the raw response text (for JSON endpoints).
    """
    if params is None:
        params = {}
    qs = [f"{k}={v}" for k, v in params.items()]
    query_string = "&".join(qs)
    url = f"{binja_server_url}/{endpoint}"
    if query_string:
        url += "?" + query_string

    try:
        response = requests.get(url, timeout=5)
        response.encoding = "utf-8"
        if response.ok:
            return response.text.strip()
        else:
            return f"Error {response.status_code}: {response.text.strip()}"
    except Exception as e:
        return f"Request failed: {str(e)}"


def safe_post(endpoint: str, data: dict | str) -> str:
    try:
        if isinstance(data, dict):
            response = requests.post(f"{binja_server_url}/{endpoint}", json=data, timeout=5)
        else:
            response = requests.post(
                f"{binja_server_url}/{endpoint}", data=data.encode("utf-8"), timeout=5
            )
        response.encoding = "utf-8"
        if response.ok:
            return response.text.strip()
        else:
            return f"Error {response.status_code}: {response.text.strip()}"
    except Exception as e:
        return f"Request failed: {str(e)}"


@mcp.tool()
def list_methods(offset: int = 0, limit: int = 100) -> list:
    """
    List all function names in the program with pagination.
    """
    return safe_get("methods", {"offset": offset, "limit": limit})


@mcp.tool()
def retype_variable(function_name: str, variable_name: str, type_str: str) -> str:
    """
    Retype a variable in a function.
    """
    return safe_get_json(
        "retypeVariable",
        {"functionName": function_name, "variableName": variable_name, "type": type_str},
    )


@mcp.tool()
def rename_variable(function_name: str, variable_name: str, new_name: str) -> str:
    """
    Rename a variable in a function.
    """
    return safe_get_json(
        "renameVariable",
        {"functionName": function_name, "variableName": variable_name, "newName": new_name},
    )


@mcp.tool()
def define_types(c_code: str) -> str:
    """
    Define types from a C code string.
    """
    return safe_get_json("defineTypes", {"cCode": c_code})


@mcp.tool()
def edit_function_signature(function_name: str, signature: str) -> str:
    """
    Edit the signature of a function.
    """
    return safe_get_json(
        "editFunctionSignature", {"functionName": function_name, "signature": signature}
    )


@mcp.tool()
def list_classes(offset: int = 0, limit: int = 100) -> list:
    """
    List all namespace/class names in the program with pagination.
    """
    return safe_get("classes", {"offset": offset, "limit": limit})


@mcp.tool()
def decompile_function(name: str) -> str:
    """
    Decompile a specific function by name and return the decompiled C code.
    """
    return safe_get_json("decompile", {"name": name})


@mcp.tool()
def fetch_disassembly(name: str) -> str:
    """
    Retrive the disassembled code of a function with a given name as assemby mnemonic instructions.
    """
    return safe_get_json("assembly", {"name": name})


@mcp.tool()
def rename_function(old_name: str, new_name: str) -> str:
    """
    Rename a function by its current name to a new user-defined name.
    """
    return safe_post("renameFunction", {"oldName": old_name, "newName": new_name})


@mcp.tool()
def rename_data(address: str, new_name: str) -> str:
    """
    Rename a data label at the specified address.
    """
    return safe_post("renameData", {"address": address, "newName": new_name})


@mcp.tool()
def set_comment(address: str, comment: str) -> str:
    """
    Set a comment at a specific address.
    """
    return safe_post("comment", {"address": address, "comment": comment})


@mcp.tool()
def set_function_comment(function_name: str, comment: str) -> str:
    """
    Set a comment for a function.
    """
    return safe_post("comment/function", {"name": function_name, "comment": comment})


@mcp.tool()
def get_comment(address: str) -> str:
    """
    Get the comment at a specific address.
    """
    return safe_get_json("comment", {"address": address})


@mcp.tool()
def get_function_comment(function_name: str) -> str:
    """
    Get the comment for a function.
    """
    return safe_get_json("comment/function", {"name": function_name})


@mcp.tool()
def list_segments(offset: int = 0, limit: int = 100) -> list:
    """
    List all memory segments in the program with pagination.
    """
    return safe_get("segments", {"offset": offset, "limit": limit})


@mcp.tool()
def list_imports(offset: int = 0, limit: int = 100) -> list:
    """
    List imported symbols in the program with pagination.
    """
    return safe_get("imports", {"offset": offset, "limit": limit})


@mcp.tool()
def list_exports(offset: int = 0, limit: int = 100) -> list:
    """
    List exported functions/symbols with pagination.
    """
    return safe_get("exports", {"offset": offset, "limit": limit})


@mcp.tool()
def list_namespaces(offset: int = 0, limit: int = 100) -> list:
    """
    List all non-global namespaces in the program with pagination.
    """
    return safe_get("namespaces", {"offset": offset, "limit": limit})


@mcp.tool()
def list_data_items(offset: int = 0, limit: int = 100) -> list:
    """
    List defined data labels and their values with pagination.
    """
    return safe_get("data", {"offset": offset, "limit": limit})


@mcp.tool()
def search_functions_by_name(query: str, offset: int = 0, limit: int = 100) -> list:
    """
    Search for functions whose name contains the given substring.
    """
    if not query:
        return ["Error: query string is required"]
    return safe_get("searchFunctions", {"query": query, "offset": offset, "limit": limit})


@mcp.tool()
def get_binary_status() -> str:
    """
    Get the current status of the loaded binary.
    """
    return safe_get_json("status")


@mcp.tool()
def delete_comment(address: str) -> str:
    """
    Delete the comment at a specific address.
    """
    return safe_post("comment", {"address": address, "_method": "DELETE"})


@mcp.tool()
def delete_function_comment(function_name: str) -> str:
    """
    Delete the comment for a function.
    """
    return safe_post("comment/function", {"name": function_name, "_method": "DELETE"})


@mcp.tool()
def function_at(address: str) -> str:
    """
    Retrive the name of the function the address belongs to. Address must be in hexadecimal format 0x00001
    """
    return safe_get_json("functionAt", {"address": address})


@mcp.tool()
def code_references(function_name: str) -> str:
    """
    Retrive names and addresses of functions that call the given function_name
    """
    return safe_get_json("codeReferences", {"function": function_name})


@mcp.tool()
def get_user_defined_type(type_name: str) -> str:
    """
    Retrive definition of a user defined type (struct, enumeration, typedef, union)
    """
    return safe_get_json("getUserDefinedType", {"name": type_name})


@mcp.tool()
def get_logs(count: int = 100, level: str = None, search: str = None, start_id: int = None) -> list:
    """
    Get Binary Ninja log messages.

    Args:
        count: Maximum number of logs to return (default: 100)
        level: Filter by log level (DebugLog, InfoLog, WarningLog, ErrorLog, AlertLog)
        search: Search for text in log messages
        start_id: Return logs with ID greater than this value (for pagination)
    """
    params = {"count": count}
    if level:
        params["level"] = level
    if search:
        params["search"] = search
    if start_id is not None:
        params["start_id"] = start_id
    return safe_get("logs", params)


@mcp.tool()
def get_log_stats() -> str:
    """
    Get statistics about captured Binary Ninja logs (counts by level, total logs, etc).
    """
    return safe_get_json("logs/stats")


@mcp.tool()
def get_log_errors(count: int = 10) -> list:
    """
    Get the most recent error logs from Binary Ninja.

    Args:
        count: Maximum number of errors to return (default: 10)
    """
    return safe_get("logs/errors", {"count": count})


@mcp.tool()
def get_log_warnings(count: int = 10) -> list:
    """
    Get the most recent warning logs from Binary Ninja.

    Args:
        count: Maximum number of warnings to return (default: 10)
    """
    return safe_get("logs/warnings", {"count": count})


@mcp.tool()
def clear_logs() -> str:
    """
    Clear all captured Binary Ninja logs.
    """
    return safe_post("logs/clear", {})


@mcp.tool()
def get_console_output(
    count: int = 100, type_filter: str = None, search: str = None, start_id: int = None
) -> list:
    """
    Get Python console output from Binary Ninja.

    Args:
        count: Maximum number of entries to return (default: 100)
        type_filter: Filter by output type (output, error, warning, input)
        search: Search for text in console output
        start_id: Return entries with ID greater than this value (for pagination)
    """
    params = {"count": count}
    if type_filter:
        params["type"] = type_filter
    if search:
        params["search"] = search
    if start_id is not None:
        params["start_id"] = start_id
    return safe_get("console", params)


@mcp.tool()
def get_console_stats() -> str:
    """
    Get statistics about captured console output (counts by type, total entries, etc).
    """
    return safe_get_json("console/stats")


@mcp.tool()
def get_console_errors(count: int = 10) -> list:
    """
    Get the most recent error output from the Python console.

    Args:
        count: Maximum number of errors to return (default: 10)
    """
    return safe_get("console/errors", {"count": count})


@mcp.tool()
def execute_python_command(command: str) -> str:
    """
    Execute a Python command in Binary Ninja's console and return the result.

    Args:
        command: Python code to execute

    Returns JSON with:
        - success: bool indicating if execution succeeded
        - stdout: captured standard output
        - stderr: captured standard error
        - return_value: JSON-serialized return value
        - return_type: type name of return value
        - variables: dict of created/modified variables
        - error: error details if execution failed
        - execution_time: time taken in seconds
    """
    result = safe_post("console/execute", {"command": command})

    # If it's a string response, try to parse as JSON
    if isinstance(result, str):
        try:
            return json.dumps(json.loads(result), indent=2)
        except json.JSONDecodeError:
            return result

    return result


@mcp.tool()
def clear_console() -> str:
    """
    Clear all captured console output.
    """
    return safe_post("console/clear", {})


if __name__ == "__main__":
    print("Starting MCP bridge service...")
    mcp.run()
