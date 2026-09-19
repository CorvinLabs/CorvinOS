#!/bin/bash

# E2E Test Orchestration Script
#
# Runs Playwright E2E tests with priority-based filtering.
# Supports P0–P3 priority levels (critical path) and full suite.
#
# Usage:
#   ./scripts/run-e2e-tests.sh [--p0] [--p1] [--p2] [--p3] [--all] [--fast] [--report]
#
# Examples:
#   ./scripts/run-e2e-tests.sh --p0          # Run P0 tests only (critical)
#   ./scripts/run-e2e-tests.sh --p0 --p1     # Run P0 + P1 tests
#   ./scripts/run-e2e-tests.sh --all         # Run all tests (P0-P7)
#   ./scripts/run-e2e-tests.sh --fast --p0   # Run P0 tests fast (skip retries)
#   ./scripts/run-e2e-tests.sh --report      # Generate HTML report

set -euo pipefail

# ─ Configuration ─────────────────────────────────────────────────────────────

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../" && pwd)"
PLAYWRIGHT_CONFIG="${PROJECT_DIR}/playwright.config.ts"
PLAYWRIGHT_TIMEOUT="${PLAYWRIGHT_TIMEOUT:-120000}"  # 2 minutes per test

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ─ Defaults ──────────────────────────────────────────────────────────────────

PRIORITIES=()
RUN_ALL=false
FAST_MODE=false
GENERATE_REPORT=false
BROWSERS="chromium,firefox"
WORKERS="${PLAYWRIGHT_WORKERS:-4}"
RETRIES="${PLAYWRIGHT_RETRIES:-2}"

# ─ Parse Arguments ───────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case $1 in
    --p0) PRIORITIES+=("P0"); shift ;;
    --p1) PRIORITIES+=("P1"); shift ;;
    --p2) PRIORITIES+=("P2"); shift ;;
    --p3) PRIORITIES+=("P3"); shift ;;
    --all) RUN_ALL=true; shift ;;
    --fast)
      FAST_MODE=true
      WORKERS=1
      RETRIES=0
      shift
      ;;
    --report) GENERATE_REPORT=true; shift ;;
    --browsers) BROWSERS="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

# ─ Determine Test Pattern ────────────────────────────────────────────────────

if [ "$RUN_ALL" = true ]; then
  TEST_PATTERN=""
  PRIORITY_NAME="all (P0–P7)"
elif [ ${#PRIORITIES[@]} -gt 0 ]; then
  # Build regex for priorities: "P0|P1|P2|P3" → matches "test-panels-p0", etc.
  PRIORITY_REGEX="$(IFS="|"; echo "${PRIORITIES[*]}")"
  TEST_PATTERN="test-panels-$(echo "$PRIORITY_REGEX" | tr '|' '-')"
  PRIORITY_NAME="${PRIORITIES[*]}"
else
  # Default: P0 tests only
  PRIORITIES=("P0")
  TEST_PATTERN="test-panels-p0"
  PRIORITY_NAME="P0 (default)"
fi

# ─ Print Configuration ───────────────────────────────────────────────────────

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║         E2E Test Orchestration — CorvinOS Console          ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Configuration:${NC}"
echo "  Priorities:      $PRIORITY_NAME"
echo "  Test Pattern:    ${TEST_PATTERN:-all tests}"
echo "  Browsers:        $BROWSERS"
echo "  Workers:         $WORKERS"
echo "  Retries:         $RETRIES"
echo "  Fast Mode:       $FAST_MODE"
echo "  Report:          $GENERATE_REPORT"
echo ""

# ─ Build Playwright Command ──────────────────────────────────────────────────

PLAYWRIGHT_CMD=(
  "npm run test:e2e"
  "--project=chromium"
  "--project=firefox"
  "--workers=$WORKERS"
  "--retries=$RETRIES"
  "--timeout=$PLAYWRIGHT_TIMEOUT"
)

# Add grep pattern if specified
if [ -n "$TEST_PATTERN" ]; then
  PLAYWRIGHT_CMD+=("--grep=$TEST_PATTERN")
fi

# Add reporter
if [ "$GENERATE_REPORT" = true ]; then
  PLAYWRIGHT_CMD+=("--reporter=html")
fi

# ─ Run Tests ─────────────────────────────────────────────────────────────────

echo -e "${BLUE}Starting test run...${NC}"
echo ""

cd "$PROJECT_DIR"

# Verify Playwright is installed
if ! npm list @playwright/test > /dev/null 2>&1; then
  echo -e "${RED}Error: @playwright/test is not installed${NC}"
  echo "Run: npm install"
  exit 1
fi

# Run Playwright
if PLAYWRIGHT_TIMEOUT=$PLAYWRIGHT_TIMEOUT npm run test:e2e -- \
  --grep="${TEST_PATTERN:-.*}" \
  --workers="$WORKERS" \
  --retries="$RETRIES" \
  ${GENERATE_REPORT:+--reporter=html}; then

  RESULT=$?
  echo ""
  echo -e "${GREEN}✓ Test run completed successfully${NC}"

  if [ "$GENERATE_REPORT" = true ]; then
    echo -e "${BLUE}HTML Report: file://${PROJECT_DIR}/playwright-report/index.html${NC}"
  fi

  exit 0
else
  RESULT=$?
  echo ""
  echo -e "${RED}✗ Test run failed with exit code $RESULT${NC}"

  if [ "$GENERATE_REPORT" = true ]; then
    echo -e "${YELLOW}Partial Report: file://${PROJECT_DIR}/playwright-report/index.html${NC}"
  fi

  exit $RESULT
fi
