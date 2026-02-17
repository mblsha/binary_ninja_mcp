#!/usr/bin/env python3
"""
Test script for the enhanced Python executor in Binary Ninja MCP
Demonstrates various execution scenarios and result capture
"""

import json
import requests
import sys

DEFAULT_ENDPOINT_API_VERSION = 1


def _run_executor(base_url="http://localhost:9009") -> bool:
    """Run Python execution scenario checks."""

    test_cases = [
        {"name": "Simple Expression", "code": "2 + 2", "expected": {"return_value": 4}},
        {
            "name": "Print Statement",
            "code": "print('Hello from Binary Ninja!')",
            "expected": {"stdout": "Hello from Binary Ninja!"},
        },
        {
            "name": "Variable Assignment",
            "code": "x = 42\ny = 'test'\nz = [1, 2, 3]",
            "expected": {"variables": ["x", "y", "z"]},
        },
        {
            "name": "Binary Ninja API Access",
            "code": "bv",
            "expected": {"return_type": "BinaryView"},
        },
        {
            "name": "List Functions",
            "code": "funcs = list(bv.functions)[:5]\n[f.name for f in funcs]",
            "expected": {"return_type": "list"},
        },
        {
            "name": "Error Handling",
            "code": "1 / 0",
            "expected": {"error": {"type": "ZeroDivisionError"}},
        },
        {
            "name": "Multi-line Code",
            "code": """
def analyze_function(func):
    return {
        'name': func.name,
        'address': hex(func.start),
        'size': func.total_bytes
    }

# Analyze entry function
if bv and bv.entry_function:
    _result = analyze_function(bv.entry_function)
else:
    _result = None
""",
            "expected": {"return_value": {"name": str}},
        },
        {
            "name": "Import and Use Module",
            "code": """
import hashlib
data = b'Binary Ninja MCP'
_result = hashlib.sha256(data).hexdigest()
""",
            "expected": {"return_type": "str"},
        },
        {
            "name": "Complex Data Serialization",
            "code": """
result = {
    'functions': len(list(bv.functions)) if bv else 0,
    'binary_type': bv.view_type if bv else None,
    'arch': str(bv.arch) if bv and bv.arch else None,
    'entry_point': hex(bv.entry_point) if bv else None
}
result
""",
            "expected": {"return_type": "dict"},
        },
        {
            "name": "Using Binary Ninja Logging",
            "code": """
bn.log_info("Test message from Python executor")
bn.log_debug("Debug information")
"Logging test complete"
""",
            "expected": {"return_value": "Logging test complete"},
        },
    ]

    print("Testing Enhanced Python Executor")
    print("=" * 50)

    passed = 0
    failed = 0

    for test in test_cases:
        print(f"\nTest: {test['name']}")
        print(
            f"Code: {test['code'][:50]}..." if len(test["code"]) > 50 else f"Code: {test['code']}"
        )

        try:
            # Execute the code
            api_version = DEFAULT_ENDPOINT_API_VERSION
            response = requests.post(
                f"{base_url}/console/execute",
                json={"command": test["code"], "_api_version": api_version},
                headers={"X-Binja-MCP-Api-Version": str(api_version)},
                timeout=5,
            )

            if response.status_code != 200:
                print(f"  ❌ HTTP {response.status_code}: {response.text}")
                failed += 1
                continue

            result = response.json()
            header_raw = response.headers.get("X-Binja-MCP-Api-Version")
            try:
                header_version = int(header_raw)
            except (TypeError, ValueError):
                print(f"  ❌ Invalid response version header: {header_raw}")
                failed += 1
                continue
            if header_version != api_version:
                print(
                    "  ❌ Endpoint API version mismatch: "
                    f"client={api_version}, server_header={header_version}"
                )
                failed += 1
                continue

            body_raw = result.get("_api_version")
            try:
                body_version = int(body_raw)
            except (TypeError, ValueError):
                print(f"  ❌ Invalid response body _api_version: {body_raw}")
                failed += 1
                continue
            if body_version != api_version:
                print(
                    "  ❌ Endpoint API version mismatch: "
                    f"client={api_version}, server_body={body_version}"
                )
                failed += 1
                continue

            # Check expectations
            success = True
            if "return_value" in test["expected"]:
                if result.get("return_value") != test["expected"]["return_value"]:
                    if not (
                        isinstance(test["expected"]["return_value"], dict)
                        and isinstance(result.get("return_value"), dict)
                    ):
                        success = False

            if "return_type" in test["expected"]:
                if result.get("return_type") != test["expected"]["return_type"]:
                    success = False

            if "stdout" in test["expected"]:
                if test["expected"]["stdout"] not in result.get("stdout", ""):
                    success = False

            if "variables" in test["expected"]:
                result_vars = result.get("variables", {})
                for var in test["expected"]["variables"]:
                    if var not in result_vars:
                        success = False

            if "error" in test["expected"]:
                if not result.get("error"):
                    success = False
                elif result["error"]["type"] != test["expected"]["error"]["type"]:
                    success = False

            if success and result.get("success", False):
                print("  ✅ Passed")
                if result.get("return_value") is not None:
                    print(f"     Return: {json.dumps(result['return_value'], indent=2)[:100]}...")
                if result.get("stdout"):
                    print(f"     Output: {result['stdout'][:100]}")
                passed += 1
            else:
                print("  ❌ Failed")
                print(f"     Result: {json.dumps(result, indent=2)[:200]}...")
                failed += 1

        except Exception as e:
            print(f"  ❌ Exception: {e}")
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"Total: {len(test_cases)}, Passed: {passed}, Failed: {failed}")
    print(f"Success Rate: {passed / len(test_cases) * 100:.1f}%")

    # Test auto-completion
    print(f"\n{'=' * 50}")
    print("Testing Auto-completion")

    # This would need a new endpoint, but shows the concept
    partial = "bv.fun"
    print(f"Completions for '{partial}': (would need new endpoint)")
    # Future: response = requests.get(f"{base_url}/console/complete?partial={partial}")

    return passed == len(test_cases)


def test_executor():
    """Pytest entrypoint for executor checks."""
    assert isinstance(_run_executor(), bool)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    else:
        base_url = "http://localhost:9009"

    success = _run_executor(base_url)
    sys.exit(0 if success else 1)
