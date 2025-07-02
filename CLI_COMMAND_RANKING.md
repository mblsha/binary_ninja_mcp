# Binary Ninja MCP CLI Commands Ranked by Usefulness

## Ranking Criteria
- **Frequency of use** in typical RE workflows
- **Time saved** vs manual operations
- **Automation potential** for batch processing
- **Unique capabilities** not easily available elsewhere

## Command Rankings

### 🥇 Tier 1: Essential (Daily Use)

#### 1. **`python`** - Execute Python code
**Why #1:** Most versatile command. Enables custom analysis, automation, and access to full Binary Ninja API.
```bash
./cli.py python "find_functions('decrypt')"
./cli.py python script.py
```
**Use cases:** Custom analysis, batch processing, data extraction, automation scripts

#### 2. **`decompile`** - Decompile functions
**Why #2:** Core RE task. CLI enables batch decompilation and integration with external tools.
```bash
./cli.py decompile main
./cli.py --json decompile sub_401000 | jq -r .decompiled
```
**Use cases:** Code analysis, documentation generation, diff comparisons

#### 3. **`functions`** - List functions
**Why #3:** Foundation for many workflows. Quick overview and filtering capabilities.
```bash
./cli.py functions --search crypt
./cli.py --json functions | jq -r '.functions[]' | grep -v sub_
```
**Use cases:** Function enumeration, finding interesting code, statistics

### 🥈 Tier 2: Very Useful (Weekly Use)

#### 4. **`refs`** - Find code references
**Why #4:** Critical for understanding code flow and dependencies.
```bash
./cli.py refs malloc
./cli.py refs --to 0x401234
```
**Use cases:** Tracing function usage, finding callers, data flow analysis

#### 5. **`rename`** - Rename functions/data
**Why #5:** Essential for organizing analysis. Batch renaming saves significant time.
```bash
./cli.py rename function sub_401000 parse_config
for i in {1..10}; do ./cli.py rename function "sub_$i" "handler_$i"; done
```
**Use cases:** Code organization, meaningful names, collaborative RE

#### 6. **`assembly`** - Get disassembly
**Why #6:** Low-level analysis when decompilation isn't enough.
```bash
./cli.py assembly main
./cli.py --json assembly crypto_func | jq -r .assembly
```
**Use cases:** Optimization analysis, compiler artifacts, obfuscation

### 🥉 Tier 3: Useful (Occasional Use)

#### 7. **`comment`** - Manage comments
**Why #7:** Documentation during analysis. Less frequent but important for complex RE.
```bash
./cli.py comment set 0x401234 "Vulnerability here"
./cli.py comment set-function main "Entry point, parses argv"
```
**Use cases:** Analysis notes, vulnerability marking, team collaboration

#### 8. **`type`** - Define/get types
**Why #8:** Important for complex structures but less frequently needed.
```bash
./cli.py type define "struct config { int version; char name[32]; };"
./cli.py type get config_t
```
**Use cases:** Structure recovery, protocol analysis, API definitions

#### 9. **`imports`/`exports`** - List symbols
**Why #9-10:** Useful for initial analysis but typically done once per binary.
```bash
./cli.py imports | grep -i crypto
./cli.py exports
```
**Use cases:** Dependency analysis, API surface identification

### 📊 Tier 4: Situational (Rare Use)

#### 11. **`logs`** - View Binary Ninja logs
**Why #11:** Primarily for debugging issues, not core RE work.
```bash
./cli.py logs --errors
./cli.py logs --search "failed"
```
**Use cases:** Troubleshooting, plugin debugging

#### 12. **`status`** - Check server status
**Why #12:** Basic connectivity check, minimal functionality.
```bash
./cli.py status
```
**Use cases:** Verification, scripting prerequisites

## Usage Patterns

### Most Common Workflows

1. **Quick Analysis**
   ```bash
   ./cli.py functions | grep -i interest
   ./cli.py decompile interesting_func
   ./cli.py refs interesting_func
   ```

2. **Batch Processing**
   ```bash
   ./cli.py python "for f in find_functions('handler'): print(f.name)"
   ./cli.py --json functions | jq -r '.functions[]' | xargs -I{} ./cli.py decompile {}
   ```

3. **Documentation**
   ```bash
   ./cli.py python "for f in bv.functions: print(f'{f.name},{f.start:#x},{f.total_bytes}')" > functions.csv
   ```

## Recommendations

1. **Start with `python`** - It can do everything other commands do and more
2. **Use `--json` flag** for automation and tool integration
3. **Combine commands** with Unix pipes for powerful workflows
4. **Write Python scripts** for complex analysis instead of shell loops

## Summary

The `python` command stands out as the most powerful and flexible tool, essentially providing a scriptable interface to all Binary Ninja functionality. The traditional RE commands (`decompile`, `functions`, `refs`) form the core toolkit for daily analysis. The remaining commands serve specific purposes but are used less frequently in typical workflows.