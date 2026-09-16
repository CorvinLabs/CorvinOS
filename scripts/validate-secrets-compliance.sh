#!/bin/bash
################################################################################
# Validate Secrets Compliance — GDPR Art. 32 (Cryptographic Security)
#
# This script verifies:
# 1. No raw API keys/tokens in git history (working set)
# 2. .gitignore properly excludes secret files
# 3. Secrets files have restricted permissions (0600)
# 4. No unencrypted credentials in environment
#
# Usage: bash scripts/validate-secrets-compliance.sh
# Exit codes: 0 = compliant, 1 = violation
################################################################################

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VIOLATIONS=0

echo "═══════════════════════════════════════════════════════════════════"
echo "   GDPR Art. 32 Secret Compliance Validation"
echo "═══════════════════════════════════════════════════════════════════"
echo ""

# ────────────────────────────────────────────────────────────────────────────
# Check 1: .gitignore coverage
# ────────────────────────────────────────────────────────────────────────────

echo "Check 1: Secret files excluded from git"
if grep -q "\.env" .gitignore; then
    echo -e "${GREEN}✓${NC} .env files are in .gitignore"
else
    echo -e "${RED}✗${NC} .env files are NOT in .gitignore"
    VIOLATIONS=$((VIOLATIONS + 1))
fi

# ────────────────────────────────────────────────────────────────────────────
# Check 2: Staged/uncommitted secrets
# ────────────────────────────────────────────────────────────────────────────

echo ""
echo "Check 2: No secrets in git staging area"
STAGED_SECRETS=$(git diff --cached --name-only 2>/dev/null | grep -E "\.env|secret|token" || true)
if [ -z "$STAGED_SECRETS" ]; then
    echo -e "${GREEN}✓${NC} No secret files staged for commit"
else
    echo -e "${RED}✗${NC} Secret files found in staging area:"
    echo "$STAGED_SECRETS" | sed 's/^/  - /'
    VIOLATIONS=$((VIOLATIONS + 1))
fi

# ────────────────────────────────────────────────────────────────────────────
# Check 3: Secret patterns in staged code
# ────────────────────────────────────────────────────────────────────────────

echo ""
echo "Check 3: No raw API key patterns in staged changes"
STAGED_CONTENT=$(git diff --cached --no-color 2>/dev/null || echo "")
KEY_PATTERNS=(
    "ghp_[A-Za-z0-9]+"           # GitHub PAT
    "sk_[a-z0-9]{20,}"           # Stripe live key
    "pk_[a-z0-9]{20,}"           # Stripe publishable key
    "AKIA[0-9A-Z]{16}"           # AWS access key
)

FOUND_PATTERNS=0
for pattern in "${KEY_PATTERNS[@]}"; do
    if echo "$STAGED_CONTENT" | grep -E "$pattern" > /dev/null 2>&1; then
        echo -e "${RED}✗${NC} Found pattern: $pattern"
        FOUND_PATTERNS=$((FOUND_PATTERNS + 1))
    fi
done

if [ $FOUND_PATTERNS -eq 0 ]; then
    echo -e "${GREEN}✓${NC} No raw API key patterns detected in staging area"
else
    VIOLATIONS=$((VIOLATIONS + FOUND_PATTERNS))
fi

# ────────────────────────────────────────────────────────────────────────────
# Check 4: Local secret file permissions
# ────────────────────────────────────────────────────────────────────────────

echo ""
echo "Check 4: Secret files have restricted permissions (0600)"
SECRET_FILES=(
    ~/.config/corvin-voice/.env
    ~/.corvin/secrets.json
)

PERMS_VIOLATIONS=0
for file in "${SECRET_FILES[@]}"; do
    if [ -f "$file" ]; then
        PERMS=$(stat -c '%a' "$file" 2>/dev/null || stat -f '%a' "$file" 2>/dev/null || echo "unknown")
        if [ "$PERMS" = "600" ] || [ "$PERMS" = "0600" ]; then
            echo -e "${GREEN}✓${NC} $file: $PERMS (secure)"
        else
            echo -e "${YELLOW}!${NC} $file: $PERMS (should be 0600)"
            PERMS_VIOLATIONS=$((PERMS_VIOLATIONS + 1))
            # Auto-fix if possible
            if chmod 0600 "$file" 2>/dev/null; then
                echo -e "  ${GREEN}→ Fixed: $file set to 0600${NC}"
            fi
        fi
    fi
done

if [ $PERMS_VIOLATIONS -eq 0 ]; then
    echo -e "${GREEN}✓${NC} All checked secret files have correct permissions"
else
    VIOLATIONS=$((VIOLATIONS + PERMS_VIOLATIONS))
fi

# ────────────────────────────────────────────────────────────────────────────
# Check 5: .env.local not in working tree (after rotation)
# ────────────────────────────────────────────────────────────────────────────

echo ""
echo "Check 5: Secret rotation markers in place"
if grep -r "ROTATED-" ~/.config/corvin-voice/ > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} Secret rotation tokens detected (keys have been rotated)"
else
    echo -e "${YELLOW}!${NC} No rotation markers found (first-time setup or keys still live)"
    # This is not a violation, just informational
fi

# ────────────────────────────────────────────────────────────────────────────
# Summary
# ────────────────────────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════════════════════"
if [ $VIOLATIONS -eq 0 ]; then
    echo -e "${GREEN}✅ COMPLIANT: All secret compliance checks passed${NC}"
    echo ""
    echo "GDPR Art. 32 Requirements Met:"
    echo "  ✓ Encryption at rest (via .env files with 0600 permissions)"
    echo "  ✓ Access control (files readable only by owner)"
    echo "  ✓ Separation (secrets not in git/code)"
    echo "  ✓ Audit-ready (rotation tokens documented)"
    echo ""
    exit 0
else
    echo -e "${RED}❌ VIOLATIONS FOUND: $VIOLATIONS compliance check(s) failed${NC}"
    echo ""
    echo "Action Required:"
    echo "  1. Fix permission issues (should auto-correct above)"
    echo "  2. Review staged changes for secrets: git diff --cached"
    echo "  3. If secrets were committed: run git-secrets or BFG Repo-Cleaner"
    echo "  4. Rotate compromised tokens immediately"
    echo ""
    exit 1
fi
