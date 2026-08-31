#!/bin/bash
# Manual deployment script for CorvinOS Stats Dashboard
# Usage: ./scripts/deploy-stats-manual.sh [up|down|restart|logs]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.stats.yml"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

log_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

log_error() {
    echo -e "${RED}❌ $1${NC}"
}

log_warn() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."

    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed"
        exit 1
    fi
    log_success "Docker found: $(docker --version)"

    if ! command -v docker-compose &> /dev/null; then
        log_error "Docker Compose is not installed"
        exit 1
    fi
    log_success "Docker Compose found: $(docker-compose --version)"

    if [ ! -f "$COMPOSE_FILE" ]; then
        log_error "docker-compose.stats.yml not found at $COMPOSE_FILE"
        exit 1
    fi
    log_success "Docker Compose file found"
}

# Deploy (start containers)
deploy() {
    log_info "🚀 Deploying CorvinOS Stats Dashboard..."

    cd "$PROJECT_ROOT"

    log_info "Building Docker image..."
    docker-compose -f docker-compose.stats.yml build --no-cache

    log_info "Starting containers..."
    docker-compose -f docker-compose.stats.yml up -d

    # Wait for containers to be healthy
    log_info "Waiting for services to be ready..."
    sleep 5

    # Check if containers are running
    if docker-compose -f docker-compose.stats.yml ps | grep -q "Up"; then
        log_success "All containers are running"
    else
        log_error "Some containers failed to start"
        docker-compose -f docker-compose.stats.yml logs
        exit 1
    fi

    # Wait a bit more for services to fully initialize
    sleep 3

    # Health checks
    log_info "Running health checks..."

    # Check stats dashboard
    if curl -s http://localhost/health | grep -q "OK"; then
        log_success "Stats dashboard is healthy"
    else
        log_error "Stats dashboard health check failed"
    fi

    # Check mock API
    if curl -s http://localhost:8000/health | grep -q "OK"; then
        log_success "Mock API is healthy"
    else
        log_error "Mock API health check failed"
    fi

    # Show access information
    log_success "✨ Deployment complete!"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  📊 Stats Dashboard: http://localhost/stats"
    echo "  🔌 API Endpoint:    http://localhost:8000/api/metrics/stats"
    echo "  🩺 Health Check:    http://localhost/health"
    echo "  📈 Prometheus:      http://localhost:9090"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "Next steps:"
    echo "  1. Open http://localhost/stats in your browser"
    echo "  2. Watch live token metrics update every 5 seconds"
    echo "  3. View logs: docker-compose -f docker-compose.stats.yml logs -f"
    echo ""
}

# Stop containers
teardown() {
    log_info "🛑 Stopping CorvinOS Stats Dashboard..."

    cd "$PROJECT_ROOT"
    docker-compose -f docker-compose.stats.yml down

    log_success "Containers stopped"
}

# Show logs
show_logs() {
    log_info "📋 Showing container logs..."
    cd "$PROJECT_ROOT"
    docker-compose -f docker-compose.stats.yml logs -f
}

# Restart containers
restart() {
    log_info "🔄 Restarting CorvinOS Stats Dashboard..."

    cd "$PROJECT_ROOT"
    docker-compose -f docker-compose.stats.yml restart

    log_success "Containers restarted"
    sleep 3

    # Show status
    docker-compose -f docker-compose.stats.yml ps
}

# Show status
status() {
    log_info "📊 Container Status:"
    cd "$PROJECT_ROOT"
    docker-compose -f docker-compose.stats.yml ps
}

# Main
main() {
    local command="${1:-up}"

    check_prerequisites

    case "$command" in
        up|deploy)
            deploy
            ;;
        down|stop)
            teardown
            ;;
        restart)
            restart
            ;;
        logs)
            show_logs
            ;;
        status)
            status
            ;;
        *)
            log_error "Unknown command: $command"
            echo ""
            echo "Usage: $0 [up|down|restart|logs|status]"
            echo ""
            echo "Commands:"
            echo "  up (deploy)  - Start all containers"
            echo "  down (stop)  - Stop all containers"
            echo "  restart      - Restart all containers"
            echo "  logs         - Show container logs (tail -f)"
            echo "  status       - Show container status"
            exit 1
            ;;
    esac
}

main "$@"
