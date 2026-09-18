#!/bin/bash
# setup.sh — CorvinOS Post-Install Setup (Watchdog + systemd services)
# Called by install.sh after phase 3 to register watchdog and critical services.
#
# This script ensures:
#   1. Watchdog service is installed and enabled
#   2. Health check probe is in place
#   3. Systemd user services are properly configured
#
# Usage:
#   bash scripts/setup.sh
#   bash scripts/setup.sh --no-autostart    # install but don't start
#
set -eu

# Styling utilities (from install.sh)
_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_green() { printf '\033[32m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }
_yellow(){ printf '\033[33m%s\033[0m' "$*"; }
_dim()   { printf '\033[2m%s\033[0m' "$*"; }

# Parse arguments
AUTOSTART=1
while [ $# -gt 0 ]; do
    case "$1" in
        --no-autostart) AUTOSTART=0; shift ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# Resolve script directory and CORVIN_HOME
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CORVIN_HOME="${CORVIN_HOME:-$HOME/.corvin}"
SYSTEMD_DIR="$HOME/.config/systemd/user"

echo ""
echo "$(_bold "Setting up CorvinOS services (watchdog + systemd)...")"

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Create systemd user directory
# ─────────────────────────────────────────────────────────────────────────────
mkdir -p "$SYSTEMD_DIR" || true
echo "  ✓ Systemd directory ready: $SYSTEMD_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Install watchdog service (ADR-0867)
# ─────────────────────────────────────────────────────────────────────────────
echo "  📌 Installing watchdog service..."

WATCHDOG_HEALTH_CHECK="${REPO_DIR}/scripts/watchdog-health-check.sh"
if [ ! -f "$WATCHDOG_HEALTH_CHECK" ]; then
    echo "  $(_red '✗') Watchdog health check script not found at: $WATCHDOG_HEALTH_CHECK"
    exit 1
fi

# Make watchdog scripts executable
chmod +x "$WATCHDOG_HEALTH_CHECK" 2>/dev/null || true
if [ -f "${REPO_DIR}/scripts/corvin-watchdog-alert.sh" ]; then
    chmod +x "${REPO_DIR}/scripts/corvin-watchdog-alert.sh" 2>/dev/null || true
fi

# Install watchdog.service systemd unit (waits for console to be ready)
cat > "${SYSTEMD_DIR}/corvin-watchdog.service" <<'EOF'
[Unit]
Description=CorvinOS Watchdog Service
After=corvin-console.service
Wants=corvin-console.service

[Service]
Type=simple
ExecStart=/bin/bash -c 'while true; do curl -fs -m 5 http://localhost:8765/v1/console/healthz >/dev/null 2>&1 || { systemctl --user restart corvin-console.service; sleep 10; }; sleep 30; done'
Restart=on-failure
RestartSec=10
User=%u
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

chmod 0644 "${SYSTEMD_DIR}/corvin-watchdog.service"
echo "  $(_green '✓') Watchdog service installed: ${SYSTEMD_DIR}/corvin-watchdog.service"

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Enable and optionally start watchdog service
# ─────────────────────────────────────────────────────────────────────────────
systemctl --user daemon-reload 2>/dev/null || true
systemctl --user enable corvin-watchdog.service 2>/dev/null || {
    echo "  $(_yellow '⚠') Warning: could not enable corvin-watchdog.service"
    exit 1
}
echo "  $(_green '✓') Watchdog service enabled"

if [ "$AUTOSTART" -eq 1 ]; then
    # Only start if console is already up
    if curl -fs -m 2 http://localhost:8765/v1/console/healthz >/dev/null 2>&1; then
        systemctl --user start corvin-watchdog.service 2>/dev/null || true
        echo "  $(_green '✓') Watchdog service started"
    else
        echo "  $(_dim 'ℹ') Console not yet responding; watchdog will start on next boot"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Create CORVIN_HOME directories
# ─────────────────────────────────────────────────────────────────────────────
mkdir -p "$CORVIN_HOME/logs" "$CORVIN_HOME/cache" "$CORVIN_HOME/config" 2>/dev/null || true
echo "  $(_green '✓') CorvinOS directories created: $CORVIN_HOME"

# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Verify watchdog service is properly configured
# ─────────────────────────────────────────────────────────────────────────────
if systemctl --user list-unit-files | grep -q "corvin-watchdog.service"; then
    echo "  $(_green '✓') Watchdog service verified in systemd"
else
    echo "  $(_red '✗') Watchdog service not found in systemd unit files"
    exit 1
fi

echo ""
echo "$(_bold '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')"
echo "$(_green '✅ Setup complete')"
echo "$(_bold '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')"
echo ""
echo "  Watchdog will monitor console health and auto-restart on failure"
echo "  Status: systemctl --user status corvin-watchdog.service"
echo "  Logs:   journalctl --user -u corvin-watchdog.service -f"
echo ""
