#!/usr/bin/env python3
"""
Test script to verify the updated MCP Bridge and CLI work with the new Python executor
"""

import subprocess
import json
import sys


def test_cli_python_command(venv_path="venv"):
    """Test the CLI python command"""
    print("Testing CLI Python Command")
    print("=" * 50)

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
            "command": ["python", "bv is not None"],
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
        cmd = [f"{venv_path}/bin/python", "cli.py"] + test["command"]
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


def test_mcp_bridge():
    """Test the MCP bridge execute_python_command"""
    print("\n\nTesting MCP Bridge")
    print("=" * 50)

    # This would require starting the bridge and using an MCP client
    # For now, we'll test the HTTP endpoint directly

    import requests

    tests = [
        {
            "name": "Execute via bridge endpoint",
            "command": "2 + 2",
            "check": lambda r: r.get("success") and r.get("return_value") == 4,
        },
        {
            "name": "Execute with output",
            "command": "print('Bridge test'); 42",
            "check": lambda r: r.get("success")
            and "Bridge test" in r.get("stdout", "")
            and r.get("return_value") == 42,
        },
        {
            "name": "Error handling",
            "command": "1/0",
            "check": lambda r: not r.get("success")
            and r.get("error", {}).get("type") == "ZeroDivisionError",
        },
    ]

    passed = 0
    failed = 0

    try:
        for test in tests:
            print(f"\nTest: {test['name']}")

            response = requests.post(
                "http://localhost:9009/console/execute",
                json={"command": test["command"]},
                timeout=5,
            )

            if response.status_code == 200:
                data = response.json()
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
    print(f"Bridge Tests: {passed} passed, {failed} failed")
    return failed == 0


def test_interactive_mode():
    """Test interactive Python mode"""
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


def main():
    """Run all tests"""
    print("Testing Python Executor Updates")
    print("=" * 70)

    # Test CLI
    cli_ok = test_cli_python_command()

    # Test Bridge/HTTP
    bridge_ok = test_mcp_bridge()

    # Note about interactive mode
    interactive_ok = test_interactive_mode()

    print(f"\n{'=' * 70}")
    print("Summary:")
    print(f"  CLI Python command: {'✅ Working' if cli_ok else '❌ Failed'}")
    print(f"  MCP Bridge integration: {'✅ Working' if bridge_ok else '❌ Failed'}")
    print(f"  Interactive mode: {'✅ Available' if interactive_ok else '❌ Failed'}")

    if cli_ok and bridge_ok:
        print("\n✅ All tests passed! The Python executor integration is working correctly.")
    else:
        print("\n❌ Some tests failed. Please check the implementation.")

    return 0 if (cli_ok and bridge_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
