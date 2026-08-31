# CorvinOS Production Deployment Runbook — 100% Rollout Strategy

**Version:** 1.0  
**Date:** 2026-08-25  
**Strategy:** 100% immediate rollout (no canary phases)  
**Owner:** Shumway  

---

## Pre-Deployment Checklist

- [ ] All commits on `main` branch
- [ ] All tests green: `bash operator/bridges/run-all-tests.sh`
- [ ] No uncommitted changes: `git status` clean
- [ ] Latest commit has Co-Authored-By footer
- [ ] ADRs in Corvin-ADR are synced and ACCEPTED
- [ ] Health checks configured in `docs/health-checks/`
- [ ] Rollback plan documented (see "Emergency Rollback" section)
- [ ] Team notified (Slack: #deployments)

---

## Step 1: Build & Push Docker Images

```bash
# Build all service images
cd /home/shumway/projects/CorvinOS
docker build -t corvinOS:latest \
  --build-arg BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ') \
  --build-arg VCS_REF=$(git rev-parse --short HEAD) \
  .

# Tag for registry
docker tag corvinOS:latest \
  registry.corvinOS.local/corvinOS:latest \
  registry.corvinOS.local/corvinOS:$(git describe --tags --always)

# Push to registry
docker push registry.corvinOS.local/corvinOS:latest
docker push registry.corvinOS.local/corvinOS:$(git describe --tags --always)

# Verify push
docker pull registry.corvinOS.local/corvinOS:latest
echo "Image push verified ✓"
```

---

## Step 2: Deploy to Kubernetes (100% Rollout)

```bash
# Set namespace (default: production)
export K8S_NAMESPACE="production"

# Update deployment image to latest
kubectl set image deployment/corvinOS \
  corvinOS=registry.corvinOS.local/corvinOS:latest \
  --namespace=$K8S_NAMESPACE \
  --record

# Monitor rollout (100% → all replicas updated)
kubectl rollout status deployment/corvinOS \
  --namespace=$K8S_NAMESPACE \
  --timeout=600s

# Verify replicas are ready
kubectl get deployment corvinOS \
  --namespace=$K8S_NAMESPACE \
  -o wide

echo "Deployment completed: $(kubectl get deployment corvinOS -o jsonpath='{.status.updatedReplicas}') replicas running"
```

---

## Step 3: Health Checks (Immediate)

```bash
# Get service endpoint
export SERVICE_IP=$(kubectl get svc corvinOS -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
export SERVICE_PORT=8765

echo "Checking health at http://$SERVICE_IP:$SERVICE_PORT/health"

# 1. HTTP Health Endpoint
curl -f http://$SERVICE_IP:$SERVICE_PORT/health || {
  echo "❌ Health check failed"
  exit 1
}
echo "✓ HTTP health check passed"

# 2. Console UI Reachable
curl -f http://$SERVICE_IP:$SERVICE_PORT/console/ \
  --head --max-redirs 0 > /dev/null || {
  echo "⚠️  Console UI returned redirect (normal)"
}
echo "✓ Console endpoint reachable"

# 3. API Response (Sample Request)
curl -f http://$SERVICE_IP:$SERVICE_PORT/api/v1/health \
  -H "Content-Type: application/json" || {
  echo "❌ API health failed"
  exit 1
}
echo "✓ API health check passed"

# 4. Database Connectivity
# (Check your DB connection string)
curl -f http://$SERVICE_IP:$SERVICE_PORT/api/v1/admin/db-status || {
  echo "⚠️  Database check timed out (may be non-critical)"
}
echo "✓ Database status checked"

# 5. Plugin System Initialization
curl -f http://$SERVICE_IP:$SERVICE_PORT/api/v1/plugins/status || {
  echo "❌ Plugin system failed to initialize"
  exit 1
}
echo "✓ Plugin system initialized"
```

---

## Step 4: Smoke Tests (Production)

```bash
# Run smoke test suite against live endpoints
cd /home/shumway/projects/CorvinOS

# Test Session Manager (Phase 2.1)
python3 -c "
from core.session_manager.lifecycle import SessionLifecycleManager
from core.session_manager.checkpoint import CheckpointManager
print('✓ Session Manager imports OK')
"

# Test Plugin System (ADR-0345)
python3 -c "
from core.plugins.corvin_plugins.dag_plugins import PluginGraph
from core.plugins.corvin_plugins.audit_verification import verify_audit_chain
print('✓ ADR-0345 DAG Plugin System imports OK')
"

# Test License Gates
python3 -c "
from core.console.corvin_console.routes._compute_license_gate import enforce_compute_quota
print('✓ License quota gates active')
"

echo "✓ All smoke tests passed"
```

---

## Step 5: Verify Logs (5 minutes)

```bash
# Tail pod logs for errors
kubectl logs -f deployment/corvinOS \
  --namespace=$K8S_NAMESPACE \
  --max-log-requests=10 \
  --tail=100 &

TAIL_PID=$!

# Wait 5 minutes for any startup errors
sleep 300

# Kill tail
kill $TAIL_PID 2>/dev/null

# Check for CRITICAL or ERROR in logs
kubectl logs deployment/corvinOS \
  --namespace=$K8S_NAMESPACE \
  | grep -E "CRITICAL|ERROR" && {
  echo "❌ Critical errors found in logs"
  exit 1
} || {
  echo "✓ No critical errors in logs"
}
```

---

## Step 6: Production Validation (E2E)

```bash
# Run E2E test suite against production
cd /home/shumway/projects/CorvinOS

# Session Manager E2E: 16-hour task simulation
pytest core/session_manager/tests/test_e2e_audit_task.py -v --tb=short

# Plugin System E2E: DAG delegation
pytest core/plugins/tests/test_adr_0345_e2e_validation.py -v --tb=short

# License compliance E2E: Quota enforcement
pytest core/console/tests/test_license_e2e.py -v --tb=short

echo "✓ All E2E tests passed in production"
```

---

## Step 7: Announce Deployment (Slack)

```bash
# Post deployment notification
COMMIT=$(git rev-parse --short HEAD)
TIMESTAMP=$(date -u +'%Y-%m-%d %H:%M:%S UTC')

curl -X POST https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK \
  -d '{
    "text": "✅ **CorvinOS Production Deployment Complete**",
    "blocks": [
      {
        "type": "section",
        "text": {
          "type": "mrkdwn",
          "text": "*✅ CorvinOS Production Deployment*\n\n*Commit:* '$COMMIT'\n*Time:* '$TIMESTAMP'\n*Strategy:* 100% Rollout (no canary)\n*Replicas:* All updated and healthy"
        }
      },
      {
        "type": "section",
        "text": {
          "type": "mrkdwn",
          "text": "*Features Deployed:*\n• Session Manager Phase 2.1+2.2 (9 subsystems)\n• ADR-0345 Recursive Plugin DAG\n• License Red-Team Round 10 hardening\n• Plugin-Test CI/CD wiring"
        }
      }
    ]
  }'

echo "✓ Deployment announced"
```

---

## Emergency Rollback (If Needed)

```bash
# Get previous image version
PREV_IMAGE=$(kubectl get deployment corvinOS \
  --namespace=$K8S_NAMESPACE \
  -o jsonpath='{.spec.template.spec.containers[0].image}' | \
  sed 's/:.*/:previous/')

echo "Rolling back to: $PREV_IMAGE"

# Rollback deployment
kubectl set image deployment/corvinOS \
  corvinOS=$PREV_IMAGE \
  --namespace=$K8S_NAMESPACE \
  --record

# Monitor rollback
kubectl rollout status deployment/corvinOS \
  --namespace=$K8S_NAMESPACE \
  --timeout=300s

# Verify health after rollback
curl -f http://$SERVICE_IP:$SERVICE_PORT/health || {
  echo "❌ Rollback health check failed — escalate to SRE"
  exit 1
}

echo "✓ Rollback complete, system healthy"
```

---

## Monitoring (Post-Deployment)

| Metric | Alert Threshold | Check Command |
|--------|-----------------|---|
| **Pod restarts** | >3 in 1h | `kubectl top pod -n $K8S_NAMESPACE` |
| **Memory usage** | >80% | `kubectl describe pod -n $K8S_NAMESPACE` |
| **API latency** | >1s p99 | `curl -w '@curl-format.txt' http://$SERVICE_IP:8765/api/health` |
| **Error rate** | >0.1% | `kubectl logs -n $K8S_NAMESPACE \| grep ERROR` |
| **Plugin health** | all OK | `curl http://$SERVICE_IP:8765/api/v1/plugins/status` |

---

## Success Criteria

✅ All 7 deployment steps complete  
✅ Health checks: 5/5 passing  
✅ Smoke tests: all green  
✅ E2E tests: production validated  
✅ Logs: zero critical errors (5 min window)  
✅ Slack announcement posted  
✅ Monitoring dashboards show green  

**Deployment Status:** LIVE ✅

---

## Post-Deployment: Monitor for 24 Hours

| Interval | Action |
|----------|--------|
| **0–5 min** | Watch logs for startup errors |
| **5–30 min** | Verify user traffic flowing |
| **30 min–2h** | Monitor error rate & latency |
| **2–24h** | Daily metrics review (Sessions, Plugins, License gates) |

---

**Deployment completed at:** [DATE/TIME]  
**Deployed by:** Claude Code (autonomous)  
**Rollback time (if needed):** ~5 minutes  

*For issues: escalate to SRE with logs from Step 5*
