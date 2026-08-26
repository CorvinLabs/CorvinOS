# CorvinOS v1.0.0 Production Runbook

**Version:** 1.0.0  
**Release Date:** 2026-08-26  
**Target Environment:** Production (all regions)  
**Operator Audience:** DevOps, SRE, Operations Engineers  
**Emergency Contact:** ops-team@example.com  

---

## Quick Reference

| Procedure | Command | Time | Risk |
|---|---|---|---|
| **Pre-Deployment** | `./scripts/pre-deploy-check.sh v1.0.0` | 5 min | LOW |
| **Canary Deploy** | `./scripts/deploy.sh v1.0.0 --canary 10` | 10 min | LOW |
| **Full Deploy** | `./scripts/deploy.sh v1.0.0` | 20 min | MEDIUM |
| **Health Check** | `curl http://localhost:8765/health` | 1 min | NONE |
| **Rollback** | `./scripts/rollback.sh v0.9.0` | 5 min | LOW |
| **Monitoring** | See §4 (Monitoring & Observability) | — | — |

---

## Part 1: Pre-Deployment Checklist

### 1.1 Environment Validation

**MUST PASS before any deployment:**

```bash
# Verify upstream sync
git status
# Expected: "On branch main, Your branch is up to date with 'origin/main'."

# Verify version bumps
grep "version.*1.0.0" pyproject.toml package.json core/__init__.py
# Expected: All three files show version = "1.0.0"

# Verify no uncommitted changes
git diff --exit-code
# Expected: Exit code 0 (no diff)

# Verify disk space (need 5GB free)
df -h / | awk 'NR==2 {if ($4+0 < 5000000) print "FAIL: Disk < 5GB"; else print "PASS"}'

# Verify database connectivity
python -c "from core.persistence import get_db; db = get_db(); assert db.ping() == True"
# Expected: No exception

# Verify audit trail reachable
python -c "from core.compliance.audit_trail import AuditWriter; w = AuditWriter(); assert w.verify_chain() == True"
# Expected: No exception, chain verified
```

### 1.2 Backup & Snapshot

**DO NOT SKIP:**

```bash
# Backup current audit trail (hash-chain will be verified)
tar czf backups/audit-trail-v0.9.0-$(date +%Y%m%d-%H%M%S).tar.gz \
    ~/.corvin/audit.jsonl \
    ~/.corvin/tenants/

# Verify backup integrity
tar -tzf backups/audit-trail-v0.9.0-*.tar.gz > /dev/null
echo "Backup verified"

# Snapshot configuration
cp tenant.corvin.yaml tenant.corvin.yaml.backup.v0.9.0
cp ~/.config/corvin-voice/config.yaml ~/.config/corvin-voice/config.yaml.backup.v0.9.0

# Record baseline metrics
curl -s http://localhost:8765/metrics | jq . > metrics-v0.9.0-baseline.json
```

### 1.3 Operator Readiness

```bash
# Operator has runbook open (this file)
# Operator has rollback procedure memorized (see §5)
# Incident commander notified
# Monitoring dashboard open
# Slack channel #corvin-deployments ready
```

---

## Part 2: Canary Deployment (10% users, RECOMMENDED FIRST)

### 2.1 Deploy to Canary Ring

```bash
# Start canary (10% of users, auto-monitor for 30 minutes)
./scripts/deploy.sh v1.0.0 --canary 10 --auto-monitor 30m

# Expected output:
# [01:23:45] Deploying v1.0.0 to canary ring (10% capacity)
# [01:23:50] Health check PASSED
# [01:24:10] Feature flags verified
# [01:24:15] Audit trail chain verified
# [01:24:20] DEPLOYMENT COMPLETE
# [01:24:20] Monitoring enabled for 30 minutes
# [01:54:20] Canary period complete, metrics stable, ready for rollout
```

### 2.2 Monitor Canary Period (30 minutes)

**Metrics to watch:**

| Metric | Alert Threshold | Action |
|---|---|---|
| Error Rate | > 2% | Immediate rollback |
| P99 Latency | > 2s | Investigate (may be acceptable) |
| Memory Growth | > 20MB/hour | Investigate |
| Plugin Errors | > 5/min | Immediate rollback |
| Audit Trail Lag | > 5s | Immediate rollback |

**Monitoring command:**

```bash
# Terminal 1: Real-time metrics dashboard
./scripts/monitor.sh v1.0.0

# Terminal 2: Watch error logs
tail -f ~/.corvin/logs/errors.log | grep "ERROR\|CRITICAL"

# Terminal 3: Watch plugin isolation
python -c "from core.plugins import get_plugin_status; get_plugin_status()" --watch 5s
```

### 2.3 Canary Checkpoint

After 30 minutes, review:

```bash
# Get canary metrics
curl -s http://localhost:8765/metrics/canary | jq .

# If metrics look good:
echo "Canary PASSED, ready for full rollout"

# If metrics show problems:
echo "Canary FAILED, initiating rollback..."
./scripts/rollback.sh v0.9.0
# Go to §5 (Troubleshooting)
```

---

## Part 3: Full Production Deployment (50% → 100%)

### 3.1 Increase Canary to 50%

```bash
# After canary succeeds, increase to 50%
./scripts/deploy.sh v1.0.0 --canary 50 --auto-monitor 15m

# Monitor metrics again (see §2.2)
# Alert thresholds same as canary
```

### 3.2 Increase to 100%

```bash
# After 50% succeeds, deploy to all
./scripts/deploy.sh v1.0.0 --canary 100

# Health check
curl http://localhost:8765/health
# Expected: 200 OK, {"status": "healthy"}

# Verify version
curl -s http://localhost:8765/version
# Expected: {"version": "1.0.0", "released": "2026-08-26"}
```

### 3.3 Post-Deployment Verification

```bash
# Verify audit trail hash-chain intact
python -c "from core.compliance.audit_trail import AuditWriter; \
    w = AuditWriter(); \
    result = w.verify_chain(); \
    print('Chain verified: TRUE' if result else 'Chain verified: FALSE')"
# Expected: Chain verified: TRUE

# Verify all feature flags active
curl -s http://localhost:8765/flags | jq .

# Verify tenant isolation
python -c "from core.multi_tenant import validate_isolation; \
    assert validate_isolation() == True; \
    print('Tenant isolation: PASS')"
# Expected: Tenant isolation: PASS

# Verify GDPR consent gate operational
python -c "from core.compliance.consent import check_consent_gate; \
    assert check_consent_gate() == True; \
    print('Consent gate: ACTIVE')"
# Expected: Consent gate: ACTIVE

# Verify EU AI Act disclosure active
curl -s http://localhost:8765/disclosure | jq .
# Expected: {"disclosure_active": true, "ai_nature": "LLM-backed assistant"}
```

---

## Part 4: Monitoring & Observability

### 4.1 Health Check Endpoint

```bash
# Health status (liveness + readiness)
curl -s http://localhost:8765/health | jq .

# Expected response:
# {
#   "status": "healthy",
#   "timestamp": "2026-08-26T12:34:56Z",
#   "checks": {
#     "database": "OK",
#     "audit_trail": "OK",
#     "plugins": "OK",
#     "compliance": "OK"
#   },
#   "version": "1.0.0"
# }
```

### 4.2 Metrics to Monitor (SLOs)

| SLO | Target | Alert |
|---|---|---|
| Availability | 99.9% | < 99.8% for 5 min |
| Error Rate | < 1% | > 1% sustained |
| P50 Latency | < 100ms | > 200ms sustained |
| P95 Latency | < 500ms | > 1s sustained |
| P99 Latency | < 2s | > 5s sustained |
| Memory (per instance) | < 800MB | > 900MB sustained |
| Audit Trail Lag | < 1s | > 5s sustained |

**Prometheus metrics:**

```bash
# Query SLOs
curl -s http://localhost:9090/api/v1/query?query='corvin_http_request_duration_seconds{quantile="0.99"}' | jq .

# Alert on breach
# (configured in prometheus.yml)
```

### 4.3 Dashboard Setup

**Grafana dashboards (auto-created on deploy):**

1. **System Health**
   - CPU, Memory, Disk
   - Network I/O
   - Process health

2. **Application Metrics**
   - Request latency (P50, P95, P99)
   - Error rate by endpoint
   - Feature flag usage

3. **Compliance & Audit**
   - Audit trail write latency
   - Hash-chain verification status
   - Consent gate enforcement

4. **Business Metrics**
   - Task completion rate
   - Learning confidence
   - Cost per decision

**Access:** `http://localhost:3000/d/corvinOS-v1-0-0/`

### 4.4 Alerting

**Critical alerts (page on-call immediately):**

```yaml
- name: HighErrorRate
  expr: rate(corvin_http_requests_total{status=~"5.."}[5m]) > 0.01
  for: 5m
  action: page

- name: AuditTrailBroken
  expr: corvin_audit_trail_chain_verified == 0
  for: 1m
  action: page

- name: PluginEscape
  expr: corvin_plugin_isolation_violation == 1
  for: 1m
  action: page
```

---

## Part 5: Troubleshooting & Rollback

### 5.1 Symptoms & Responses

| Symptom | Likely Cause | Response |
|---|---|---|
| 500 errors spike | Deployment issue | Immediate rollback |
| P99 latency > 5s | Resource contention or deadlock | Investigate logs, may roll back |
| Audit trail lag > 10s | Database bottleneck | Scale DB, monitor |
| Plugin errors > 10/min | Plugin incompatibility | Disable plugin, investigate |
| Memory growth > 50MB/min | Memory leak | Immediate rollback |
| GDPR gate failing | Compliance bug | Immediate rollback |

### 5.2 Immediate Rollback Procedure

**If ANY critical metric breaches, execute this IMMEDIATELY:**

```bash
# 1. Stop accepting new requests
./scripts/drain.sh v1.0.0 --timeout 30s
# (Waits up to 30s for in-flight requests to complete)

# 2. Start rollback
./scripts/rollback.sh v0.9.0

# Expected:
# [12:34:56] Stopping v1.0.0
# [12:35:01] Starting v0.9.0
# [12:35:15] Health check: PASS
# [12:35:20] Rollback complete

# 3. Verify rollback
curl -s http://localhost:8765/version | jq .version
# Expected: "0.9.0"

# 4. Notify team
echo "ROLLBACK COMPLETE: v1.0.0 → v0.9.0" | mail -s "URGENT: Production Rollback" ops-team@example.com
```

### 5.3 Audit Trail Recovery (if corrupted)

```bash
# Verify chain is broken
python -c "from core.compliance.audit_trail import AuditWriter; \
    w = AuditWriter(); \
    print('Chain valid:', w.verify_chain())"

# If chain is broken:
# DO NOT attempt to fix - instead:
# 1. Immediately notify compliance team
# 2. Preserve audit log for investigation
# 3. Rollback to last known good state
# 4. Root cause analysis required before re-deploy

# Preserve for investigation
cp ~/.corvin/audit.jsonl audit-trail-corrupted-$(date +%Y%m%d-%H%M%S).jsonl
tar czf audit-evidence.tar.gz audit-trail-corrupted-*.jsonl

# Notify
echo "Audit trail corrupted, preserved in audit-evidence.tar.gz" | \
    mail -s "CRITICAL: Audit Trail Integrity Failure" compliance@example.com
```

### 5.4 Post-Rollback Analysis

After rollback, preserve evidence and investigate:

```bash
# Collect logs
mkdir -p rollback-analysis/
cp ~/.corvin/logs/* rollback-analysis/
cp metrics-v1.0.0-postmortem.json rollback-analysis/

# Identify root cause
python scripts/debug-deployment.py \
    --version v1.0.0 \
    --reason "rollback" \
    --logs rollback-analysis/

# Notify team
echo "Rollback analysis available in rollback-analysis/" | \
    mail -t ops-team@example.com
```

---

## Part 6: Feature Flags

### 6.1 View All Flags

```bash
curl -s http://localhost:8765/flags | jq .

# Expected (v1.0.0):
# {
#   "task_orchestrator_multiphase": true,
#   "auto_session_renewal": true,
#   "notification_system_v1": false,  # opt-in
#   "learning_phase3": true,
#   "concurrency_framework_rw_lock": true,
#   "context_propagation_adr0424": true,
#   ...
# }
```

### 6.2 Enable/Disable Feature

```bash
# Enable notification system (opt-in by default)
curl -X POST http://localhost:8765/flags/notification_system_v1 \
    -H "Content-Type: application/json" \
    -d '{"enabled": true}'

# Verify
curl -s http://localhost:8765/flags | jq '.notification_system_v1'
# Expected: true
```

### 6.3 Feature Flag Reset

```bash
# Reset to v1.0.0 defaults (all flags to v1.0.0 shipping state)
./scripts/reset-flags.sh v1.0.0
```

---

## Part 7: Disaster Recovery

### 7.1 Complete Service Loss

If the entire service is down:

```bash
# 1. Assess the situation
systemctl status corvin-console
systemctl status corvin-gateway

# 2. Check logs
journalctl -u corvin-console -u corvin-gateway -n 100 --no-pager

# 3. Attempt restart (one-time)
systemctl restart corvin-console corvin-gateway
sleep 5
curl http://localhost:8765/health

# If still down after restart:
# → Go to step 5.2 (Immediate Rollback Procedure)
```

### 7.2 Partial Service Degradation

```bash
# Scale down to shed load
./scripts/scale.sh v1.0.0 --replicas 1

# Monitor recovery
watch -n 1 'curl -s http://localhost:8765/metrics | jq .error_rate'

# If error rate improves:
# → Gradually scale back up
./scripts/scale.sh v1.0.0 --replicas 3

# If error rate persists:
# → Go to step 5.2 (Immediate Rollback Procedure)
```

### 7.3 Data Corruption (Audit Trail)

**See §5.3 above** — audit trail corruption is CRITICAL and requires immediate escalation.

---

## Part 8: Maintenance Windows

### 8.1 Weekly Tasks

Every Monday 02:00 UTC (low-traffic window):

```bash
# Verify audit trail integrity
python -c "from core.compliance.audit_trail import AuditWriter; \
    w = AuditWriter(); \
    assert w.verify_chain() == True; \
    print('Audit chain: PASS')"

# Run optimization
python -c "from core.persistence import optimize_db; optimize_db()"

# Prune old logs (>30 days)
find ~/.corvin/logs -name "*.log" -mtime +30 -delete
```

### 8.2 Monthly Tasks

First of each month, 03:00 UTC:

```bash
# Full backup
./scripts/backup.sh --full

# Capacity planning
python scripts/capacity-forecast.py --output capacity-report-$(date +%Y-%m).json

# Compliance report
python -c "from core.compliance.reporter import monthly_report; monthly_report()"
```

### 8.3 Quarterly Tasks

First day of Q1/Q2/Q3/Q4, 00:00 UTC:

```bash
# Security audit
./scripts/security-audit.sh v1.0.0

# Performance baseline reset
./scripts/baseline.sh --reset

# Feature flag audit
./scripts/audit-flags.sh
```

---

## Part 9: Emergency Contacts

| Role | Contact | Escalation |
|---|---|---|
| **On-Call SRE** | ops-team@example.com | Slack: #corvin-oncall |
| **Compliance Officer** | compliance@example.com | Phone: +1-XXX-YYY-ZZZZ |
| **Security Officer** | security@example.com | Phone: +1-XXX-YYY-ZZZZ |
| **VP Engineering** | vp-eng@example.com | Director escalation |

**Incident Severity:**

- **CRITICAL** (Audit trail down, GDPR breach, plugin escape): Page VP + Compliance + Security
- **HIGH** (Error rate > 5%, P99 latency > 10s): Page on-call SRE + Compliance
- **MEDIUM** (Error rate 1-5%, performance degradation): Notify ops-team, create incident ticket
- **LOW** (Isolated errors, minor degradation): Log ticket, normal business hours response

---

## Part 10: Post-Deployment Handoff

After deployment completes and stabilizes (24 hours without incident):

1. **Operations team acknowledges receipt of runbook** (this document)
2. **Monitoring team confirms dashboards are active**
3. **On-call rotation includes v1.0.0 escalation procedures**
4. **Compliance team confirms GDPR/EU AI Act monitoring is active**

```bash
# Final handoff checklist
echo "Deployment v1.0.0 stable for 24 hours" > /dev/null
echo "✓ Operations ready"
echo "✓ Monitoring active"
echo "✓ Incident response tested"
echo "✓ Compliance verified"
echo "✓ Handoff complete"
```

---

**Document Version:** 1.0.0  
**Last Updated:** 2026-08-26  
**Next Review:** Post-canary analysis (2026-08-29)

For questions or updates, contact: ops-team@example.com
