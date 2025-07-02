#!/usr/bin/env python3
"""
Test script for Binary Ninja MCP Python execution
Demonstrates file execution and complex string handling
"""

# Test basic functionality
print("=== Binary Ninja Python Execution Test ===")
print()

# Check if we have access to Binary Ninja
if 'bv' in globals() and bv:
    print(f"✓ Binary loaded: {bv.file.filename if bv.file else 'Unknown'}")
    print(f"  Architecture: {bv.arch}")
    print(f"  Functions: {len(list(bv.functions))}")
    print()
else:
    print("✗ No binary loaded")
    print()

# Test helper functions
print("Testing helper functions:")
if 'info' in globals():
    print("\n--- info() output ---")
    print(info())
    print("--- end info() ---\n")

# Test string with quotes and escapes
test_string = '''This is a complex string with:
- Single quotes: 'hello'
- Double quotes: "world"
- Escaped chars: \n\t\r
- Unicode: 🎉 ✨
- Multiline text
'''

print("Complex string test:")
print(test_string)

# Test Binary Ninja specific operations
if 'find_functions' in globals():
    print("\nSearching for 'main' functions:")
    main_funcs = find_functions('main')
    for f in main_funcs[:5]:  # Show first 5
        print(f"  - {f.name} at {hex(f.start)}")

# Test calculations
import math
result = sum(math.factorial(i) for i in range(10))
print(f"\nCalculation result: sum of factorials 0-9 = {result}")

# Test error handling
print("\nTesting error handling:")
try:
    x = 1 / 0
except ZeroDivisionError as e:
    print(f"  Caught expected error: {e}")

# Define a function
def analyze_binary():
    """Analyze the current binary"""
    if not bv:
        return "No binary loaded"
    
    analysis = {
        'total_functions': len(list(bv.functions)),
        'entry_point': hex(bv.entry_point) if bv.entry_point else None,
        'strings_count': len([s for s in bv.strings if len(s.value) > 10])
    }
    
    return analysis

# Use the function
print("\nBinary analysis:")
print(analyze_binary())

# Show that variables persist
persistent_var = "This variable should persist between executions"
print(f"\nCreated persistent variable: {persistent_var}")

print("\n=== Test completed successfully ===")