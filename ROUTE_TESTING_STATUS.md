# Route Testing Status

## Summary

We've successfully added integration tests for the core API routes. Here's the current testing status:

**Total Tests: 91 (all passing ✓)**
- Unit tests: 81
- Integration tests: 10

**Overall Coverage: 29.60%**
- This is low because many routes still need tests, but the tested components have excellent coverage!

## Routes Coverage Breakdown

### ✅ Well Tested Routes (>70%)

| Route File | Coverage | Status | Tests |
|------------|----------|--------|-------|
| **app/routes/api.py** | 76.92% | ✓ Excellent | 5 integration tests |
| **app/routes/pages.py** | 100% | ✓ Perfect | 3 integration tests |
| **app/routes/mockups.py** | 70.00% | ✓ Good | (tested via page routes) |
| **app/routes/ai.py** | 100% | ✓ Perfect | Blueprint only |

### ⚠️ Routes Needing Tests (<50%)

| Route File | Coverage | Lines | Priority | Complexity |
|------------|----------|-------|----------|------------|
| **app/routes/printify_api.py** | 5.28% | 568 | High | Complex |
| **app/routes/shopify_api.py** | 3.74% | 589 | High | Complex |
| **app/routes/printify.py** | 6.67% | 225 | Medium | Medium |
| **app/routes/shopify.py** | 25.00% | 92 | Medium | Medium |
| **app/routes/designs_api.py** | 22.30% | 139 | Medium | Medium |
| **app/routes/mockups_api.py** | 55.56% | 9 | Low | Simple |
| **app/routes/designs.py** | 41.67% | 12 | Low | Simple |

## Tested Routes

### app/routes/api.py (76.92% coverage)

**Covered endpoints:**
- ✓ GET `/api/products` - List cached products
- ✓ POST `/api/products/cache/update` - Fetch and cache Shopify products
- ✓ POST `/api/recommend/colors` - Get AI color recommendations

**Missing coverage:**
- Error handling edge cases (lines 154, 162-167, 180-188, 191, 200-203, 251-252)
- Some fallback logic for color extraction

**Tests:** `tests/integration/test_api_routes_real.py::TestAPIRoutes`

### app/routes/pages.py (100% coverage)

**Covered endpoints:**
- ✓ GET `/` - Homepage
- ✓ GET `/shopify/` - Shopify products page
- ✓ GET `/printify` - Printify products page

**Tests:** `tests/integration/test_api_routes_real.py::TestPageRoutes`

### Error Handling (tested)

**Covered scenarios:**
- ✓ 400 Bad Request (missing required parameters)
- ✓ 404 Not Found (non-existent endpoints)
- ✓ Invalid JSON handling

**Tests:** `tests/integration/test_api_routes_real.py::TestErrorHandling`

## Services (Excellent Coverage!)

All core services have excellent test coverage:

| Service | Coverage | Status |
|---------|----------|--------|
| **JsonStore** | 100% | ✓ Perfect |
| **Mockup Utils** | 100% | ✓ Perfect |
| **ShopifyClient** | 94.12% | ✓ Excellent |
| **OpenAI Service** | 93.85% | ✓ Excellent |
| **PrintifyClient** | 81.25% | ✓ Good |

## How to Run Tests

### Command Line

```bash
# Run all tests with coverage
./test.sh coverage

# Run just unit tests (fast)
./test.sh unit

# Run integration tests only
pytest tests/integration/test_api_routes_real.py -v

# Run specific test
pytest tests/integration/test_api_routes_real.py::TestAPIRoutes::test_get_products_empty -v
```

### PyCharm

**Option 1: Run with coverage**
1. Right-click `tests/unit` in Project view
2. Select "Run 'pytest in unit' with Coverage"

**Option 2: Use run configuration**
1. Select "Run Tests With Coverage" from dropdown
2. Click Run (or press Ctrl+R)

**View coverage:**
1. Run → Show Coverage Data
2. Click + button
3. Select `coverage.xml`
4. Coverage indicators appear in editor gutters

## Next Steps to Improve Coverage

To increase overall coverage, we should add integration tests for these routes in priority order:

### 1. High Priority - Large Complex Routes

**app/routes/printify_api.py (568 lines, 5% coverage)**
- Main product management API
- Needs tests for:
  - Product CRUD operations
  - Image uploads
  - Mockup generation
  - Publishing workflow

**app/routes/shopify_api.py (589 lines, 4% coverage)**
- Main Shopify management API
- Needs tests for:
  - Product editing
  - Image management
  - Metadata updates
  - Bulk operations

### 2. Medium Priority - Page Routes

**app/routes/printify.py (225 lines, 7% coverage)**
- Printify UI pages
- Mostly template rendering with complex data

**app/routes/shopify.py (92 lines, 25% coverage)**
- Shopify UI pages
- Some coverage from existing tests

**app/routes/designs_api.py (139 lines, 22% coverage)**
- Design management API
- File upload and metadata

### 3. Low Priority - Simple Routes

**app/routes/mockups_api.py (9 lines, 56% coverage)**
- Simple mockup endpoints
- Already mostly covered

**app/routes/designs.py (12 lines, 42% coverage)**
- Simple design pages
- Low complexity

## Writing New Route Tests

When adding tests for other routes, follow the pattern in `tests/integration/test_api_routes_real.py`:

```python
@pytest.mark.integration
class TestYourRoutes:
    """Tests for app/routes/your_routes.py"""

    def test_your_endpoint(self, client):
        """Test description."""
        # Mock external dependencies
        with patch('app.routes.your_routes.external_service') as mock_service:
            mock_service.method.return_value = {"data": "test"}

            # Make request via Flask test client
            response = client.get('/your/endpoint')

            # Assert response
            assert response.status_code == 200
            data = response.get_json()
            assert data["field"] == "expected_value"
```

**Key principles:**
1. Use Flask test client (`client` fixture) for requests
2. Mock external APIs (Printify, Shopify, OpenAI)
3. Don't mock internal code (routes, services, storage)
4. Test both success and error cases
5. Verify response status codes and data structure

## Files

- **Integration tests:** `tests/integration/test_api_routes_real.py`
- **Template tests (skip):** `tests/integration/test_api_routes.py`
- **Unit tests:** `tests/unit/`
- **Test runner:** `./test.sh`
- **Coverage config:** `.coveragerc`, `pytest.ini`
- **PyCharm configs:** `.idea/runConfigurations/`

## Coverage Goals

| Component | Current | Target | Status |
|-----------|---------|--------|--------|
| Core Services | ~90% | 80%+ | ✓ Exceeds |
| Core API Routes | 77% | 75%+ | ✓ Meets |
| Page Routes | 50% | 50%+ | ✓ Meets |
| **Overall** | **30%** | **60%+** | ⚠️ Needs work |

To reach 60% overall coverage, we need to add tests for the large API route files (printify_api.py, shopify_api.py).

## Questions?

See the full testing documentation:
- **Testing Guide:** `tests/README.md`
- **PyCharm Coverage:** `PYCHARM_COVERAGE_GUIDE.md`
- **Test Summary:** `TESTING_SUMMARY.md`
