#!/bin/bash
# Start Full Stack for E2E Testing
#
# Usage:
#   ./start-e2e-stack.sh
#
# Starts:
#   - Console Mock Backend on :8765
#   - Frontend Dev Server on :5175
#   - Then runs E2E tests
#
# Prerequisites:
#   - Node.js 18+ (npm)
#   - Python 3.9+
#   - Playwright browsers installed

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "╔════════════════════════════════════════════════════════════╗"
echo "║   Starting Full Stack for E2E Testing                    ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to kill background processes on exit
cleanup() {
  echo ""
  echo "🛑 Shutting down..."
  kill $(jobs -p) 2>/dev/null || true
  exit 0
}
trap cleanup EXIT

# 1. Start Console Mock Backend
echo "🚀 1/3: Starting Console API Backend on :8765..."
cd "$PROJECT_ROOT/core/gateway" || exit 1

if [ ! -d "venv" ]; then
  echo "   Creating Python virtualenv..."
  python3 -m venv venv
fi

source venv/bin/activate
pip install -q fastapi uvicorn pydantic 2>/dev/null || true

echo "   Running: python3 -m corvin_gateway.console_api"
python3 -m corvin_gateway.console_api &
BACKEND_PID=$!

sleep 2

# Check if backend is running
if ! curl -s http://localhost:8765/v1/console/health > /dev/null 2>&1; then
  echo -e "${RED}❌ Backend failed to start${NC}"
  wait $BACKEND_PID || true
  exit 1
fi

echo -e "${GREEN}✅ Backend running (PID: $BACKEND_PID)${NC}"
echo ""

# 2. Start Frontend Dev Server
echo "🚀 2/3: Starting Frontend Dev Server on :5175..."
cd "$PROJECT_ROOT/core/console/corvin_console/web-next" || exit 1

echo "   Running: npm run dev"
npm run dev &
FRONTEND_PID=$!

sleep 5

# Check if frontend is running
if ! curl -s http://localhost:5175/console/ > /dev/null 2>&1; then
  echo -e "${RED}❌ Frontend failed to start${NC}"
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
  exit 1
fi

echo -e "${GREEN}✅ Frontend running (PID: $FRONTEND_PID)${NC}"
echo ""

# 3. Run E2E Tests
echo "🚀 3/3: Running E2E Tests..."
echo ""

sleep 3

npx playwright test tests/e2e/critical-flows/real-backend.spec.ts --reporter=html

# Capture test result
TEST_RESULT=$?

echo ""
if [ $TEST_RESULT -eq 0 ]; then
  echo -e "${GREEN}✅ E2E Tests PASSED${NC}"
else
  echo -e "${RED}❌ E2E Tests FAILED${NC}"
fi

echo ""
echo "📊 Reports:"
echo "   - Playwright HTML report: playwright-report/index.html"
echo ""

exit $TEST_RESULT
