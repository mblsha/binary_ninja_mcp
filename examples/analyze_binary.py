#!/usr/bin/env python3
"""
Example script showing how to use the Binary Ninja MCP CLI programmatically
This script performs basic analysis on a loaded binary
"""

import subprocess
import json
import sys


def run_cli(command: list) -> dict:
    """Run CLI command and return JSON output"""
    cmd = ["python3", "./cli.py", "--json"] + command
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error running command: {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"output": result.stdout}


def main():
    print("Binary Analysis Report")
    print("=" * 50)

    # Check status
    status = run_cli(["status"])
    if not status.get("loaded"):
        print("Error: No binary loaded in Binary Ninja")
        sys.exit(1)

    print(f"Analyzing: {status.get('filename', 'Unknown')}")
    print()

    # Get function count
    functions = run_cli(["functions", "--limit", "10000"])
    func_list = functions.get("functions", [])
    print(f"Total functions: {len(func_list)}")

    # Look for interesting functions
    print("\nInteresting functions:")
    keywords = ["main", "init", "auth", "crypt", "key", "password", "secret", "flag"]

    for keyword in keywords:
        results = run_cli(["functions", "--search", keyword])
        matches = results.get("matches", [])
        if matches:
            print(f"\n  {keyword}:")
            for func in matches[:5]:  # Show max 5 per keyword
                print(f"    • {func}")

    # Get imports
    print("\n\nImports:")
    imports = run_cli(["imports", "--limit", "1000"])
    import_list = imports.get("imports", [])

    # Categorize imports
    crypto_imports = [
        imp
        for imp in import_list
        if any(x in imp.lower() for x in ["crypt", "aes", "rsa", "hash", "md5", "sha"])
    ]
    network_imports = [
        imp
        for imp in import_list
        if any(x in imp.lower() for x in ["socket", "recv", "send", "connect", "bind"])
    ]
    file_imports = [
        imp
        for imp in import_list
        if any(x in imp.lower() for x in ["open", "read", "write", "file", "create"])
    ]

    if crypto_imports:
        print(f"\n  Cryptography ({len(crypto_imports)}):")
        for imp in crypto_imports[:10]:
            print(f"    • {imp}")

    if network_imports:
        print(f"\n  Networking ({len(network_imports)}):")
        for imp in network_imports[:10]:
            print(f"    • {imp}")

    if file_imports:
        print(f"\n  File I/O ({len(file_imports)}):")
        for imp in file_imports[:10]:
            print(f"    • {imp}")

    # Get exports
    print("\n\nExports:")
    exports = run_cli(["exports", "--limit", "100"])
    export_list = exports.get("exports", [])
    if export_list:
        for exp in export_list[:10]:
            print(f"  • {exp}")
    else:
        print("  No exports found")

    # Check logs for any errors during analysis
    print("\n\nRecent errors:")
    errors = run_cli(["logs", "--errors", "--count", "5"])
    error_list = errors.get("errors", [])
    if error_list:
        for err in error_list:
            print(f"  • {err.get('message', 'Unknown error')}")
    else:
        print("  No recent errors")

    print("\n" + "=" * 50)
    print("Analysis complete!")


if __name__ == "__main__":
    main()
