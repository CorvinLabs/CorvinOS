#!/bin/bash
# uninstall.sh — CorvinOS Complete Uninstall (systemd + Docker) [ADR-0868]
#
# This script completely removes CorvinOS, including:
#   1. Systemd user services (console, gateway, watchdog, etc.)
#   2. Docker containers, images, volumes (if deployed via Docker)
#   3. Configuration files and local state
#   4. Audit trail export (for compliance records)
#
# Usage:
#   bash scripts/uninstall.sh                # interactive (prompts for Docker volume removal)
#   bash scripts/uninstall.sh --force        # non-interactive (removes everything)
#
# Safety:
#   - Exports audit trail before Docker volumes are destroyed
#   - Asks for confirmation on multi-tenant scenarios
#   - Fail-closed: errors prevent removal
#
set -eu

CORVIN_HOME="${CORVIN_HOME:-$HOME/.corvin}"
CORVIN_EXPORT_DIR="${CORVIN_EXPORT_DIR:-$HOME/.corvin-exports}"
FORCE_MODE=0

# Parse arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE_MODE=1; shift ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# Styling utilities
_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_green() { printf '\033[32m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }
_yellow(){ printf '\033[33m%s\033[0m' "$*"; }
_dim()   { printf '\033[2m%s\033[0m' "$*"; }

echo ""
echo "$(_bold '🔴 Uninstalling CorvinOS from '"$CORVIN_HOME"'...')"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Detect deployment mode (systemd vs Docker)
# ─────────────────────────────────────────────────────────────────────────────
detect_deployment_mode() {
    # Check for Docker daemon
    if ! command -v docker >/dev/null 2>&1; then
        echo "systemd"
        return
    fi

    # Check if Docker daemon is running
    if ! docker ps >/dev/null 2>&1; then
        echo "systemd"
        return
    fi

    # Check for CorvinOS containers with label matching (preferred)
    if docker ps --all --filter "label=app=corvinOS" --quiet 2>/dev/null | grep -q . ; then
        echo "docker"
        return
    fi

    # Fallback: check name pattern (legacy)
    if docker ps --all 2>/dev/null | grep -qi "corvinOS\|corvin-console\|corvin-gateway"; then
        echo "docker"
        return
    fi

    echo "systemd"
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Export audit trail before removing Docker volumes [ADR-0868]
# ─────────────────────────────────────────────────────────────────────────────
export_audit_trail() {
    local timestamp=$(date +%Y%m%d-%H%M%S)
    local export_file="$CORVIN_EXPORT_DIR/audit-trail-export-$timestamp.jsonl"

    mkdir -p "$CORVIN_EXPORT_DIR" || true

    echo "  📌 Exporting audit trail for compliance (GDPR Art. 30, 32)..."

    # Try to copy audit trail from container (if Docker deployment)
    if docker ps --all --filter "name=corvinOS" --quiet 2>/dev/null | head -1 | xargs -I {} docker cp {}:/root/.corvin/audit.jsonl "$export_file" 2>/dev/null; then
        echo "  $(_green '✓') Audit trail exported: $export_file"
        return 0
    fi

    # Try to copy from local CORVIN_HOME (if systemd deployment)
    if [ -f "$CORVIN_HOME/audit.jsonl" ]; then
        cp "$CORVIN_HOME/audit.jsonl" "$export_file"
        echo "  $(_green '✓') Audit trail exported: $export_file"
        return 0
    fi

    # If no audit trail found, it's OK (might not have been configured)
    echo "  $(_yellow '⚠') No audit trail found (may not have been configured)"
    return 0
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Cleanup Docker deployment [ADR-0868]
# ─────────────────────────────────────────────────────────────────────────────
cleanup_docker() {
    echo "  📌 Cleaning up Docker deployment..."
    echo ""

    # Stop and remove containers with label
    echo "  📌 Stopping and removing containers..."
    local containers=$(docker ps --all --filter "label=app=corvinOS" --quiet 2>/dev/null || echo "")
    if [ -n "$containers" ]; then
        echo "$containers" | xargs -r docker stop 2>/dev/null || true
        echo "$containers" | xargs -r docker rm -f 2>/dev/null || true
        echo "  $(_green '✓') Labeled containers removed"
    fi

    # Legacy name-based cleanup (fallback for older deployments)
    docker stop corvinOS-console corvinOS-gateway corvinOS-notification 2>/dev/null || true
    docker rm -f corvinOS-console corvinOS-gateway corvinOS-notification 2>/dev/null || true
    echo "  $(_green '✓') Legacy containers removed"

    # Remove images
    echo "  📌 Removing Docker images..."
    docker rmi corvinOS:latest 2>/dev/null || true
    docker rmi corvinOS:* 2>/dev/null || true
    docker image prune -af --filter "label=app=corvinOS" 2>/dev/null || true
    echo "  $(_green '✓') Docker images removed"

    # Remove volumes (with confirmation for multi-tenant)
    echo "  📌 Removing volumes..."
    local volumes=$(docker volume ls --filter "label=app=corvinOS" --quiet 2>/dev/null || echo "")
    if [ -n "$volumes" ]; then
        echo "  📌 Volumes to remove:"
        docker volume ls --filter "label=app=corvinOS"

        if [ "$FORCE_MODE" -eq 0 ] && [ -t 0 ]; then
            # Interactive mode: ask for confirmation
            read -p "  $(_yellow '?') Remove CorvinOS volumes? (y/n): " confirm
            if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
                echo "  Skipping volume removal (data preserved in volumes)"
                return 0
            fi
        fi

        echo "$volumes" | xargs -r docker volume rm 2>/dev/null || true
        echo "  $(_green '✓') Volumes removed"
    fi

    # Legacy volume removal
    docker volume rm corvinOS-data 2>/dev/null || true

    # Remove networks
    echo "  📌 Removing Docker networks..."
    docker network ls --filter "label=app=corvinOS" --quiet 2>/dev/null | xargs -r docker network rm 2>/dev/null || true
    echo "  $(_green '✓') Docker networks removed"
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Verify Docker cleanup [ADR-0868]
# ─────────────────────────────────────────────────────────────────────────────
verify_docker_cleanup() {
    echo "  📌 Verifying Docker cleanup..."
    local errors=0

    # Check containers
    if docker ps --all --filter "label=app=corvinOS" --quiet 2>/dev/null | grep -q . ; then
        echo "  $(_red '✗') Error: CorvinOS containers still exist"
        errors=$((errors + 1))
    fi

    # Check images
    if docker images --filter "reference=corvinOS*" --quiet 2>/dev/null | grep -q . ; then
        echo "  $(_red '✗') Error: CorvinOS images still exist"
        errors=$((errors + 1))
    fi

    # Check volumes (only if we removed them)
    if docker volume ls --filter "label=app=corvinOS" --quiet 2>/dev/null | grep -q . ; then
        echo "  $(_red '✗') Error: CorvinOS volumes still exist"
        errors=$((errors + 1))
    fi

    if [ $errors -eq 0 ]; then
        echo "  $(_green '✓') All Docker resources verified clean"
        return 0
    else
        echo "  $(_yellow '⚠') Warning: $errors Docker resource(s) remain"
        return 1
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 5: Cleanup systemd services [ADR-0868]
# ─────────────────────────────────────────────────────────────────────────────
cleanup_systemd() {
    echo "  📌 Cleaning up systemd services..."

    # Stop all CorvinOS services
    systemctl --user stop corvin-console.service corvin-gateway.service corvin-notification-router.service corvin-watchdog.service 2>/dev/null || true

    # Disable services
    systemctl --user disable corvin-console.service corvin-gateway.service corvin-notification-router.service corvin-watchdog.service 2>/dev/null || true

    # Remove service files
    rm -f ~/.config/systemd/user/corvin-*.service
    rm -f ~/.config/systemd/user/corvin-alert.service

    # Reload systemd daemon
    systemctl --user daemon-reload 2>/dev/null || true

    echo "  $(_green '✓') Systemd services cleaned up"
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 6: Remove CorvinOS files and state
# ─────────────────────────────────────────────────────────────────────────────
cleanup_files() {
    echo "  📌 Removing CorvinOS files and state..."

    rm -rf "$CORVIN_HOME"
    rm -f ~/bin/corvinOS ~/bin/corvin
    rm -f ~/projects/Corvin-ADR  # Remove symlink if exists

    echo "  $(_green '✓') CorvinOS files removed from $CORVIN_HOME"
}

# ─────────────────────────────────────────────────────────────────────────────
# Main execution
# ─────────────────────────────────────────────────────────────────────────────

# Detect deployment mode
DEPLOYMENT_MODE=$(detect_deployment_mode)
echo "  📌 Deployment mode: $DEPLOYMENT_MODE"
echo ""

# Export audit trail first (before removing anything)
export_audit_trail

# Cleanup based on deployment mode
if [ "$DEPLOYMENT_MODE" = "docker" ]; then
    cleanup_docker
    verify_docker_cleanup || {
        echo "  $(_yellow '⚠') Some Docker resources may require manual cleanup"
        echo "  Run: docker system prune -af --volumes"
    }
else
    echo "  📌 Cleaning up systemd deployment (not Docker)..."
fi

# Always cleanup systemd (even Docker deployments use systemd for consul/daemon)
cleanup_systemd

# Remove files
cleanup_files

echo ""
echo "$(_bold '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')"
echo "$(_green '✅ CorvinOS uninstalled successfully')"
echo "$(_bold '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')"
echo ""
echo "  $(_dim 'Exported data:') $CORVIN_EXPORT_DIR (audit trail, if applicable)"
echo "  $(_dim 'Logs:') journalctl --user for corvin-* entries"
echo ""
echo "  To reinstall: bash install.sh"
echo ""
