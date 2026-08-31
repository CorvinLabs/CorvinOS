#!/bin/bash
# CorvinOS Console Production Startup Script
# Properly configures Python environment and starts Console API server

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="$PROJECT_ROOT/.venv"
LOG_FILE="${LOG_FILE:-/tmp/corvin-console-startup.log}"
PORT="${PORT:-8765}"
HOST="${HOST:-127.0.0.1}"

# Ensure venv exists
if [ ! -d "$VENV_PATH" ]; then
    echo "ERROR: Virtual environment not found at $VENV_PATH"
    echo "Run: python3 -m venv $VENV_PATH"
    exit 1
fi

# Activate venv
source "$VENV_PATH/bin/activate"

# Install dependencies if missing
if ! python -c "import fastapi" 2>/dev/null; then
    echo "Installing dependencies..."
    pip install -q -r core/console/requirements.txt
fi

# Configure Python environment
export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export CORVIN_TENANT_ID="_default"
export CORVIN_HOME="${CORVIN_HOME:-$HOME/.corvin}"

# Verify Python environment
echo "=== Python Environment ===" | tee -a "$LOG_FILE"
echo "Python: $(python --version)" | tee -a "$LOG_FILE"
echo "venv: $VIRTUAL_ENV" | tee -a "$LOG_FILE"
echo "PYTHONPATH: $PYTHONPATH" | tee -a "$LOG_FILE"

# Verify imports work
echo "=== Verifying Imports ===" | tee -a "$LOG_FILE"
python -c "from core.console.corvin_console import app; print('✅ Console imports OK')" | tee -a "$LOG_FILE" || {
    echo "❌ Import failed" | tee -a "$LOG_FILE"
    exit 1
}

# Stop any existing process on same port
pkill -f "uvicorn.*:$PORT" 2>/dev/null || true

# Start Console
echo "=== Starting Console ===" | tee -a "$LOG_FILE"
echo "Listening on http://$HOST:$PORT" | tee -a "$LOG_FILE"

exec uvicorn \
    core.console.corvin_console.app:app \
    --host "$HOST" \
    --port "$PORT" \
    --log-level info \
    --timeout-graceful-shutdown 10 \
    2>&1 | tee -a "$LOG_FILE"
