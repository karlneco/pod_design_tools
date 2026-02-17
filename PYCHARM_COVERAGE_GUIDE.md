# PyCharm Coverage Guide

## Quick Start

### Option 1: Run with Coverage in PyCharm (Recommended)

1. **Right-click** `tests/unit/` in Project view
2. Select **"Run 'pytest in unit' with Coverage"**
3. Coverage shows automatically inline in editor

**Keyboard shortcut:**
- Mac: `Ctrl+Cmd+R` → Select "with Coverage"
- Windows/Linux: `Ctrl+Shift+F10` → Select "with Coverage"

### Option 2: Import Coverage from Terminal

1. Run in terminal: `./test.sh coverage`
2. In PyCharm: **Run → Show Coverage Data**
3. Click **+** button
4. Select `coverage.xml` from project root
5. Click **OK**

## Understanding Coverage Display

### In Editor (Inline)

| Color | Meaning |
|-------|---------|
| **Green stripe** | Line is covered by tests ✓ |
| **Red stripe** | Line is NOT covered ✗ |
| **Yellow stripe** | Partially covered (only one branch) |
| **No stripe** | Non-executable line (comments, etc.) |

### In Coverage Tool Window

**Bottom panel → Coverage tab:**
- Lists all files/packages
- Shows coverage % for each
- Click to jump to uncovered lines
- Sort by name or coverage %

### In Project View

Files and folders show coverage % badges:
- `app/storage/json_store.py` **100%** ✓
- `app/services/shopify_client.py` **94%** ✓
- `app/routes/api.py` **34%** ⚠️

## Keyboard Shortcuts

| Action | Mac | Windows/Linux |
|--------|-----|---------------|
| Run with Coverage | `Ctrl+Cmd+R` → Coverage | `Ctrl+Shift+F10` → Coverage |
| Show Coverage Data | - | - |
| Toggle Highlighting | `Cmd+Alt+F6` | `Ctrl+Alt+F6` |
| Navigate to Next Uncovered | `F2` | `F2` |
| Generate Report | - | - |

## Useful Features

### 1. Filter to Show Only Uncovered

In Coverage tool window:
- Click the funnel icon
- Select "Show only files with problems"
- Focuses on files needing more tests

### 2. Navigate to Uncovered Lines

- Press `F2` to jump to next uncovered line
- Helps you quickly find what needs testing

### 3. Generate HTML Report

1. **Run → Generate Coverage Report**
2. Select HTML format
3. Opens detailed report in browser
4. Shows branch coverage, complexity, etc.

### 4. Compare Coverage Runs

Track coverage changes over time:
1. Save multiple coverage.xml files (rename them)
2. **Run → Show Coverage Data**
3. Load different reports to compare

### 5. Set Coverage Targets

**Settings → Build, Execution, Deployment → Coverage:**
- Set minimum coverage % threshold
- PyCharm warns if coverage drops below target
- Useful for teams/CI requirements

## Coverage Configuration

### Current Setup

Your project is configured to:
- Test source: `app/` directory
- Exclude: `tests/`, `venv/`, `__pycache__/`
- Output: `coverage.xml`, `htmlcov/`, terminal

Configuration files:
- **pytest.ini** - Pytest coverage settings
- **.coveragerc** - Coverage.py settings

### Adjusting Coverage

To change what's measured, edit `.coveragerc`:

```ini
[run]
source = app
omit =
    */tests/*
    */venv/*
    */__pycache__/*
    app/__init__.py  # Add files to skip

[report]
precision = 2
show_missing = True
```

## Troubleshooting

### "No coverage data available"

**Solution 1: Run tests with coverage**
- Use "Run with Coverage" instead of regular "Run"
- Or generate coverage.xml first: `./test.sh coverage`

**Solution 2: Check coverage.xml exists**
```bash
ls -la coverage.xml
```
If missing, run: `./test.sh coverage`

**Solution 3: Reimport coverage**
- **Run → Show Coverage Data**
- Remove old entries (select and press Delete)
- Click **+** and re-import `coverage.xml`

### Coverage not showing inline

**Solution 1: Enable highlighting**
- **Run → Show Coverage Data**
- Check "Show coverage in editor" is enabled
- Press `Cmd+Alt+F6` (Mac) or `Ctrl+Alt+F6` to toggle

**Solution 2: PyCharm setting**
- **Settings → Editor → Code Editing → Coverage**
- Enable "Show coverage in editor gutter"

### Coverage numbers don't match terminal

**Possible causes:**
1. PyCharm using different test scope
   - Ensure running same tests (tests/unit/ not tests/)
2. Stale coverage.xml
   - Delete coverage.xml and regenerate
3. Different source exclusions
   - Check .coveragerc matches pytest.ini

**Solution:**
```bash
# Clear everything and regenerate
rm -rf .coverage coverage.xml htmlcov/
./test.sh coverage
# Then reimport in PyCharm
```

### "Module 'app' not found" in coverage

**Solution:**
Verify Python interpreter:
- **Settings → Project → Python Interpreter**
- Should be: `Python 3.12.2 (.venv)`
- If not, select the correct .venv interpreter

## Best Practices

### 1. Run Coverage Regularly

Before committing code:
```bash
./test.sh coverage
```

Check coverage didn't drop significantly.

### 2. Focus on Critical Code

100% coverage isn't always necessary:
- **Prioritize:** Business logic, services, utilities
- **Lower priority:** Routes, UI, one-liners
- **Skip:** Simple getters/setters, obvious code

### 3. Use Coverage to Find Missing Tests

Red lines in PyCharm show:
- Edge cases you forgot
- Error handling not tested
- Code paths not exercised

### 4. Don't Game the Metrics

Writing tests just to hit 100% coverage is counterproductive:
- ✗ Testing trivial getters/setters
- ✗ Tests that don't assert anything meaningful
- ✓ Tests that verify actual behavior
- ✓ Tests that would catch real bugs

### 5. Integration with Development

**Good workflow:**
1. Write feature code in PyCharm
2. Run relevant tests with coverage (right-click test)
3. Check new code is green (covered)
4. If red, write additional tests
5. Before commit: `./test.sh coverage` for full check

## Coverage Goals by Component

Your current coverage (excellent!):

| Component | Coverage | Target | Status |
|-----------|----------|--------|--------|
| JsonStore | 100% | 95%+ | ✓ Exceeds |
| ShopifyClient | 94% | 80%+ | ✓ Exceeds |
| PrintifyClient | 81% | 80%+ | ✓ Meets |
| OpenAI Service | 94% | 80%+ | ✓ Exceeds |
| Mockup Utils | 100% | 80%+ | ✓ Exceeds |
| **Overall** | **~85%** | **75%+** | **✓ Exceeds** |

Routes have lower coverage (~30-40%) but that's expected - they're integration code with external dependencies.

## PyCharm Pro Features

If you have PyCharm Professional:

### Branch Coverage
- Shows if both true/false branches of conditions are tested
- More detailed than line coverage
- Enable in Settings → Coverage → Show branch coverage

### Coverage per Test
- See which specific tests cover each line
- Right-click line → Show Tests Covering This Line
- Helps identify redundant tests

### Continuous Testing
- **Run → Toggle auto-test**
- Tests run automatically as you type
- Coverage updates in real-time

## Exporting Coverage

### For CI/CD

Generate coverage.xml for CI tools:
```bash
pytest tests/unit --cov=app --cov-report=xml
```

Most CI platforms (GitHub Actions, GitLab CI, etc.) can consume coverage.xml.

### For Team Review

Generate HTML report:
```bash
./test.sh coverage
open htmlcov/index.html  # Mac
xdg-open htmlcov/index.html  # Linux
```

Share htmlcov/ folder with team for detailed review.

### For Badges

Services like Codecov or Coveralls can generate coverage badges from coverage.xml:

[![Coverage Status](https://img.shields.io/badge/coverage-85%25-brightgreen)]()

## Additional Resources

- **PyCharm Docs**: https://www.jetbrains.com/help/pycharm/code-coverage.html
- **Coverage.py Docs**: https://coverage.readthedocs.io/
- **Project Test README**: `tests/README.md`
- **Test Summary**: `TESTING_SUMMARY.md`
