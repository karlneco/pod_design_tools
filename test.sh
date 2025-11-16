#!/bin/bash
# Test runner script for POD Design Tools

set -e

# Clear Python bytecode cache to avoid PyCharm/IDE caching issues
export PYTHONDONTWRITEBYTECODE=1
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete 2>/dev/null || true

echo "=========================================="
echo "POD Design Tools - Test Runner"
echo "=========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Parse command line arguments
TEST_TYPE="${1:-all}"
VERBOSE="${2}"

run_tests() {
    local test_path="$1"
    local description="$2"

    echo -e "${YELLOW}Running $description...${NC}"

    if [ "$VERBOSE" = "-v" ] || [ "$VERBOSE" = "--verbose" ]; then
        pytest "$test_path" -v --tb=short
    else
        pytest "$test_path" --tb=short
    fi

    echo ""
}

run_with_coverage() {
    echo -e "${YELLOW}Running all tests with coverage...${NC}"
    pytest --cov=app --cov-report=term --cov-report=html --cov-report=xml
    echo ""
    echo -e "${GREEN}Coverage report generated:${NC}"
    echo "  - Terminal: (shown above)"
    echo "  - HTML: htmlcov/index.html"
    echo "  - XML: coverage.xml"
}

case "$TEST_TYPE" in
    unit)
        run_tests "tests/unit" "unit tests"
        ;;
    integration)
        echo -e "${YELLOW}Note: Integration tests are template stubs and will fail${NC}"
        echo -e "${YELLOW}They need to be updated to match your actual route implementations${NC}"
        echo ""
        run_tests "tests/integration" "integration tests"
        ;;
    coverage)
        echo -e "${YELLOW}Running unit and integration tests with coverage...${NC}"
        pytest tests/unit tests/integration/test_api_routes_real.py tests/integration/test_printify_routes_real.py tests/integration/test_shopify_api_routes_real.py --cov=app --cov-report=term --cov-report=html --cov-report=xml
        echo ""
        echo -e "${GREEN}Coverage report generated:${NC}"
        echo "  - Terminal: (shown above)"
        echo "  - HTML: htmlcov/index.html"
        echo "  - XML: coverage.xml"
        echo "  - PyCharm: Run → Show Coverage Data → + → Select coverage.xml"
        ;;
    fast)
        echo -e "${YELLOW}Running fast unit tests only...${NC}"
        pytest tests/unit -v --tb=short -x
        ;;
    all)
        echo "Running all unit tests..."
        echo -e "${YELLOW}(Skipping integration test templates)${NC}"
        echo ""
        run_tests "tests/unit" "unit tests"
        echo -e "${GREEN}All tests completed!${NC}"
        ;;
    *)
        echo -e "${RED}Unknown test type: $TEST_TYPE${NC}"
        echo ""
        echo "Usage: ./test.sh [type] [options]"
        echo ""
        echo "Test types:"
        echo "  all          - Run all tests (default)"
        echo "  unit         - Run unit tests only"
        echo "  integration  - Run integration tests only"
        echo "  coverage     - Run all tests with coverage report"
        echo "  fast         - Run unit tests, stop on first failure"
        echo ""
        echo "Options:"
        echo "  -v, --verbose  - Verbose output"
        echo ""
        echo "Examples:"
        echo "  ./test.sh                    # Run all tests"
        echo "  ./test.sh unit              # Run unit tests"
        echo "  ./test.sh coverage          # Run with coverage"
        echo "  ./test.sh unit -v           # Run unit tests (verbose)"
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}=========================================="
echo "Test run completed!"
echo "==========================================${NC}"
