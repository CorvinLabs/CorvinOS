#!/bin/bash
# Stream 2 Staging Deployment Readiness Check
# Run this before 2026-09-27 deployment to verify all prerequisites
# Usage: bash scripts/staging_deployment_readiness_check.sh

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0
WARN=0

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Stream 2 Deployment Readiness Check${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Helper functions
check_pass() {
    echo -e "${GREEN}✅ $1${NC}"
    ((PASS++))
}

check_fail() {
    echo -e "${RED}❌ $1${NC}"
    ((FAIL++))
}

check_warn() {
    echo -e "${YELLOW}⚠️  $1${NC}"
    ((WARN++))
}

# ==============================================================================
# 1. Codebase Checks
# ==============================================================================

echo -e "${BLUE}1. Codebase Checks${NC}"
echo "==================="

cd "$REPO_ROOT"

# Check Stream 2 directory exists
if [[ -d "core/skills/os_skills/security_orchestrator" ]]; then
    check_pass "Stream 2 directory found: core/skills/os_skills/security_orchestrator"
else
    check_fail "Stream 2 directory NOT found"
fi

# Check completion report
if [[ -f "core/skills/os_skills/security_orchestrator/STREAM_2_COMPLETION_REPORT.md" ]]; then
    check_pass "Stream 2 completion report found"
    STATUS=$(grep "^Status:" core/skills/os_skills/security_orchestrator/STREAM_2_COMPLETION_REPORT.md | head -1)
    check_pass "  $STATUS"
else
    check_fail "Stream 2 completion report NOT found"
fi

# Check test directories
if [[ -d "core/skills/os_skills/security_orchestrator/tests" ]]; then
    TEST_COUNT=$(find core/skills/os_skills/security_orchestrator/tests -name "test_*.py" | wc -l)
    if [[ $TEST_COUNT -gt 0 ]]; then
        check_pass "Found $TEST_COUNT Stream 2 test files"
    else
        check_fail "No Stream 2 test files found"
    fi
else
    check_fail "Stream 2 tests directory NOT found"
fi

# Check routes
if [[ -d "core/skills/os_skills/security_orchestrator/routes" ]]; then
    check_pass "Stream 2 routes directory found"
else
    check_fail "Stream 2 routes directory NOT found"
fi

echo ""

# ==============================================================================
# 2. Deployment Scripts
# ==============================================================================

echo -e "${BLUE}2. Deployment Scripts${NC}"
echo "======================"

# Check deployment script exists
if [[ -f "scripts/staging_deploy_stream2.sh" ]]; then
    if [[ -x "scripts/staging_deploy_stream2.sh" ]]; then
        check_pass "Deployment script exists and is executable"
    else
        check_fail "Deployment script exists but is NOT executable"
    fi
else
    check_fail "Deployment script NOT found: scripts/staging_deploy_stream2.sh"
fi

# Check load generator exists
if [[ -f "scripts/staging_load_generator.py" ]]; then
    if [[ -x "scripts/staging_load_generator.py" ]]; then
        check_pass "Load generator exists and is executable"
    else
        check_fail "Load generator exists but is NOT executable"
    fi
else
    check_fail "Load generator NOT found: scripts/staging_load_generator.py"
fi

# Check dashboard config
if [[ -f "dashboards/grafana_stream2_soak_test.json" ]]; then
    check_pass "Grafana dashboard configuration found"
else
    check_fail "Grafana dashboard configuration NOT found"
fi

echo ""

# ==============================================================================
# 3. Test Suite
# ==============================================================================

echo -e "${BLUE}3. Test Suite${NC}"
echo "=============="

# Check smoke tests
if [[ -f "tests/ops/test_staging_soak_smoke.py" ]]; then
    check_pass "Smoke tests file found"
    TEST_COUNT=$(grep -c "def test_" tests/ops/test_staging_soak_smoke.py || echo "0")
    check_pass "  Contains $TEST_COUNT smoke test cases"
else
    check_fail "Smoke tests file NOT found: tests/ops/test_staging_soak_smoke.py"
fi

echo ""

# ==============================================================================
# 4. Documentation
# ==============================================================================

echo -e "${BLUE}4. Documentation${NC}"
echo "=================="

# Check staging soak test plan
if [[ -f "STAGING_SOAK_TEST_PLAN.md" ]]; then
    check_pass "Staging soak test plan found"
else
    check_fail "Staging soak test plan NOT found"
fi

# Check quickstart
if [[ -f "STREAM_2_OPS_QUICKSTART.md" ]]; then
    check_pass "Stream 2 ops quickstart found"
else
    check_fail "Stream 2 ops quickstart NOT found"
fi

echo ""

# ==============================================================================
# 5. Infrastructure Prerequisites
# ==============================================================================

echo -e "${BLUE}5. Infrastructure Prerequisites${NC}"
echo "================================"

# Check Python 3
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    check_pass "Python 3 available: $PYTHON_VERSION"
else
    check_fail "Python 3 NOT found"
fi

# Check pytest
if python3 -m pytest --version &> /dev/null; then
    PYTEST_VERSION=$(python3 -m pytest --version | awk '{print $2}')
    check_pass "Pytest available: $PYTEST_VERSION"
else
    check_warn "Pytest NOT found (required for smoke tests)"
fi

# Check Docker
if command -v docker &> /dev/null; then
    DOCKER_VERSION=$(docker --version | awk '{print $3}' | sed 's/,//')
    check_pass "Docker available: $DOCKER_VERSION"
else
    check_warn "Docker NOT found (optional, can use local deployment)"
fi

# Check kubectl (if Kubernetes deployment)
if command -v kubectl &> /dev/null; then
    check_pass "Kubectl available (Kubernetes deployment ready)"
else
    check_warn "Kubectl NOT found (optional, can use Docker directly)"
fi

# Check Prometheus
if curl -s http://localhost:9090/-/healthy &> /dev/null; then
    check_pass "Prometheus running at localhost:9090"
else
    check_warn "Prometheus NOT responding at localhost:9090 (can start it now)"
fi

# Check Grafana
if curl -s http://localhost:3000 &> /dev/null; then
    check_pass "Grafana running at localhost:3000"
else
    check_warn "Grafana NOT responding at localhost:3000 (can start it now)"
fi

echo ""

# ==============================================================================
# 6. Storage & Directories
# ==============================================================================

echo -e "${BLUE}6. Storage & Directories${NC}"
echo "========================="

# Create staging directories
STAGING_DIR=".staging/stream2"
if [[ ! -d "$STAGING_DIR" ]]; then
    mkdir -p "$STAGING_DIR"/{audit,threats,policies,metrics}
    check_pass "Created staging directory: $STAGING_DIR"
else
    if [[ -d "$STAGING_DIR/audit" ]]; then
        check_pass "Staging directory exists: $STAGING_DIR"
    else
        mkdir -p "$STAGING_DIR"/{audit,threats,policies,metrics}
        check_pass "Completed staging directory structure"
    fi
fi

# Check disk space
DISK_AVAILABLE=$(df -BG "$STAGING_DIR" | tail -1 | awk '{print $4}' | sed 's/G//')
if [[ $DISK_AVAILABLE -gt 10 ]]; then
    check_pass "Sufficient disk space available: ${DISK_AVAILABLE}GB (need ≥10GB)"
else
    check_fail "Insufficient disk space: ${DISK_AVAILABLE}GB (need ≥10GB for 100K audit events)"
fi

echo ""

# ==============================================================================
# 7. Stream 2 Code Quality
# ==============================================================================

echo -e "${BLUE}7. Stream 2 Code Quality${NC}"
echo "========================"

# Check for Python syntax errors
echo "Checking Stream 2 Python files for syntax errors..."
if python3 -m py_compile core/skills/os_skills/security_orchestrator/*.py 2>&1 | grep -q "SyntaxError"; then
    check_fail "Syntax errors found in Stream 2 code"
else
    check_pass "No syntax errors in Stream 2 code"
fi

# Count lines of code
LOC=$(find core/skills/os_skills/security_orchestrator -name "*.py" -type f | xargs wc -l | tail -1 | awk '{print $1}')
check_pass "Stream 2 implementation: ~$LOC lines of code"

echo ""

# ==============================================================================
# 8. Configuration
# ==============================================================================

echo -e "${BLUE}8. Configuration${NC}"
echo "================="

# Check for required environment variables (optional)
if [[ -n "$SLACK_WEBHOOK_URL" ]]; then
    check_pass "Slack webhook configured (alerts enabled)"
else
    check_warn "Slack webhook NOT configured (alerts will be disabled)"
fi

if [[ -n "$CORVIN_TENANT_ID" ]]; then
    check_pass "Tenant ID configured: $CORVIN_TENANT_ID"
else
    check_warn "Tenant ID not set (will default to _default)"
fi

echo ""

# ==============================================================================
# 9. Final Readiness Summary
# ==============================================================================

echo "========================================="
echo -e "${BLUE}READINESS SUMMARY${NC}"
echo "========================================="
echo -e "✅ Passed: ${GREEN}$PASS${NC}"
echo -e "❌ Failed: ${RED}$FAIL${NC}"
echo -e "⚠️  Warnings: ${YELLOW}$WARN${NC}"
echo ""

if [[ $FAIL -eq 0 ]]; then
    echo -e "${GREEN}🟢 READY FOR DEPLOYMENT${NC}"
    echo ""
    echo "Next steps (2026-09-27):"
    echo "  1. bash scripts/staging_deploy_stream2.sh"
    echo "  2. pytest tests/ops/test_staging_soak_smoke.py -v"
    echo "  3. python3 scripts/staging_load_generator.py --duration=7d"
    echo "  4. Monitor: http://localhost:3000/d/stream2-soak-test"
    echo ""
    exit 0
else
    echo -e "${RED}🔴 NOT READY FOR DEPLOYMENT${NC}"
    echo ""
    echo "Fix the $FAIL failure(s) above before proceeding."
    echo ""
    exit 1
fi
