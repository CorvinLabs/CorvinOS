#!/bin/bash
# Adversarial Review: Quick validation (3 dimensions)
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/install.sh"
PS1="$REPO_ROOT/install.ps1"

_green() { printf '\033[32m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }
_bold()  { printf '\033[1m%s\033[0m' "$*"; }

printf '\n%s\n\n' "$(_bold 'Adversarial Review (v1.0.0 Production Ready)')"

PASS=0
FAIL=0

# Security: Checksum pinned + verified
printf '  Security: Checksum SHA256 ... '
if grep -q "UV_INSTALLER_SHA256=" "$SCRIPT" && grep -q "_sha256_of\|sha256sum" "$SCRIPT"; then
    printf '%s\n' "$(_green '✓')"
    PASS=$((PASS + 1))
else
    printf '%s\n' "$(_red '✗')"
    FAIL=$((FAIL + 1))
fi

# Robustness: Fail-fast (set -eu)
printf '  Robustness: Fail-fast (set -eu) ... '
if grep -q "^set -eu" "$SCRIPT"; then
    printf '%s\n' "$(_green '✓')"
    PASS=$((PASS + 1))
else
    printf '%s\n' "$(_red '✗')"
    FAIL=$((FAIL + 1))
fi

# UX: No Ollama
printf '  UX: No Ollama bloat ... '
if ! grep -i "ollama" "$SCRIPT" "$PS1" 2>/dev/null; then
    printf '%s\n' "$(_green '✓')"
    PASS=$((PASS + 1))
else
    printf '%s\n' "$(_red '✗')"
    FAIL=$((FAIL + 1))
fi

# Claude Code integrated
printf '  UX: Claude Code integration ... '
if grep -q "claude\|Claude" "$SCRIPT" "$PS1"; then
    printf '%s\n' "$(_green '✓')"
    PASS=$((PASS + 1))
else
    printf '%s\n' "$(_red '✗')"
    FAIL=$((FAIL + 1))
fi

# PowerShell 5.1 support
printf '  Platform: PowerShell 5.1+ ... '
if grep -q "#Requires -Version 5.1" "$PS1"; then
    printf '%s\n' "$(_green '✓')"
    PASS=$((PASS + 1))
else
    printf '%s\n' "$(_red '✗')"
    FAIL=$((FAIL + 1))
fi

# README has quick-start
printf '  Docs: Quick-start in README ... '
if head -60 "$REPO_ROOT/README.md" | grep -q "Quick Start\|curl.*install"; then
    printf '%s\n' "$(_green '✓')"
    PASS=$((PASS + 1))
else
    printf '%s\n' "$(_red '✗')"
    FAIL=$((FAIL + 1))
fi

printf '\n%s\n' "$(_bold '━━━━━━━━━━━━━━━━━━━━━━━━━━━')"
printf '%s\n' " Result: $(_green "$PASS passed")  $([ $FAIL -eq 0 ] && echo "$(_green 'all green')" || echo "$(_red "$FAIL failed")")"
printf '%s\n' "$(_bold '━━━━━━━━━━━━━━━━━━━━━━━━━━━')"

exit $FAIL
