#!/bin/bash
# Demonstrate all Python CLI features

echo "=== Binary Ninja MCP Python CLI Demo ==="
echo

# Setup
cd "$(dirname "$0")/.."
source venv/bin/activate

echo "1. Inline code execution:"
./cli.py python "print('Hello from inline code!')"
echo

echo "2. Execute from file:"
./cli.py python examples/simple_test.py
echo

echo "3. Execute file (explicit flag):"
./cli.py python -f examples/simple_test.py
echo

echo "4. Pipe to stdin:"
echo "print('From pipe!')" | ./cli.py python
echo

echo "5. Stdin with --stdin flag:"
echo "print('With --stdin flag')" | ./cli.py python --stdin
echo

echo "6. Multi-line code from stdin:"
cat << 'EOF' | ./cli.py python
# Multi-line script
for i in range(3):
    print(f"Line {i+1}")
    
if bv:
    print(f"Binary has {len(list(bv.functions))} functions")
EOF
echo

echo "7. Code completion:"
echo "Completions for 'find_':"
./cli.py python -c "find_"
echo

echo "8. Complex strings (no escaping needed with files):"
cat << 'EOF' > /tmp/test_strings.py
# Test complex strings
print('''Complex string with:
- Single quotes: 'hello'
- Double quotes: "world"
- Backslashes: C:\path\to\file
- Unicode: 🎉 ✨ 🚀
''')
EOF
./cli.py python /tmp/test_strings.py
rm /tmp/test_strings.py
echo

echo "9. JSON output mode:"
./cli.py --json python "{'result': 2+2, 'binary': bv.file.filename if bv else None}" | python3 -m json.tool | head -10
echo

echo "10. Error handling:"
./cli.py python "1/0" 2>&1 | grep -A1 "Error:"
echo

echo "=== Demo Complete ==="