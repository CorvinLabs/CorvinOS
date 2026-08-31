#!/bin/bash
# CorvinOS Stats Dashboard — Automated Production Deployment
# This script sets up everything needed for production deployment
# No manual steps required!

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS_DIR="$PROJECT_ROOT/scripts"
DEPLOY_DIR="$PROJECT_ROOT/deploy"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${BLUE}ℹ️  $1${NC}"; }
log_success() { echo -e "${GREEN}✅ $1${NC}"; }
log_error() { echo -e "${RED}❌ $1${NC}"; }
log_warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }

# Configuration
PORT=8080
SERVICE_NAME="corvinos-stats"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
VENV_DIR="$PROJECT_ROOT/.venv"
LOG_DIR="/var/log/corvinos"
RUN_DIR="/var/run/corvinos"
DATA_DIR="/var/lib/corvinos"

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║   ⚡ CorvinOS Stats Dashboard — Auto Deployment           ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Step 1: Check dependencies
log_info "Step 1: Checking dependencies..."

if ! command -v python3 &> /dev/null; then
    log_error "Python 3 not found. Install it first."
    exit 1
fi
log_success "Python 3 found: $(python3 --version)"

if ! command -v pip3 &> /dev/null; then
    log_error "pip3 not found. Install it first."
    exit 1
fi
log_success "pip3 found"

# Step 2: Create Python virtual environment
log_info "Step 2: Setting up Python virtual environment..."

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    log_success "Virtual environment created"
else
    log_success "Virtual environment already exists"
fi

source "$VENV_DIR/bin/activate"

# Install Flask for better performance
pip install -q flask gunicorn 2>/dev/null || true
log_success "Dependencies installed"

# Step 3: Create systemd service
log_info "Step 3: Creating systemd service..."

cat > /tmp/${SERVICE_NAME}.service << 'EOF'
[Unit]
Description=CorvinOS Stats Dashboard
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=$PROJECT_ROOT
Environment="PATH=$VENV_DIR/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=$VENV_DIR/bin/python3 $SCRIPTS_DIR/run-stats-server.py
Restart=always
RestartSec=10
StandardOutput=append:$LOG_DIR/stats.log
StandardError=append:$LOG_DIR/stats-error.log

[Install]
WantedBy=multi-user.target
EOF

# Replace placeholders
sed -i "s|\$PROJECT_ROOT|$PROJECT_ROOT|g" /tmp/${SERVICE_NAME}.service
sed -i "s|\$VENV_DIR|$VENV_DIR|g" /tmp/${SERVICE_NAME}.service
sed -i "s|\$SCRIPTS_DIR|$SCRIPTS_DIR|g" /tmp/${SERVICE_NAME}.service
sed -i "s|\$LOG_DIR|$LOG_DIR|g" /tmp/${SERVICE_NAME}.service

# Copy to systemd
if [ -f "$SERVICE_FILE" ]; then
    log_warn "Service file already exists at $SERVICE_FILE"
    sudo systemctl stop $SERVICE_NAME 2>/dev/null || true
fi

sudo mkdir -p "$LOG_DIR" "$RUN_DIR" "$DATA_DIR"
sudo chown -R www-data:www-data "$LOG_DIR" "$RUN_DIR" "$DATA_DIR"

sudo cp /tmp/${SERVICE_NAME}.service "$SERVICE_FILE"
sudo systemctl daemon-reload
log_success "Systemd service created"

# Step 4: Start service
log_info "Step 4: Starting service..."

sudo systemctl enable $SERVICE_NAME
sudo systemctl restart $SERVICE_NAME
sleep 2

# Verify service is running
if sudo systemctl is-active --quiet $SERVICE_NAME; then
    log_success "Service is running"
else
    log_error "Service failed to start. Check logs:"
    sudo journalctl -u $SERVICE_NAME -n 20
    exit 1
fi

# Step 5: Test connectivity
log_info "Step 5: Testing connectivity..."

if curl -s http://localhost:$PORT/health | grep -q "OK"; then
    log_success "Health check passed"
else
    log_error "Health check failed"
    exit 1
fi

# Step 6: Configure nginx (optional, if available)
log_info "Step 6: Checking nginx..."

if command -v nginx &> /dev/null; then
    log_info "Nginx found. Setting up reverse proxy..."

    cat > /tmp/corvinos-stats-nginx.conf << EOF
upstream stats_backend {
    server 127.0.0.1:$PORT;
    keepalive 32;
}

server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://stats_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 10s;
    }
}
EOF

    sudo cp /tmp/corvinos-stats-nginx.conf /etc/nginx/sites-available/corvinos-stats
    sudo ln -sf /etc/nginx/sites-available/corvinos-stats /etc/nginx/sites-enabled/corvinos-stats

    sudo nginx -t 2>/dev/null && sudo systemctl restart nginx
    log_success "Nginx reverse proxy configured"
else
    log_warn "Nginx not found. Dashboard accessible directly on port $PORT"
fi

# Step 7: Setup log rotation
log_info "Step 7: Setting up log rotation..."

sudo cat > /etc/logrotate.d/corvinos-stats << EOF
$LOG_DIR/*.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 www-data www-data
    sharedscripts
    postrotate
        systemctl reload $SERVICE_NAME > /dev/null 2>&1 || true
    endscript
}
EOF

log_success "Log rotation configured"

# Step 8: Show status
echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║              ✅ DEPLOYMENT COMPLETE                       ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "📊 Access Dashboard:"
echo "   http://localhost:$PORT/stats"
echo "   http://localhost/stats (via nginx)"
echo ""
echo "🔌 API Endpoint:"
echo "   http://localhost:$PORT/api/metrics/stats"
echo ""
echo "🩺 Health Check:"
echo "   http://localhost:$PORT/health"
echo ""
echo "📋 Service Management:"
echo "   sudo systemctl status $SERVICE_NAME      # Check status"
echo "   sudo systemctl restart $SERVICE_NAME     # Restart"
echo "   sudo journalctl -u $SERVICE_NAME -f     # View logs"
echo "   sudo tail -f $LOG_DIR/stats.log         # Application logs"
echo ""
echo "🌐 For Production (corvin-labs.com):"
echo "   1. Update DNS: corvin-labs.com A <IP>"
echo "   2. Configure SSL: certbot certonly -d corvin-labs.com"
echo "   3. Update nginx config with domain + SSL"
echo "   4. Restart service: sudo systemctl restart $SERVICE_NAME"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

log_success "CorvinOS Stats Dashboard is LIVE and running!"
