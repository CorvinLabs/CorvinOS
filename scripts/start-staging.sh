#!/bin/bash
# Start TaskEngine staging infrastructure (Prometheus + AlertManager + TaskEngine Server)

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "🚀 Starting TaskEngine Staging Infrastructure..."
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check prerequisites
echo "📋 Checking prerequisites..."

if ! command -v prometheus &> /dev/null; then
    echo -e "${RED}❌ prometheus not found. Install: brew install prometheus (macOS) or apt-get install prometheus (Linux)${NC}"
    exit 1
fi

if ! command -v alertmanager &> /dev/null; then
    echo -e "${RED}❌ alertmanager not found. Install: brew install alertmanager (macOS) or apt-get install alertmanager (Linux)${NC}"
    exit 1
fi

echo -e "${GREEN}✅ prometheus and alertmanager found${NC}"
echo ""

# Create log directory
mkdir -p logs/staging

# Start Prometheus
echo "📊 Starting Prometheus..."
prometheus \
    --config.file=config/prometheus-staging.yml \
    --storage.tsdb.path=logs/staging/prometheus \
    --web.listen-address=:9090 \
    > logs/staging/prometheus.log 2>&1 &
PROMETHEUS_PID=$!
echo -e "${GREEN}✅ Prometheus started (PID: $PROMETHEUS_PID)${NC}"
echo "   Dashboard: http://localhost:9090"
sleep 2

# Start AlertManager
echo "📢 Starting AlertManager..."
alertmanager \
    --config.file=config/alertmanager-staging.yml \
    --storage.path=logs/staging/alertmanager \
    --web.listen-address=:9093 \
    > logs/staging/alertmanager.log 2>&1 &
ALERTMANAGER_PID=$!
echo -e "${GREEN}✅ AlertManager started (PID: $ALERTMANAGER_PID)${NC}"
echo "   Dashboard: http://localhost:9093"
sleep 2

# Start TaskEngine server
# Note: Can't use -m operator.task_analysis due to stdlib 'operator' module conflict
# Use standalone script instead
echo "🔧 Starting TaskEngine server..."
uv run python scripts/taskengine-server.py \
    --host localhost \
    --port 8765 \
    --log-level DEBUG \
    > logs/staging/taskengine.log 2>&1 &
TASKENGINE_PID=$!
echo -e "${GREEN}✅ TaskEngine server started (PID: $TASKENGINE_PID)${NC}"
echo "   API: http://localhost:8765"
sleep 2

echo ""
echo "================================"
echo -e "${GREEN}✅ Staging Infrastructure Ready${NC}"
echo "================================"
echo ""
echo "Endpoints:"
echo "  TaskEngine:  http://localhost:8765"
echo "    POST /analyze       — Route a task"
echo "    GET  /health       — Health check"
echo "    GET  /metrics      — Prometheus metrics"
echo ""
echo "  Prometheus: http://localhost:9090"
echo "  AlertManager: http://localhost:9093"
echo ""
echo "Logs:"
echo "  TaskEngine:   logs/staging/taskengine.log"
echo "  Prometheus:   logs/staging/prometheus.log"
echo "  AlertManager: logs/staging/alertmanager.log"
echo ""
echo "Next steps:"
echo "  1. Expand staging/test_data.json to 50 tasks"
echo "  2. Run staging validation:"
echo "     python -c \"from operator.task_analysis.staging_harness import StagingHarness; StagingHarness().run()\""
echo "  3. Monitor dashboards:"
echo "     - Prometheus: http://localhost:9090/graph"
echo "     - Alerts: http://localhost:9093"
echo ""
echo "To stop all services, run:"
echo "  kill $PROMETHEUS_PID $ALERTMANAGER_PID $TASKENGINE_PID"
echo ""

# Trap for cleanup on exit
cleanup() {
    echo ""
    echo "🛑 Stopping infrastructure..."
    kill $PROMETHEUS_PID 2>/dev/null || true
    kill $ALERTMANAGER_PID 2>/dev/null || true
    kill $TASKENGINE_PID 2>/dev/null || true
    echo -e "${GREEN}✅ All services stopped${NC}"
}

trap cleanup EXIT

# Keep script running
wait
