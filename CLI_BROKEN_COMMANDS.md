# CLI Commands That Don't Work or Need Major Improvements

## 🔴 Broken Commands

### 1. **`comment delete`** - Doesn't work at all
```bash
./cli.py comment delete --address 0x1000  # Fails
```
**Issue**: Uses wrong HTTP method (POST with _method=DELETE instead of actual DELETE)
**Impact**: Cannot delete comments via CLI

### 2. **`type`** - Barely useful
```bash
./cli.py type define "struct Test { int x; };"  # Returns only {'Test': 'struct'}
./cli.py type get Test                          # Missing full definition
```
**Issue**: Returns only type category, not the actual type definition
**Impact**: Can't see struct fields, making it useless for analysis

## 🟡 Commands That Need Major Improvements

### 3. **`functions`** - Ugly output
```bash
./cli.py functions
# Current: • {'name': 'main', 'address': '0x1000', 'raw_name': 'main'}
# Should be: • main @ 0x1000
```
**Issue**: Raw dict output instead of formatted text
**Impact**: Hard to read, requires parsing

### 4. **`imports`/`exports`** - Same formatting issue
```bash
./cli.py imports
# Shows raw Python objects instead of clean list
```

### 5. **`rename data`** - Silent failures
```bash
./cli.py rename data 0x1000 new_name
# Returns success even when it fails
```
**Issue**: No proper error messages, always returns exit code 0

## ❌ Missing Essential Commands

These server endpoints exist but have no CLI commands:

1. **`functionAt`** - Get function at address
   ```bash
   # Should exist: ./cli.py function-at 0x401000
   ```

2. **`segments`** - List binary segments
   ```bash
   # Should exist: ./cli.py segments
   ```

3. **`data`** - List defined data
   ```bash
   # Should exist: ./cli.py data
   ```

4. **Variable operations** - Critical for RE
   ```bash
   # Should exist:
   ./cli.py rename-variable main old_var new_var
   ./cli.py retype-variable main var_name "int*"
   ./cli.py edit-signature main "int main(int argc, char** argv)"
   ```

## 📊 Impact Assessment

### High Impact Issues:
- **Variable operations missing** - Can't rename/retype variables via CLI
- **Comment deletion broken** - Can't remove incorrect comments
- **Poor formatting** - Makes automation harder

### Medium Impact:
- **Missing commands** - Segments, data listing unavailable
- **Type system** - Almost useless in current state

### Low Impact:
- **Error handling** - Inconvenient but workable

## 🛠️ Fixes Needed

1. **Immediate**: Fix comment deletion HTTP method
2. **High Priority**: Add variable operation commands
3. **Important**: Fix output formatting for all list commands
4. **Nice to Have**: Better error messages and exit codes

## Bottom Line

About **40% of the CLI functionality is broken or severely limited**. The core commands work (`python`, `decompile`, basic `functions`), but many features that would make the CLI truly useful for automation are missing or broken.