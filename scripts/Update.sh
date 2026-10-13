#!/bin/bash
# Update.sh — CorvinOS Core Update Script (Unix/Linux/macOS)
#
# Usage:
#   ./Update.sh              — install latest update
#   ./Update.sh --check-only — check for updates without installing
#
# Output:
#   On --check-only: JSON on stdout with version info
#   On success: exit code 0
#   On failure: exit code 1 with error on stderr
#
# Environment:
#   CORVIN_HOME — installation root (default: ~/.corvin)
#   CORVIN_UPDATE_REPO — git remote to pull from (default: origin)

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Paths
CORVIN_HOME="${CORVIN_HOME:=$HOME/.corvin}"
CORVIN_REPO="${CORVIN_REPO:=$(git -C "$CORVIN_HOME" rev-parse --show-toplevel 2>/dev/null || echo "$CORVIN_HOME")}"
CORVIN_UPDATE_REPO="${CORVIN_UPDATE_REPO:=origin}"

# Logging functions
log() {
    echo -e "${GREEN}[UPDATE]${NC} $*" >&2
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $*" >&2
}

error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

# Get current version
get_current_version() {
    # Try: importlib.metadata
    python3 -c "import importlib.metadata; print(importlib.metadata.version('corvinos'))" 2>/dev/null ||
    # Try: pyproject.toml
    grep -E '^\s*version\s*=' "$CORVIN_REPO/../pyproject.toml" 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' ||
    # Fallback
    echo "unknown"
}

# Get latest available version
get_latest_version() {
    # Try: git tag on origin
    git -C "$CORVIN_REPO" fetch --quiet "$CORVIN_UPDATE_REPO" 2>/dev/null || true
    git -C "$CORVIN_REPO" tag -l "v*" --sort=-v:refname 2>/dev/null | head -1 | sed 's/^v//' ||
    echo "unknown"
}

# Check for updates
check_for_update() {
    local current
    local latest

    current=$(get_current_version)
    latest=$(get_latest_version)

    local has_update=false
    if [[ "$current" != "unknown" && "$latest" != "unknown" && "$latest" != "$current" ]]; then
        has_update=true
    fi

    # Output JSON
    cat <<EOF
{
    "has_update": $has_update,
    "current_version": "$current",
    "available_version": "$latest"
}
EOF
}

# Perform the update
do_update() {
    log "Starting update of CorvinOS Core..."

    # Verify repo exists
    if [[ ! -d "$CORVIN_REPO/.git" ]]; then
        error "Git repository not found at $CORVIN_REPO"
        return 1
    fi

    # Verify we have git
    if ! command -v git &>/dev/null; then
        error "git not found. Please install git and try again."
        return 1
    fi

    # Fetch latest
    log "Fetching latest code from $CORVIN_UPDATE_REPO..."
    if ! git -C "$CORVIN_REPO" fetch "$CORVIN_UPDATE_REPO" main; then
        error "Failed to fetch from $CORVIN_UPDATE_REPO"
        return 1
    fi

    # Merge/rebase (try merge first)
    log "Merging updates..."
    if ! git -C "$CORVIN_REPO" merge "$CORVIN_UPDATE_REPO/main" --no-edit; then
        warn "Merge conflict. Attempting auto-resolve..."
        git -C "$CORVIN_REPO" merge --abort 2>/dev/null || true
        # If merge failed, try rebase as fallback
        if ! git -C "$CORVIN_REPO" rebase "$CORVIN_UPDATE_REPO/main"; then
            error "Failed to merge updates. Manual intervention required."
            return 1
        fi
    fi

    # Reinstall if venv present
    if [[ -f "$CORVIN_HOME/.venv/bin/pip" || -f "$CORVIN_HOME/.venv/Scripts/pip" ]]; then
        log "Reinstalling Python packages..."
        local pip_cmd
        if [[ -f "$CORVIN_HOME/.venv/bin/pip" ]]; then
            pip_cmd="$CORVIN_HOME/.venv/bin/pip"
        else
            pip_cmd="$CORVIN_HOME/.venv/Scripts/pip"
        fi

        if ! "$pip_cmd" install -e "$CORVIN_REPO" --quiet; then
            warn "Failed to reinstall packages. Console may not reflect all changes until restart."
        fi
    fi

    # Verify update
    local new_version
    new_version=$(get_current_version)
    log "Update complete. Version: $new_version"

    # Summary
    cat <<EOF
{
    "success": true,
    "message": "Update completed. Restart Console to apply changes.",
    "new_version": "$new_version"
}
EOF

    return 0
}

# Main
main() {
    case "${1:---update}" in
        --check-only)
            check_for_update
            ;;
        --update|'')
            do_update || exit 1
            ;;
        *)
            error "Unknown option: $1"
            echo "Usage: $0 [--check-only|--update]" >&2
            exit 1
            ;;
    esac
}

main "$@"
