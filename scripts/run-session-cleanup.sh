#!/bin/bash
# CorvinOS Session Cleanup Script
# Runs daily via systemd timer to clean up expired sessions
# Invoked by: corvin-session-cleanup.service (systemd)

set -euo pipefail

CORVIN_HOME="${CORVIN_HOME:-$HOME/.corvin}"
SESSIONS_DIR="$CORVIN_HOME/global/console/sessions"
LOG_FILE="$CORVIN_HOME/logs/session-cleanup.log"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Log function
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "🧹 Starting session cleanup (CORVIN_HOME=$CORVIN_HOME)"

# Check if sessions directory exists
if [[ ! -d "$SESSIONS_DIR" ]]; then
    log "⚠️  Sessions directory not found: $SESSIONS_DIR (skipping cleanup)"
    exit 0
fi

# Count files before cleanup
BEFORE=$(find "$SESSIONS_DIR" -name "*.json" -type f 2>/dev/null | wc -l)
log "📊 Sessions before cleanup: $BEFORE"

# Delete sessions older than 24 hours (except those actively in use)
# Note: This is a simple approach that deletes based on file mtime.
# The more sophisticated approach (checking is_alive() on each) happens
# via SessionManager.cleanup_expired_sessions() during app shutdown.
find "$SESSIONS_DIR" -name "*.json" -type f -mtime +1 -delete 2>/dev/null || true

# Count files after cleanup
AFTER=$(find "$SESSIONS_DIR" -name "*.json" -type f 2>/dev/null | wc -l)
DELETED=$((BEFORE - AFTER))

log "📊 Sessions after cleanup: $AFTER (deleted: $DELETED)"

# Optional: Compact the sessions directory (re-compress if using compression)
# (This is a placeholder for future optimization)

log "✅ Session cleanup complete"
exit 0
