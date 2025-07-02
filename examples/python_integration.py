#!/usr/bin/env python3
"""
Example showing how to use the enhanced Python execution in Binary Ninja MCP
Demonstrates integration patterns and result processing
"""

import json
import requests
from typing import Any, Dict, List


class BinaryNinjaMCP:
    """Client for Binary Ninja MCP with Python execution"""
    
    def __init__(self, base_url: str = "http://localhost:9009"):
        self.base_url = base_url
        self.session = requests.Session()
    
    def execute_python(self, code: str) -> Dict[str, Any]:
        """Execute Python code in Binary Ninja context"""
        response = self.session.post(
            f"{self.base_url}/console/execute",
            json={"command": code},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    
    def get_functions(self) -> List[Dict[str, Any]]:
        """Get all functions using Python execution"""
        result = self.execute_python("""
# Get all functions with details
functions = []
for func in bv.functions:
    functions.append({
        'name': func.name,
        'address': hex(func.start),
        'size': func.total_bytes,
        'blocks': len(list(func.basic_blocks))
    })
_result = functions
""")
        
        if result['success'] and result.get('return_value'):
            return result['return_value']
        return []
    
    def analyze_strings(self, min_length: int = 10) -> List[Dict[str, Any]]:
        """Analyze strings in the binary"""
        code = f"""
# Find interesting strings
strings = []
for s in bv.strings:
    if len(s.value) >= {min_length}:
        strings.append({{
            'value': s.value,
            'address': hex(s.start),
            'length': len(s.value),
            'type': str(s.type)
        }})

# Sort by length
strings.sort(key=lambda x: x['length'], reverse=True)
_result = strings[:20]  # Top 20 longest strings
"""
        result = self.execute_python(code)
        
        if result['success'] and result.get('return_value'):
            return result['return_value']
        return []
    
    def find_crypto_functions(self) -> List[str]:
        """Find potential cryptographic functions"""
        code = """
# Common crypto keywords
crypto_keywords = ['crypt', 'aes', 'des', 'rsa', 'sha', 'md5', 'hash', 
                  'cipher', 'encrypt', 'decrypt', 'key', 'random']

crypto_funcs = []
for func in bv.functions:
    name_lower = func.name.lower()
    if any(keyword in name_lower for keyword in crypto_keywords):
        crypto_funcs.append(func.name)

# Also check imports
for sym in bv.symbols:
    if sym.type == bn.SymbolType.ImportedFunctionSymbol:
        name_lower = sym.name.lower()
        if any(keyword in name_lower for keyword in crypto_keywords):
            crypto_funcs.append(f"[Import] {sym.name}")

_result = sorted(set(crypto_funcs))
"""
        result = self.execute_python(code)
        
        if result['success'] and result.get('return_value'):
            return result['return_value']
        return []
    
    def get_call_graph(self, function_name: str) -> Dict[str, Any]:
        """Get call graph for a function"""
        code = f"""
# Get function by name
target_func = None
for func in bv.functions:
    if func.name == '{function_name}':
        target_func = func
        break

if not target_func:
    _result = {{'error': 'Function not found'}}
else:
    # Get callers and callees
    callers = []
    for ref in bv.get_code_refs(target_func.start):
        calling_func = ref.function
        if calling_func:
            callers.append(calling_func.name)
    
    callees = []
    for bb in target_func.basic_blocks:
        for i in bb:
            if i[0][0].name in ['CALL', 'CALLK']:
                # Try to resolve call target
                try:
                    target = i[0][1]
                    if isinstance(target, int):
                        called_func = bv.get_function_at(target)
                        if called_func:
                            callees.append(called_func.name)
                except:
                    pass
    
    _result = {{
        'function': target_func.name,
        'address': hex(target_func.start),
        'callers': sorted(set(callers)),
        'callees': sorted(set(callees)),
        'complexity': len(list(target_func.basic_blocks))
    }}
"""
        result = self.execute_python(code)
        
        if result['success'] and result.get('return_value'):
            return result['return_value']
        return {'error': 'Execution failed'}
    
    def create_analysis_report(self) -> str:
        """Create a comprehensive analysis report"""
        code = """
import json
from datetime import datetime

# Gather analysis data
report = {
    'timestamp': datetime.now().isoformat(),
    'binary': {
        'filename': bv.file.filename if bv.file else 'Unknown',
        'type': bv.view_type,
        'arch': str(bv.arch) if bv.arch else 'Unknown',
        'platform': str(bv.platform) if bv.platform else 'Unknown',
        'size': len(bv),
        'entry_point': hex(bv.entry_point) if bv.entry_point else None
    },
    'analysis': {
        'functions': len(list(bv.functions)),
        'basic_blocks': sum(len(list(f.basic_blocks)) for f in bv.functions),
        'imports': len([s for s in bv.symbols if s.type == bn.SymbolType.ImportedFunctionSymbol]),
        'exports': len([s for s in bv.symbols if s.type == bn.SymbolType.FunctionSymbol]),
        'strings': len(list(bv.strings)),
        'data_vars': len(list(bv.data_vars))
    }
}

# Find interesting functions
interesting = {
    'entry_function': bv.entry_function.name if bv.entry_function else None,
    'largest_function': None,
    'most_complex_function': None,
    'most_called_function': None
}

# Find largest and most complex
if list(bv.functions):
    largest = max(bv.functions, key=lambda f: f.total_bytes)
    interesting['largest_function'] = {
        'name': largest.name,
        'size': largest.total_bytes
    }
    
    most_complex = max(bv.functions, key=lambda f: len(list(f.basic_blocks)))
    interesting['most_complex_function'] = {
        'name': most_complex.name,
        'blocks': len(list(most_complex.basic_blocks))
    }

report['interesting'] = interesting

# Format as markdown
md = f"\"\"\"
# Binary Analysis Report

Generated: {report['timestamp']}

## Binary Information
- **File**: {report['binary']['filename']}
- **Type**: {report['binary']['type']}
- **Architecture**: {report['binary']['arch']}
- **Platform**: {report['binary']['platform']}
- **Size**: {report['binary']['size']:,} bytes
- **Entry Point**: {report['binary']['entry_point']}

## Analysis Summary
- **Functions**: {report['analysis']['functions']:,}
- **Basic Blocks**: {report['analysis']['basic_blocks']:,}
- **Imports**: {report['analysis']['imports']:,}
- **Exports**: {report['analysis']['exports']:,}
- **Strings**: {report['analysis']['strings']:,}
- **Data Variables**: {report['analysis']['data_vars']:,}

## Interesting Functions
- **Entry**: {interesting['entry_function']}
- **Largest**: {interesting['largest_function']['name'] if interesting['largest_function'] else 'N/A'} ({interesting['largest_function']['size'] if interesting['largest_function'] else 0} bytes)
- **Most Complex**: {interesting['most_complex_function']['name'] if interesting['most_complex_function'] else 'N/A'} ({interesting['most_complex_function']['blocks'] if interesting['most_complex_function'] else 0} blocks)
\"\"\"

_result = md
"""
        result = self.execute_python(code)
        
        if result['success'] and result.get('return_value'):
            return result['return_value']
        return "Error generating report"


def main():
    """Example usage of the Binary Ninja MCP Python integration"""
    
    # Create client
    client = BinaryNinjaMCP()
    
    print("Binary Ninja MCP Python Integration Example")
    print("=" * 50)
    
    # Test basic execution
    print("\n1. Testing basic Python execution:")
    result = client.execute_python("2 + 2")
    print(f"   Result: {result.get('return_value')} (took {result.get('execution_time', 0):.3f}s)")
    
    # Check if binary is loaded
    print("\n2. Checking binary status:")
    result = client.execute_python("bv is not None")
    if not result.get('return_value'):
        print("   Error: No binary loaded in Binary Ninja")
        return
    
    print("   Binary is loaded!")
    
    # Get functions
    print("\n3. Analyzing functions:")
    functions = client.get_functions()
    print(f"   Found {len(functions)} functions")
    if functions:
        # Show top 5 by size
        functions.sort(key=lambda f: f['size'], reverse=True)
        print("   Top 5 largest functions:")
        for func in functions[:5]:
            print(f"     - {func['name']} at {func['address']} ({func['size']} bytes, {func['blocks']} blocks)")
    
    # Analyze strings
    print("\n4. Analyzing strings:")
    strings = client.analyze_strings(min_length=20)
    print(f"   Found {len(strings)} interesting strings")
    for s in strings[:3]:
        print(f"     - '{s['value'][:50]}...' at {s['address']}")
    
    # Find crypto functions
    print("\n5. Looking for cryptographic functions:")
    crypto_funcs = client.find_crypto_functions()
    if crypto_funcs:
        print(f"   Found {len(crypto_funcs)} potential crypto functions:")
        for func in crypto_funcs[:5]:
            print(f"     - {func}")
    else:
        print("   No crypto functions found")
    
    # Get call graph for a function
    if functions:
        target = functions[0]['name']
        print(f"\n6. Analyzing call graph for '{target}':")
        call_graph = client.get_call_graph(target)
        if 'error' not in call_graph:
            print(f"   - Called by: {len(call_graph['callers'])} functions")
            print(f"   - Calls: {len(call_graph['callees'])} functions")
            print(f"   - Complexity: {call_graph['complexity']} basic blocks")
    
    # Generate report
    print("\n7. Generating analysis report:")
    report = client.create_analysis_report()
    print(report)
    
    # Demonstrate error handling
    print("\n8. Error handling example:")
    result = client.execute_python("undefined_variable")
    if not result['success']:
        error = result.get('error', {})
        print(f"   Expected error: {error.get('type')}: {error.get('message')}")
    
    # Show execution context
    print("\n9. Execution context:")
    result = client.execute_python("""
import sys
{
    'python_version': sys.version.split()[0],
    'binaryninja_version': bn.__version__ if hasattr(bn, '__version__') else 'Unknown',
    'available_modules': [m for m in sys.modules.keys() if 'binaryninja' in m][:5]
}
""")
    if result['success']:
        context = result['return_value']
        print(f"   Python: {context.get('python_version')}")
        print(f"   Binary Ninja: {context.get('binaryninja_version')}")
        print(f"   Modules: {', '.join(context.get('available_modules', []))}")


if __name__ == "__main__":
    main()