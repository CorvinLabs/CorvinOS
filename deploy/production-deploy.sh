#!/bin/bash
# CorvinOS Stats Dashboard — Production Deployment to corvin-labs.com
# This script sets up a complete production deployment with:
# - Systemd service
# - Nginx reverse proxy
# - Let's Encrypt SSL/TLS
# - Real instance data (no mock)
# - Health monitoring
#
# Usage: bash <(curl -fsSL https://raw.githubusercontent.com/corvinOS/stats/main/deploy.sh)
# OR locally: bash deploy/production-deploy.sh

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${BLUE}ℹ️  $1${NC}"; }
log_success() { echo -e "${GREEN}✅ $1${NC}"; }
log_error() { echo -e "${RED}❌ $1${NC}"; exit 1; }
log_warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
log_step() { echo -e "${CYAN}▶  $1${NC}"; }

# Configuration
DOMAIN="${1:-corvin-labs.com}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="corvinos-stats"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_CONF="/etc/nginx/sites-available/${SERVICE_NAME}"
NGINX_ENABLE="/etc/nginx/sites-enabled/${SERVICE_NAME}"

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  🚀 CorvinOS Stats Dashboard — Production Deployment      ║"
echo "║     Domain: $DOMAIN"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Step 1: Prerequisites
log_step "Checking prerequisites..."

[[ $EUID -ne 0 ]] && log_error "This script must be run as root. Use: sudo bash $0"

for cmd in python3 pip3 nginx certbot; do
    if ! command -v $cmd &> /dev/null; then
        log_warn "$cmd not found. Installing..."
        apt-get update -qq
        apt-get install -y -qq $cmd 2>/dev/null || true
    fi
done

log_success "Prerequisites OK"

# Step 2: Create application user
log_step "Setting up application user..."

if id "$SERVICE_NAME" &>/dev/null 2>&1; then
    log_warn "User $SERVICE_NAME already exists"
else
    useradd -r -s /bin/bash -d /var/lib/$SERVICE_NAME -m $SERVICE_NAME
    log_success "User created"
fi

# Step 3: Copy project files
log_step "Deploying project files..."

DEPLOY_DIR="/opt/$SERVICE_NAME"
mkdir -p "$DEPLOY_DIR"
cp -r "$PROJECT_ROOT"/{scripts,docs,core/learning} "$DEPLOY_DIR/" 2>/dev/null || cp -r "$PROJECT_ROOT"/{scripts,docs} "$DEPLOY_DIR/"
chown -R $SERVICE_NAME:$SERVICE_NAME "$DEPLOY_DIR"
chmod -R 755 "$DEPLOY_DIR"

log_success "Project deployed to $DEPLOY_DIR"

# Step 4: Create Python virtual environment
log_step "Setting up Python environment..."

VENV_DIR="$DEPLOY_DIR/.venv"
sudo -u $SERVICE_NAME python3 -m venv "$VENV_DIR" 2>/dev/null || python3 -m venv "$VENV_DIR"
$VENV_DIR/bin/pip install -q flask 2>/dev/null || true

log_success "Virtual environment ready"

# Step 5: Create production stats server (real data, no mock)
log_step "Creating production stats server..."

cat > "$DEPLOY_DIR/scripts/production-server.py" << 'EOFPROD'
#!/usr/bin/env python3
"""
CorvinOS Stats Dashboard — Production Server (Real Data)
Serves real instance metrics instead of mock data
"""

import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# Try Flask, fallback to http.server
try:
    from flask import Flask, jsonify
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from urllib.parse import urlparse


def load_instance_registry():
    """Load real instance data from registry."""
    registry_path = Path.home() / '.corvin' / 'instances.json'
    if registry_path.exists():
        try:
            with open(registry_path) as f:
                data = json.load(f)
            return data.get('instances', [])
        except:
            pass

    # Fallback: get from telemetry events
    events_path = Path.home() / '.corvin' / 'audit.jsonl'
    instances = {}
    if events_path.exists():
        try:
            with open(events_path) as f:
                for line in f:
                    try:
                        event = json.loads(line)
                        if 'instance_id' in event:
                            iid = event['instance_id']
                            if iid not in instances:
                                instances[iid] = {
                                    'instance_id': iid,
                                    'hostname': event.get('hostname', iid),
                                    'location': event.get('location', '0,0'),
                                    'turn_count': 0,
                                    'total_tokens': 0,
                                    'savings_percent': 0,
                                }
                            instances[iid]['turn_count'] += 1
                            instances[iid]['total_tokens'] += event.get('tokens_used', 0)
                    except:
                        pass
            return list(instances.values())
        except:
            pass

    # Last resort: return empty (will show 0 instances)
    return []


def get_real_stats():
    """Generate real cluster statistics from instance registry."""
    instances = load_instance_registry()

    total_turns = sum(i.get('turn_count', 0) for i in instances)
    total_tokens = sum(i.get('total_tokens', 0) for i in instances)
    avg_tokens_per_turn = total_tokens // total_turns if total_turns > 0 else 0

    # Calculate average savings
    savings_list = [i.get('savings_percent', 0) for i in instances]
    avg_savings = sum(savings_list) / len(savings_list) if savings_list else 0

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "cluster": {
            "instance_count": len(instances),
            "total_turns": total_turns,
            "total_tokens": total_tokens,
            "avg_tokens_per_turn": avg_tokens_per_turn,
            "avg_savings_percent": round(avg_savings, 1),
            "instances": instances,
        },
        "summary": {
            "instance_count": len(instances),
            "total_turns": total_turns,
            "total_tokens": total_tokens,
            "avg_tokens_per_turn": avg_tokens_per_turn,
            "avg_savings_percent": round(avg_savings, 1),
        },
    }


if HAS_FLASK:
    app = Flask(__name__)

    @app.route('/', methods=['GET'])
    def index():
        return '<meta http-equiv="refresh" content="0;url=/stats" />'

    @app.route('/stats', methods=['GET'])
    def stats_dashboard():
        html_path = Path(__file__).parent.parent / 'docs' / 'stats.html'
        if html_path.exists():
            with open(html_path) as f:
                html = f.read()
            html = html.replace(
                "'https://api.corvin-labs.com/api/metrics/stats'",
                "'/api/metrics/stats'"
            )
            return html
        return '<h1>Dashboard not found</h1>', 404

    @app.route('/api/metrics/stats', methods=['GET'])
    def api_stats():
        return jsonify(get_real_stats())

    @app.route('/health', methods=['GET'])
    def health():
        return 'OK\n', 200

    if __name__ == '__main__':
        print("\n" + "="*60)
        print("⚡ CorvinOS Stats Dashboard (Production — Real Data)")
        print("="*60)
        print("\n📊 Dashboard:  https://corvin-labs.com/stats")
        print("🔌 API:        https://corvin-labs.com/api/metrics/stats")
        print("🩺 Health:     https://corvin-labs.com/health")
        print("\nLoading real instance data from:")
        print("  ~/.corvin/instances.json")
        print("  ~/.corvin/audit.jsonl")
        print("\n" + "="*60 + "\n")
        app.run(host='127.0.0.1', port=8080, debug=False)

else:
    class StatsHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path

            if path in ('/', '/stats'):
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()

                html_path = Path(__file__).parent.parent / 'docs' / 'stats.html'
                if html_path.exists():
                    with open(html_path, 'rb') as f:
                        html = f.read().decode('utf-8')
                    html = html.replace(
                        "'https://api.corvin-labs.com/api/metrics/stats'",
                        "'/api/metrics/stats'"
                    )
                    self.wfile.write(html.encode('utf-8'))
                else:
                    self.wfile.write(b'<h1>Dashboard not found</h1>')

            elif path == '/api/metrics/stats':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                stats = get_real_stats()
                self.wfile.write(json.dumps(stats, indent=2).encode('utf-8'))

            elif path == '/health':
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'OK\n')

            else:
                self.send_response(404)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'Not found\n')

        def log_message(self, format, *args):
            pass

    server = HTTPServer(('127.0.0.1', 8080), StatsHandler)
    print("\n" + "="*60)
    print("⚡ CorvinOS Stats Dashboard (Production — Real Data)")
    print("="*60)
    print("\n📊 Dashboard:  https://corvin-labs.com/stats")
    print("🔌 API:        https://corvin-labs.com/api/metrics/stats")
    print("🩺 Health:     https://corvin-labs.com/health")
    print("\nLoading real instance data from:")
    print("  ~/.corvin/instances.json")
    print("  ~/.corvin/audit.jsonl")
    print("\n" + "="*60 + "\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Server stopped")
        sys.exit(0)
EOFPROD

chmod +x "$DEPLOY_DIR/scripts/production-server.py"
log_success "Production server created"

# Step 6: Create systemd service
log_step "Creating systemd service..."

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=CorvinOS Stats Dashboard (Production)
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_NAME
WorkingDirectory=$DEPLOY_DIR
Environment="PATH=$VENV_DIR/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=$VENV_DIR/bin/python3 $DEPLOY_DIR/scripts/production-server.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

[Install]
WantedBy=multi-user.target
EOF

chmod 644 "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable $SERVICE_NAME
log_success "Systemd service created and enabled"

# Step 7: Create Nginx configuration
log_step "Configuring Nginx..."

cat > "$NGINX_CONF" << 'EOFNGINX'
upstream stats_backend {
    server 127.0.0.1:8080;
    keepalive 32;
}

# HTTP → HTTPS redirect
server {
    listen 80;
    listen [::]:80;
    server_name DOMAIN_PLACEHOLDER;
    return 301 https://$server_name$request_uri;
}

# HTTPS
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name DOMAIN_PLACEHOLDER;

    # SSL certificates (Let's Encrypt)
    ssl_certificate /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/privkey.pem;
    ssl_trusted_certificate /etc/letsencrypt/live/DOMAIN_PLACEHOLDER/chain.pem;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Gzip compression
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml;
    gzip_vary on;
    gzip_comp_level 6;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=stats:10m rate=100r/m;
    limit_req zone=stats burst=200 nodelay;

    # Cache for API responses
    proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=stats_cache:10m max_size=100m inactive=60m;

    # Static files
    location /stats {
        proxy_pass http://stats_backend;
        proxy_cache stats_cache;
        proxy_cache_valid 200 5s;
        proxy_cache_use_stale error timeout invalid_header updating http_500 http_502 http_503 http_504;
        proxy_cache_bypass $http_pragma $http_authorization;
        add_header X-Cache-Status $upstream_cache_status;
    }

    # API endpoint
    location /api/metrics/stats {
        proxy_pass http://stats_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 10s;

        # Cache API responses
        proxy_cache stats_cache;
        proxy_cache_valid 200 5s;
        proxy_cache_use_stale error timeout;
        add_header X-Cache-Status $upstream_cache_status;
    }

    # Health check
    location /health {
        proxy_pass http://stats_backend;
        access_log off;
    }

    # Root redirect
    location / {
        return 301 /stats;
    }
}
EOFNGINX

sed -i "s/DOMAIN_PLACEHOLDER/$DOMAIN/g" "$NGINX_CONF"
ln -sf "$NGINX_CONF" "$NGINX_ENABLE" 2>/dev/null || true

nginx -t > /dev/null 2>&1 && {
    systemctl restart nginx
    log_success "Nginx configured and restarted"
} || log_error "Nginx configuration invalid"

# Step 8: Get SSL certificate from Let's Encrypt
log_step "Provisioning SSL certificate..."

if certbot certonly --nginx -d "$DOMAIN" --non-interactive --agree-tos --email admin@$DOMAIN 2>/dev/null; then
    log_success "SSL certificate obtained"
else
    log_warn "SSL certificate not available yet (verify DNS first)"
    log_warn "Run: certbot certonly --nginx -d $DOMAIN"
fi

# Step 9: Start the service
log_step "Starting service..."

systemctl restart $SERVICE_NAME
sleep 2

if systemctl is-active --quiet $SERVICE_NAME; then
    log_success "Service is running"
else
    log_error "Service failed to start"
fi

# Step 10: Verify
log_step "Running health checks..."

if curl -s http://127.0.0.1:8080/health | grep -q "OK"; then
    log_success "Backend health check: PASS"
else
    log_error "Backend not responding"
fi

# Step 11: Setup auto-renewal
log_step "Configuring auto-renewal..."

cat > /etc/systemd/system/certbot-renew.timer << 'EOFCERT'
[Unit]
Description=Certbot Renewal Timer
After=network.target

[Timer]
OnCalendar=daily
OnBootSec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOFCERT

cat > /etc/systemd/system/certbot-renew.service << 'EOFCERTSVC'
[Unit]
Description=Certbot Renewal Service
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/bin/certbot renew --quiet --renew-hook "systemctl reload nginx"
EOFCERTSVC

systemctl daemon-reload
systemctl enable certbot-renew.timer
systemctl start certbot-renew.timer 2>/dev/null || true
log_success "Auto-renewal configured"

# Done
echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║           ✅ PRODUCTION DEPLOYMENT COMPLETE               ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "🌐 Access your dashboard:"
echo "   https://$DOMAIN/stats"
echo ""
echo "📊 API endpoint:"
echo "   https://$DOMAIN/api/metrics/stats"
echo ""
echo "📋 Service management:"
echo "   sudo systemctl status $SERVICE_NAME"
echo "   sudo systemctl restart $SERVICE_NAME"
echo "   sudo journalctl -u $SERVICE_NAME -f"
echo ""
echo "📝 Configuration:"
echo "   Nginx:        $NGINX_CONF"
echo "   Service:      $SERVICE_FILE"
echo "   Application:  $DEPLOY_DIR"
echo ""
echo "🔒 SSL Status:"
certbot certificates 2>/dev/null | grep -A 2 "$DOMAIN" || echo "   (SSL not yet configured)"
echo ""
echo "💡 Next steps:"
echo "   1. Verify DNS is pointing to this server"
echo "   2. Verify HTTPS works: curl -I https://$DOMAIN/stats"
echo "   3. Check Nginx logs: tail -f /var/log/nginx/error.log"
echo "   4. Monitor service: sudo journalctl -u $SERVICE_NAME -f"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

log_success "CorvinOS Stats Dashboard is LIVE at https://$DOMAIN/stats with REAL data!"
