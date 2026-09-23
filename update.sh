#!/bin/bash
# update.sh — CorvinOS updater for Linux and macOS.
#
# Usage:
#   sh update.sh                              # update and rebuild, restart console
#   sh update.sh --no-restart                 # update and rebuild, don't restart
#   sh update.sh --console-only               # rebuild console UI only (no git pull)
#   sh update.sh --force                      # force rebuild even if unchanged
#
# POSIX sh, self-contained. Requires curl/wget and Git for remote updates.
set -eu

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
CONSOLE_PORT="${CORVIN_CONSOLE_PORT:-8765}"
CORVIN_REPO="${CORVIN_REPO:-.}"
UPDATE_LOG="${CORVIN_UPDATE_LOG:-${TMPDIR:-/tmp}/corvinos-update.log}"
NO_RESTART=0
CONSOLE_ONLY=0
FORCE_BUILD=0

# ─────────────────────────────────────────────────────────────────────────────
# Styling utilities
# ─────────────────────────────────────────────────────────────────────────────
_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_green() { printf '\033[32m%s\033[0m' "$*"; }
_red()   { printf '\033[31m%s\033[0m' "$*"; }
_yellow(){ printf '\033[33m%s\033[0m' "$*"; }
_dim()   { printf '\033[2m%s\033[0m' "$*"; }

die() { printf '%s %s\n' "$(_red 'Error:')" "$*" >&2; exit 1; }

_await() {
    _aw_msg="$1"; shift
    printf '  %s %s ' "$(_dim '⏳')" "$_aw_msg"
    printf '\n=== %s: %s ===\n' "$(date '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || echo now)" "$_aw_msg" >>"$UPDATE_LOG" 2>/dev/null || true
    "$@" </dev/null >>"$UPDATE_LOG" 2>&1 &
    _aw_pid=$!
    while kill -0 "$_aw_pid" 2>/dev/null; do printf '.'; sleep 1; done
    if wait "$_aw_pid"; then printf ' %s\n' "$(_green '✓')"; return 0
    else printf ' %s  %s\n' "$(_yellow '⚠')" "$(_dim "(details: $UPDATE_LOG)")"; return 1; fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
    case "$1" in
        --no-restart) NO_RESTART=1; shift ;;
        --console-only) CONSOLE_ONLY=1; shift ;;
        --force) FORCE_BUILD=1; shift ;;
        *)
            die "Unknown argument: $1
Usage: $0 [--no-restart] [--console-only] [--force]" ;;
    esac
done

printf '\n%s\n\n' "$(_bold 'CorvinOS updater')"

# ─────────────────────────────────────────────────────────────────────────────
# Pre-flight checks
# ─────────────────────────────────────────────────────────────────────────────
if [ ! -d "$CORVIN_REPO/.git" ] && [ "$CONSOLE_ONLY" != "1" ]; then
    die "$CORVIN_REPO is not a git repository. Use --console-only to rebuild just the console UI."
fi

if [ ! -f "$CORVIN_REPO/scripts/console-deploy.sh" ]; then
    die "console-deploy.sh not found at $CORVIN_REPO/scripts/"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Git update (unless --console-only)
# ─────────────────────────────────────────────────────────────────────────────
if [ "$CONSOLE_ONLY" != "1" ]; then
    echo "  Fetching latest changes from origin/main ..."
    cd "$CORVIN_REPO" || die "Cannot cd to $CORVIN_REPO"

    if ! _await "Fetching origin" git fetch origin main; then
        printf '  %s Git fetch failed — %s\n' "$(_yellow '⚠')" "check network and retry"
        exit 1
    fi

    CURRENT_HASH="$(git rev-parse HEAD 2>/dev/null || echo 'unknown')"
    LATEST_HASH="$(git rev-parse origin/main 2>/dev/null || echo 'unknown')"

    if [ "$CURRENT_HASH" = "$LATEST_HASH" ]; then
        printf '  %s Already up-to-date (commit %s)\n' "$(_green '✓')" "${LATEST_HASH:0:8}"
    else
        printf '  %s New commits available (%s → %s). Pulling...\n' "$(_yellow 'ℹ')" "${CURRENT_HASH:0:8}" "${LATEST_HASH:0:8}"
        if ! _await "Pulling latest main" git pull origin main --ff-only; then
            printf '  %s Git pull failed — check for local changes\n' "$(_red 'Error')"
            exit 1
        fi
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Stop console server (gracefully)
# ─────────────────────────────────────────────────────────────────────────────
printf '\n  Checking for running console server...\n'
RUNNING_PIDS=""
if command -v pgrep >/dev/null 2>&1; then
    RUNNING_PIDS="$(pgrep -f 'corvinos-serve|corvin_gateway' 2>/dev/null || true)"
fi

if [ -n "$RUNNING_PIDS" ]; then
    printf '  %s Found running process(es): %s\n' "$(_yellow 'ℹ')" "$RUNNING_PIDS"
    printf '  %s Stopping console gracefully...\n' "$(_dim '⏳')"

    for pid in $RUNNING_PIDS; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            # Wait up to 5 seconds for clean shutdown
            for i in $(seq 1 5); do
                if ! kill -0 "$pid" 2>/dev/null; then
                    printf '  %s Process %d stopped.\n' "$(_green '✓')" "$pid"
                    break
                fi
                sleep 1
            done
            # Force kill if still running
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
                printf '  %s Process %d force-killed.\n' "$(_yellow '⚠')" "$pid"
            fi
        fi
    done
else
    printf '  %s No running console found.\n' "$(_green '✓')"
fi

sleep 1  # Brief pause to ensure ports are released

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Rebuild console UI
# ─────────────────────────────────────────────────────────────────────────────
printf '\n  Preparing console build...\n'

CONSOLE_DEPLOY="${CORVIN_REPO}/scripts/console-deploy.sh"
if [ ! -x "$CONSOLE_DEPLOY" ]; then
    chmod +x "$CONSOLE_DEPLOY" || die "Cannot make console-deploy.sh executable"
fi

BUILD_ARGS=""
if [ "$FORCE_BUILD" = "1" ]; then
    BUILD_ARGS="--force"
fi

if ! _await "Building console UI" bash "$CONSOLE_DEPLOY" $BUILD_ARGS; then
    printf '%s — Console build failed. Check the output above.\n' "$(_red 'Error')"
    exit 1
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Refresh Python dependencies (if git pull happened)
# ─────────────────────────────────────────────────────────────────────────────
if [ "$CONSOLE_ONLY" != "1" ] && command -v uv >/dev/null 2>&1; then
    printf '\n  Refreshing Python dependencies...\n'
    if ! _await "Syncing dependencies" uv pip sync --refresh --quiet; then
        printf '  %s Dependency sync had issues, but continuing anyway\n' "$(_yellow '⚠')"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 5: Restart console (unless --no-restart)
# ─────────────────────────────────────────────────────────────────────────────
if [ "$NO_RESTART" != "1" ]; then
    printf '\n  Starting CorvinOS console...\n'

    if ! command -v corvinos-serve >/dev/null 2>&1; then
        die "corvinos-serve not found on PATH. Run: uv tool upgrade corvinos"
    fi

    nohup corvinos-serve >/dev/null 2>&1 &
    NEW_PID=$!
    printf '  %s Console started (PID %d)\n' "$(_green '✓')" "$NEW_PID"

    # ─────────────────────────────────────────────────────────────────────
    # Phase 5a: Health check with exponential backoff (ADR-0867)
    # ─────────────────────────────────────────────────────────────────────
    printf '  %s Waiting for console to come up ' "$(_dim '⏳')"

    MAX_RETRIES=60
    RETRY_COUNT=0
    BACKOFF=1
    SERVER_READY=0

    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -fs -m 2 "http://localhost:${CONSOLE_PORT}/v1/console/healthz" >/dev/null 2>&1; then
            printf ' %s Server is ready! (%ds)\n' "$(_green '✓')" "$RETRY_COUNT"
            SERVER_READY=1
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        printf '.'
        sleep "$BACKOFF"
        if [ $BACKOFF -lt 8 ]; then
            BACKOFF=$((BACKOFF * 2))
        fi
    done

    if [ "$SERVER_READY" != "1" ]; then
        printf '\n  %s Server did not respond after %ds. It may still be starting...\n' "$(_yellow '⚠')" "$MAX_RETRIES"
        printf '  %s Open the console: http://localhost:%d/console/\n' "$(_yellow 'ℹ')" "$CONSOLE_PORT"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 6: Summary
# ─────────────────────────────────────────────────────────────────────────────
cat <<EOF

$(_bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
 $(_green "$(_bold 'CorvinOS update complete!')")
$(_bold "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

EOF

if [ "$NO_RESTART" = "1" ]; then
    printf '  Console build complete but not restarted.\n'
    printf '  Start it with: %s\n\n' "$(_bold 'corvinos-serve')"
else
    printf '  Console is running at: %s\n\n' "$(_bold "http://localhost:${CONSOLE_PORT}/console/")"
fi

printf '  %s (updates: %s)\n\n' "$(_dim 'Details:' )" "$UPDATE_LOG"
