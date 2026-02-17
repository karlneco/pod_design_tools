# PyCharm Testing Guide

This guide helps you run tests successfully in PyCharm without cache issues.

## The Problem

PyCharm's terminal can cache Python modules, causing tests to use old code even after you've updated files. This is especially common with test files using decorators and mocking.

## Solutions

### Quick Fix (Try First)

**In PyCharm's Terminal:**
```bash
./test.sh fast
```

The test script now automatically clears Python bytecode caches before running.

### If Tests Still Fail in PyCharm Terminal

#### Option 1: Invalidate PyCharm Caches
1. Go to `File → Invalidate Caches...`
2. Check "Clear file system cache and Local History"
3. Click "Invalidate and Restart"

#### Option 2: Use PyCharm's Built-in Test Runner
Instead of using the terminal, use PyCharm's test runner:

1. Right-click on `tests/` folder in Project view
2. Select "Run 'pytest in tests'"

Or use the pre-configured run configurations:
- **Run → Run Unit Tests** (runs tests/unit/)
- **Run → Run All Tests** (runs all tests)

#### Option 3: Manual Cache Clear
```bash
# In PyCharm terminal
export PYTHONDONTWRITEBYTECODE=1
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
./test.sh fast
```

#### Option 4: Use External Terminal
Open a regular terminal (outside PyCharm):
```bash
cd /Users/karl/Documents/dev/pod_design_tools
source .venv/bin/activate
./test.sh fast
```

## PyCharm Test Runner Configuration

### Using the GUI

1. Open **Run → Edit Configurations...**
2. Click **+** → **Python tests → pytest**
3. Configure:
   - **Name**: Unit Tests
   - **Target**: Custom
   - **Working directory**: `$PROJECT_DIR$`
   - **Path**: `$PROJECT_DIR$/tests/unit`
   - **Additional Arguments**: `-v`
4. Click **OK**

### Pre-configured Run Configurations

Two run configurations have been added:
- **Run Unit Tests** - Runs only unit tests (fast)
- **Run All Tests** - Runs all tests including integration

Access them via:
- Top toolbar dropdown menu
- **Run → Run...** → Select configuration

## Environment Setup in PyCharm

Ensure PyCharm is using the correct Python interpreter:

1. **File → Settings (or Preferences on Mac)**
2. **Project: pod_design_tools → Python Interpreter**
3. Verify it shows: `Python 3.12.2 (.venv)`
4. If not, click gear icon → Add Interpreter → Existing → Select `.venv/bin/python`

## Debugging Tests in PyCharm

### Debug a Single Test
1. Open test file (e.g., `tests/unit/test_json_store.py`)
2. Click the green arrow next to a test function
3. Select "Debug 'pytest for test_...'"

### Debug with Breakpoints
1. Set breakpoints by clicking in the gutter (left of line numbers)
2. Right-click on test file or folder
3. Select "Debug 'pytest in...'"
4. PyCharm will pause at breakpoints

## Common PyCharm Issues

### Issue: "ModuleNotFoundError: No module named 'app'"
**Solution**:
- Right-click on project root in Project view
- **Mark Directory as → Sources Root**
- Or add to pytest.ini: `pythonpath = .` (already done)

### Issue: Tests pass in terminal but fail in PyCharm
**Solution**:
1. Check Python interpreter (see Environment Setup above)
2. Invalidate caches (File → Invalidate Caches...)
3. Ensure pytest is installed in correct venv: `which pytest` should show `.venv/bin/pytest`

### Issue: "respx not mocking" errors
**Solution**:
1. Clear all caches: `./test.sh fast` (now does this automatically)
2. Restart PyCharm
3. If still failing, use external terminal

### Issue: Tests are slow to start in PyCharm
**Solution**:
- PyCharm indexes files on startup
- Wait for indexing to complete (bottom right status bar)
- Disable unnecessary inspections: **File → Settings → Editor → Inspections**

## Recommended PyCharm Workflow

### Development Cycle
1. **Write code** in PyCharm editor
2. **Run tests** using one of these methods:
   - Quick: `./test.sh fast` in terminal
   - GUI: Right-click test → Run
   - Keyboard: `Ctrl+Shift+F10` (or `Cmd+Shift+R` on Mac)
3. **Debug failing tests** using PyCharm debugger
4. **Check coverage** by running `./test.sh coverage` in terminal

### Best Practices
- ✅ Use PyCharm for editing and debugging
- ✅ Use terminal for quick test runs (`./test.sh fast`)
- ✅ Use PyCharm test runner for debugging specific tests
- ✅ Run full coverage before committing (`./test.sh coverage`)
- ✅ Close unused test tabs to free memory

## Keyboard Shortcuts (Mac)

| Action | Shortcut |
|--------|----------|
| Run tests at cursor | `Ctrl+Shift+R` |
| Debug tests at cursor | `Ctrl+Shift+D` |
| Re-run last test | `Ctrl+R` |
| Run with coverage | `Ctrl+Shift+R` then select "Run with Coverage" |
| Jump to test | `Cmd+Shift+T` |

## Keyboard Shortcuts (Windows/Linux)

| Action | Shortcut |
|--------|----------|
| Run tests at cursor | `Ctrl+Shift+F10` |
| Debug tests at cursor | `Shift+F9` |
| Re-run last test | `Shift+F10` |
| Jump to test | `Ctrl+Shift+T` |

## Additional Resources

- **PyCharm Testing Docs**: https://www.jetbrains.com/help/pycharm/pytest.html
- **Project Test README**: `tests/README.md`
- **Test Summary**: `TESTING_SUMMARY.md`

## Still Having Issues?

Try this complete reset:
```bash
# Close PyCharm completely

# In terminal, from project root:
rm -rf .pytest_cache __pycache__ tests/__pycache__ tests/unit/__pycache__
rm -rf app/__pycache__ app/**/__pycache__
find . -name "*.pyc" -delete

# Restart PyCharm
# File → Invalidate Caches → Invalidate and Restart

# After restart:
./test.sh fast
```

If tests still fail, check that you're on the correct git branch and your virtual environment is activated.
