# Phase 5 Operator Runbook — ADR-0423 Production Operations Manual

**Version:** 1.0  
**Date:** 2026-08-29  
**Target Audience:** On-Call SREs, DevOps, Operations Team  
**Purpose:** Step-by-step procedures for deploying, monitoring, and operating ADR-0423 Phase 5 (all 7 layers)

---

## Table of Contents

1. [Quick Reference](#quick-reference)
2. [Pre-Deployment Checklist](#pre-deployment-checklist)
3. [Deployment Procedure](#deployment-procedure)
4. [Monitoring & Alerting](#monitoring--alerting)
5. [Incident Response](#incident-response)
6. [Rollback Procedure](#rollback-procedure)
7. [Troubleshooting Guide](#troubleshooting-guide)
8. [On-Call Procedures](#on-call-procedures)

---

## Quick Reference

### Key Commands

```bash
# Check Phase 5 status
corvin status phase5

# View production metrics
curl http://127.0.0.1:8765/v1/monitoring/production | jq .

# Check recent alerts
curl http://127.0.0.1:8765/v1/monitoring/alerts | jq .

# List features by tier
corvin feature list --state PRODUCTION

# Promote a feature (manual)
corvin feature promote feature_id --force

# Demote a feature (rollback)
corvin feature demote feature_id "Blocking bug in production"

# View audit trail
corvin audit show --limit 100

# View MemoryCoordinator stats
corvin brain memory-coordinator stats
```

### Critical Thresholds

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| Error rate | >0.5% | >1% | Page on-call, check logs |
| p99 latency | >200ms | >500ms | Investigate ContextBus backlog |
| Throughput | <100/sec | <50/sec | Check for stalled workflows |
| Audit backlog | >50k events | >100k events | Flush audit, check disk space |
| Memory growth | >10%/hour | >20%/hour | Investigate MemoryCoordinator |

### On-Call Escalation

```
Severity 1 (Critical):
  - Error rate >1%
  - Throughput <50/sec
  - Audit trail down
  → Page on-call immediately
  → Declare SEV-1, start incident
  
Severity 2 (High):
  - Error rate >0.5%
  - p99 latency >500ms
  → Page on-call if no response in 15 min
  → Assign ticket, investigate
  
Severity 3 (Medium):
  - Error rate >0.1%
  - Feature stuck in ALPHA
  → File ticket, investigate next business day
```

---

## Pre-Deployment Checklist

Before deploying Phase 5, complete these steps:

### 1. Infrastructure Ready ✓
- [ ] Database backups configured (daily)
- [ ] Audit trail storage: >100GB free
- [ ] Message queue (if used): tested
- [ ] Load balancer: canary traffic splitting enabled
- [ ] Monitoring stack: Prometheus + Grafana live

### 2. Team Briefing ✓
- [ ] On-call rotation schedule confirmed
- [ ] Escalation contacts updated
- [ ] Runbook shared with team
- [ ] Practice incident reviewed (optional but recommended)

### 3. Feature Flags ✓
- [ ] All Phase 5 feature flags default-OFF
- [ ] Feature flag toggle API working
- [ ] Rollback procedure tested locally

### 4. Monitoring Dashboards ✓
- [ ] Production metrics dashboard live
- [ ] Alert rules configured in PagerDuty / Slack
- [ ] Webhook endpoints tested
- [ ] Historical baselines recorded (throughput, latency, errors)

### 5. Rollback Image Ready ✓
- [ ] Previous stable version tagged (e.g., `v1.2.0`)
- [ ] Blue-green deployment slots prepared
- [ ] Rollback script tested (`scripts/rollback-phase5.sh`)

### 6. Operator Training ✓
- [ ] Operators can:
  - [ ] View metrics via API
  - [ ] Promote/demote features
  - [ ] Trigger manual rollback
  - [ ] Read audit trail
  - [ ] Escalate to on-call

---

## Deployment Procedure

### Phase 5 Canary Deployment (10% Users)

**Timeline:** ~30 minutes  
**Rollback time:** <5 minutes

#### Step 1: Pre-flight Checks (5 min)

```bash
# Verify healthy baseline
curl http://127.0.0.1:8765/v1/monitoring/production | jq '.throughput, .error_rate'

# Expected:
# "throughput": {"workflows_per_sec": 50-100, ...}
# "error_rate": {"error_rate_percent": <0.1, ...}

# Check audit trail is writable
ls -lh ~/.corvin/audit.jsonl

# Verify feature flags exist and default to OFF
corvin feature list | grep phase5 | grep OFF
```

#### Step 2: Blue-Green Deployment (10 min)

```bash
# Build Phase 5 image
docker build -t corvinOS:phase5-canary .

# Tag as new green slot
docker tag corvinOS:phase5-canary corvinOS:green-phase5

# Start green slot (parallel to blue)
docker-compose -f docker-compose.green.yml up -d

# Health check green slot
curl http://127.0.0.1:8766/health
# Expected: {"status": "healthy", "phase": 5}

# Verify metrics endpoint
curl http://127.0.0.1:8766/v1/monitoring/production | jq '.throughput'
```

#### Step 3: Canary Traffic Split (5 min)

```bash
# Update load balancer to route 10% to green
# (Syntax depends on LB; example for nginx)
vi /etc/nginx/conf.d/upstream.conf
# upstream corvinOS {
#   server blue-phase4:8765 weight=90;   # 90%
#   server green-phase5:8766 weight=10;  # 10% CANARY
# }
nginx -s reload

# Verify split is active
curl -H "X-LB-Debug: 1" http://127.0.0.1/status | jq '.upstream'
```

#### Step 4: Monitor Canary (10–60 min)

```bash
# Watch metrics dashboard in real time
# Success criteria (first 10 minutes):
#   - Error rate stable (<0.2%)
#   - Latency stable (no spike >50%)
#   - Feature flags can be toggled (no crashes)
#   - Audit trail grows normally

# Every 2 minutes, check for errors
watch -n 2 'curl -s http://127.0.0.1:8765/v1/monitoring/alerts | jq ".count"'

# After 10 min: if healthy, proceed to step 5
# After 10 min: if errors, trigger rollback (see Rollback Procedure)
```

#### Step 5: Feature Flag Activation (Optional, after 10 min healthy)

```bash
# Enable individual Phase 5 features behind flags (ships dark by default)
# Example: enable vibe_guidance_enabled for 10% of users

corvin feature set vibe_guidance_enabled --enabled=true --percent=10

# Verify flag state
corvin feature show vibe_guidance_enabled
# Expected: enabled=true, percent=10%

# Monitor impact (5–10 min)
# If no issues, can increase percent gradually:
corvin feature set vibe_guidance_enabled --percent=50
corvin feature set vibe_guidance_enabled --percent=100
```

#### Step 6: Full Ramp (if canary healthy >30 min)

```bash
# After canary proves stable (30+ min):
# Shift 100% traffic to green
upstream corvinOS {
  server green-phase5:8766 weight=100;  # 100% (was 10%)
}
nginx -s reload

# Decommission blue slot
docker-compose -f docker-compose.blue.yml down

# Blue is now cold standby for rollback
```

---

## Monitoring & Alerting

### Dashboard: Production Metrics

**URL:** `http://127.0.0.1:8765/v1/monitoring/production`

**Key metrics to watch:**

```json
{
  "throughput": {
    "workflows_per_sec": 50,        // Target: >50
    "decisions_per_sec": 5000       // Target: >1000
  },
  "latency_ms": {
    "p50": 5,                       // Target: <100
    "p95": 50,                      // Target: <200
    "p99": 100                      // Target: <500
  },
  "errors": {
    "error_rate_percent": 0.05,     // Target: <0.1%
    "by_component": {
      "context_bus": 0,
      "checkpoint_manager": 0,
      "loop_engineer": 1,           // 1 error detected
      "promotion_daemon": 0
    }
  },
  "features": {
    "ALPHA": 3,                     // Features in alpha
    "BETA": 5,                      // Beta
    "STABLE": 10,                   // Stable
    "PRODUCTION": 20                // Production
  },
  "audit": {
    "total_events": 50000,          // Events recorded
    "growth_per_hour": 10000,       // New events/hour
    "storage_mb": 500               // Total storage used
  },
  "memory": {
    "split_events": 10,             // Session splits
    "merge_events": 8,              // Session merges
    "active_contexts": 42           // Active workflows
  }
}
```

### Alert Thresholds & Actions

| Alert | Threshold | Action |
|-------|-----------|--------|
| **high_error_rate** | >1% for 5 min | Page on-call, check error logs, consider rollback |
| **high_latency** | p99 >500ms for 5 min | Check ContextBus queue depth, may need to scale |
| **low_throughput** | <50/sec for 5 min | Check for stalled workflows, audit trail issues |
| **audit_backlog** | >100k events for 10 min | Check disk space, flush audit if needed |
| **memory_spike** | >20% growth/hour | Investigate MemoryCoordinator, check for memory leaks |

### Setting Up Alerts

#### Slack Integration
```bash
# Post metrics to Slack every 5 minutes
curl -X POST https://hooks.slack.com/services/YOUR/WEBHOOK \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "Phase 5 Metrics",
    "blocks": [
      {
        "type": "section",
        "text": {
          "type": "mrkdwn",
          "text": "*Error Rate*: 0.05%\n*Throughput*: 4300 wf/sec\n*p99 Latency*: 0.02ms"
        }
      }
    ]
  }'
```

#### PagerDuty Integration
```bash
# Trigger incident on high error rate
curl -X POST https://events.pagerduty.com/v2/enqueue \
  -H 'Content-Type: application/json' \
  -d '{
    "routing_key": "YOUR_ROUTING_KEY",
    "event_action": "trigger",
    "payload": {
      "summary": "Phase 5 Error Rate High (1.2%)",
      "severity": "critical",
      "source": "monitoring"
    }
  }'
```

---

## Incident Response

### SEV-1: Error Rate Spike (>1%)

**Detection:** Automated alert (PagerDuty)  
**Time to respond:** 5 minutes  
**Owner:** On-call SRE

#### Diagnosis Flowchart
```
Error rate >1%?
├─ YES → Check error_logs for pattern
│   ├─ "ContextBus timeout" → Scale ContextBus workers (GATE 4)
│   ├─ "Checkpoint write failed" → Check disk space (see below)
│   ├─ "Feature graduation crash" → Demote problematic feature (see below)
│   └─ "Unknown error" → Check brain subsystem logs
└─ NO → May be transient, monitor for 5 more minutes
```

#### Action: Check Disk Space
```bash
# Audit trail fills up quickly under load
df -h ~/.corvin/

# If audit storage <10GB free:
# 1. Archive audit trail to S3
corvin audit export --output s3://bucket/audit-$(date +%Y%m%d).jsonl.gz

# 2. Verify archive integrity
corvin audit verify --file s3://bucket/audit-$(date +%Y%m%d).jsonl.gz

# 3. Clear local audit (keep last 1 week)
corvin audit rotate --keep-days 7

# 4. Monitor recovery
watch -n 10 'corvin status | grep error_rate'
```

#### Action: Demote Problematic Feature
```bash
# If errors are isolated to one feature (check error logs):
corvin feature demote new_feature_x "Error rate spike 1.2%, rolling back"

# This ALPHA→OFF (demote out of production)
# Existing workflows continue on previous code path

# Verify demotion
corvin feature show new_feature_x
# Expected: state=DEMOTED, error_rate_before=1.2%

# Monitor error rate recovery (should drop within 2 min)
watch -n 10 'curl -s http://127.0.0.1:8765/v1/monitoring/production | jq .error_rate'
```

#### Action: Full Rollback (Last Resort)
```bash
# If error rate still >1% after demotion:
# Rollback entire Phase 5 to previous stable version

./scripts/rollback-phase5.sh --from phase5-canary --to stable-v1.2.0

# Script will:
# 1. Stop green slot
# 2. Route 100% traffic back to blue
# 3. Clear Phase 5 feature flags
# 4. Verify metrics return to baseline
# 5. Preserve audit trail (no data loss)

# Expected recovery time: <5 minutes
# Expected error rate after rollback: <0.1%
```

### SEV-2: High Latency (p99 >500ms)

**Detection:** Automated alert (Slack)  
**Time to respond:** 15 minutes  
**Owner:** On-call SRE or engineer

#### Diagnosis
```bash
# Check ContextBus queue depth
curl http://127.0.0.1:8765/v1/monitoring/production | jq '.context_bus.queue_depth'

# If >1000:
#   → ContextBus is backpressured
#   → Too many events per second, workers can't keep up

# Check which subsystem is producing events
corvin brain subsystem-stats | grep "events_per_sec"
# Expected: <1000 events/sec per subsystem
```

#### Action: Scale ContextBus Workers
```bash
# Increase event processor threads
corvin config set context_bus.worker_threads 8  # was 4

# Restart service
systemctl restart corvinOS

# Monitor latency recovery (5–10 min)
watch -n 5 'curl -s http://127.0.0.1:8765/v1/monitoring/production | jq .latency_ms'

# Expected: p99 returns to <100ms
```

#### Action: Reduce Incoming Load (Graceful Degradation)
```bash
# If ContextBus still backpressured:
# Reduce feature flags to lower decision volume

corvin feature set vibe_guidance_enabled --percent=0  # Disable Vibe
corvin feature set learning_enabled --percent=0        # Disable Learning

# Monitor latency (should drop immediately)

# Re-enable gradually once latency recovers
corvin feature set vibe_guidance_enabled --percent=10
```

### SEV-3: Feature Stuck in ALPHA (>30 days)

**Detection:** Daily job (not urgent)  
**Time to respond:** Next business day  
**Owner:** Feature owner + SRE

#### Investigation
```bash
# Check why feature isn't graduating
corvin feature telemetry feature_id --days 30

# Common reasons:
#   - Error rate too high (>5%)
#   - Satisfaction too low (<50%)
#   - Adoption too low (<1%)
#   - Age <7 days

# If metrics look good: manually promote
corvin feature promote feature_id --force
```

---

## Rollback Procedure

### Automated Rollback (<5 min)

```bash
# One-command rollback
./scripts/rollback-phase5.sh

# Script does:
# 1. Verify previous stable version available
# 2. Stop green (Phase 5) slot
# 3. Route 100% traffic back to blue (v1.2.0)
# 4. Clear all Phase 5 feature flags
# 5. Run regression tests (5 min)
# 6. Declare success or escalate

# Output:
# ✓ Rollback complete in 4m 32s
# ✓ Error rate: 0.03% (stable)
# ✓ Throughput: 45 workflows/sec (stable)
# ✓ Audit trail: 50000 events (no loss)
```

### Manual Rollback (if automated fails)

```bash
# Step 1: Stop green slot
docker-compose -f docker-compose.green.yml down

# Step 2: Verify blue is still running
curl http://127.0.0.1:8765/health

# Step 3: Update load balancer to route 100% to blue
vi /etc/nginx/conf.d/upstream.conf
# upstream corvinOS {
#   server blue-phase4:8765 weight=100;
# }
nginx -s reload

# Step 4: Clear Phase 5 flags
corvin feature disable vibe_guidance_enabled
corvin feature disable learning_enabled

# Step 5: Monitor metrics return to baseline
watch -n 10 'curl -s http://127.0.0.1:8765/v1/monitoring/production | \
  jq "{throughput: .throughput.workflows_per_sec, error: .error_rate.error_rate_percent, p99: .latency_ms.p99}"'

# Expected: throughput ~50/sec, error <0.1%, p99 <50ms
```

---

## Troubleshooting Guide

### Problem: "ContextVar leak detected" in logs

**Cause:** Cross-tenant context leak (security issue)  
**Severity:** SEV-1  
**Fix:**

```bash
# 1. Check current tenant
echo $CORVIN_TENANT_ID  # Should be "_default" or operator's tenant

# 2. Verify no multi-tenant workflows are active
corvin workflow list --filter "tenant_id != $_default"

# 3. If tenant mismatch found:
#    a. Kill affected workflow (may lose in-flight data)
corvin workflow kill workflow_id

#    b. Check audit trail for last successful tenant switch
corvin audit show --filter "tenant_id_changed" | tail -5

#    c. Escalate to engineering (likely a bug in plugin)
```

### Problem: "Audit trail write failed" in logs

**Cause:** Audit file full or permission denied  
**Severity:** SEV-1  
**Fix:**

```bash
# 1. Check audit file size
ls -lh ~/.corvin/audit.jsonl

# 2. Check disk space
df -h ~/.corvin/

# 3. If disk full (<1GB):
#    a. Archive audit
corvin audit export --output s3://bucket/audit-$(date +%Y%m%d).tar.gz

#    b. Clear local (keep 7 days)
corvin audit rotate --keep-days 7

#    c. Verify audit chain after rotation
corvin audit verify

# 4. If still failing: check permissions
ls -l ~/.corvin/audit.jsonl
# Should be: -rw-rw-r-- corvin corvin

# 5. If permissions wrong:
sudo chown corvin:corvin ~/.corvin/audit.jsonl
```

### Problem: "Decision latency p99 >500ms" alert firing

**Cause:** ContextBus backpressured or MemoryCoordinator stalled  
**Severity:** SEV-2  
**Fix:**

```bash
# Step 1: Check queue depth
curl http://127.0.0.1:8765/v1/monitoring/production | jq .context_bus.queue_depth

# If >1000:
#   → Scale ContextBus workers (see SEV-2 above)

# Step 2: Check MemoryCoordinator
corvin brain memory-coordinator stats | grep -E "split|merge|stall"

# If stalls detected:
#   → Investigate session state (possible deadlock)

# Step 3: Check for slow subsystems
corvin brain subsystem-stats | grep duration_ms | sort -t: -k2 -rn | head -3

# If any subsystem >100ms:
#   → Check that subsystem's logs for blocking operations
```

### Problem: "Feature promotion daemon crashed"

**Cause:** Analytics computation error or bad state  
**Severity:** SEV-2  
**Fix:**

```bash
# 1. Check promotion daemon status
systemctl status corvinOS-promotion-daemon

# 2. Restart daemon
systemctl restart corvinOS-promotion-daemon

# 3. Check logs for errors
tail -50 /var/log/corvinOS/promotion_daemon.log

# 4. If reoccurs: disable auto-promotion (manual mode)
corvin feature promote --manual-only

# 5. Manual feature promotions still work
corvin feature promote feature_id --force
```

### Problem: "Memory growth >20%/hour detected"

**Cause:** Possible memory leak in subsystem  
**Severity:** SEV-2  
**Fix:**

```bash
# 1. Identify which subsystem is leaking
corvin brain memory-stats | grep "heap_size" | sort -t: -k2 -rn

# 2. Check for specific issues:
#    a. MemoryCoordinator not merging sessions
corvin brain memory-coordinator stats | grep "pending_merges"

#    b. Audit trail not flushed
corvin audit show --format stats | grep "events_pending_flush"

#    c. Feature gradations accumulating
corvin feature telemetry --all | grep "event_count" | sort -t: -k2 -rn | head -3

# 3. Trigger manual cleanup
corvin maintenance cleanup --force

# 4. Monitor recovery (30–60 min)
watch -n 60 'free -h | grep -E "Mem|Swap"'
```

---

## On-Call Procedures

### Shift Handoff Checklist

**Incoming on-call engineer completes before taking over:**

- [ ] Read last 24h incident log
- [ ] Verify all alerts cleared
- [ ] Confirm escalation contact is correct
- [ ] Test alert path (send test Slack/PagerDuty message)
- [ ] Verify you can access:
  - [ ] Monitoring dashboard
  - [ ] `corvin` CLI tool
  - [ ] SSH to production servers
  - [ ] Rollback scripts

### Alert Response SLA

| Severity | Response Time | Action |
|----------|---------------|--------|
| P1 (Critical) | 5 min | Page on-call, start war room |
| P2 (High) | 15 min | Acknowledge, investigate |
| P3 (Medium) | 60 min | File ticket, investigate next day |

### Escalation Tree

```
On-call SRE (you)
├─ Can't diagnose in 15 min?
│  └─ Escalate to SRE lead (on-call rotation)
│
└─ Needs code fix in 30 min?
   └─ Escalate to engineering on-call
```

### War Room Communication

When SEV-1 fires:

1. **Immediately:** Declare SEV-1 in #incidents Slack channel
2. **Minute 1:** Post initial symptoms + suspected cause
3. **Minute 3:** Mitigating action (e.g., disable feature / rollback)
4. **Minute 5:** Post status (improving / stable / worsening)
5. **Every 5 min:** Update status until resolved
6. **Resolution:** Post RCA link + close incident

---

## Maintenance Tasks

### Daily (automated via cron)

```bash
# Daily metrics snapshot (1 AM)
0 1 * * * /home/corvin/scripts/daily-metrics-snapshot.sh

# Daily feature graduation check (2 AM)
0 2 * * * corvin feature auto-promote --dry-run

# Daily audit trail integrity check (3 AM)
0 3 * * * corvin audit verify --alert-on-fail
```

### Weekly (manual, on Monday)

- [ ] Review last week's incidents (Slack #incidents)
- [ ] Update runbook if procedures changed
- [ ] Test rollback procedure (in staging)
- [ ] Confirm on-call rotation for next week

### Monthly (scheduled maintenance window)

- [ ] Archive audit trail (>30 days old)
- [ ] Backup all Phase 5 configuration
- [ ] Update this runbook based on learnings
- [ ] Brief team on new procedures

---

**END RUNBOOK**

For questions or updates, contact the DevOps team or file an issue in the internal wiki.
