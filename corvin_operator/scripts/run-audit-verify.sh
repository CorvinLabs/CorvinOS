#!/bin/bash
# Daily Audit Chain Verification (Phase B, ADR-0541)
#
# Runs verification of all completed tasks to detect tampering.
# Scheduled: 02:00 UTC every day via systemd timer
#
# Exit codes:
#   0 - All verifications passed
#   1 - One or more verifications failed (alerts operator)
#   2 - Script error

set -euo pipefail

# Configuration
CORVIN_HOME="${CORVIN_HOME:=$HOME/.corvin}"
TENANT_ID="${CORVIN_TENANT_ID:=_default}"
MIN_AGE_DAYS="${AUDIT_VERIFY_MIN_AGE_DAYS:=30}"

# Logging
LOG_DIR="$CORVIN_HOME/verification_logs"
LOG_FILE="$LOG_DIR/audit-verify-$(date +%Y-%m-%d).log"
mkdir -p "$LOG_DIR"

log() {
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG_FILE"
}

error() {
    echo "[ERROR] $*" | tee -a "$LOG_FILE" >&2
}

log "Starting audit chain verification (tenant=$TENANT_ID, min_age=$MIN_AGE_DAYS days)"

# Check if CORVIN_HOME exists
if [ ! -d "$CORVIN_HOME" ]; then
    error "CORVIN_HOME directory not found: $CORVIN_HOME"
    exit 2
fi

# Find all completed tasks (tasks older than MIN_AGE_DAYS)
SNAPSHOTS_DIR="$CORVIN_HOME/tenants/$TENANT_ID/snapshots"

if [ ! -d "$SNAPSHOTS_DIR" ]; then
    log "No snapshots found (snapshots dir does not exist)"
    exit 0
fi

# Count snapshots
SNAPSHOT_COUNT=$(find "$SNAPSHOTS_DIR" -name "*.json" -type f | wc -l)
log "Found $SNAPSHOT_COUNT snapshot(s)"

# Run Python verification script
PYTHON_SCRIPT="$CORVIN_HOME/../../../projects/CorvinOS/operator/scripts/audit_verify.py"

if [ ! -f "$PYTHON_SCRIPT" ]; then
    error "Verification script not found: $PYTHON_SCRIPT"
    exit 2
fi

# Run verification (Python script handles the actual verification)
log "Running verification script..."
python3 "$PYTHON_SCRIPT" \
    --corvin-home "$CORVIN_HOME" \
    --tenant-id "$TENANT_ID" \
    --min-age-days "$MIN_AGE_DAYS" \
    --log-file "$LOG_FILE" \
    2>&1 | tee -a "$LOG_FILE"

VERIFY_EXIT_CODE=$?

if [ $VERIFY_EXIT_CODE -eq 0 ]; then
    log "✅ Audit verification completed successfully"
    exit 0
else
    error "❌ Audit verification found issues (exit code: $VERIFY_EXIT_CODE)"
    # TODO: Alert operator via monitoring system (Prometheus, email, Slack)
    exit 1
fi
