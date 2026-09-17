# Context-Drift Production Deployment Guide

**Document Date:** 2026-09-17  
**Status:** Production-Ready ✅  
**ADR References:** ADR-0407 (Session Context Drift Prevention), ADR-0362 (Production Deployment)

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Pre-Deployment Checklist](#pre-deployment-checklist)
4. [Deployment Steps](#deployment-steps)
5. [Post-Deployment Verification](#post-deployment-verification)
6. [Troubleshooting](#troubleshooting)
7. [Rollback Procedure](#rollback-procedure)

---

## Overview

Context-Drift is a production-ready system for detecting and preventing goal drift in long-running CorvinOS sessions. This guide covers:

- **Staging Deployment:** Validate in pre-production environment
- **Production Deployment:** Safe rollout to production with zero downtime
- **Monitoring:** Prometheus metrics, Grafana dashboards, alert rules
- **Learning Loop:** Automatic threshold tuning based on user feedback
- **Compliance:** GDPR Art. 30/32, EU AI Act Art. 50 verification

---

## Prerequisites

### Required Components

- Kubernetes cluster (1.20+)
- Helm 3.x
- Docker registry access
- Prometheus + Grafana (optional, but recommended)
- kubectl access to production cluster
- Git access to CorvinOS repository

### System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| Memory | 512 MB | 2 GB |
| CPU | 250m | 1000m |
| Disk | 10 GB | 50 GB |
| Network | 10 Mbps | 100 Mbps |

### Access Requirements

- Production cluster: kubectl access
- Docker registry: push/pull permissions
- Monitoring: Prometheus/Grafana admin access
- On-call: Slack channel #corvin-prod-alerts

---

## Pre-Deployment Checklist

Run this checklist before any deployment:

```bash
# 1. Validate code quality
cd /home/shumway/projects/CorvinOS
python3 -m core.deployment.context_drift_deployment_validation

# 2. Run all tests (must be 100% passing)
pytest tests/e2e/test_staging_integration_full.py -v
pytest tests/e2e/test_context_drift_learning_loop.py -v

# 3. Check Git status (must be clean)
git status  # Should show: "nothing to commit, working tree clean"

# 4. Verify ADRs are up-to-date
ls -la corvin_decisions/decisions/ADR-0407*.md
ls -la corvin_decisions/decisions/ADR-0404*.md
ls -la corvin_decisions/decisions/ADR-0405*.md
ls -la corvin_decisions/decisions/ADR-0406*.md

# 5. Run compliance verification
python3 -m core.compliance.context_drift_compliance_report

# All checks must pass before proceeding!
```

---

## Deployment Steps

### Step 1: Staging Deployment (Days 1-2)

```bash
# 1. Deploy to staging namespace
cd /home/shumway/projects/CorvinOS
bash scripts/deploy_context_drift_staging.sh

# Expected output:
# ✅ Deployment validation passed
# ✅ Docker image built: corvin/context-drift:stage-XXXXX
# ✅ Namespace staging ready
# ✅ Helm deployment successful
# ✅ Rollout complete
```

### Step 2: Staging Smoke Tests (Days 2-3)

```bash
# Run E2E tests against staging
pytest tests/e2e/test_staging_integration_full.py::TestContextDriftStagingIntegration -v -s

# Expected: All 12 tests passing
#  - test_end_to_end_goal_lifecycle ✅
#  - test_audit_trail_immutability ✅
#  - test_compliance_gdpr_no_pii ✅
#  - test_compliance_eu_ai_act_transparency ✅
#  - test_performance_slos_met ✅
#  - test_cross_session_isolation ✅
#  - test_concurrent_goal_operations ✅
#  - test_error_handling_graceful_degradation ✅
#  - test_health_check ✅
```

### Step 3: Learning Loop Validation (Days 3-4)

```bash
# Collect feedback samples
# (This runs automatically as users interact with staging)

# After 48 hours of staging use:
curl http://<staging-service>/v1/context-drift/metrics | jq '.feedback_quality'
# Expected: > 0.80 (>80% accuracy)

curl http://<staging-service>/v1/context-drift/health | jq '.status'
# Expected: "healthy"
```

### Step 4: Monitoring Setup (Days 4-5)

```bash
# 1. Add Prometheus scrape config
kubectl create configmap context-drift-prometheus \
  --from-file=helm/context-drift/alerts.yaml \
  -n production

# 2. Import Grafana dashboard
curl -X POST http://grafana:3000/api/dashboards/db \
  -H "Authorization: Bearer $GRAFANA_TOKEN" \
  -d @helm/context-drift/dashboards/context-drift-overview.json

# 3. Configure alert channels
# - Slack: #corvin-prod-alerts
# - PagerDuty: context-drift service
# - Email: ops-team@corvin.ai
```

### Step 5: Production Deployment (Days 5-6)

```bash
# 1. Run production readiness checklist
python3 scripts/context_drift_production_readiness_checklist.py
# All 20 checks must pass

# 2. Create production deployment
bash scripts/deploy_context_drift_production.sh

# Expected output:
# 🚀 DEPLOYING CONTEXT-DRIFT TO PRODUCTION
# ✅ Production readiness checks passed
# ✅ Docker image built: corvin/context-drift:prod-XXXXX
# ✅ Kubernetes deployment successful
# ✅ Rollout complete
```

### Step 6: Production Verification (Days 6+)

```bash
# Check pod status
kubectl get pods -n production -l app=context-drift-prod

# Check service status
kubectl get svc -n production -l app=context-drift-prod

# Check logs
kubectl logs -f -l app=context-drift-prod -n production

# Verify metrics flowing
curl http://prometheus:9090/api/v1/query?query=corvin_context_drift_active_goals
# Expected: active_goals > 0

# Verify alerts are armed
curl http://prometheus:9090/api/v1/rules | jq '.data.groups[0].rules[] | select(.name | contains("drift"))'
# Expected: 10+ alert rules
```

---

## Post-Deployment Verification

### Health Checks

```bash
# 1. API Health
curl http://<prod-service>/v1/context-drift/health
# Expected: {"status": "healthy", "threshold": 0.35, "feedback_quality": 0.85}

# 2. Metrics Available
curl http://prometheus:9090/api/v1/query?query=up{job="context-drift"}
# Expected: [{"value": [timestamp, "1"]}]

# 3. Goals Being Monitored
curl http://<prod-service>/v1/context-drift/metrics | jq '.goals.active'
# Expected: > 0 (shows active monitoring)

# 4. Audit Trail Intact
curl http://<prod-service>/v1/context-drift/audit?limit=10 | jq '.events[0].hash_chain_valid'
# Expected: true
```

### Monitoring Verification

```bash
# 1. Check Grafana dashboard loads
open http://grafana:3000/d/context-drift-overview

# Expected panels:
# - Goal Alignment Checks (5m rate)
# - Alignment Score Distribution
# - Current Threshold (stat)
# - Feedback Quality (gauge)
# - Threshold Tuning Iterations
# - Alignment Check Latency
# - Drift Alerts (1h)
# - Active Goals / Sessions
# - Goals Restored (24h)

# 2. Verify alerts can fire
# Create test alert by triggering high drift condition
# (Requires manual test or synthetic traffic)

# 3. Check log aggregation
# View logs in Loki / ELK / your logging backend
kubectl logs -f deployment/context-drift-prod -n production | head -20
```

### Performance Validation

```bash
# 1. Check SLO compliance
# Alignment check latency should be <5ms p95
curl http://prometheus:9090/api/v1/query?query='histogram_quantile(0.95, rate(corvin_context_drift_alignment_check_latency_ms_bucket[5m]))'

# Expected: < 5.0 ms

# 2. Check throughput
curl http://prometheus:9090/api/v1/query?query='increase(corvin_context_drift_goal_alignment_checks_total[1m])'
# Expected: 100+ checks/min (depending on traffic)
```

---

## Troubleshooting

### Common Issues

#### 1. Pod won't start

```bash
# Check events
kubectl describe pod -n production -l app=context-drift-prod

# Check logs
kubectl logs -n production -l app=context-drift-prod --previous

# Common causes:
# - Image not found: check Docker registry
# - Resource requests too high: check node capacity
# - Config error: validate YAML

# Fix: Roll back and investigate
bash scripts/rollback_context_drift_production.sh
```

#### 2. Health check failing

```bash
# Check connectivity
kubectl exec -it <pod-name> -n production -- curl localhost:8765/v1/context-drift/health

# Check logs for errors
kubectl logs <pod-name> -n production | grep -i "error\|fail"

# Common causes:
# - Database connection: check DB credentials
# - Audit chain broken: check file permissions
# - Out of disk: check PVC size

# Fix: Fix root cause, then roll restart
kubectl rollout restart deployment/context-drift-prod -n production
```

#### 3. High drift rate alert

```bash
# Check feedback quality
curl http://<service>/v1/context-drift/metrics | jq '.feedback_quality'

# If low (<70%):
#   → Threshold needs tuning
#   → Check user feedback accuracy
#   → May indicate system drift (not bug)

# If high (>85%):
#   → Drift alerts are correct
#   → Operator needs to review/fix drifting goals

# Check trends
kubectl logs -n production -l app=context-drift-prod | grep "drift_score"
```

#### 4. Learning loop not converging

```bash
# Check feedback count
curl http://<service>/v1/context-drift/metrics | jq '.tuning.samples_since_last_tuning'

# If < 10 samples:
#   → Insufficient feedback collected
#   → Wait for more users/goals

# If > 100 with no tuning:
#   → Check threshold auto-tuning is enabled
#   → Check for errors in learning loop logs
```

---

## Rollback Procedure

### Safe Rollback

```bash
# Step 1: Switch traffic to previous version
kubectl set image deployment/context-drift-prod \
  context-drift=corvin/context-drift:prod-previous \
  -n production

# Step 2: Wait for rollout
kubectl rollout status deployment/context-drift-prod -n production --timeout=10m

# Step 3: Verify health
curl http://<service>/v1/context-drift/health

# Step 4: Notify team
# Post to #corvin-prod-alerts: "Context-Drift rolled back to previous version"
```

### Full System Rollback

```bash
# If rollback still failing, use Helm:
helm rollback context-drift-prod -1 -n production

# Verify
helm status context-drift-prod -n production
kubectl get pods -n production -l app=context-drift-prod

# Then investigate root cause
```

---

## Maintenance & Operations

### Daily Checks

```bash
# 1. Check alerts
# Open: http://prometheus:9090/alerts
# Should show 0 active alerts (or known/expected alerts)

# 2. Check metrics
# Open: Grafana dashboard
# Verify all panels have data, no anomalies

# 3. Check logs
# Open: your logging backend
# Look for ERROR/CRITICAL messages
```

### Weekly Maintenance

```bash
# 1. Review feedback quality
curl http://<service>/v1/context-drift/metrics | jq '.feedback_quality'

# 2. Check tuning history
curl http://<service>/v1/context-drift/metrics | jq '.tuning.tuning_iterations'

# 3. Verify audit trail size
du -sh ~/.corvin/tenants/*/global/audit.jsonl

# 4. Review alerting SLO
# Threshold tune at least once per week (if traffic > 100 checks/day)
```

### Quarterly Review

```bash
# 1. Generate compliance report
python3 -m core.compliance.context_drift_compliance_report
# Review GDPR/EU AI Act compliance

# 2. Audit access logs
# Verify only authorized operators accessed drift data

# 3. Review feedback quality trend
# Check if accuracy converging or degrading over time

# 4. Capacity planning
# Do we need more resources? Check CPU/memory graphs
```

---

## Support & Escalation

### On-Call Contacts

- **Primary:** ops-team@corvin.ai (Slack: #corvin-prod-alerts)
- **Escalation:** infrastructure@corvin.ai
- **Emergency:** +1-XXX-XXX-XXXX

### Runbooks

- **High Drift Rate:** See [context-drift-ops.md](../runbooks/context-drift-ops.md)
- **Performance Degradation:** See [context-drift-ops.md](../runbooks/context-drift-ops.md)
- **Audit Trail Issues:** See [context-drift-ops.md](../runbooks/context-drift-ops.md)

---

## Sign-Off

**Deployed By:** [Your Name]  
**Date:** 2026-09-17  
**Version:** Context-Drift v1.0.0  
**Approval:** ✅ Production-Ready

---

*Last Updated: 2026-09-17*  
*Next Review: 2026-10-17*
