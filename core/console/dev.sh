#!/bin/bash
# Development startup script for Corvin Console
# Runs Vite dev server (frontend) + Uvicorn (backend) with auto-reload

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_NEXT_DIR="$SCRIPT_DIR/corvin_console/web-next"
CONSOLE_DIR="$SCRIPT_DIR/corvin_console"

echo "🚀 Corvin Console Development"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Frontend: $WEB_NEXT_DIR"
echo "Backend:  $CONSOLE_DIR"
echo ""

# Start Vite dev server (frontend with hot reload)
echo "📦 Starting Vite dev server (http://localhost:5173)..."
cd "$WEB_NEXT_DIR"
npm run dev &
VITE_PID=$!

# Give Vite a moment to start
sleep 2

# Start Uvicorn with reload that includes web-next/dist
echo "🔧 Starting Uvicorn (http://localhost:8765/console)..."
cd "$CONSOLE_DIR/.."
python -m uvicorn corvin_gateway.app:app \
  --host 127.0.0.1 \
  --port 8765 \
  --log-level info \
  --ws-ping-interval 20 \
  --ws-ping-timeout 30 \
  --reload \
  --reload-dir ./console \
  --reload-dir ./gateway &
UVICORN_PID=$!

echo ""
echo "✅ Development servers started!"
echo "   Frontend:  http://localhost:5173"
echo "   Backend:   http://localhost:8765/v1/console"
echo "   Console:   http://localhost:8765/console"
echo ""
echo "Watching for changes in:"
echo "   • ./corvin_console/ (Python files → Uvicorn reload)"
echo "   • ./web-next/src/ (TypeScript/React → Vite HMR)"
echo ""
echo "Press Ctrl+C to stop both servers"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Handle Ctrl+C to kill both processes
trap 'kill $VITE_PID $UVICORN_PID 2>/dev/null; exit' INT TERM

# Wait for both to finish
wait
