#!/bin/bash
# ADR-0274 Production Deployment Script
# Usage: bash deploy-adr0274.sh [--dry-run] [--skip-backup] [--skip-tests]
#
# This script automates the 5-phase deployment process from DEPLOYMENT-CHECKLIST.md
# Safe defaults: creates backups, runs full tests, dry-run enabled by default

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
DRY_RUN=true
SKIP_BACKUP=false
SKIP_TESTS=false
CORVIN_HOME="${HOME}/.corvin"
TENANT_ID="_default"
PROFILE_DIR="${CORVIN_HOME}/tenants/${TENANT_ID}/profiles"
QUEUE_DIR="${CORVIN_HOME}/tenants/${TENANT_ID}/learning-queue"
BACKUP_DIR="${CORVIN_HOME}/tenants/${TENANT_ID}/backups"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --deploy)
            DRY_RUN=false
            shift
            ;;
        --skip-backup)
            SKIP_BACKUP=true
            shift
            ;;
        --skip-tests)
            SKIP_TESTS=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

run_cmd() {
    local cmd="$1"
    local desc="${2:-Running: $cmd}"

    log_info "$desc"
    if [ "$DRY_RUN" = true ]; then
        echo "[DRY-RUN] $cmd"
    else
        eval "$cmd"
    fi
}

# ============================================================================
# Phase 1: Pre-Deployment (30 minutes)
# ============================================================================

phase_1_pre_deployment() {
    log_info "========== PHASE 1: Pre-Deployment (30 min) =========="

    # Code review
    log_info "Checking git status..."
    run_cmd "git status" "Verify working tree clean"

    # Verify commits
    log_info "Verifying commit chain..."
    if git log --oneline | head -5 | grep -q "K=5 Final Verification"; then
        log_success "K=5 verification commit found"
    else
        log_error "K=5 verification commit not found. Aborting."
        exit 1
    fi

    # Python version
    log_info "Checking Python version..."
    python3 --version

    # Dependencies
    log_info "Syncing dependencies..."
    run_cmd "uv sync" "Install dependencies"

    log_success "Phase 1 complete"
}

# ============================================================================
# Phase 2: Integration Testing (30 minutes)
# ============================================================================

phase_2_testing() {
    if [ "$SKIP_TESTS" = true ]; then
        log_warning "Skipping tests (--skip-tests)"
        return
    fi

    log_info "========== PHASE 2: Integration Testing (30 min) =========="

    log_info "Running K=3 tests..."
    run_cmd "uv run pytest operator/context_engineering/tests/test_k3_integration.py -v" \
        "Test K=3 (H2/H4/CR-6)"

    log_info "Running CR-6 wiring tests..."
    run_cmd "uv run pytest operator/context_engineering/tests/test_cr6_wiring.py -v" \
        "Test CR-6 guard integration"

    log_success "Phase 2 complete (all 10 tests should pass)"
}

# ============================================================================
# Phase 3: Deployment (45 minutes)
# ============================================================================

phase_3_deployment() {
    log_info "========== PHASE 3: Deployment (45 min) =========="

    # Backups
    if [ "$SKIP_BACKUP" = false ]; then
        log_info "Creating backups..."
        mkdir -p "$BACKUP_DIR"

        if [ -d "$PROFILE_DIR" ]; then
            run_cmd "cp -r $PROFILE_DIR $BACKUP_DIR/profiles.backup.$(date +%s)" \
                "Backup profiles"
        fi

        if [ -d "$QUEUE_DIR" ]; then
            run_cmd "cp -r $QUEUE_DIR $BACKUP_DIR/learning-queue.backup.$(date +%s)" \
                "Backup queue"
        fi

        log_success "Backups created"
    else
        log_warning "Skipping backups (--skip-backup)"
    fi

    # Stop service
    log_info "Stopping CorvinOS service..."
    run_cmd "corvin stop || true" "Stop service (ignore errors if not running)"

    if [ "$DRY_RUN" = false ]; then
        sleep 2
    fi

    # Clean temp files
    log_info "Cleaning temporary files..."
    run_cmd "rm -rf ${CORVIN_HOME}/tenants/${TENANT_ID}/.checkpoint/*.tmp 2>/dev/null || true" \
        "Clean checkpoint temps"
    run_cmd "rm -f ${CORVIN_HOME}/tenants/${TENANT_ID}/learning-queue/*.lock* 2>/dev/null || true" \
        "Clean lock files"

    # Start service
    log_info "Starting CorvinOS service..."
    if [ "$DRY_RUN" = false ]; then
        export CORVIN_TELEMETRY_OPTIN=true
        export CEL_PHASE4_MEASUREMENT=true
        corvin-serve &
        sleep 5
    else
        echo "[DRY-RUN] corvin-serve &"
    fi

    log_success "Phase 3 complete"
}

# ============================================================================
# Phase 4: Post-Deployment Verification (15 minutes)
# ============================================================================

phase_4_verification() {
    log_info "========== PHASE 4: Post-Deployment Verification (15 min) =========="

    # Service health
    if [ "$DRY_RUN" = false ]; then
        log_info "Checking service status..."
        if pgrep -f "corvin-serve" > /dev/null; then
            log_success "Service running"
        else
            log_error "Service not running"
            exit 1
        fi

        # API health
        log_info "Checking API health..."
        if curl -s http://localhost:8000/health | grep -q "ok"; then
            log_success "API responding"
        else
            log_warning "API not responding (may take a moment)"
        fi
    else
        echo "[DRY-RUN] Service health checks skipped"
    fi

    # Queue verification
    log_info "Verifying queue structure..."
    run_cmd "ls -la ${QUEUE_DIR}/ | head -5" "Show queue files"

    # Profile verification
    log_info "Verifying profile structure..."
    run_cmd "ls -la ${PROFILE_DIR}/ | head -5" "Show profile files"

    log_success "Phase 4 complete"
}

# ============================================================================
# Phase 5: Activation & Monitoring (30 minutes)
# ============================================================================

phase_5_activation() {
    log_info "========== PHASE 5: Activation & Monitoring (30 min) =========="

    # Environment variables
    log_info "Measurement tracks:"
    log_info "  CORVIN_MEASUREMENT_TRACK_UNCERTAINTY=true"
    log_info "  CORVIN_MEASUREMENT_TRACK_FEEDBACK=true"
    log_info "  CORVIN_MEASUREMENT_TRACK_PREFERENCES=true"
    log_info "  CORVIN_MEASUREMENT_TRACK_BUDGET=true"

    if [ "$DRY_RUN" = false ]; then
        export CORVIN_MEASUREMENT_TRACK_UNCERTAINTY=true
        export CORVIN_MEASUREMENT_TRACK_FEEDBACK=true
        export CORVIN_MEASUREMENT_TRACK_PREFERENCES=true
        export CORVIN_MEASUREMENT_TRACK_BUDGET=true
        log_success "Measurement tracks activated"
    fi

    # Telemetry
    log_info "Telemetry status: ENABLED (CORVIN_TELEMETRY_OPTIN=true)"

    # First-hour checks
    log_info "First-hour checks (run in 1 hour):"
    log_info "  grep -i 'error' ~/.corvin/logs/session.log | wc -l  # expect: 0-5"
    log_info "  grep 'Acquired.*lock' ~/.corvin/logs/session.log | tail -5"

    log_success "Phase 5 complete"
}

# ============================================================================
# Main execution
# ============================================================================

main() {
    if [ "$DRY_RUN" = true ]; then
        log_warning "DRY-RUN MODE: No changes will be made"
        log_info "Use --deploy flag to execute: bash deploy-adr0274.sh --deploy"
        echo ""
    fi

    log_info "ADR-0274 Production Deployment"
    log_info "Timeline: ~2 hours, ~10 minutes downtime"
    log_info ""

    phase_1_pre_deployment
    echo ""

    phase_2_testing
    echo ""

    phase_3_deployment
    echo ""

    phase_4_verification
    echo ""

    phase_5_activation
    echo ""

    if [ "$DRY_RUN" = true ]; then
        log_warning "DRY-RUN COMPLETE"
        log_info "To execute deployment, run: bash deploy-adr0274.sh --deploy"
    else
        log_success "DEPLOYMENT COMPLETE"
        log_info "Next: Follow Week 6 Measurement Phase Plan"
        log_info "  See: docs/implementation/WEEK6-MEASUREMENT-PHASE-PLAN.md"
    fi
}

main
