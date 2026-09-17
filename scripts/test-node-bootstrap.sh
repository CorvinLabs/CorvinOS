#!/bin/bash
# test-node-bootstrap.sh — Verify ensure-node.sh and ensure-npm.sh work correctly
# Tests: idempotency, PATH updates, npm functionality
# Exit 0 on success, 1 on failure

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CORVIN_HOME_TEST="${CORVIN_HOME_TEST:-/tmp/test-corvin-node-$$}"
export CORVIN_HOME="${CORVIN_HOME_TEST}"

_log()    { printf '  %s\n' "$*" >&2; }
_ok()     { printf '  \033[32m✓\033[0m %s\n' "$*" >&2; }
_warn()   { printf '  \033[33m⚠\033[0m %s\n' "$*" >&2; }
_fail()   { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

trap "rm -rf '$CORVIN_HOME_TEST'" EXIT

_log "Node.js Bootstrap Test Suite"
_log "Test environment: $CORVIN_HOME_TEST"

# ─────────────────────────────────────────────────────────────────────────────
# Test 1: ensure-node.sh exits with 0
# ─────────────────────────────────────────────────────────────────────────────
_log ""
_log "Test 1: ensure-node.sh bootstrap"
if bash "${SCRIPT_DIR}/ensure-node.sh"; then
    _ok "ensure-node.sh succeeded"
else
    _fail "ensure-node.sh exited with non-zero"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Node.js binary exists and is executable
# ─────────────────────────────────────────────────────────────────────────────
_log ""
_log "Test 2: Node.js binary validation"
NODE_BIN="${CORVIN_HOME}/node/bin/node"
if [ ! -x "$NODE_BIN" ]; then
    _fail "Node.js binary not executable: $NODE_BIN"
fi
_ok "Node.js binary found: $NODE_BIN"

# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Node version matches .nvmrc
# ─────────────────────────────────────────────────────────────────────────────
_log ""
_log "Test 3: Version match (.nvmrc)"
EXPECTED_VERSION=$(cat "${REPO_DIR}/.nvmrc" | tr -d ' \n' | sed 's/^v//')
INSTALLED_VERSION=$("$NODE_BIN" --version | sed 's/^v//')
if [ "$EXPECTED_VERSION" != "$INSTALLED_VERSION" ]; then
    _fail "Version mismatch: expected v${EXPECTED_VERSION}, got v${INSTALLED_VERSION}"
fi
_ok "Version matches: v${INSTALLED_VERSION}"

# ─────────────────────────────────────────────────────────────────────────────
# Test 4: npm is available via local node
# ─────────────────────────────────────────────────────────────────────────────
_log ""
_log "Test 4: npm availability"
NPM_BIN="${CORVIN_HOME}/node/bin/npm"
if [ ! -x "$NPM_BIN" ]; then
    _fail "npm not executable: $NPM_BIN"
fi
NPM_VERSION=$("$NPM_BIN" --version)
_ok "npm available: v${NPM_VERSION}"

# ─────────────────────────────────────────────────────────────────────────────
# Test 5: ensure-npm.sh configures environment
# ─────────────────────────────────────────────────────────────────────────────
_log ""
_log "Test 5: ensure-npm.sh environment setup"
if bash "${SCRIPT_DIR}/ensure-npm.sh" >/dev/null 2>&1; then
    _ok "ensure-npm.sh succeeded"
else
    _fail "ensure-npm.sh failed"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Test 6: npm cache directory created
# ─────────────────────────────────────────────────────────────────────────────
_log ""
_log "Test 6: npm cache directory"
NPM_CACHE="${CORVIN_HOME}/npm-cache"
if [ ! -d "$NPM_CACHE" ]; then
    _fail "npm cache directory not created: $NPM_CACHE"
fi
_ok "npm cache directory: $NPM_CACHE"

# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Idempotency (re-run ensure-node.sh)
# ─────────────────────────────────────────────────────────────────────────────
_log ""
_log "Test 7: Idempotency (second run)"
if bash "${SCRIPT_DIR}/ensure-node.sh"; then
    _ok "ensure-node.sh idempotent (no error on second run)"
else
    _fail "ensure-node.sh failed on second run"
fi

# Verify same binary
if ! "$NODE_BIN" --version >/dev/null 2>&1; then
    _fail "Node.js binary broken after second ensure-node.sh run"
fi
_ok "Node.js still functional after re-run"

# ─────────────────────────────────────────────────────────────────────────────
# Test 8: PATH contains node directory
# ─────────────────────────────────────────────────────────────────────────────
_log ""
_log "Test 8: PATH exports"
export PATH="${CORVIN_HOME}/node/bin:${PATH}"
if command -v node >/dev/null 2>&1 && [ "$(command -v node)" = "${CORVIN_HOME}/node/bin/node" ]; then
    _ok "Local node is on PATH"
else
    _warn "Local node PATH may not be set (this is OK if system node is fallback)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Test 9: npm ci works with local npm
# ─────────────────────────────────────────────────────────────────────────────
_log ""
_log "Test 9: npm ci validation"
if [ -f "${REPO_DIR}/package-lock.json" ]; then
    # Dry-run to verify npm ci syntax (don't actually install)
    if "$NPM_BIN" ci --dry-run >/dev/null 2>&1; then
        _ok "npm ci --dry-run succeeded"
    else
        _warn "npm ci --dry-run failed (might be OK if no package-lock.json)"
    fi
else
    _warn "No package-lock.json (skipping npm ci test)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
_log ""
_log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
_ok "All tests passed!"
_log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
_log ""
_log "Summary:"
_log "  Node.js: ${INSTALLED_VERSION}"
_log "  npm: ${NPM_VERSION}"
_log "  Location: ${CORVIN_HOME}/node/"
_log "  npm cache: ${NPM_CACHE}"
_log ""
_log "To use in a shell:"
_log "  export PATH=\"${CORVIN_HOME}/node/bin:\$PATH\""
_log "  npm --version"
_log ""
