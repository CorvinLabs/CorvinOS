#!/bin/bash
# ensure-npm.sh — Verify npm from local Node.js runtime + configure cache
# Idempotent, self-contained, no sudo needed.
#
# Usage: bash scripts/ensure-npm.sh [-- args ...]
# Exports: $PATH (includes node/npm bin), $npm_config_cache (points to ~/.corvin/npm-cache)

set -eu

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
CORVIN_HOME="${CORVIN_HOME:-${HOME}/.corvin}"
NODE_BIN="${CORVIN_HOME}/node/bin"
NPM_CACHE="${CORVIN_HOME}/npm-cache"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────
_log()    { printf '  %s\n' "$*" >&2; }
_ok()     { printf '  \033[32m✓\033[0m %s\n' "$*" >&2; }
_warn()   { printf '  \033[33m⚠\033[0m %s\n' "$*" >&2; }
_fail()   { printf '  \033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# ─────────────────────────────────────────────────────────────────────────────
# Ensure Node.js is available
# ─────────────────────────────────────────────────────────────────────────────
if [ ! -x "${NODE_BIN}/node" ]; then
    _log "Ensuring Node.js runtime ..."
    if ! bash "${REPO_ROOT}/scripts/ensure-node.sh"; then
        _fail "Failed to bootstrap Node.js"
    fi
fi

# Update PATH to use local node
export PATH="${NODE_BIN}:${PATH}"

# ─────────────────────────────────────────────────────────────────────────────
# Verify npm availability
# ─────────────────────────────────────────────────────────────────────────────
NPM_BIN=""
if [ -x "${NODE_BIN}/npm" ]; then
    NPM_BIN="${NODE_BIN}/npm"
elif command -v npm >/dev/null 2>&1; then
    NPM_BIN="$(command -v npm)"
else
    _fail "npm not found in ${NODE_BIN}/npm or on PATH"
fi

NPM_VERSION=$("${NPM_BIN}" --version 2>/dev/null || echo "?")
_ok "npm ${NPM_VERSION} ready"

# ─────────────────────────────────────────────────────────────────────────────
# Configure npm cache
# ─────────────────────────────────────────────────────────────────────────────
mkdir -p "${NPM_CACHE}" || _fail "Cannot create npm cache directory: ${NPM_CACHE}"

export npm_config_cache="${NPM_CACHE}"
_ok "npm cache: ${NPM_CACHE}"

# ─────────────────────────────────────────────────────────────────────────────
# Pass through any arguments (e.g., 'npm ci' after '--')
# ─────────────────────────────────────────────────────────────────────────────
if [ $# -gt 0 ] && [ "$1" = "--" ]; then
    shift
    _log "Running: npm $*"
    exec "${NPM_BIN}" "$@"
fi

_ok "npm environment ready"
