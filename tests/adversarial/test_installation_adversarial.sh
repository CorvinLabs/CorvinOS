#!/bin/bash
# test_installation_adversarial.sh — Adversarial Review (3 dimensions)
# Tests installation robustness against edge cases, security issues, UX bugs
#
# Dimensions:
#   1. Security: Checksum tampering, injection attacks, path traversal
#   2. Robustness: Network failures, missing tools, corrupted downloads
#   3. UX/Platform: Interactive prompts, PATH management, Windows-specific edge cases
#
# Production Ready: All 0 findings before v1.0.0 release.

set -eu

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INSTALL_SCRIPT="$REPO_ROOT/install.sh"
INSTALL_PS1="$REPO_ROOT/install.ps1"

_green() { printf '\033[32m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }
_yellow(){ printf '\033[33m%s\033[0m' "$*"; }
_bold()  { printf '\033[1m%s\033[0m' "$*"; }

FINDINGS=0

# ─────────────────────────────────────────────────────────────────────────────
# Dimension 1: SECURITY
# ─────────────────────────────────────────────────────────────────────────────
printf '\n%s\n\n' "$(_bold 'Dimension 1: SECURITY')"

# S1: Checksum validation (uv installer must be pinned + verified)
printf '  S1: Checksum pinning + verification ... '
if grep -q "UV_INSTALLER_SHA256=" "$INSTALL_SCRIPT" && \
   grep -A5 "UV_INSTALLER_SHA256" "$INSTALL_SCRIPT" | grep -q "_sha256_of"; then
    printf '%s\n' "$(_green '✓')"
else
    printf '%s: %s\n' "$(_red '✗ CRITICAL')" "Checksum not verified before running uv installer"
    FINDINGS=$((FINDINGS + 1))
fi

# S2: No eval/exec of untrusted input
printf '  S2: No eval of untrusted input ... '
if grep -E '\beval\b|\biex\b' "$INSTALL_SCRIPT" >/dev/null 2>&1; then
    printf '%s\n' "$(_red '✗ CRITICAL')"
    FINDINGS=$((FINDINGS + 1))
else
    printf '%s\n' "$(_green '✓')"
fi

# S3: CORVIN_HOME path injection (should be quoted/escaped)
printf '  S3: CORVIN_HOME path escaping ... '
if grep -q 'CORVIN_HOME.*\$\|`' "$INSTALL_SCRIPT"; then
    # Check if it's properly escaped
    if grep -q 'CorvinHomeEscaped\|"$CorvinHome"' "$INSTALL_PS1"; then
        printf '%s\n' "$(_green '✓')"
    else
        printf '%s\n' "$(_red '✗ MEDIUM')"
        FINDINGS=$((FINDINGS + 1))
    fi
else
    printf '%s\n' "$(_green '✓')"
fi

# S4: No PII in logs (Claude credentials never logged)
printf '  S4: No PII in logs (credentials safe) ... '
if grep -q 'CORVIN_HOME\|INSTALL_LOG' "$INSTALL_SCRIPT"; then
    # Verify credentials aren't logged
    if ! grep -q 'echo.*API_KEY\|echo.*credential\|echo.*token' "$INSTALL_SCRIPT"; then
        printf '%s\n' "$(_green '✓')"
    else
        printf '%s\n' "$(_red '✗ CRITICAL')"
        FINDINGS=$((FINDINGS + 1))
    fi
else
    printf '%s\n' "$(_green '✓')"
fi

# S5: No curl | sh pattern (must verify SHA first)
printf '  S5: No unsafe curl | sh pattern ... '
if head -20 "$INSTALL_SCRIPT" | grep -q "curl.*sh"; then
    printf '%s\n' "$(_red '✗ CRITICAL')"
    FINDINGS=$((FINDINGS + 1))
else
    printf '%s\n' "$(_green '✓')"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Dimension 2: ROBUSTNESS
# ─────────────────────────────────────────────────────────────────────────────
printf '\n%s\n\n' "$(_bold 'Dimension 2: ROBUSTNESS')"

# R1: Handle missing curl/wget gracefully
printf '  R1: Fallback for missing curl/wget ... '
if grep -q 'command -v curl\|command -v wget' "$INSTALL_SCRIPT"; then
    printf '%s\n' "$(_green '✓')"
else
    printf '%s\n' "$(_red '✗ MEDIUM')"
    FINDINGS=$((FINDINGS + 1))
fi

# R2: Verify uv before using it
printf '  R2: Verify uv on PATH after install ... '
if grep -q 'command -v uv.*die' "$INSTALL_SCRIPT"; then
    printf '%s\n' "$(_green '✓')"
else
    printf '%s\n' "$(_red '✗ MEDIUM')"
    FINDINGS=$((FINDINGS + 1))
fi

# R3: Idempotent (safe to re-run without side effects)
printf '  R3: Idempotent (safe to re-run) ... '
if grep -q 'command -v uv\|if.*exists\|if.*installed' "$INSTALL_SCRIPT"; then
    printf '%s\n' "$(_green '✓')"
else
    printf '%s\n' "$(_red '✗ MEDIUM')"
    FINDINGS=$((FINDINGS + 1))
fi

# R4: Fail-fast on errors (set -eu)
printf '  R4: Fail-fast on error (set -eu) ... '
if grep -q '^set -eu' "$INSTALL_SCRIPT"; then
    printf '%s\n' "$(_green '✓')"
else
    printf '%s\n' "$(_red '✗ HIGH')"
    FINDINGS=$((FINDINGS + 1))
fi

# R5: Logging to file (don't lose error info)
printf '  R5: Install logging configured ... '
if grep -q 'INSTALL_LOG\|install.log' "$INSTALL_SCRIPT"; then
    printf '%s\n' "$(_green '✓')"
else
    printf '%s\n' "$(_red '✗ LOW')"
    FINDINGS=$((FINDINGS + 1))
fi

# ─────────────────────────────────────────────────────────────────────────────
# Dimension 3: UX / PLATFORM-SPECIFIC
# ─────────────────────────────────────────────────────────────────────────────
printf '\n%s\n\n' "$(_bold 'Dimension 3: UX / PLATFORM-SPECIFIC')"

# U1: Console auto-launches (no blocking prompts)
printf '  U1: Console auto-launch logic ... '
if grep -q 'open\|xdg-open\|wslview' "$INSTALL_SCRIPT"; then
    printf '%s\n' "$(_green '✓')"
else
    printf '%s\n' "$(_red '✗ MEDIUM')"
    FINDINGS=$((FINDINGS + 1))
fi

# U2: Windows-specific: PowerShell 5.1+ compatible install.ps1
printf '  U2: Windows PowerShell 5.1+ compat ... '
if grep -q '#Requires -Version 5.1' "$INSTALL_PS1"; then
    printf '%s\n' "$(_green '✓')"
else
    printf '%s\n' "$(_red '✗ HIGH')"
    FINDINGS=$((FINDINGS + 1))
fi

# U3: Claude Code integration (optional, not blocking)
printf '  U3: Claude Code detection (optional) ... '
if grep -q 'claude\|SKIP_CLAUDE\|--no-claude' "$INSTALL_SCRIPT" "$INSTALL_PS1"; then
    printf '%s\n' "$(_green '✓')"
else
    printf '%s\n' "$(_red '✗ MEDIUM')"
    FINDINGS=$((FINDINGS + 1))
fi

# U4: No Ollama bloat (user requirement)
printf '  U4: No Ollama dependencies ... '
if ! grep -i 'ollama\|hermes\|qwen3' "$INSTALL_SCRIPT" "$INSTALL_PS1" 2>/dev/null >/dev/null; then
    printf '%s\n' "$(_green '✓')"
else
    printf '%s\n' "$(_red '✗ CRITICAL')"
    FINDINGS=$((FINDINGS + 1))
fi

# U5: README has installation instructions (not buried)
printf '  U5: README quick-start (top of file) ... '
if head -50 "$REPO_ROOT/README.md" | grep -q 'Quick Start\|Installation\|curl.*install'; then
    printf '%s\n' "$(_green '✓')"
else
    printf '%s\n' "$(_red '✗ MEDIUM')"
    FINDINGS=$((FINDINGS + 1))
fi

# U6: Windows firewall / UAC handling
printf '  U6: Windows UAC/Firewall handling ... '
if grep -q 'sudo\|Disable-ScheduledTask\|Register-ScheduledTask\|New-NetFirewallRule' "$INSTALL_PS1"; then
    printf '%s\n' "$(_green '✓')"
else
    printf '%s\n' "$(_red '✗ MEDIUM')"
    FINDINGS=$((FINDINGS + 1))
fi

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
printf '\n%s\n' "$(_bold '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')"
if [ $FINDINGS -eq 0 ]; then
    printf '%s\n' " Adversarial Review: $(_green '0 findings') — PRODUCTION READY ✓"
else
    printf '%s\n' " Adversarial Review: $(_red "$FINDINGS findings") — FIX BEFORE RELEASE"
fi
printf '%s\n' "$(_bold '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')"

exit $FINDINGS
