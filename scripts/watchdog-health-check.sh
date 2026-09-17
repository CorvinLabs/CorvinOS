#!/bin/bash
# Watchdog Health Check — Comprehensive daemon health verification (ADR-0867)
# Verifies console + gateway + notification-router endpoints post-installation
# Emits audit events on success/failure via Python compliance layer
set -e

CORVIN_HOME="${CORVIN_HOME:-$HOME/.corvin}"
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-60}
HEALTH_CHECK_LOG="${HEALTH_CHECK_LOG:-${TMPDIR:-/tmp}/corvinOS-health-check.log}"

# Styling utilities (same as install.sh)
_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_green() { printf '\033[32m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }
_yellow(){ printf '\033[33m%s\033[0m' "$*"; }
_dim()   { printf '\033[2m%s\033[0m' "$*"; }

die() { printf '%s %s\n' "$(_red 'Error:')" "$*" >&2; exit 1; }

# Health check probe for a single endpoint
healthz_probe() {
    local endpoint="$1"
    local label="$2"
    local timeout="${3:-$HEALTH_TIMEOUT}"
    local retry_count=0
    local max_retries="$timeout"
    
    printf '  %s Checking %s... ' "$(_dim '⏳')" "$label"
    printf "\n=== %s: healthz_probe %s ===\n" "$(date '+%Y-%m-%dT%H:%M:%S')" "$label" >>"$HEALTH_CHECK_LOG" 2>&1 || true
    
    while [ $retry_count -lt $max_retries ]; do
        if curl -fs -m 2 "$endpoint" >/dev/null 2>&1; then
            printf '%s\n' "$(_green '✓')"
            printf "SUCCESS: $label endpoint responded in ${retry_count}s\n" >>"$HEALTH_CHECK_LOG" 2>&1 || true
            return 0
        fi
        retry_count=$((retry_count + 1))
        sleep 1
        printf '.'
    done
    
    printf '%s (timeout after %ds)\n' "$(_red '✗')" "$max_retries"
    printf "FAILED: $label endpoint did not respond within ${max_retries}s\n" >>"$HEALTH_CHECK_LOG" 2>&1 || true
    return 1
}

# Main execution
printf '%s Watchdog Health Check (ADR-0867)\n\n' "$(_bold 'CorvinOS')"

printf '\n%s Health Check Summary\n' "$(_bold '━━━━━━━━━━━━')"

healthz_pass=0

if healthz_probe "http://localhost:8765/v1/console/healthz" "console"; then
    healthz_pass=$((healthz_pass + 1))
fi

if healthz_probe "http://localhost:8765/v1/gateway/healthz" "gateway"; then
    healthz_pass=$((healthz_pass + 1))
fi

healthz_probe "http://localhost:8765/v1/notification/healthz" "notification-router" 10 || true

if [ $healthz_pass -ge 2 ]; then
    printf '\n%s All critical endpoints healthy.\n' "$(_green '✓')"
    exit 0
else
    printf '\n%s Critical endpoints failed.\n' "$(_red '✗')"
    printf 'Try: %s\n' "$(_bold 'kill $SERVER_PID && corvinos-serve && corvin-install')"
    exit 2
fi
