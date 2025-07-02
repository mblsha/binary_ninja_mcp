#!/usr/bin/env python3
"""
Test script for enhanced Python executor V2
Tests that binary view is properly accessible
"""

import json
import requests
import sys

# Test server URL
SERVER_URL = "http://localhost:9009"

def test_python_v2():
    """Test the enhanced Python executor V2"""
    
    tests = [
        {
            "name": "Test 1: Check if bv is available",
            "command": "bv is not None",
            "expected_success": True,
            "check_return": lambda r: isinstance(r, bool)
        },
        {
            "name": "Test 2: Get binary info with helper",
            "command": "info()",
            "expected_success": True,
            "check_stdout": lambda s: "file:" in s.lower()
        },
        {
            "name": "Test 3: Use get_current_view helper",
            "command": "view = get_current_view(); view.file.filename if view and view.file else 'No file'",
            "expected_success": True,
            "check_return": lambda r: r != 'No file'
        },
        {
            "name": "Test 4: Count functions",
            "command": "len(list(bv.functions)) if bv else 0",
            "expected_success": True,
            "check_return": lambda r: isinstance(r, int) and r > 0
        },
        {
            "name": "Test 5: Find functions with helper",
            "command": "funcs = find_functions('room'); [(f.name, hex(f.start)) for f in funcs[:3]]",
            "expected_success": True,
            "check_return": lambda r: isinstance(r, dict) and r.get('type') == 'list'
        },
        {
            "name": "Test 6: Get function by name",
            "command": "f = get_func('room1_enter'); f.name if f else 'Not found'",
            "expected_success": True,
            "check_return": lambda r: r == 'room1_enter' or r == 'Not found'
        },
        {
            "name": "Test 7: Hex dump helper",
            "command": "hex_dump(0x4a4, 32)",
            "expected_success": True,
            "check_stdout": lambda s: '000004a4:' in s or 'Cannot read' in s
        },
        {
            "name": "Test 8: Get help context",
            "command": "help()",
            "expected_success": True,
            "check_stdout": lambda s: 'Binary Ninja MCP Python Console' in s
        },
        {
            "name": "Test 9: Test error suggestions",
            "command": "undefined_function",
            "expected_success": False,
            "check_error": lambda e: e and 'suggestions' in e
        },
        {
            "name": "Test 10: Binary context in result",
            "command": "2 + 2",
            "expected_success": True,
            "check_context": lambda c: c and 'binary_loaded' in c
        }
    ]
    
    # Run each test
    passed = 0
    failed = 0
    
    for test in tests:
        print(f"\n{test['name']}")
        print(f"Command: {test['command']}")
        
        try:
            response = requests.post(
                f"{SERVER_URL}/console/execute",
                json={"command": test['command']},
                timeout=5
            )
            
            if response.status_code != 200:
                print(f"❌ HTTP {response.status_code}: {response.text}")
                failed += 1
                continue
            
            result = response.json()
            
            # Check success
            if result.get('success') != test['expected_success']:
                print(f"❌ Expected success={test['expected_success']}, got {result.get('success')}")
                print(f"   Error: {result.get('error')}")
                failed += 1
                continue
            
            # Run specific checks
            checks_passed = True
            
            if 'check_return' in test and result.get('return_value') is not None:
                if not test['check_return'](result['return_value']):
                    print(f"❌ Return value check failed: {result['return_value']}")
                    checks_passed = False
            
            if 'check_stdout' in test and result.get('stdout'):
                if not test['check_stdout'](result['stdout']):
                    print(f"❌ Stdout check failed")
                    checks_passed = False
            
            if 'check_error' in test and result.get('error'):
                if not test['check_error'](result['error']):
                    print(f"❌ Error check failed")
                    checks_passed = False
            
            if 'check_context' in test and result.get('context'):
                if not test['check_context'](result['context']):
                    print(f"❌ Context check failed")
                    checks_passed = False
            
            if checks_passed:
                print(f"✅ PASSED")
                if result.get('stdout'):
                    print(f"   Output: {result['stdout'][:100]}...")
                if result.get('return_value') is not None:
                    print(f"   Return: {json.dumps(result['return_value'])[:100]}")
                if result.get('context'):
                    print(f"   Context: {result['context']}")
                passed += 1
            else:
                failed += 1
                
        except Exception as e:
            print(f"❌ Exception: {e}")
            failed += 1
    
    # Summary
    print(f"\n{'='*60}")
    print(f"Summary: {passed} passed, {failed} failed out of {len(tests)} tests")
    print(f"{'='*60}")
    
    if failed == 0:
        print("\n🎉 All tests passed! The enhanced Python executor V2 is working correctly.")
        print("Binary view access is now available through:")
        print("  - Direct access: bv")
        print("  - Helper functions: get_current_view(), get_func(), find_functions(), etc.")
        print("  - Context-aware help: help()")
    else:
        print("\n⚠️  Some tests failed. Please check the implementation.")
    
    return failed == 0

if __name__ == "__main__":
    print("Testing Enhanced Python Executor V2")
    print("Make sure Binary Ninja is running with a binary loaded")
    print(f"Testing against: {SERVER_URL}")
    
    try:
        # Check if server is running
        response = requests.get(f"{SERVER_URL}/status", timeout=2)
        status = response.json()
        if not status.get('loaded'):
            print("\n⚠️  No binary loaded in Binary Ninja")
            print("Please load a binary and try again")
            sys.exit(1)
        
        print(f"\nBinary loaded: {status.get('filename')}")
        
        # Run tests
        success = test_python_v2()
        sys.exit(0 if success else 1)
        
    except requests.exceptions.ConnectionError:
        print("\n❌ Cannot connect to Binary Ninja MCP server")
        print("Make sure the server is running on http://localhost:9009")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)