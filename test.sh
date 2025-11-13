#!/bin/bash
# Test runner script for POD Design Tools

set -e

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
        run_tests "tests/integration" "integration tests"
        ;;
    coverage)
        run_with_coverage
        ;;
    fast)
        echo -e "${YELLOW}Running fast unit tests only...${NC}"
        pytest tests/unit -v --tb=short -x
        ;;
    all)
        echo "Running all tests..."
        echo ""
        run_tests "tests/unit" "unit tests"
        run_tests "tests/integration" "integration tests"
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
