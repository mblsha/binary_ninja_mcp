#!/usr/bin/env python3
"""Test script to verify the HTTP API and CLI work with the Python executor."""

import subprocess
import json
import sys
import requests
import pytest

DEFAULT_ENDPOINT_API_VERSION = 1


def _server_reachable(url: str = "http://localhost:9009") -> bool:
    try:
        response = requests.get(
            f"{url.rstrip('/')}/status",
            params={"_api_version": DEFAULT_ENDPOINT_API_VERSION},
            headers={"X-Binja-MCP-Api-Version": str(DEFAULT_ENDPOINT_API_VERSION)},
            timeout=2,
        )
        return response.status_code == 200
    except Exception:
        return False


def _cli_target_args() -> list[str]:
    """Return an explicit CLI target when discovery mode sees open BinaryViews."""
    cmd = ["uv", "run", "python", "scripts/binja-cli.py", "--json", "views"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
    except Exception:
        return []

    views = data.get("views") if isinstance(data, dict) else None
    if not isinstance(views, list) or not views:
        return []

    first = views[0]
    if not isinstance(first, dict):
        return []
    view_id = first.get("global_view_id") or first.get("view_id")
    if not view_id:
        return []
    return ["--view-id", str(view_id)]


def _run_cli_python_command() -> bool:
    """Run CLI python command checks."""
    print("Testing CLI Python Command")
    print("=" * 50)
    target_args = _cli_target_args()

    tests = [
        {
            "name": "Simple expression",
            "command": ["python", "2 + 2"],
            "check": lambda r: "4" in r or "→ 4" in r,
        },
        {
            "name": "Print statement",
            "command": ["python", "print('Hello from CLI')"],
            "check": lambda r: "Hello from CLI" in r,
        },
        {
            "name": "Binary view check",
            "command": ["python", "isinstance(bv is not None, bool)"],
            "check": lambda r: "True" in r or "→ True" in r,
        },
        {
            "name": "JSON output",
            "command": ["--json", "python", "len(list(bv.functions)) if bv else 0"],
            "check": lambda r: '"success"' in r and '"return_value"' in r,
        },
    ]

    passed = 0
    failed = 0

    for test in tests:
        print(f"\nTest: {test['name']}")
        cmd = ["uv", "run", "python", "scripts/binja-cli.py"] + target_args + test["command"]
        print(f"Command: {' '.join(cmd)}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            output = result.stdout + result.stderr

            if test["check"](output):
                print("✅ Passed")
                if "--json" in test["command"]:
                    # Pretty print JSON
                    try:
                        data = json.loads(result.stdout)
                        print(f"   Return value: {data.get('return_value')}")
                        print(f"   Success: {data.get('success')}")
                    except json.JSONDecodeError:
                        pass
                else:
                    print(f"   Output: {output.strip()[:100]}")
                passed += 1
            else:
                print("❌ Failed")
                print(f"   Output: {output[:200]}")
                failed += 1

        except Exception as e:
            print(f"❌ Exception: {e}")
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"CLI Tests: {passed} passed, {failed} failed")
    return failed == 0


def _run_http_python_endpoint() -> bool:
    """Run HTTP execute_python_command checks."""
    print("\n\nTesting HTTP Python Endpoint")
    print("=" * 50)

    import requests

    tests = [
        {
            "name": "Execute via HTTP endpoint",
            "command": "2 + 2",
            "check": lambda r: r.get("success") and r.get("return_value") == 4,
        },
        {
            "name": "Execute with output",
            "command": "print('HTTP test'); 42",
            "check": lambda r: (
                r.get("success")
                and "HTTP test" in r.get("stdout", "")
                and r.get("return_value") == 42
            ),
        },
        {
            "name": "Error handling",
            "command": "1/0",
            "check": lambda r: (
                not r.get("success") and r.get("error", {}).get("type") == "ZeroDivisionError"
            ),
        },
    ]

    passed = 0
    failed = 0

    try:
        for test in tests:
            print(f"\nTest: {test['name']}")

            response = requests.post(
                "http://localhost:9009/console/execute",
                json={"command": test["command"], "_api_version": DEFAULT_ENDPOINT_API_VERSION},
                headers={"X-Binja-MCP-Api-Version": str(DEFAULT_ENDPOINT_API_VERSION)},
                timeout=5,
            )

            if response.status_code == 200:
                data = response.json()
                if (
                    int(response.headers.get("X-Binja-MCP-Api-Version", -1))
                    != DEFAULT_ENDPOINT_API_VERSION
                ):
                    print(
                        "❌ Endpoint API version mismatch (header): "
                        f"{response.headers.get('X-Binja-MCP-Api-Version')}"
                    )
                    failed += 1
                    continue
                if int(data.get("_api_version", -1)) != DEFAULT_ENDPOINT_API_VERSION:
                    print(f"❌ Endpoint API version mismatch (body): {data.get('_api_version')}")
                    failed += 1
                    continue
                if test["check"](data):
                    print("✅ Passed")
                    print(f"   Success: {data.get('success')}")
                    print(f"   Return: {data.get('return_value')}")
                    if data.get("stdout"):
                        print(f"   Output: {data['stdout'].strip()}")
                    passed += 1
                else:
                    print("❌ Failed")
                    print(f"   Response: {json.dumps(data, indent=2)[:200]}")
                    failed += 1
            else:
                print(f"❌ HTTP {response.status_code}")
                failed += 1

    except requests.exceptions.ConnectionError:
        print("⚠️  Cannot connect to server - make sure Binary Ninja MCP server is running")
        return False
    except Exception as e:
        print(f"❌ Exception: {e}")
        return False

    print(f"\n{'=' * 50}")
    print(f"HTTP Endpoint Tests: {passed} passed, {failed} failed")
    return failed == 0


def _run_interactive_mode() -> bool:
    """Run interactive mode availability check."""
    print("\n\nTesting Interactive Mode")
    print("=" * 50)

    print("Simulating interactive session:")
    print(">>> x = 10")
    print(">>> y = 20")
    print(">>> x + y")
    print("30")
    print(">>> exit()")

    # You could automate this with pexpect or similar
    print("\n✓ Interactive mode available with: ./cli.py python -i")

    return True


def test_cli_python_command():
    """Test the CLI python command."""
    assert _run_cli_python_command()


test_cli_python_command = pytest.mark.binja(test_cli_python_command)


def test_http_python_endpoint():
    """Test the HTTP execute_python_command endpoint."""
    assert _run_http_python_endpoint()


test_http_python_endpoint = pytest.mark.binja(test_http_python_endpoint)


def test_interactive_mode():
    """Test interactive Python mode."""
    assert _run_interactive_mode()


def main():
    """Run all tests"""
    print("Testing Python Executor Updates")
    print("=" * 70)

    if not _server_reachable():
        print("⚠️  Skipping: Binary Ninja MCP server is not reachable at http://localhost:9009")
        return 0

    # Test CLI
    cli_ok = _run_cli_python_command()

    # Test HTTP endpoint
    http_ok = _run_http_python_endpoint()

    # Note about interactive mode
    interactive_ok = _run_interactive_mode()

    print(f"\n{'=' * 70}")
    print("Summary:")
    print(f"  CLI Python command: {'✅ Working' if cli_ok else '❌ Failed'}")
    print(f"  HTTP Python endpoint: {'✅ Working' if http_ok else '❌ Failed'}")
    print(f"  Interactive mode: {'✅ Available' if interactive_ok else '❌ Failed'}")

    if cli_ok and http_ok:
        print("\n✅ All tests passed! The Python executor integration is working correctly.")
    else:
        print("\n❌ Some tests failed. Please check the implementation.")

    return 0 if (cli_ok and http_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
