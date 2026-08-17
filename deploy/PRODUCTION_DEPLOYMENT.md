# CorvinOS Stats Dashboard — Production Deployment Guide

**Status:** Ready for deployment  
**Domain:** corvin-labs.com  
**Service:** Stats Dashboard + API Proxy

---

## 📋 Pre-Deployment Checklist

### Domain & DNS
- [ ] Domain `corvin-labs.com` registered
- [ ] DNS A record points to Kubernetes ingress IP: `1.2.3.4`
- [ ] DNS propagation verified: `dig corvin-labs.com`

### SSL/TLS
- [ ] Let's Encrypt certificate provisioned: `certbot certonly --dns-cloudflare -d corvin-labs.com`
- [ ] Certificate stored in `/etc/letsencrypt/live/corvin-labs.com/`
- [ ] Auto-renewal configured: `systemctl enable certbot-renew`

### Kubernetes Cluster
- [ ] Cluster running (GKE, EKS, on-prem)
- [ ] kubectl configured: `kubectl cluster-info`
- [ ] Namespace `production` exists: `kubectl create ns production`
- [ ] Service account permissions verified

### Internal API
- [ ] CorvinOS API running at `api.internal:8000`
- [ ] `/api/metrics/stats` endpoint responding
- [ ] Network connectivity from K8s to internal API verified

---

## 🚀 Step-by-Step Deployment

### Step 1: Prepare Secrets
```bash
# Create Docker registry secret
kubectl create secret docker-registry corvinlabs-registry \
  --docker-server=docker.io \
  --docker-username=$DOCKER_USER \
  --docker-password=$DOCKER_PASSWORD \
  --docker-email=ops@corvin-labs.com \
  -n production

# Create SSL certificate secret (from Let's Encrypt)
kubectl create secret tls corvin-labs-tls \
  --cert=/etc/letsencrypt/live/corvin-labs.com/fullchain.pem \
  --key=/etc/letsencrypt/live/corvin-labs.com/privkey.pem \
  -n production
```

### Step 2: Build and Push Docker Image
```bash
# Build image
docker build -t corvinlabs/stats:latest -f deploy/Dockerfile.stats .

# Push to registry
docker push corvinlabs/stats:latest

# Verify
docker inspect corvinlabs/stats:latest
```

### Step 3: Deploy to Kubernetes
```bash
# Apply Kubernetes manifests
kubectl apply -f deploy/k8s-stats-deployment.yaml

# Verify deployment
kubectl get pods -n production
kubectl get svc -n production

# Check rolling update status
kubectl rollout status deployment/stats-dashboard -n production

# Expected output:
# NAME                            READY   STATUS    RESTARTS   AGE
# stats-dashboard-abc123-xyz      1/1     Running   0          30s
# stats-dashboard-abc123-uvw      1/1     Running   0          35s
# stats-dashboard-abc123-rst      1/1     Running   0          40s
```

### Step 4: Configure Ingress
```bash
# Create Ingress resource (example for nginx-ingress controller)
kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: stats-ingress
  namespace: production
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - corvin-labs.com
    secretName: corvin-labs-tls
  rules:
  - host: corvin-labs.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: stats-dashboard
            port:
              number: 80
EOF
```

### Step 5: Verify Deployment
```bash
# Wait for pods to be ready
kubectl wait --for=condition=ready pod \
  -l app=stats-dashboard -n production \
  --timeout=300s

# Test endpoint
curl -s https://corvin-labs.com/stats | head -20

# Expected: HTML content starting with <!DOCTYPE html>

# Test API proxy
curl -s https://corvin-labs.com/api/metrics/stats | jq .

# Expected: JSON with cluster stats
```

---

## 📊 Monitoring & Health Checks

### Health Endpoints
```bash
# Service health
curl https://corvin-labs.com/health
# Response: OK

# Stats dashboard loads
curl -s https://corvin-labs.com/stats | grep "CorvinOS Telemetry"
# Response: Found

# API metrics available
curl https://corvin-labs.com/metrics
# Response: Prometheus metrics
```

### Kubernetes Monitoring
```bash
# View deployment status
kubectl describe deployment stats-dashboard -n production

# Stream logs
kubectl logs -f deployment/stats-dashboard -n production

# Monitor resources
kubectl top pods -n production

# Watch rollout progress
kubectl rollout history deployment/stats-dashboard -n production
kubectl rollout status deployment/stats-dashboard -n production -w
```

### Prometheus Scraping
```yaml
# Add to Prometheus config
- job_name: 'stats-dashboard'
  scheme: https
  static_configs:
  - targets: ['corvin-labs.com:443']
  metrics_path: '/metrics'
```

---

## 🔄 Continuous Deployment

### GitHub Actions Auto-Deploy
The `.github/workflows/deploy-stats.yml` workflow automatically:
1. Builds Docker image on commits to `main`
2. Pushes to Docker registry
3. Deploys to Kubernetes via `kubectl set image`
4. Runs health check
5. Notifies Slack on success/failure

**Trigger conditions:**
- Push to `main` branch touching:
  - `docs/stats.html`
  - `core/learning/instance_registry.py`
  - `core/console/corvin_console/routes/vibe_metrics_api.py`
- Schedule: Every 5 minutes (keep data fresh)
- Manual: Workflow dispatch button in GitHub UI

---

## 🆘 Troubleshooting

### Pod keeps restarting
```bash
# Check logs
kubectl logs <pod-name> -n production --previous

# Common issues:
# - Image pull failing → check Docker registry credentials
# - API backend unreachable → verify network policy
# - Out of memory → increase resource limits in deployment
```

### Slow responses
```bash
# Check Nginx cache hit rate
curl -I https://corvin-labs.com/api/metrics/stats | grep X-Cache-Status

# Monitor proxy upstream
kubectl logs <pod-name> -n production | grep upstream
```

### Certificate renewal issues
```bash
# Manual renewal if needed
sudo certbot renew --dry-run
sudo certbot renew --force-renewal

# Verify certificate
openssl s_client -connect corvin-labs.com:443
```

---

## 📈 Scaling

### Manual scaling
```bash
# Scale to 5 replicas
kubectl scale deployment stats-dashboard --replicas=5 -n production
```

### Auto-scaling (HPA)
```bash
# HPA configured in k8s manifest
# Automatically scales 3-10 replicas based on CPU/memory

# Monitor HPA
kubectl get hpa -n production -w
```

---

## 🔒 Security Checklist

- [x] Non-root container user
- [x] Read-only root filesystem
- [x] Network policies restrict ingress/egress
- [x] Resource limits set
- [x] Pod disruption budget (2 min replicas)
- [x] HTTPS only (HTTP redirects to HTTPS)
- [x] Security headers (HSTS, X-Frame-Options, etc.)
- [x] Rate limiting on API endpoints (100 req/min)

---

## 📞 Support

### Live status
```bash
# All checks in one command
./scripts/deployment-health-check.sh corvin-labs.com
```

### Rollback
```bash
# If needed, rollback to previous version
kubectl rollout undo deployment/stats-dashboard -n production
kubectl rollout status deployment/stats-dashboard -n production
```

---

**Deployment Status:** ✅ Ready  
**Go-Live Date:** [Set by ops team]  
**Runbook:** This file  
**On-Call:** [ops-team@corvin-labs.com]
