#!/bin/bash
# GitHub Pages Daily Export (ADR-0637)
#
# Exports learning metrics from the past 7 days, generates convergence plots,
# commits to corvin-stats repository on GitHub, and pushes live.
#
# Scheduled: 00:00 UTC daily via systemd timer
#
# Exit codes:
#   0 - Export, plot generation, and push succeeded
#   1 - Export or plot generation failed (non-fatal; metrics not exported this cycle)
#   2 - Script error (config, setup)
#   3 - Git push failed (data may be committed locally but not remote)

set -euo pipefail

# Configuration
CORVIN_HOME="${CORVIN_HOME:=$HOME/.corvin}"
TENANT_ID="${CORVIN_TENANT_ID:=_default}"
CONSOLE_URL="${CORVIN_CONSOLE_URL:=http://127.0.0.1:8765}"

# GitHub Pages repository (create if needed)
STATS_REPO="${CORVIN_STATS_REPO:=corvin-stats}"
STATS_REPO_PATH="${CORVIN_STATS_PATH:=$HOME/projects/corvin-stats}"
STATS_REMOTE="${CORVIN_STATS_REMOTE:=https://github.com/CorvinLabs/corvin-stats.git}"

# Logging
LOG_DIR="$CORVIN_HOME/learning/export_logs"
LOG_FILE="$LOG_DIR/export-$(date +%Y-%m-%d).log"
mkdir -p "$LOG_DIR"

log() {
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG_FILE"
}

error() {
    echo "[ERROR] $*" | tee -a "$LOG_FILE" >&2
}

# ============================================================================
# Step 1: Check dependencies
# ============================================================================

log "Starting GitHub Pages Daily Export (tenant=$TENANT_ID)"

# Verify curl
if ! command -v curl &> /dev/null; then
    error "curl not found in PATH"
    exit 2
fi

# Verify Python (for plot generation)
if ! command -v python3 &> /dev/null; then
    error "python3 not found in PATH"
    exit 2
fi

# Verify git
if ! command -v git &> /dev/null; then
    error "git not found in PATH"
    exit 2
fi

log "✓ All dependencies present (curl, python3, git)"

# ============================================================================
# Step 2: Initialize stats repository
# ============================================================================

if [ ! -d "$STATS_REPO_PATH" ]; then
    log "Cloning stats repository: $STATS_REMOTE"
    mkdir -p "$(dirname "$STATS_REPO_PATH")"
    if ! git clone "$STATS_REMOTE" "$STATS_REPO_PATH"; then
        error "Failed to clone stats repository"
        exit 2
    fi
    log "✓ Repository cloned"
else
    log "Repository already exists, updating..."
    cd "$STATS_REPO_PATH"
    if ! git pull origin main 2>&1 | tee -a "$LOG_FILE"; then
        error "Failed to pull latest changes"
        exit 3
    fi
fi

# ============================================================================
# Step 3: Query learning metrics (past 7 days)
# ============================================================================

METRICS_DIR="$STATS_REPO_PATH/metrics"
mkdir -p "$METRICS_DIR"

START_DATE=$(date -u -d '7 days ago' +'%Y-%m-%dT00:00:00Z')
END_DATE=$(date -u +'%Y-%m-%dT23:59:59Z')
EXPORT_STAMP=$(date -u +'%Y%m%dT%H%M%SZ')

log "Exporting metrics: $START_DATE to $END_DATE"

# Export as JSON (JSONL)
EXPORT_FILE="$METRICS_DIR/learning-events-${EXPORT_STAMP}.jsonl"

# Call export endpoint: POST /v1/console/learning/metrics/export
# (requires session auth; we assume the daemon has a valid session or uses a service token)
if ! curl -s -X POST "$CONSOLE_URL/v1/console/learning/metrics/export" \
    -H "Content-Type: application/json" \
    -d "{\"format\": \"json\", \"window\": \"custom\", \"start_date\": \"$START_DATE\", \"end_date\": \"$END_DATE\"}" \
    -b "$CORVIN_HOME/console_session.cookie" \
    -o "$EXPORT_FILE" 2>&1 | tee -a "$LOG_FILE"; then
    error "Failed to export metrics from $CONSOLE_URL"
    exit 1
fi

# Check if export succeeded (non-empty file)
if [ ! -s "$EXPORT_FILE" ]; then
    error "Export file is empty; endpoint may have returned an error"
    rm -f "$EXPORT_FILE"
    exit 1
fi

EXPORT_ROWS=$(wc -l < "$EXPORT_FILE")
log "✓ Exported $EXPORT_ROWS events to $EXPORT_FILE"

# Always keep latest.jsonl symlink updated
ln -sf "learning-events-${EXPORT_STAMP}.jsonl" "$METRICS_DIR/latest.jsonl"

# ============================================================================
# Step 4: Generate convergence plots (Python)
# ============================================================================

PLOT_SCRIPT="$CORVIN_HOME/../../../projects/CorvinOS/operator/scripts/generate_convergence_plots.py"

if [ ! -f "$PLOT_SCRIPT" ]; then
    error "Plot generation script not found: $PLOT_SCRIPT"
    exit 2
fi

log "Generating convergence plots..."

if ! python3 "$PLOT_SCRIPT" \
    --input "$EXPORT_FILE" \
    --output-dir "$METRICS_DIR" \
    --timestamp "$EXPORT_STAMP" \
    --log-file "$LOG_FILE"; then
    error "Plot generation failed"
    exit 1
fi

log "✓ Plots generated"

# ============================================================================
# Step 5: Commit and push to GitHub
# ============================================================================

cd "$STATS_REPO_PATH"

log "Staging metrics and plots for commit..."
git add metrics/ 2>&1 | tee -a "$LOG_FILE"

# Check if there are changes
if ! git diff --cached --quiet; then
    COMMIT_MSG="feat(metrics): daily export — $EXPORT_STAMP

Learning events: $EXPORT_ROWS (7-day window)
Generated plots: convergence, loss breakdown, skill accuracy
Dashboard: https://corvinlabs.github.io/corvin-stats/

Co-Authored-By: CorvinOS Export Bot <noreply@corvin.local>"

    log "Committing: $COMMIT_MSG"
    if ! git commit -m "$COMMIT_MSG" 2>&1 | tee -a "$LOG_FILE"; then
        error "Commit failed"
        exit 1
    fi

    log "Pushing to GitHub..."
    if ! git push origin main 2>&1 | tee -a "$LOG_FILE"; then
        error "Push to origin failed (changes committed locally but not remote)"
        exit 3
    fi

    log "✓ Push successful"
else
    log "No changes to commit (metrics unchanged since last export)"
fi

log "✅ Export cycle completed successfully"
exit 0
