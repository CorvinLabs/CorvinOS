#!/bin/bash
# ADR-0274 Health Check Script
# Monitors system health during Week 6 measurement phase
#
# Usage: bash health-check.sh [--continuous] [--interval 60]

set -euo pipefail

# Configuration
CORVIN_HOME="${HOME}/.corvin"
TENANT_ID="_default"
MEASUREMENT_DIR="${CORVIN_HOME}/measurement"
QUEUE_DIR="${CORVIN_HOME}/tenants/${TENANT_ID}/learning-queue"
PROFILE_DIR="${CORVIN_HOME}/tenants/${TENANT_ID}/profiles"

# Defaults
CONTINUOUS=false
INTERVAL=60

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --continuous)
            CONTINUOUS=true
            shift
            ;;
        --interval)
            INTERVAL="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# Helper functions
log_status() {
    local status=$1
    local message=$2

    if [ "$status" = "ok" ]; then
        echo -e "${GREEN}[OK]${NC} $message"
    elif [ "$status" = "warn" ]; then
        echo -e "${YELLOW}[WARN]${NC} $message"
    else
        echo -e "${RED}[FAIL]${NC} $message"
    fi
}

# ============================================================================
# Health Checks
# ============================================================================

check_service_running() {
    if pgrep -f "corvin-serve" > /dev/null; then
        log_status "ok" "Service running"
        return 0
    else
        log_status "fail" "Service NOT running"
        return 1
    fi
}

check_queue_health() {
    if [ ! -d "$QUEUE_DIR" ]; then
        log_status "fail" "Queue directory missing: $QUEUE_DIR"
        return 1
    fi

    local queue_files=$(find "$QUEUE_DIR" -name "*.jsonl" -type f | wc -l)
    if [ "$queue_files" -gt 0 ]; then
        log_status "ok" "Queue healthy ($queue_files files)"
        return 0
    else
        log_status "warn" "No queue files found (first run?)"
        return 0
    fi
}

check_profiles_healthy() {
    if [ ! -d "$PROFILE_DIR" ]; then
        log_status "fail" "Profile directory missing: $PROFILE_DIR"
        return 1
    fi

    # Check for tenant-baseline symlink
    if [ -L "$PROFILE_DIR/tenant-baseline.json" ]; then
        log_status "ok" "Profile symlink valid"
        return 0
    elif [ ! -e "$PROFILE_DIR/tenant-baseline.json" ]; then
        log_status "warn" "No baseline profile yet (first run?)"
        return 0
    else
        log_status "fail" "Baseline profile is not a symlink (corruption?)"
        return 1
    fi
}

check_measurement_collecting() {
    local today=$(date +%Y-%m-%d)
    local measurement_today="${MEASUREMENT_DIR}/${today}"

    if [ ! -d "$measurement_today" ]; then
        log_status "warn" "No measurement directory for today (first collection?)"
        return 0
    fi

    # Check for all 4 measurement files
    local files_present=0
    for file in predictions.jsonl feedback.jsonl user_choices.jsonl budget_allocations.jsonl; do
        if [ -f "$measurement_today/$file" ]; then
            ((files_present++))
        fi
    done

    if [ "$files_present" -eq 4 ]; then
        log_status "ok" "All 4 measurement tracks active"
        return 0
    elif [ "$files_present" -gt 0 ]; then
        log_status "warn" "Only $files_present/4 tracks collecting data"
        return 0
    else
        log_status "warn" "No measurement files yet (no tasks executed?)"
        return 0
    fi
}

check_locks_healthy() {
    local stale_locks=$(find "$QUEUE_DIR" -name "*.lock" -mtime +0.5 -type f 2>/dev/null | wc -l)

    if [ "$stale_locks" -gt 0 ]; then
        log_status "warn" "$stale_locks stale lock files (manual cleanup may be needed)"
        return 0
    else
        log_status "ok" "No stale locks"
        return 0
    fi
}

check_disk_space() {
    local queue_size=$(du -sh "$QUEUE_DIR" 2>/dev/null | cut -f1 || echo "0B")
    local measurement_size=$(du -sh "$MEASUREMENT_DIR" 2>/dev/null | cut -f1 || echo "0B")

    log_status "ok" "Queue: $queue_size, Measurement: $measurement_size"
    return 0
}

check_log_errors() {
    local log_file="${CORVIN_HOME}/logs/session.log"

    if [ ! -f "$log_file" ]; then
        log_status "warn" "No session log yet"
        return 0
    fi

    local error_count=$(grep -c "ERROR\|CRITICAL" "$log_file" 2>/dev/null || echo 0)

    if [ "$error_count" -eq 0 ]; then
        log_status "ok" "No errors in logs"
        return 0
    else
        log_status "warn" "$error_count errors in logs (check session.log)"
        return 0
    fi
}

check_checkpoint_fresh() {
    local checkpoint_file="${QUEUE_DIR}/.checkpoint/aggregator.checkpoint.json"

    if [ ! -f "$checkpoint_file" ]; then
        log_status "warn" "No checkpoint yet (aggregation hasn't run)"
        return 0
    fi

    local mtime=$(stat -f %m "$checkpoint_file" 2>/dev/null || stat -c %Y "$checkpoint_file" 2>/dev/null || echo 0)
    local now=$(date +%s)
    local age=$((now - mtime))

    # Warn if checkpoint older than 25 hours (aggregation should run daily)
    if [ "$age" -gt 90000 ]; then
        log_status "warn" "Checkpoint is $(($age / 3600))h old (aggregation may have stalled)"
        return 0
    else
        log_status "ok" "Checkpoint fresh ($(($age / 3600))h old)"
        return 0
    fi
}

# ============================================================================
# Main Check Routine
# ============================================================================

run_checks() {
    echo ""
    echo -e "${BLUE}========== ADR-0274 HEALTH CHECK ==========${NC}"
    echo "Time: $(date)"
    echo ""

    local failed=0

    # Core service
    if ! check_service_running; then ((failed++)); fi

    # Queue + profiles
    if ! check_queue_health; then ((failed++)); fi
    if ! check_profiles_healthy; then ((failed++)); fi

    # Measurement
    if ! check_measurement_collecting; then ((failed++)); fi

    # Operational
    if ! check_locks_healthy; then ((failed++)); fi
    if ! check_disk_space; then ((failed++)); fi
    if ! check_log_errors; then ((failed++)); fi
    if ! check_checkpoint_fresh; then ((failed++)); fi

    echo ""
    if [ "$failed" -eq 0 ]; then
        echo -e "${GREEN}✓ All checks passed${NC}"
    else
        echo -e "${YELLOW}⚠ $failed check(s) need attention${NC}"
    fi
    echo ""
}

# ============================================================================
# Continuous Monitoring
# ============================================================================

if [ "$CONTINUOUS" = true ]; then
    echo "Starting continuous health monitoring (interval: ${INTERVAL}s)"
    while true; do
        run_checks
        echo "Next check in ${INTERVAL}s... (Ctrl+C to stop)"
        sleep "$INTERVAL"
    done
else
    run_checks
fi
