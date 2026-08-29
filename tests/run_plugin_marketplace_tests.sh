#!/bin/bash

# Plugin Marketplace Integration Test Runner (ADR-0249)
#
# Usage:
#   ./run_plugin_marketplace_tests.sh [options]
#
# Options:
#   --all              Run all tests (default)
#   --marketplace      Run marketplace discovery tests only
#   --installation     Run installation workflow tests only
#   --governance       Run governance and trust tests only
#   --ui               Run Playwright E2E tests only
#   --coverage         Generate coverage report
#   --parallel         Run tests in parallel (requires pytest-xdist)
#   --headed           Run Playwright tests in headed mode
#   --debug            Run with verbose output and pdb on failures
#   --help             Show this help message

set -e

REPO_ROOT="/home/shumway/projects/CorvinOS"
TESTS_DIR="${REPO_ROOT}/tests"
CONSOLE_DIR="${REPO_ROOT}/core/console/corvin_console/web-next"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default options
TEST_MODE="all"
COVERAGE=false
PARALLEL=false
VERBOSE=false
HEADED=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --all)
            TEST_MODE="all"
            shift
            ;;
        --marketplace)
            TEST_MODE="marketplace"
            shift
            ;;
        --installation)
            TEST_MODE="installation"
            shift
            ;;
        --governance)
            TEST_MODE="governance"
            shift
            ;;
        --ui)
            TEST_MODE="ui"
            shift
            ;;
        --coverage)
            COVERAGE=true
            shift
            ;;
        --parallel)
            PARALLEL=true
            shift
            ;;
        --headed)
            HEADED=true
            shift
            ;;
        --debug)
            VERBOSE=true
            shift
            ;;
        --help)
            grep "^#" "$0" | grep -v "^#!/bin/bash"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Helper functions
print_header() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}ℹ $1${NC}"
}

# Check dependencies
check_dependencies() {
    print_header "Checking Dependencies"

    # Check Python
    if ! command -v python &> /dev/null; then
        print_error "Python not found"
        exit 1
    fi
    print_success "Python $(python --version 2>&1 | awk '{print $2}')"

    # Check pytest
    if ! python -m pytest --version &> /dev/null; then
        print_error "pytest not found. Install with: pip install pytest pytest-asyncio pytest-cov"
        exit 1
    fi
    print_success "pytest $(python -m pytest --version 2>&1 | awk '{print $2}')"

    # Check for xdist if parallel mode requested
    if [ "$PARALLEL" = true ]; then
        if ! python -c "import xdist" 2>/dev/null; then
            print_error "pytest-xdist not found. Install with: pip install pytest-xdist"
            exit 1
        fi
        print_success "pytest-xdist installed"
    fi

    echo ""
}

# Run pytest tests
run_pytest_tests() {
    local test_file=$1
    local test_name=$2

    print_header "Running: $test_name"

    local pytest_args=(
        "$test_file"
        "-v"
        "--tb=short"
    )

    # Add verbose flag
    if [ "$VERBOSE" = true ]; then
        pytest_args+=("-vv" "--tb=long" "-s")
    fi

    # Add coverage
    if [ "$COVERAGE" = true ]; then
        pytest_args+=("--cov=core/plugins" "--cov-append")
    fi

    # Add parallel execution
    if [ "$PARALLEL" = true ]; then
        pytest_args+=("-n" "auto")
    fi

    # Run pytest
    if python -m pytest "${pytest_args[@]}"; then
        print_success "$test_name passed"
        return 0
    else
        print_error "$test_name failed"
        return 1
    fi
}

# Run Playwright tests
run_playwright_tests() {
    print_header "Running: Playwright E2E Tests"

    # Check if npm is available
    if ! command -v npm &> /dev/null; then
        print_error "npm not found. Skipping Playwright tests."
        print_info "Install Node.js to run UI tests"
        return 0
    fi

    # Check if Playwright is installed
    if [ ! -d "${CONSOLE_DIR}/node_modules/@playwright" ]; then
        print_info "Installing Playwright dependencies..."
        cd "${CONSOLE_DIR}"
        npm install -D @playwright/test
        npx playwright install chromium
        cd - > /dev/null
    fi

    # Build playwright args
    local pw_args=(
        "${CONSOLE_DIR}/tests/e2e/plugins-integration.spec.ts"
    )

    if [ "$HEADED" = true ]; then
        pw_args+=(--headed)
    fi

    if [ "$VERBOSE" = true ]; then
        pw_args+=(--debug)
    fi

    # Run tests
    if npm run test:e2e -- "${pw_args[@]}" 2>/dev/null; then
        print_success "Playwright E2E tests passed"
        return 0
    else
        print_error "Playwright E2E tests failed"
        return 1
    fi
}

# Initialize coverage
init_coverage() {
    if [ "$COVERAGE" = true ]; then
        print_info "Initializing coverage report..."
        python -m pytest --cov=core/plugins --cov-report=term-missing:skip-covered > /dev/null 2>&1 || true
    fi
}

# Generate coverage report
generate_coverage_report() {
    if [ "$COVERAGE" = true ]; then
        print_header "Coverage Report"
        python -m pytest --cov=core/plugins --cov-report=term-missing:skip-covered --cov-report=html
        print_success "Coverage report generated in htmlcov/index.html"
    fi
}

# Main execution
main() {
    cd "${REPO_ROOT}"

    print_header "Plugin Marketplace Integration Test Suite (ADR-0249)"

    check_dependencies
    init_coverage

    FAILED_TESTS=()
    PASSED_TESTS=()

    case "$TEST_MODE" in
        all)
            print_info "Running ALL test suites..."

            if run_pytest_tests "${TESTS_DIR}/test_marketplace_integration_e2e.py" "Marketplace Discovery & Integration"; then
                PASSED_TESTS+=("Marketplace Integration")
            else
                FAILED_TESTS+=("Marketplace Integration")
            fi

            if run_pytest_tests "${TESTS_DIR}/test_plugin_installation_workflows.py" "Plugin Installation Workflows"; then
                PASSED_TESTS+=("Installation Workflows")
            else
                FAILED_TESTS+=("Installation Workflows")
            fi

            if run_pytest_tests "${TESTS_DIR}/test_plugin_governance_and_trust.py" "Plugin Governance & Trust"; then
                PASSED_TESTS+=("Governance & Trust")
            else
                FAILED_TESTS+=("Governance & Trust")
            fi

            if run_playwright_tests; then
                PASSED_TESTS+=("Playwright E2E")
            else
                FAILED_TESTS+=("Playwright E2E")
            fi
            ;;

        marketplace)
            if run_pytest_tests "${TESTS_DIR}/test_marketplace_integration_e2e.py" "Marketplace Discovery"; then
                PASSED_TESTS+=("Marketplace")
            else
                FAILED_TESTS+=("Marketplace")
            fi
            ;;

        installation)
            if run_pytest_tests "${TESTS_DIR}/test_plugin_installation_workflows.py" "Installation Workflows"; then
                PASSED_TESTS+=("Installation")
            else
                FAILED_TESTS+=("Installation")
            fi
            ;;

        governance)
            if run_pytest_tests "${TESTS_DIR}/test_plugin_governance_and_trust.py" "Governance & Trust"; then
                PASSED_TESTS+=("Governance")
            else
                FAILED_TESTS+=("Governance")
            fi
            ;;

        ui)
            if run_playwright_tests; then
                PASSED_TESTS+=("Playwright E2E")
            else
                FAILED_TESTS+=("Playwright E2E")
            fi
            ;;
    esac

    # Generate coverage if requested
    if [ "$COVERAGE" = true ]; then
        generate_coverage_report
    fi

    # Print summary
    print_header "Test Summary"

    if [ ${#PASSED_TESTS[@]} -gt 0 ]; then
        echo -e "${GREEN}Passed:${NC}"
        for test in "${PASSED_TESTS[@]}"; do
            echo "  ✓ $test"
        done
    fi

    if [ ${#FAILED_TESTS[@]} -gt 0 ]; then
        echo ""
        echo -e "${RED}Failed:${NC}"
        for test in "${FAILED_TESTS[@]}"; do
            echo "  ✗ $test"
        done
        echo ""
        exit 1
    else
        echo -e "${GREEN}All tests passed!${NC}"
        echo ""
    fi
}

# Run main function
main
