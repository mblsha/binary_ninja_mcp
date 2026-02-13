#!/usr/bin/env python3
"""
Automated test script for all Binary Ninja MCP commands
"""

import json
import subprocess
from typing import Dict, Any
import urllib.parse

import requests

# Configuration
CLI_PATH = "scripts/binja-cli.py"
BRIDGE_URL = "http://localhost:9009"
DEFAULT_ENDPOINT_API_VERSION = 1
ENDPOINT_API_VERSION_OVERRIDES = {
    "/ui/open": 2,
    "/ui/quit": 2,
    "/ui/statusbar": 2,
}


class CommandTester:
    def __init__(self):
        self.results = []
        self.test_function = "room1_entry_point"  # Using the renamed function
        self.test_address = "0x4a4"

    def run_cli(self, command: str) -> Dict[str, Any]:
        """Run CLI command and return result"""
        full_cmd = f"uv run python {CLI_PATH} --json {command}"
        try:
            result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                return {"success": True, "output": json.loads(result.stdout)}
            else:
                return {"success": False, "error": result.stderr}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def _expected_api_version(endpoint: str) -> int:
        path = str(endpoint or "").split("?", 1)[0]
        if not path.startswith("/"):
            path = f"/{path}"
        return ENDPOINT_API_VERSION_OVERRIDES.get(path, DEFAULT_ENDPOINT_API_VERSION)

    def run_curl(self, endpoint: str, method: str = "GET", data: Dict = None) -> Dict[str, Any]:
        """Run curl command to test bridge-only endpoints"""
        path, _, query = endpoint.partition("?")
        url = f"{BRIDGE_URL}/{path}"
        params = dict(urllib.parse.parse_qsl(query)) if query else {}
        api_version = self._expected_api_version(endpoint)
        params["_api_version"] = api_version
        headers = {"X-Binja-MCP-Api-Version": str(api_version)}
        try:
            if method == "GET":
                response = requests.get(url, params=params, headers=headers, timeout=10)
            else:
                payload = dict(data or {})
                payload["_api_version"] = api_version
                response = requests.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    json=payload,
                    timeout=10,
                )

            response.raise_for_status()
            response_data = response.json()

            header_version = int(response.headers.get("X-Binja-MCP-Api-Version", -1))
            if header_version != api_version:
                return {
                    "success": False,
                    "error": (
                        "Version mismatch in response header: "
                        f"client={api_version} server={header_version}"
                    ),
                }
            body_version = int(response_data.get("_api_version", -1))
            if body_version != api_version:
                return {
                    "success": False,
                    "error": (
                        "Version mismatch in response body: "
                        f"client={api_version} server={body_version}"
                    ),
                }
            return {"success": True, "output": response_data}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def test_command(
        self,
        name: str,
        cli_cmd: str = None,
        bridge_endpoint: str = None,
        method: str = "GET",
        data: Dict = None,
    ) -> Dict[str, Any]:
        """Test a single command"""
        print(f"Testing {name}...")

        if cli_cmd:
            result = self.run_cli(cli_cmd)
            interface = "CLI"
        elif bridge_endpoint:
            result = self.run_curl(bridge_endpoint, method, data)
            interface = "Bridge"
        else:
            result = {"success": False, "error": "No command specified"}
            interface = "Unknown"

        test_result = {
            "name": name,
            "interface": interface,
            "command": cli_cmd or bridge_endpoint,
            "success": result["success"],
            "output": result.get("output"),
            "error": result.get("error"),
        }

        self.results.append(test_result)
        return test_result

    def run_all_tests(self):
        """Run all command tests"""
        print("Starting comprehensive MCP command tests...\n")

        # 1. Binary Status & Information
        self.test_command("get_binary_status", cli_cmd="status")

        # 2. Code Listing & Search
        self.test_command("list_methods", cli_cmd="functions --limit 5")
        self.test_command("list_classes", bridge_endpoint="classes?limit=5")
        self.test_command("list_segments", bridge_endpoint="segments?limit=5")
        self.test_command("list_imports", cli_cmd="imports --limit 5")
        self.test_command("list_exports", cli_cmd="exports --limit 5")
        self.test_command("list_namespaces", bridge_endpoint="namespaces?limit=5")
        self.test_command("list_data_items", bridge_endpoint="data?limit=5")
        self.test_command("search_functions_by_name", cli_cmd="functions --search room --limit 5")

        # 3. Code Analysis
        self.test_command("decompile_function", cli_cmd=f"decompile {self.test_function}")
        self.test_command("fetch_disassembly", cli_cmd=f"assembly {self.test_function}")
        self.test_command("function_at", bridge_endpoint=f"functionAt?address={self.test_address}")
        self.test_command("code_references", cli_cmd=f"refs {self.test_function}")
        self.test_command("get_user_defined_type", cli_cmd="type Point")

        # 4. Code Modification
        self.test_command("rename_function", cli_cmd="rename function room2_enter room2_entry")
        self.test_command("rename_data", cli_cmd="rename data 0x8282 my_data")
        self.test_command(
            "rename_variable",
            bridge_endpoint="renameVariable?functionName=room2_entry&variableName=var_1&newName=counter",
        )
        self.test_command(
            "retype_variable",
            bridge_endpoint="retypeVariable?functionName=room2_entry&variableName=counter&type=uint32_t",
        )
        self.test_command(
            "define_types", cli_cmd='type --define "struct Rectangle { int width; int height; };"'
        )
        self.test_command(
            "edit_function_signature",
            bridge_endpoint="editFunctionSignature?functionName=room2_entry&signature=void room2_entry(int param)",
        )

        # 5. Comments
        self.test_command("set_comment", cli_cmd='comment 0x8250 "Room 2 exit function"')
        self.test_command("get_comment", cli_cmd="comment 0x8250")
        self.test_command(
            "set_function_comment",
            cli_cmd='comment --function room2_entry "Entry point for room 2"',
        )
        self.test_command(
            "get_function_comment", bridge_endpoint="comment/function?name=room2_entry"
        )
        self.test_command("delete_comment", cli_cmd="comment --delete 0x8250")
        self.test_command(
            "delete_function_comment",
            bridge_endpoint="comment/function",
            method="POST",
            data={"name": "room2_entry", "_method": "DELETE"},
        )

        # 6. Logging
        self.test_command("get_logs", cli_cmd="logs --count 5")
        self.test_command("get_log_stats", cli_cmd="logs --stats")
        self.test_command("get_log_errors", cli_cmd="logs --errors --count 5")
        self.test_command("get_log_warnings", cli_cmd="logs --warnings --count 5")
        self.test_command("clear_logs", cli_cmd="logs --clear")

        # 7. Console
        self.test_command("get_console_output", bridge_endpoint="console?count=5")
        self.test_command("get_console_stats", bridge_endpoint="console/stats")
        self.test_command("get_console_errors", bridge_endpoint="console/errors?count=5")
        self.test_command(
            "execute_python_command",
            bridge_endpoint="console/execute",
            method="POST",
            data={"command": "print('Test from MCP')"},
        )
        self.test_command("clear_console", bridge_endpoint="console/clear", method="POST")

        print(f"\nTests completed: {len(self.results)}")

    def generate_report(self) -> str:
        """Generate markdown report of test results"""
        passed = sum(1 for r in self.results if r["success"])
        failed = sum(1 for r in self.results if not r["success"])

        report = f"""# MCP Commands Test Results

## Summary
- Total Commands Tested: {len(self.results)}
- Passed: {passed}
- Failed: {failed}
- Success Rate: {passed / len(self.results) * 100:.1f}%

## Detailed Results

"""

        for result in self.results:
            status = "✅" if result["success"] else "❌"
            report += f"### {result['name']}\n"
            report += f"- **Status**: {status}\n"
            report += f"- **Interface**: {result['interface']}\n"
            report += f"- **Command**: `{result['command']}`\n"

            if result["success"] and result.get("output"):
                report += f"- **Sample Output**:\n```json\n{json.dumps(result['output'], indent=2)[:500]}\n```\n"
            elif not result["success"]:
                report += f"- **Error**: {result.get('error', 'Unknown error')}\n"

            report += "\n"

        return report


if __name__ == "__main__":
    tester = CommandTester()
    tester.run_all_tests()

    # Generate and save report
    report = tester.generate_report()
    with open("test_results.md", "w") as f:
        f.write(report)

    print("\nReport saved to test_results.md")

    # Also update the main report
    print("Run this script from the MCP directory to test all commands")
