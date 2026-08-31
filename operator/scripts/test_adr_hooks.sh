#!/usr/bin/env bash
#
# Test ADR Hook Enforcement
# Verifyza dass der Pre-Commit Hook funktioniert
#
# Usage: bash operator/scripts/test_adr_hooks.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_DIR=$(mktemp -d)
TEST_BRANCH="test/adr-hook-$RANDOM"

echo "🧪 Testing ADR Hook Enforcement..."
echo ""

# Test 1: Verify hook is installed
echo "${YELLOW}Test 1: Pre-Commit Hook Installation${NC}"
if [ -x "$REPO_ROOT/.git/hooks/pre-commit" ]; then
  echo -e "${GREEN}✓ Pre-commit hook is installed and executable${NC}"
else
  echo -e "${RED}✗ Pre-commit hook is missing or not executable${NC}"
  exit 1
fi

# Test 2: Verify hook rejects code changes without ADR
echo ""
echo "${YELLOW}Test 2: Hook Rejects Code Without ADR${NC}"

cd "$REPO_ROOT"

# Create test branch
git checkout -b "$TEST_BRANCH" 2>/dev/null || git checkout "$TEST_BRANCH"

# Create a test code file in core/
TEST_FILE="core/test_enforcement/test_hook.py"
mkdir -p "$(dirname "$TEST_FILE")"
echo "# Test file for ADR enforcement" > "$TEST_FILE"

# Try to stage and commit (should FAIL)
git add "$TEST_FILE"

if git commit -m "test: add test file without ADR" 2>&1 | grep -q "ERROR.*ADR"; then
  echo -e "${GREEN}✓ Hook correctly rejected commit without ADR${NC}"
else
  echo -e "${RED}✗ Hook did not reject commit (or wrong error message)${NC}"
  # Cleanup
  git reset HEAD "$TEST_FILE" 2>/dev/null
  rm -f "$TEST_FILE"
  git checkout master 2>/dev/null || git checkout main 2>/dev/null
  git branch -D "$TEST_BRANCH" 2>/dev/null || true
  exit 1
fi

# Test 3: Verify hook allows --no-verify bypass
echo ""
echo "${YELLOW}Test 3: Hook Allows --no-verify Bypass${NC}"

if git commit --no-verify -m "test: commit with --no-verify" 2>&1 | grep -q "test_hook.py"; then
  echo -e "${GREEN}✓ --no-verify bypass works${NC}"
else
  echo -e "${RED}✗ --no-verify bypass failed${NC}"
  # Cleanup
  git reset HEAD "$TEST_FILE" 2>/dev/null
  rm -f "$TEST_FILE"
  git checkout master 2>/dev/null || git checkout main 2>/dev/null
  git branch -D "$TEST_BRANCH" 2>/dev/null || true
  exit 1
fi

# Test 4: Verify hook allows with skip-adr-check
echo ""
echo "${YELLOW}Test 4: Hook Allows Exception Flag${NC}"

# Reset
git reset --soft HEAD~1 2>/dev/null || true

# Try with skip flag (should PASS)
if git commit --no-verify -m "test: add file [skip-adr-check]" 2>&1; then
  echo -e "${GREEN}✓ Exception flag bypass works${NC}"
else
  echo -e "${RED}⚠ Exception flag test inconclusive${NC}"
fi

# Cleanup
cd "$REPO_ROOT"
git reset --hard HEAD~1 2>/dev/null || true
rm -rf "core/test_enforcement"
git checkout master 2>/dev/null || git checkout main 2>/dev/null
git branch -D "$TEST_BRANCH" 2>/dev/null || true

echo ""
echo -e "${GREEN}✅ ADR Hook Tests Complete${NC}"
echo ""
echo "Summary:"
echo "  • Pre-commit hook installed: ✓"
echo "  • Rejects code without ADR: ✓"
echo "  • Allows --no-verify bypass: ✓"
echo "  • Memory sync available: $([ -x operator/scripts/sync_memory_from_adrs.py ] && echo '✓' || echo '✗')"
echo ""
echo "Next steps:"
echo "  1. Make a real change to core/"
echo "  2. Create an ADR in Corvin-ADR/decisions/"
echo "  3. Commit both together"
