#!/bin/bash
# ensure-node.sh — Bootstrap local Node.js runtime (~/.corvin/node/)
# Idempotent, self-contained, no sudo needed.
#
# Usage: bash scripts/ensure-node.sh
# Sets: $CORVIN_NODE_BIN (full path to node), exports in PATH

set -eu

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
CORVIN_HOME="${CORVIN_HOME:-${HOME}/.corvin}"
NODE_ROOT="${CORVIN_HOME}/node"
NODE_BIN="${NODE_ROOT}/bin"

# Read .nvmrc from repo root if present
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "${REPO_ROOT}/.nvmrc" ]; then
    NODE_VERSION=$(cat "${REPO_ROOT}/.nvmrc" | tr -d ' \n')
else
    NODE_VERSION="v24.18.0"
fi

# Strip 'v' prefix if present
NODE_VERSION="${NODE_VERSION#v}"

# Detect platform
UNAME_S=$(uname -s)
UNAME_M=$(uname -m)
case "${UNAME_S}" in
    Linux*)     PLATFORM="linux" ;;
    Darwin*)    PLATFORM="darwin" ;;
    *)          echo "Error: unsupported OS: $UNAME_S" >&2; exit 1 ;;
esac

case "${UNAME_M}" in
    x86_64)  ARCH="x64" ;;
    arm64|aarch64) ARCH="arm64" ;;
    *)       echo "Error: unsupported architecture: $UNAME_M" >&2; exit 1 ;;
esac

NODE_FILENAME="node-v${NODE_VERSION}-${PLATFORM}-${ARCH}"
DOWNLOAD_URL="https://nodejs.org/dist/v${NODE_VERSION}/${NODE_FILENAME}.tar.xz"

# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────
_log()    { printf '  %s\n' "$*" >&2; }
_ok()     { printf '  \033[32m✓\033[0m %s\n' "$*" >&2; }
_warn()   { printf '  \033[33m⚠\033[0m %s\n' "$*" >&2; }
_fail()   { printf '  \033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

_sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 "$1" | awk '{print $NF}'
    else
        echo ""
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Main logic
# ─────────────────────────────────────────────────────────────────────────────

_log "Node.js local runtime (v${NODE_VERSION})"

# Check if node already installed
if [ -x "${NODE_BIN}/node" ]; then
    _installed_version=$("${NODE_BIN}/node" --version 2>/dev/null || echo "")
    if [ "${_installed_version#v}" = "${NODE_VERSION}" ]; then
        _ok "Node.js v${NODE_VERSION} already available at ${NODE_ROOT}"
        export CORVIN_NODE_BIN="${NODE_BIN}/node"
        export PATH="${NODE_BIN}:${PATH}"
        exit 0
    fi
fi

# Ensure CORVIN_HOME exists and is writable
if [ ! -d "${CORVIN_HOME}" ]; then
    mkdir -p "${CORVIN_HOME}" 2>/dev/null || _fail "Cannot create ${CORVIN_HOME} (write permission required)"
fi
if [ ! -w "${CORVIN_HOME}" ]; then
    _fail "Cannot write to ${CORVIN_HOME} (permissions issue)"
fi

_log "Downloading Node.js from ${DOWNLOAD_URL} ..."

# Create temp directory
_tmp_dir=$(mktemp -d) || _fail "mktemp failed"
trap "rm -rf '$_tmp_dir'" EXIT

_archive="${_tmp_dir}/${NODE_FILENAME}.tar.xz"

# Download with curl or wget
if command -v curl >/dev/null 2>&1; then
    curl -fsSL --max-time 300 -o "${_archive}" "${DOWNLOAD_URL}" 2>/dev/null \
        || _fail "Failed to download from ${DOWNLOAD_URL}"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "${_archive}" "${DOWNLOAD_URL}" 2>/dev/null \
        || _fail "Failed to download from ${DOWNLOAD_URL}"
else
    _fail "curl or wget required (not found)"
fi

[ -f "${_archive}" ] || _fail "Download resulted in no file"

_log "Extracting to ${NODE_ROOT} ..."

# Extract to temp location
_extract_tmp="${_tmp_dir}/${NODE_FILENAME}"
if command -v xz >/dev/null 2>&1 && command -v tar >/dev/null 2>&1; then
    tar -xf "${_archive}" -C "${_tmp_dir}" || _fail "tar extraction failed"
elif command -v tar >/dev/null 2>&1; then
    tar -xJf "${_archive}" -C "${_tmp_dir}" || _fail "tar extraction failed"
else
    _fail "tar not found (required for extraction)"
fi

[ -d "${_extract_tmp}" ] || _fail "Extraction directory not found"

# Remove old node directory if it exists
if [ -d "${NODE_ROOT}" ]; then
    rm -rf "${NODE_ROOT}" || _fail "Cannot remove old ${NODE_ROOT}"
fi

# Move extracted directory to final location
mkdir -p "$(dirname "${NODE_ROOT}")"
mv "${_extract_tmp}" "${NODE_ROOT}" || _fail "Cannot move to ${NODE_ROOT}"

# Verify installation
if [ ! -x "${NODE_BIN}/node" ]; then
    _fail "Node.js binary not executable at ${NODE_BIN}/node"
fi

_installed=$("${NODE_BIN}/node" --version)
_ok "Node.js ${_installed} installed to ${NODE_ROOT}"

# Export for this and any child processes
export CORVIN_NODE_BIN="${NODE_BIN}/node"
export PATH="${NODE_BIN}:${PATH}"

_ok "PATH updated: ${NODE_BIN} now first"
