#!/usr/bin/env python3
"""
Test script for Binary Ninja MCP log capture functionality
Run this from Binary Ninja's Python console to test log capture
"""

import sys


def main() -> int:
    try:
        import binaryninja as bn
    except ModuleNotFoundError:
        print("SKIP: binaryninja module is not available in this Python environment.")
        return 0

    # Test log capture
    print("[TEST] Testing Binary Ninja MCP log capture...")

    # Generate some test logs
    bn.log_info("[TEST] This is an info message")
    bn.log_debug("[TEST] This is a debug message")
    bn.log_warn("[TEST] This is a warning message")
    bn.log_error("[TEST] This is an error message")

    print("[TEST] Log messages generated. Check MCP server endpoints:")
    print("  - http://localhost:9009/logs")
    print("  - http://localhost:9009/logs/errors")
    print("  - http://localhost:9009/logs/warnings")
    print("  - http://localhost:9009/logs/stats")

    # Test with curl:
    print("\n[TEST] Test with curl:")
    print('  curl "http://localhost:9009/logs?count=10"')
    print('  curl "http://localhost:9009/logs/stats"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
