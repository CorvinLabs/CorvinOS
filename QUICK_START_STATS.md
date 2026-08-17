# CorvinOS Stats Dashboard — Quick Start (Manual Deployment)

**Status:** Ready to deploy locally  
**Time to Deploy:** ~2 minutes  
**No GitHub Actions needed** ✅

---

## 🚀 Deploy in 3 Commands

### 1. Navigate to project
```bash
cd /home/shumway/projects/CorvinOS
```

### 2. Deploy containers
```bash
./scripts/deploy-stats-manual.sh up
```

### 3. Open dashboard
```bash
# Open in browser:
http://localhost/stats
```

**That's it!** 🎉

---

## 📊 What You'll See

- **Real-time Stats Dashboard** at `http://localhost/stats`
- **Live metrics** updating every 5 seconds
- **World map** showing instance locations
- **Token savings** calculations
- **Instance table** with performance data

---

## 🛠️ Available Commands

```bash
# Deploy (start containers)
./scripts/deploy-stats-manual.sh up

# Stop containers
./scripts/deploy-stats-manual.sh down

# Restart containers
./scripts/deploy-stats-manual.sh restart

# View logs (tail -f)
./scripts/deploy-stats-manual.sh logs

# Show status
./scripts/deploy-stats-manual.sh status
```

---

## 📡 Service URLs

| Service | URL | Purpose |
|---------|-----|---------|
| Stats Dashboard | http://localhost/stats | Main dashboard |
| Mock API | http://localhost:8000 | Fake metrics endpoint |
| API Docs | http://localhost:8000 | API documentation |
| Health Check | http://localhost/health | Service health |
| Prometheus | http://localhost:9090 | Metrics monitoring |

---

## 📋 What's Running

| Container | Port | Purpose |
|-----------|------|---------|
| `corvinos-stats` | 80, 443 | Nginx (stats dashboard) |
| `corvinos-api-mock` | 8000 | Mock API (fake metrics) |
| `corvinos-prometheus` | 9090 | Prometheus monitoring |

---

## 🔍 Testing

### Check if everything is running
```bash
docker-compose -f docker-compose.stats.yml ps
```

Expected output:
```
NAME                  STATUS
corvinos-stats        Up 2 minutes (healthy)
corvinos-api-mock     Up 2 minutes (healthy)
corvinos-prometheus   Up 2 minutes
```

### Test API
```bash
curl http://localhost:8000/api/metrics/stats | jq .
```

Expected: JSON with cluster stats

### Test Dashboard
```bash
curl http://localhost/stats | grep "CorvinOS Telemetry"
```

Expected: HTML content

---

## 🛑 Stop & Clean Up

```bash
# Stop containers (keep data)
./scripts/deploy-stats-manual.sh down

# Remove everything (including data)
docker-compose -f docker-compose.stats.yml down -v
```

---

## 🚨 Troubleshooting

### Port already in use
```bash
# Kill existing process on port 80
sudo lsof -i :80
sudo kill -9 <PID>

# Try again
./scripts/deploy-stats-manual.sh up
```

### Docker daemon not running
```bash
# Start Docker daemon
sudo systemctl start docker

# Or on Mac:
open /Applications/Docker.app
```

### Containers keep restarting
```bash
# Check logs
./scripts/deploy-stats-manual.sh logs

# Rebuild from scratch
docker-compose -f docker-compose.stats.yml down -v
./scripts/deploy-stats-manual.sh up
```

---

## 🎯 Next Steps

1. ✅ Dashboard is live at http://localhost/stats
2. 📊 Mock API serving fake metrics at http://localhost:8000
3. 🌍 World map showing instance locations
4. 📈 Live charts updating every 5 seconds
5. 💾 Ready for production deployment to corvin-labs.com

---

## 🔗 To Deploy to Production (corvin-labs.com)

```bash
# 1. Update domain DNS
# corvin-labs.com A 1.2.3.4

# 2. Deploy to Kubernetes
kubectl apply -f deploy/k8s-stats-deployment.yaml

# 3. Configure SSL
certbot certonly --dns-cloudflare -d corvin-labs.com

# 4. Verify
curl https://corvin-labs.com/stats
```

See `PRODUCTION_DEPLOYMENT.md` for full instructions.

---

**Status:** ✅ **Stats Dashboard is live and running**

Open browser → http://localhost/stats → See live metrics! 🎉
