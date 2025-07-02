# CLI Commands Ranked by Usefulness - Short Report

## Quick Ranking (Most → Least Useful)

1. **`python`** ⭐⭐⭐⭐⭐ - Swiss army knife, can do everything
2. **`decompile`** ⭐⭐⭐⭐⭐ - Core RE task, batch decompilation
3. **`functions`** ⭐⭐⭐⭐ - Foundation for analysis workflows  
4. **`refs`** ⭐⭐⭐⭐ - Trace code flow and dependencies
5. **`rename`** ⭐⭐⭐ - Organize analysis with meaningful names
6. **`assembly`** ⭐⭐⭐ - Low-level analysis when needed
7. **`comment`** ⭐⭐ - Document findings during analysis
8. **`type`** ⭐⭐ - Define structures (situational)
9. **`imports`** ⭐⭐ - Initial recon
10. **`exports`** ⭐⭐ - API surface analysis  
11. **`logs`** ⭐ - Debugging only
12. **`status`** ⭐ - Basic connectivity check

## Key Insights

- **80% of work** can be done with just `python`, `decompile`, and `functions`
- **`python` is king** - It provides access to the full Binary Ninja API and can replace all other commands
- **Batch operations** make CLI especially valuable (e.g., decompiling 100 functions)
- **JSON output** (`--json`) enables powerful integrations with other tools

## Most Valuable Use Cases

1. **Custom Analysis**: `python "find_functions('crypt')"` 
2. **Batch Decompilation**: `functions | xargs -I{} decompile {}`
3. **Data Extraction**: `python script.py > analysis.json`
4. **Quick Queries**: `functions --search vuln`
5. **Automation**: Chain commands with pipes and scripts

## Bottom Line

Focus on mastering `python` command first - it's the most powerful and flexible. Use specialized commands (`decompile`, `functions`) for quick tasks, but switch to Python scripts for anything complex.