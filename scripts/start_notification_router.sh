#!/bin/bash
# Start NotificationRouter daemon — delivers CompletionEvents to Discord (ADR-0661)
# Usage: ./scripts/start_notification_router.sh [start|stop|status]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PID_FILE="${HOME}/.corvin/notification_router_minimal.pid"
LOG_FILE="${HOME}/.corvin/logs/notification_router.log"

mkdir -p "$(dirname "$LOG_FILE")"

start_router() {
    if [ -f "$PID_FILE" ]; then
        existing_pid=$(cat "$PID_FILE")
        if kill -0 "$existing_pid" 2>/dev/null; then
            echo "✓ NotificationRouter already running (PID: $existing_pid)"
            return 0
        fi
    fi

    echo "🚀 Starting NotificationRouter (minimal: pure stdlib)..."
    python3 "$SCRIPT_DIR/notification_router_minimal.py" > "$LOG_FILE" 2>&1 &

    ROUTER_PID=$!
    echo "$ROUTER_PID" > "$PID_FILE"
    echo "✓ NotificationRouter started (PID: $ROUTER_PID)"
    echo "  Log: $LOG_FILE"
    sleep 2
    tail -5 "$LOG_FILE" || true
}

stop_router() {
    if [ ! -f "$PID_FILE" ]; then
        echo "✓ NotificationRouter not running"
        return 0
    fi

    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
        echo "⏹️  Stopping NotificationRouter (PID: $pid)..."
        kill "$pid"
        rm -f "$PID_FILE"
        echo "✓ Stopped"
    else
        echo "✓ NotificationRouter not running (PID file stale)"
        rm -f "$PID_FILE"
    fi
}

status_router() {
    if [ ! -f "$PID_FILE" ]; then
        echo "❌ NotificationRouter not running"
        return 1
    fi

    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
        echo "✅ NotificationRouter running (PID: $pid)"
        echo "   Log tail:"
        tail -10 "$LOG_FILE" | sed 's/^/   /' || true
        return 0
    else
        echo "❌ NotificationRouter not running (PID file stale)"
        rm -f "$PID_FILE"
        return 1
    fi
}

case "${1:-start}" in
    start) start_router ;;
    stop)  stop_router ;;
    status) status_router ;;
    *) echo "Usage: $0 {start|stop|status}"; exit 1 ;;
esac
