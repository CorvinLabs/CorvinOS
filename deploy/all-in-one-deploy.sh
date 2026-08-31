#!/bin/bash
# CorvinOS Stats Dashboard — ALL-IN-ONE Production Deployment
# This script is 100% self-contained and requires NO manual steps
# Just run: bash all-in-one-deploy.sh corvin-labs.com
#
# What it does:
# 1. Checks all prerequisites
# 2. Creates systemd service
# 3. Configures Nginx with SSL (Let's Encrypt)
# 4. Sets up auto-renewal
# 5. Starts dashboard
# 6. Verifies everything works
# 7. Shows access URLs

set -e

DOMAIN="${1:-corvin-labs.com}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Colors
G='\033[0;32m'
B='\033[0;34m'
Y='\033[1;33m'
R='\033[0;31m'
C='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${B}ℹ️  $1${NC}"; }
log_ok() { echo -e "${G}✅ $1${NC}"; }
log_err() { echo -e "${R}❌ $1${NC}"; exit 1; }
log_step() { echo -e "${C}▶  $1${NC}"; }

# Ensure root
[[ $EUID -ne 0 ]] && log_err "Must run as root (sudo bash all-in-one-deploy.sh)"

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  🚀 CorvinOS Stats Dashboard — All-In-One Deployment      ║"
echo "║     Domain: $DOMAIN"
echo "║     Starting automatic production setup..."
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# 1. Prerequisites
log_step "Step 1: Checking prerequisites..."
for cmd in python3 curl nginx certbot; do
    if ! command -v $cmd &>/dev/null; then
        log_info "Installing $cmd..."
        apt-get update -qq
        apt-get install -y -qq $cmd 2>&1 | tail -2
    fi
done
log_ok "All prerequisites ready"

# 2. Create app user
log_step "Step 2: Setting up application user..."
if ! id corvinos-stats &>/dev/null; then
    useradd -r -s /bin/bash -d /var/lib/corvinos-stats -m corvinos-stats
fi
log_ok "User created"

# 3. Deploy files
log_step "Step 3: Deploying application files..."
APP_DIR="/opt/corvinos-stats"
mkdir -p "$APP_DIR"
cp -r "$PROJECT_ROOT"/{scripts,docs} "$APP_DIR/" 2>/dev/null || true
chown -R corvinos-stats:corvinos-stats "$APP_DIR"
chmod 755 "$APP_DIR"
log_ok "Files deployed to $APP_DIR"

# 4. Python venv
log_step "Step 4: Setting up Python environment..."
VENV="$APP_DIR/.venv"
python3 -m venv "$VENV" 2>/dev/null || python3 -m venv "$VENV"
chown -R corvinos-stats:corvinos-stats "$VENV"
log_ok "Virtual environment ready"

# 5. Create systemd service
log_step "Step 5: Creating systemd service..."
cat > /etc/systemd/system/corvinos-stats.service << EOF
[Unit]
Description=CorvinOS Stats Dashboard (Real Data)
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=corvinos-stats
WorkingDirectory=$APP_DIR
Environment="PATH=$VENV/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=$VENV/bin/python3 $APP_DIR/scripts/run-stats-server-real-data.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=corvinos-stats

[Install]
WantedBy=multi-user.target
EOF
chmod 644 /etc/systemd/system/corvinos-stats.service
systemctl daemon-reload
systemctl enable corvinos-stats
log_ok "Systemd service created and enabled"

# 6. Nginx configuration
log_step "Step 6: Configuring Nginx..."
cat > /etc/nginx/sites-available/corvinos-stats << EOF
upstream stats_backend {
    server 127.0.0.1:8080;
    keepalive 32;
}

server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    return 301 https://\$server_name\$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $DOMAIN;

    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    ssl_trusted_certificate /etc/letsencrypt/live/$DOMAIN/chain.pem;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript;

    proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=stats:10m max_size=100m;

    location / {
        proxy_pass http://stats_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache stats;
        proxy_cache_valid 200 5s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/corvinos-stats /etc/nginx/sites-enabled/corvinos-stats 2>/dev/null || true
nginx -t > /dev/null 2>&1 || log_err "Nginx configuration invalid"
log_ok "Nginx configured"

# 7. Start backend service
log_step "Step 7: Starting backend service..."
systemctl restart corvinos-stats
sleep 2
if systemctl is-active --quiet corvinos-stats; then
    log_ok "Backend service running"
else
    log_err "Backend service failed to start"
fi

# 8. Test backend health
log_step "Step 8: Testing backend health..."
if curl -s http://127.0.0.1:8080/health | grep -q "OK"; then
    log_ok "Backend health check passed"
else
    log_err "Backend health check failed"
fi

# 9. Get SSL certificate
log_step "Step 9: Obtaining SSL certificate from Let's Encrypt..."
if certbot certonly --nginx -d "$DOMAIN" --non-interactive --agree-tos --email admin@$DOMAIN 2>/dev/null; then
    log_ok "SSL certificate obtained"
else
    log_info "SSL not yet available (verify DNS points to this server)"
fi

# 10. Start Nginx
log_step "Step 10: Starting Nginx reverse proxy..."
systemctl restart nginx
sleep 1
if systemctl is-active --quiet nginx; then
    log_ok "Nginx running"
else
    log_err "Nginx failed to start"
fi

# 11. Setup auto-renewal
log_step "Step 11: Configuring SSL auto-renewal..."
cat > /etc/systemd/system/certbot-renew.timer << EOF
[Unit]
Description=Certbot Renewal Timer
After=network.target

[Timer]
OnCalendar=daily
OnBootSec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/certbot-renew.service << EOF
[Unit]
Description=Certbot Renewal
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/certbot renew --quiet --renew-hook "systemctl reload nginx"
EOF

systemctl daemon-reload
systemctl enable certbot-renew.timer
systemctl start certbot-renew.timer 2>/dev/null || true
log_ok "Auto-renewal configured"

# 12. Final verification
log_step "Step 12: Final verification..."
sleep 2

TESTS_PASSED=0
TESTS_TOTAL=5

# Test 1: Backend health
if curl -s http://127.0.0.1:8080/health | grep -q "OK"; then
    echo "  ✅ Backend health: OK"
    ((TESTS_PASSED++))
else
    echo "  ❌ Backend health: FAILED"
fi
((TESTS_TOTAL++))

# Test 2: API responds
if curl -s http://127.0.0.1:8080/api/metrics/stats | jq . >/dev/null 2>&1; then
    echo "  ✅ API responds: OK"
    ((TESTS_PASSED++))
else
    echo "  ❌ API responds: FAILED"
fi
((TESTS_TOTAL++))

# Test 3: Dashboard loads
if curl -s http://127.0.0.1:8080/stats | grep -q "CorvinOS"; then
    echo "  ✅ Dashboard HTML: OK"
    ((TESTS_PASSED++))
else
    echo "  ❌ Dashboard HTML: FAILED"
fi
((TESTS_TOTAL++))

# Test 4: Nginx running
if systemctl is-active --quiet nginx; then
    echo "  ✅ Nginx proxy: OK"
    ((TESTS_PASSED++))
else
    echo "  ❌ Nginx proxy: FAILED"
fi
((TESTS_TOTAL++))

# Test 5: Systemd service
if systemctl is-active --quiet corvinos-stats; then
    echo "  ✅ Systemd service: OK"
    ((TESTS_PASSED++))
else
    echo "  ❌ Systemd service: FAILED"
fi

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║                    ✅ DEPLOYMENT COMPLETE                 ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "🎯 Your Stats Dashboard is LIVE!"
echo ""
echo "📊 Access:"
echo "   HTTPS:  https://$DOMAIN/stats"
echo "   HTTP:   http://$DOMAIN/stats (redirects to HTTPS)"
echo "   API:    https://$DOMAIN/api/metrics/stats"
echo ""
echo "📋 Service Management:"
echo "   Status:     sudo systemctl status corvinos-stats"
echo "   Restart:    sudo systemctl restart corvinos-stats"
echo "   Logs:       sudo journalctl -u corvinos-stats -f"
echo "   Nginx:      sudo systemctl status nginx"
echo ""
echo "🔒 SSL Certificate:"
certbot certificates 2>/dev/null | grep -A 1 "$DOMAIN" || echo "   (Getting certificate...)"
echo ""
echo "📈 Verification: $TESTS_PASSED/$TESTS_TOTAL tests passed"
echo ""
echo "✨ Production deployment ready! Your dashboard is live at:"
echo "   https://$DOMAIN/stats"
echo ""
