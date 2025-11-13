# Testing Documentation

This directory contains the comprehensive test suite for the POD Design Tools Flask application.

## Table of Contents

- [Overview](#overview)
- [Test Structure](#test-structure)
- [Running Tests](#running-tests)
- [Writing Tests](#writing-tests)
- [Coverage](#coverage)
- [CI/CD Integration](#cicd-integration)

## Overview

The test suite uses **pytest** as the testing framework, with additional plugins for Flask integration, mocking, and coverage reporting. Tests are organized into two main categories:

- **Unit Tests**: Fast, isolated tests for individual components with all dependencies mocked
- **Integration Tests**: Tests that verify multiple components work together correctly

### Testing Stack

- **pytest** - Core testing framework
- **pytest-flask** - Flask testing utilities
- **pytest-cov** - Coverage reporting
- **pytest-mock** - Enhanced mocking capabilities
- **respx** - Mock HTTP requests (for httpx-based clients)
- **pytest-env** - Environment variable management

## Test Structure

```
tests/
├── conftest.py                    # Shared fixtures and test configuration
├── pytest.ini                     # Pytest configuration (in project root)
├── .coveragerc                    # Coverage configuration (in project root)
├── README.md                      # This file
├── fixtures/
│   ├── sample_designs/            # Test design images
│   ├── mockup_templates/          # Sample mockup templates
│   └── api_responses/             # JSON fixtures for API mocks
│       ├── printify_product.json
│       ├── shopify_product.json
│       └── openai_metadata.json
├── unit/
│   ├── test_json_store.py         # Storage layer tests
│   ├── test_printify_client.py    # Printify service tests
│   ├── test_shopify_client.py     # Shopify service tests
│   ├── test_openai_service.py     # OpenAI service tests
│   └── test_mockup_utils.py       # Mockup generation tests
└── integration/
    └── test_api_routes.py         # API route integration tests
```

## Running Tests

### Quick Start

```bash
# Run all tests
./test.sh

# Or use pytest directly
pytest
```

### Test Runner Script

The `test.sh` script provides convenient commands for running different test suites:

```bash
# Run all tests
./test.sh all

# Run only unit tests
./test.sh unit

# Run only integration tests
./test.sh integration

# Run tests with coverage report
./test.sh coverage

# Run unit tests, stop on first failure (fast feedback)
./test.sh fast

# Add verbose output
./test.sh unit -v
```

### Using Pytest Directly

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/unit/test_json_store.py

# Run specific test class
pytest tests/unit/test_json_store.py::TestJsonStore

# Run specific test method
pytest tests/unit/test_json_store.py::TestJsonStore::test_upsert_creates_new_item

# Run tests matching a pattern
pytest -k "shopify"

# Run only unit tests
pytest tests/unit/

# Run only integration tests
pytest tests/integration/

# Stop on first failure
pytest -x

# Show local variables in tracebacks
pytest -l

# Run tests in parallel (if pytest-xdist installed)
pytest -n auto
```

## Coverage

### Generating Coverage Reports

```bash
# Run tests with coverage
pytest --cov=app --cov-report=term --cov-report=html

# Or use the test script
./test.sh coverage
```

### Viewing Coverage Reports

After running tests with coverage, you can view the reports:

- **Terminal**: Displayed automatically after test run
- **HTML**: Open `htmlcov/index.html` in your browser
- **XML**: `coverage.xml` (useful for CI/CD)

### Coverage Goals

| Component | Target Coverage |
|-----------|----------------|
| Storage Layer (JsonStore) | 95%+ |
| Services (Clients) | 80%+ |
| Utilities | 80%+ |
| Routes | 70%+ |
| **Overall** | **75%+** |

## Writing Tests

### Unit Tests

Unit tests should be fast, isolated, and test a single component. All external dependencies should be mocked.

**Example:**

```python
import pytest
import respx
import httpx

@pytest.mark.unit
class TestMyService:
    @respx.mock
    def test_api_call(self, mock_env_vars):
        """Test API call with mocked HTTP response."""
        respx.get("https://api.example.com/data").mock(
            return_value=httpx.Response(200, json={"result": "success"})
        )

        result = my_service.fetch_data()

        assert result["result"] == "success"
```

### Integration Tests

Integration tests verify that multiple components work together correctly. External APIs should still be mocked, but internal components interact normally.

**Example:**

```python
import pytest

@pytest.mark.integration
class TestAPIRoutes:
    def test_create_product(self, client, mocker):
        """Test product creation endpoint."""
        mock_client = mocker.Mock()
        mock_client.create_product.return_value = {"id": "123"}

        mocker.patch('app.routes.api.printify_client', mock_client)

        response = client.post('/api/products', json={"title": "Test"})

        assert response.status_code == 200
        assert response.json["id"] == "123"
```

### Available Fixtures

The following fixtures are available in all tests (defined in `conftest.py`):

- `app` - Flask application instance
- `client` - Flask test client
- `runner` - Flask CLI test runner
- `json_store` - JsonStore instance with temp directory
- `printify_client` - PrintifyClient instance
- `shopify_client` - ShopifyClient instance
- `mock_env_vars` - Mock environment variables
- `sample_design_image` - Test design image (PNG)
- `sample_mockup_template` - Test mockup template
- `sample_printify_product` - Sample Printify product JSON
- `sample_shopify_product` - Sample Shopify product JSON
- `sample_openai_metadata` - Sample OpenAI metadata response

### Mocking External APIs

Use `respx` to mock HTTP requests made with `httpx`:

```python
import respx
import httpx

@respx.mock
def test_with_mocked_api():
    # Mock a specific URL
    respx.get("https://api.printify.com/v1/products").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    # Your test code here
```

### Test Markers

Tests can be marked with pytest markers:

```python
@pytest.mark.unit          # Unit test
@pytest.mark.integration   # Integration test
@pytest.mark.slow          # Slow running test
@pytest.mark.external      # Requires external services (skip in CI)
```

Run specific markers:
```bash
pytest -m unit              # Run only unit tests
pytest -m "not slow"        # Skip slow tests
```

## Best Practices

1. **Test Naming**: Use descriptive names that explain what is being tested
   - Good: `test_get_product_returns_404_when_not_found`
   - Bad: `test_get_product_2`

2. **Arrange-Act-Assert**: Structure tests clearly
   ```python
   def test_example():
       # Arrange - Set up test data and mocks
       mock_data = {"id": "123"}

       # Act - Execute the code being tested
       result = function_under_test(mock_data)

       # Assert - Verify the results
       assert result["id"] == "123"
   ```

3. **Test One Thing**: Each test should verify one specific behavior

4. **Use Fixtures**: Reuse common setup code via fixtures

5. **Mock External Dependencies**: Never make real API calls in tests

6. **Test Edge Cases**: Include tests for error conditions and boundary cases

7. **Keep Tests Fast**: Unit tests should run in milliseconds

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v3

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'

    - name: Install dependencies
      run: |
        pip install -r requirements.txt

    - name: Run tests with coverage
      run: |
        pytest --cov=app --cov-report=xml --cov-report=term

    - name: Upload coverage to Codecov
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
```

### Pre-commit Hook

Add to `.git/hooks/pre-commit`:

```bash
#!/bin/bash
echo "Running tests before commit..."
./test.sh fast
```

## Troubleshooting

### Tests are slow
- Run only unit tests: `./test.sh unit`
- Use `-x` flag to stop on first failure: `pytest -x`
- Check for tests hitting real APIs (should be mocked)

### Import errors
- Ensure you're in the project root directory
- Verify all dependencies are installed: `pip install -r requirements.txt`
- Check that `PYTHONPATH` includes the project root

### Mock not working
- Verify the import path in `mocker.patch()` matches where the object is used, not where it's defined
- Example: If `routes/api.py` imports `from app.services import printify_client`, patch `app.routes.api.printify_client`

### Coverage too low
- Run with `--cov-report=html` to see which lines aren't covered
- Focus on critical paths first (services, storage)
- Some defensive code doesn't need coverage

## Additional Resources

- [pytest Documentation](https://docs.pytest.org/)
- [pytest-flask Documentation](https://pytest-flask.readthedocs.io/)
- [respx Documentation](https://lundberg.github.io/respx/)
- [Coverage.py Documentation](https://coverage.readthedocs.io/)

## Questions?

If you encounter issues with the test suite:
1. Check this README first
2. Look at existing tests for examples
3. Review the test fixtures in `conftest.py`
4. Check pytest output for helpful error messages
