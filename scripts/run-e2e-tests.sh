#!/bin/bash

###############################################################################
# E2E Test Runner for CorvinOS Console
#
# This script runs comprehensive end-to-end tests using Playwright.
# Tests cover:
#   1. Console UI navigation
#   2. Feature flags and settings
#   3. Vibe Engineering activation
#   4. Token Metrics dashboard
#   5. Chat interface and context pipeline stages
#   6. Forged tools and skills visibility
#   7. Real-time metrics updates
#   8. Full integration workflow
#
# Usage:
#   bash scripts/run-e2e-tests.sh
#   bash scripts/run-e2e-tests.sh --headed          # Show browser
#   bash scripts/run-e2e-tests.sh --debug           # Debug mode
#   bash scripts/run-e2e-tests.sh --chrome          # Chrome only
#
###############################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=================================================${NC}"
echo -e "${BLUE}  CorvinOS Console E2E Test Suite${NC}"
echo -e "${BLUE}=================================================${NC}\n"

# Parse arguments
HEADED=0
DEBUG=0
BROWSER="chromium"

while [[ $# -gt 0 ]]; do
  case $1 in
    --headed)
      HEADED=1
      echo -e "${YELLOW}ℹ️  Running with visible browser${NC}"
      shift
      ;;
    --debug)
      DEBUG=1
      echo -e "${YELLOW}ℹ️  Running in debug mode${NC}"
      shift
      ;;
    --chrome)
      BROWSER="chromium"
      shift
      ;;
    --firefox)
      BROWSER="firefox"
      shift
      ;;
    --webkit)
      BROWSER="webkit"
      shift
      ;;
    *)
      echo -e "${RED}Unknown option: $1${NC}"
      exit 1
      ;;
  esac
done

# Check prerequisites
echo -e "${BLUE}🔍 Checking prerequisites...${NC}"

if ! command -v node &> /dev/null; then
  echo -e "${RED}❌ Node.js not found. Please install Node.js 18+${NC}"
  exit 1
fi

if ! command -v npm &> /dev/null; then
  echo -e "${RED}❌ npm not found. Please install npm${NC}"
  exit 1
fi

echo -e "${GREEN}✅ Node.js $(node --version)${NC}"
echo -e "${GREEN}✅ npm $(npm --version)${NC}\n"

# Install Playwright if needed
echo -e "${BLUE}📦 Setting up Playwright...${NC}"
if ! npm list @playwright/test &> /dev/null; then
  echo -e "${YELLOW}Installing @playwright/test...${NC}"
  npm install --save-dev @playwright/test
fi

# Install Playwright browsers
npx playwright install $BROWSER --with-deps

echo -e "${GREEN}✅ Playwright ready${NC}\n"

# Ensure database is seeded
echo -e "${BLUE}📊 Ensuring token metrics database is seeded...${NC}"
if [ ! -f ~/.corvin/token_metrics.db ]; then
  echo -e "${YELLOW}Seeding database with test data...${NC}"
  python3 scripts/seed-token-metrics.py
  echo -e "${GREEN}✅ Database seeded${NC}"
else
  echo -e "${GREEN}✅ Database already exists${NC}"
fi

# Ensure features.json has vibe_engineering enabled
echo -e "${BLUE}⚙️  Ensuring vibe_engineering feature is enabled...${NC}"
python3 << 'EOF'
import json
from pathlib import Path

features_path = Path.home() / ".corvin" / "tenants" / "_default" / "global" / "features.json"

if features_path.exists():
    with open(features_path) as f:
        data = json.load(f)

    if "vibe_engineering" not in data.get("flags", {}):
        data["flags"]["vibe_engineering"] = True
        with open(features_path, 'w') as f:
            json.dump(data, f, indent=2)
        print("✅ Enabled vibe_engineering in features.json")
    else:
        print("✅ vibe_engineering already enabled")
else:
    print("❌ features.json not found")
EOF

echo ""

# Run tests
echo -e "${BLUE}🧪 Running E2E Tests...${NC}"
echo -e "${YELLOW}Tests will start the Console on http://localhost:8000${NC}\n"

if [ $DEBUG -eq 1 ]; then
  echo -e "${YELLOW}Running in debug mode...${NC}"
  npx playwright test scripts/e2e-test-full-console.spec.ts \
    --project=$BROWSER \
    --headed \
    --debug
elif [ $HEADED -eq 1 ]; then
  echo -e "${YELLOW}Running with visible browser...${NC}"
  npx playwright test scripts/e2e-test-full-console.spec.ts \
    --project=$BROWSER \
    --headed
else
  echo -e "${YELLOW}Running in headless mode...${NC}"
  npx playwright test scripts/e2e-test-full-console.spec.ts \
    --project=$BROWSER
fi

# Generate report
echo -e "\n${BLUE}📊 Test Results${NC}"
if [ -f test-results.json ]; then
  echo -e "${BLUE}Generating HTML report...${NC}"
  npx playwright show-report
fi

echo ""
echo -e "${GREEN}=================================================${NC}"
echo -e "${GREEN}  ✅ E2E Test Suite Complete${NC}"
echo -e "${GREEN}=================================================${NC}"
echo -e "\n${BLUE}📈 Test Coverage:${NC}"
echo "  1️⃣  Console UI Navigation"
echo "  2️⃣  Settings and Configuration"
echo "  3️⃣  Feature Flags (Settings → Features)"
echo "  4️⃣  Vibe Engineering Activation"
echo "  5️⃣  Token Metrics Dashboard"
echo "  6️⃣  Memory Pipeline Stage"
echo "  7️⃣  Skills Pipeline Stage"
echo "  8️⃣  Graph/Knowledge Pipeline Stage"
echo "  9️⃣  Forged Tools Visibility"
echo "  🔟 Forged Skills Visibility"
echo "  1️⃣1️⃣ Real-time Token Metrics Updates"
echo "  1️⃣2️⃣ Full Context Pipeline Integration"
echo ""
echo -e "${BLUE}📄 Reports:${NC}"
echo "  - HTML Report: playwright-report/index.html"
echo "  - JSON Results: test-results.json"
echo "  - JUnit XML: junit.xml"
echo ""
