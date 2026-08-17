# 🚀 Production Deployment — corvin-labs.com/stats

**Status:** Ready to Deploy  
**Real Data:** ✅ Enabled (no mock)  
**SSL/TLS:** ✅ Automatic (Let's Encrypt)  
**Auto-Renewal:** ✅ Configured

---

## ⚡ Quick Start (5 minutes)

### Step 1: SSH into your production server
```bash
ssh root@corvin-labs.com
# or your production server IP
```

### Step 2: Download and run deployment script
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/corvinOS/CorvinOS/main/deploy/production-deploy.sh) corvin-labs.com
```

**OR locally (if you have the repo):**
```bash
cd /home/shumway/projects/CorvinOS
sudo bash deploy/production-deploy.sh corvin-labs.com
```

### Step 3: Verify deployment
```bash
# Check service status
sudo systemctl status corvinos-stats

# Test API
curl https://corvin-labs.com/api/metrics/stats

# View logs
sudo journalctl -u corvinos-stats -f
```

### Step 4: Open dashboard
```
https://corvin-labs.com/stats
```

**Done!** Your dashboard is live with real instance data. 🎉

---

## 📋 Prerequisites

Before running the deployment script, ensure:

- ✅ **Linux server** (Ubuntu 20.04+ recommended)
- ✅ **Root access** (or sudo)
- ✅ **Domain DNS** pointing to this server
  ```
  corvin-labs.com A <your-server-ip>
  ```
- ✅ **Port 80 & 443 open** (firewall)
- ✅ **Python 3.8+** installed
- ✅ **At least 1GB RAM, 500MB disk** available

### DNS Setup Example
```bash
# If using Cloudflare:
corvin-labs.com A 203.0.113.42  # Replace with your IP

# If using Route53, GoDaddy, etc., add A record:
# Type: A
# Name: corvin-labs.com (or @)
# Value: 203.0.113.42 (your server IP)
# TTL: 3600 (1 hour)
```

Wait 5-15 minutes for DNS to propagate, then test:
```bash
nslookup corvin-labs.com
# Should show your server IP
```

---

## 🔄 What the Script Does

The `production-deploy.sh` script automatically:

1. **Checks prerequisites** — Python, Nginx, Certbot
2. **Creates application user** — `corvinos-stats` (non-root)
3. **Deploys project files** — to `/opt/corvinos-stats/`
4. **Sets up Python environment** — virtual environment with dependencies
5. **Creates production server** — real data from instance registry
6. **Configures systemd service** — auto-start on reboot, auto-restart on crash
7. **Configures Nginx** — reverse proxy, caching, security headers
8. **Gets SSL certificate** — Let's Encrypt (requires DNS working)
9. **Starts service** — runs the stats dashboard
10. **Configures auto-renewal** — SSL cert auto-renews every 60 days
11. **Verifies setup** — health checks

---

## 📊 What Gets Deployed

### Directory Structure
```
/opt/corvinos-stats/          # Application root
├── scripts/
│   ├── production-server.py   # Real data server
│   └── ...
├── docs/
│   └── stats.html             # Dashboard UI
├── core/learning/             # Metrics libraries
└── .venv/                      # Python virtual env
```

### Systemd Service
```
/etc/systemd/system/corvinos-stats.service
```

### Nginx Configuration
```
/etc/nginx/sites-available/corvinos-stats
/etc/nginx/sites-enabled/corvinos-stats → /etc/nginx/sites-available/corvinos-stats
```

### SSL Certificates (Let's Encrypt)
```
/etc/letsencrypt/live/corvin-labs.com/
├── fullchain.pem              # Full cert chain
├── privkey.pem                # Private key
└── chain.pem                  # Intermediate certs
```

---

## 🔐 Real Data Sources

The production server loads **real** instance metrics from:

1. **Instance Registry** (`~/.corvin/instances.json`)
   - Auto-discovered CorvinOS instances
   - Persisted instance metadata

2. **Audit Trail** (`~/.corvin/audit.jsonl`)
   - Token usage per turn
   - Event history (hash-chained)
   - GDPR-compliant

3. **Instance Metrics**
   - Turn count (API calls)
   - Total tokens consumed
   - Token savings %
   - Location (geo-coordinates)

**Zero mock data** — everything is real!

---

## 🛠️ Managing the Service

### View Status
```bash
sudo systemctl status corvinos-stats
```

### Restart Service
```bash
sudo systemctl restart corvinos-stats
```

### View Real-Time Logs
```bash
sudo journalctl -u corvinos-stats -f
```

### View Historical Logs
```bash
sudo journalctl -u corvinos-stats -n 100
```

### Stop Service
```bash
sudo systemctl stop corvinos-stats
```

### Enable/Disable Auto-Start
```bash
sudo systemctl enable corvinos-stats    # Start on boot
sudo systemctl disable corvinos-stats   # Don't start on boot
```

---

## 📈 Performance Tuning

### Increase Cache TTL (if many instances)
Edit `/etc/nginx/sites-available/corvinos-stats`:
```nginx
proxy_cache_valid 200 10s;  # Was 5s, now 10s
```

### Increase Rate Limit (if hitting limit)
```nginx
limit_req_zone $binary_remote_addr zone=stats:10m rate=200r/m;  # Was 100r/m
```

### Increase Upstream Connections
```nginx
upstream stats_backend {
    server 127.0.0.1:8080;
    keepalive 64;  # Was 32
}
```

Then restart:
```bash
sudo systemctl reload nginx
```

---

## 🔄 SSL Certificate Management

### Check Certificate Status
```bash
certbot certificates
```

### Manual Renewal
```bash
sudo certbot renew --force-renewal
```

### Troubleshooting SSL
```bash
# Check if auto-renewal timer is running
sudo systemctl status certbot-renew.timer

# View renewal logs
sudo journalctl -u certbot-renew -f

# Test renewal (dry run)
sudo certbot renew --dry-run
```

---

## 📊 Health Monitoring

### Health Endpoint
```bash
curl https://corvin-labs.com/health
# Response: OK
```

### API Endpoint
```bash
curl https://corvin-labs.com/api/metrics/stats | jq .
# Returns JSON with cluster metrics
```

### Nginx Status
```bash
sudo systemctl status nginx
```

### Disk Space
```bash
df -h /opt/corvinos-stats
df -h /var/cache/nginx       # Cache partition
```

### Memory Usage
```bash
ps aux | grep production-server.py
```

---

## 🚨 Troubleshooting

### "Connection refused" on https://corvin-labs.com/stats

1. Check if service is running:
   ```bash
   sudo systemctl status corvinos-stats
   ```

2. Check if Nginx is running:
   ```bash
   sudo systemctl status nginx
   ```

3. Check if port 443 is open:
   ```bash
   sudo netstat -tlnp | grep 443
   ```

4. Check Nginx error log:
   ```bash
   sudo tail -20 /var/log/nginx/error.log
   ```

### "SSL certificate problem" on https://

1. Verify Let's Encrypt certificate exists:
   ```bash
   sudo ls -la /etc/letsencrypt/live/corvin-labs.com/
   ```

2. If missing, run certbot:
   ```bash
   sudo certbot certonly --nginx -d corvin-labs.com
   ```

3. Reload Nginx:
   ```bash
   sudo systemctl reload nginx
   ```

### "API returns empty/no instances"

1. Check if instance registry exists:
   ```bash
   ls -la ~/.corvin/instances.json
   ls -la ~/.corvin/audit.jsonl
   ```

2. Check service logs:
   ```bash
   sudo journalctl -u corvinos-stats -n 50
   ```

3. Test backend directly:
   ```bash
   curl http://127.0.0.1:8080/api/metrics/stats
   ```

### Service keeps crashing

1. Check error logs:
   ```bash
   sudo journalctl -u corvinos-stats -n 100
   ```

2. Verify file permissions:
   ```bash
   sudo ls -la /opt/corvinos-stats/
   sudo chown -R corvinos-stats:corvinos-stats /opt/corvinos-stats/
   ```

3. Check disk space:
   ```bash
   df -h
   ```

---

## 🔄 Updating the Dashboard

### Update Dashboard UI (docs/stats.html)

1. On your development machine:
   ```bash
   cd /home/shumway/projects/CorvinOS
   # Make changes to docs/stats.html
   git add docs/stats.html
   git commit -m "Update dashboard UI"
   git push
   ```

2. On production server:
   ```bash
   cd /opt/corvinos-stats
   git pull
   # OR copy manually:
   scp docs/stats.html root@corvin-labs.com:/opt/corvinos-stats/docs/
   ```

3. Restart service (optional, uses cache):
   ```bash
   sudo systemctl restart corvinos-stats
   ```

### Update Production Server Code

1. Update `/opt/corvinos-stats/scripts/production-server.py`
2. Restart service:
   ```bash
   sudo systemctl restart corvinos-stats
   ```

---

## 📊 Monitoring & Alerting (Optional)

### Add Prometheus Scraping

1. Install Prometheus (if not already):
   ```bash
   sudo apt-get install -y prometheus
   ```

2. Add to `/etc/prometheus/prometheus.yml`:
   ```yaml
   scrape_configs:
     - job_name: 'corvinos-stats'
       scheme: https
       static_configs:
         - targets: ['corvin-labs.com']
       metrics_path: '/metrics'
   ```

3. Restart Prometheus:
   ```bash
   sudo systemctl restart prometheus
   ```

### Add Grafana Dashboard

1. Install Grafana:
   ```bash
   sudo apt-get install -y grafana-server
   ```

2. Add Prometheus data source in Grafana UI
3. Import community dashboard for HTTP/service metrics

---

## 🎯 Next Steps

1. ✅ Deploy script ready
2. ✅ Real data integration complete
3. ⏳ Deploy to production:
   ```bash
   sudo bash /home/shumway/projects/CorvinOS/deploy/production-deploy.sh corvin-labs.com
   ```
4. ⏳ Verify at https://corvin-labs.com/stats
5. ⏳ Monitor logs: `sudo journalctl -u corvinos-stats -f`
6. ⏳ (Optional) Set up Prometheus/Grafana monitoring

---

## 📞 Support

**For immediate help:**
```bash
# Show all relevant info
echo "=== Service Status ==="
sudo systemctl status corvinos-stats
echo "=== Recent Logs ==="
sudo journalctl -u corvinos-stats -n 20
echo "=== Health Check ==="
curl https://corvin-labs.com/health
echo "=== API Test ==="
curl https://corvin-labs.com/api/metrics/stats | jq . | head -20
```

---

**Status:** ✅ Ready for production deployment!

Run the deployment script on your server and your dashboard will be live in under 5 minutes. All data is real, SSL is automatic, and auto-renewal is configured.

```bash
sudo bash <(curl -fsSL https://raw.githubusercontent.com/corvinOS/CorvinOS/main/deploy/production-deploy.sh) corvin-labs.com
```

**Then:** → https://corvin-labs.com/stats ← See your live metrics! 🎉
