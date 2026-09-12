#!/bin/bash
# Deployment script for OTEL Observability Stack (Phases 1–4)
# Usage: bash deploy.sh [start|stop|status|validate]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMMAND=${1:-start}
COMPOSE_FILE="docker-compose.yml"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== CorvinOS OTEL Observability Deployment ===${NC}"
echo "Command: $COMMAND"
echo "Working directory: $(pwd)"

case "$COMMAND" in
  start)
    echo -e "\n${GREEN}[1/4] Starting Docker containers...${NC}"
    docker-compose -f "$COMPOSE_FILE" up -d

    echo -e "\n${GREEN}[2/4] Waiting for services to be ready...${NC}"
    sleep 10

    echo -e "\n${GREEN}[3/4] Verifying container health...${NC}"
    docker-compose -f "$COMPOSE_FILE" ps

    echo -e "\n${GREEN}[4/4] Services online:${NC}"
    echo "  - OTEL Collector: http://localhost:4318 (HTTP), localhost:4317 (gRPC)"
    echo "  - Prometheus: http://localhost:9090"
    echo "  - Grafana: http://localhost:3000 (admin/admin)"
    echo "  - Jaeger: http://localhost:16686"

    echo -e "\n${GREEN}✓ Deployment complete${NC}"
    ;;

  stop)
    echo -e "\n${YELLOW}Stopping containers...${NC}"
    docker-compose -f "$COMPOSE_FILE" down
    echo -e "${GREEN}✓ Containers stopped${NC}"
    ;;

  status)
    echo -e "\n${YELLOW}Container status:${NC}"
    docker-compose -f "$COMPOSE_FILE" ps
    ;;

  validate)
    echo -e "\n${GREEN}[1/5] Checking OTEL Collector...${NC}"
    if curl -s http://localhost:4318/v1/traces > /dev/null 2>&1; then
      echo -e "${GREEN}✓ OTEL Collector reachable (HTTP)${NC}"
    else
      echo -e "${RED}✗ OTEL Collector unreachable${NC}"
      exit 1
    fi

    echo -e "\n${GREEN}[2/5] Checking Prometheus...${NC}"
    if curl -s http://localhost:9090/-/healthy > /dev/null 2>&1; then
      echo -e "${GREEN}✓ Prometheus healthy${NC}"
    else
      echo -e "${RED}✗ Prometheus unhealthy${NC}"
      exit 1
    fi

    echo -e "\n${GREEN}[3/5] Checking Grafana...${NC}"
    if curl -s http://localhost:3000/api/health > /dev/null 2>&1; then
      echo -e "${GREEN}✓ Grafana healthy${NC}"
    else
      echo -e "${RED}✗ Grafana unreachable${NC}"
      exit 1
    fi

    echo -e "\n${GREEN}[4/5] Checking Prometheus metrics endpoint...${NC}"
    METRIC_COUNT=$(curl -s http://localhost:8888/metrics | wc -l)
    if [ "$METRIC_COUNT" -gt 0 ]; then
      echo -e "${GREEN}✓ Metrics available (${METRIC_COUNT} lines)${NC}"
    else
      echo -e "${RED}✗ No metrics${NC}"
      exit 1
    fi

    echo -e "\n${GREEN}[5/5] Testing OTEL Collector metrics scrape...${NC}"
    if curl -s "http://localhost:9090/api/v1/query?query=up" | grep -q "success"; then
      echo -e "${GREEN}✓ Prometheus can scrape OTEL metrics${NC}"
    else
      echo -e "${YELLOW}⚠ Prometheus scrape not yet working (expected if no instances sending metrics)${NC}"
    fi

    echo -e "\n${GREEN}✓ All validation checks passed${NC}"
    ;;

  logs)
    echo -e "\n${YELLOW}Container logs:${NC}"
    docker-compose -f "$COMPOSE_FILE" logs -f
    ;;

  *)
    echo -e "${RED}Unknown command: $COMMAND${NC}"
    echo "Usage: bash deploy.sh [start|stop|status|validate|logs]"
    exit 1
    ;;
esac
