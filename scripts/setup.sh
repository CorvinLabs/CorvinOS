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
After=corvin-webui.service
Wants=corvin-webui.service

[Service]
Type=simple
# Restart the console only after 3 consecutive failed probes (~60 s): a single
# miss during a cold boot or a long first plugin scan is not a crash, and
# restarting then just restarts the boot. Stands down while an install/update
# holds the setup lock (its owner PID is alive; a stale lock is ignored). 127.0.0.1, not
# localhost: the console binds v4 loopback only. $$ is systemd's escape for $.
# NO User=: in a --user unit it fails every start with 216/GROUP.
ExecStart=/bin/bash -c 'f=0; while true; do if curl -fs -m 5 http://127.0.0.1:8765/v1/console/healthz >/dev/null 2>&1 || kill -0 "$$(cat "$${TMPDIR:-/tmp}/corvinos-setup.lock/pid" 2>/dev/null || echo none)" 2>/dev/null; then f=0; else f=$$((f+1)); if [ "$$f" -ge 3 ]; then systemctl --user restart corvin-webui.service; f=0; sleep 60; fi; fi; sleep 20; done'
Restart=always
RestartSec=10
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
    # Safe to start before the console answers: the probe tolerates ~60 s.
    systemctl --user restart corvin-watchdog.service 2>/dev/null || true
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Create CORVIN_HOME directories
# ─────────────────────────────────────────────────────────────────────────────
mkdir -p "$CORVIN_HOME/logs" "$CORVIN_HOME/cache" "$CORVIN_HOME/config" 2>/dev/null || true
echo "  $(_green '✓') CorvinOS directories created: $CORVIN_HOME"

# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Verify watchdog service is properly configured
# ─────────────────────────────────────────────────────────────────────────────
# "Enabled" is not "running": until 2026-09-24 the unit carried User=%u and
# died on every start (216/GROUP) while this step printed a green tick.
if [ "$AUTOSTART" -eq 1 ]; then
    sleep 3
    if systemctl --user is-active --quiet corvin-watchdog.service; then
        echo "  $(_green '✓') Watchdog service running"
    else
        echo "  $(_red '✗') Watchdog service does not stay up — journalctl --user -u corvin-watchdog -n 20"
        exit 1
    fi
elif systemctl --user list-unit-files | grep -q "corvin-watchdog.service"; then
    echo "  $(_green '✓') Watchdog service enabled (starts at next login)"
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
