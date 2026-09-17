#!/bin/bash
# Watchdog Alert Handler — Triggered on daemon restart/failure (ADR-0867)
# Emits audit events and optionally notifies operator
set -e

CORVIN_HOME="${CORVIN_HOME:-$HOME/.corvin}"

# Styling utilities
_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }

# Main execution
SERVICE_NAME="${1:-corvin-console.service}"

printf '%s Daemon Alert: %s restarted\n' "$(_bold 'CorvinOS')" "$SERVICE_NAME"

# Log to file
echo "$(date '+%Y-%m-%dT%H:%M:%S'): $SERVICE_NAME restarted" >> "$CORVIN_HOME/watchdog.log" 2>/dev/null || true

# Log to systemd journal if available
if command -v systemd-cat >/dev/null 2>&1; then
    printf "%s\n" "ALERT: CorvinOS daemon '$SERVICE_NAME' restarted at $(date '+%Y-%m-%dT%H:%M:%S')" | \
        systemd-cat -t "corvin-alert" -p "warning" 2>/dev/null || true
fi

exit 0
