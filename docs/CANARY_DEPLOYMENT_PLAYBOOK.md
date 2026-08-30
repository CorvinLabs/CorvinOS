# Week 2 Canary Deployment Playbook (ADR-0461)

**Version:** 1.0  
**Date:** 2026-08-30  
**Status:** Production-Ready  
**Duration:** 11 days (Day 1–11)

## Executive Summary

This playbook orchestrates the **autonomous production rollout** of the Unified Architecture v0.2-rc1 from the operator's perspective. The system automatically manages traffic ramps (10% → 50% → 100%) with **48-hour health gates at each stage**, but the operator remains accountable for the deployment's success.

**Key Facts:**
- Minimal operator intervention (gates auto-open when SLOs pass)
- Reproducible decision logic (deterministic based on 3 KPIs)
- Automatic rollback on critical thresholds
- Real-time alerts via Slack/Email
- Complete audit trail (decisions.jsonl)

---

## Architecture Overview

### Deployment Stages

| Stage | Traffic | Duration | Gate | Failure Mode |
|-------|---------|----------|------|--------------|
| INITIAL | 0% | — | Manual | Phase 5 stable |
| CANARY_10 | 10% | 48h minimum | SLO-based | Auto-rollback |
| RAMP_50 | 50% | 48h minimum | SLO-based | Auto-rollback |
| FULL_100 | 100% | 7d minimum | Stability check | Manual intervention |
| COMPLETE | 100% | ∞ | — | Production |

### Decision Gates

Each stage requires **48 hours of healthy metrics** to advance:

```
┌─────────────────────────────────────────────────────────────────┐
│ HEALTH GATE LOGIC                                               │
├─────────────────────────────────────────────────────────────────┤
│ IF error_rate ≤ 0.1% AND latency_p99 ≤ 500ms AND               │
│    audit_integrity ≥ 99.9%                                      │
│ THEN stage_healthy = true                                       │
│                                                                  │
│ IF stage_healthy FOR 48 hours                                   │
│ THEN auto_promote()                                             │
└─────────────────────────────────────────────────────────────────┘
```

### Automatic Rollback Triggers

The system **immediately rolls back** if any critical threshold is exceeded:

```
┌─────────────────────────────────────────────────────────────────┐
│ ROLLBACK TRIGGERS (CRITICAL)                                    │
├─────────────────────────────────────────────────────────────────┤
│ IF error_rate > 5%                                              │
│    THEN rollback(stage, "Error spike > 5%")                     │
│                                                                  │
│ IF latency_p99 > 1000ms                                         │
│    THEN rollback(stage, "Severe latency > 1000ms")              │
│                                                                  │
│ IF audit_integrity < 99%                                        │
│    THEN rollback(stage, "Audit chain corrupted")                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 11-Day Timeline

### Pre-Deployment (Day 0)

**Checklist:**
- [ ] Prometheus is running (port 9090)
- [ ] Alertmanager is configured (port 9093)
- [ ] Slack webhook URL is set: `export CANARY_SLACK_WEBHOOK=https://hooks.slack.com/...`
- [ ] Email alerts configured: `export CANARY_ALERT_EMAIL=ops@corvin-labs.com`
- [ ] canary-rollout.sh is executable: `chmod +x deploy/canary-rollout.sh`
- [ ] Phase 5 (baseline) is stable for ≥24 hours
- [ ] Incident response team is on standby
- [ ] Backup runbook is printed and available

**Verification:**
```bash
# Check Prometheus connectivity
curl -s http://localhost:9090/api/v1/query?query=up | jq '.data.result[0]'

# Verify script
/home/shumway/projects/CorvinOS/deploy/canary-rollout.sh status
```

### Day 1: Deployment Initialization

**Time:** 09:00 UTC  
**Stage:** INITIAL → CANARY_10 (automatic)

**What Happens:**
1. Operator runs: `./canary-rollout.sh promote`
2. Feature flags are updated: 10% traffic routes to Phase 6 (90% stay on Phase 5)
3. State file: `~/.corvin/canary-deployment/state.json` records transition
4. Initial metrics collected (baseline for comparison)

**Operator Actions:**
```bash
# Verify current status
./canary-rollout.sh status

# Expected output:
# Current Stage: CANARY_10
# Started At: 2026-08-30T09:00:00Z
# Healthy Since: null (not yet tracking)
```

**Monitoring:**
- Watch Prometheus dashboard: `http://localhost:9090`
- Focus on: error_rate, latency_p99, throughput
- Expected: Slight 2-5% variance from baseline (normal)

### Day 2: Canary Stabilization

**Time:** Continuous  
**Stage:** CANARY_10 (monitoring)

**What Happens:**
- 10% traffic on Phase 6, 90% on Phase 5
- Metrics being collected every 15 seconds
- First 24 hours: **"stabilization window"** (don't judge yet)

**Operator Actions:**
- **Morning check:** `./canary-rollout.sh status` (look for alert counts)
- **Afternoon review:** Inspect Prometheus graphs for anomalies
- **Evening report:** Email team with 24h summary

**Common Issues & Responses:**

| Issue | Signal | Action |
|-------|--------|--------|
| Error spike (1-2%) | Alert: `CanaryErrorRateSpike` | Check logs, decide if this is acceptable variance. Can be warning if recovering. |
| Latency bump (+50ms) | Alert: `CanaryLatencyDegradation` | Normal during initial load. Monitor trend. |
| Audit chain lag | Alert: `CanaryAuditIntegrityLoss` | If < 30s lag: expected. If > 1min: investigate disk I/O. |

### Day 3: Health Gate Assessment #1

**Time:** 09:00 UTC (48 hours after canary start)  
**Gate:** CANARY_10 → RAMP_50 (if healthy)

**Automatic Evaluation:**

The system evaluates:

```
error_rate = average of last 5m buckets over 48h
latency_p99 = 99th percentile of latency over 48h
audit_integrity = (valid_events / total_events)

SLO_PASS = (error_rate ≤ 0.1%) AND 
           (latency_p99 ≤ 500ms) AND 
           (audit_integrity ≥ 99.9%)

IF SLO_PASS:
    PROMOTE to RAMP_50
    UPDATE state.json
    SEND alert "Promoted to 50% traffic"
ELSE:
    HOLD in CANARY_10 (no automatic rollback)
    LOG health_reasons
    ALERT operator: "Gate not passed, investigating..."
```

**Operator Decision:**

1. **If auto-promoted (normal case):**
   - Review Slack notification
   - Verify Prometheus shows 50% routing
   - Note: Second 48-hour window starts now

2. **If auto-held (stage didn't pass gates):**
   - Run: `./canary-rollout.sh health-check`
   - Identify which SLO failed (error/latency/audit)
   - **Option A:** Wait and re-evaluate (system checks again every 60 min)
   - **Option B:** Manual investigation + deep dive
   - **Option C:** Rollback + debug (only if serious issue found)

**Manual Promotion (if needed):**
```bash
# If you're confident despite SLO warnings, force promotion:
./canary-rollout.sh manual-promote FORCE

# This logs to decisions.jsonl with operator name and timestamp
# (Use sparingly - auto-promotion is the normal path)
```

### Day 4: Ramp Stabilization

**Time:** Continuous  
**Stage:** RAMP_50 (monitoring)

**What Happens:**
- 50% traffic on Phase 6
- Same SLO thresholds apply
- Larger user base = higher statistical confidence

**Operator Actions:**
- **Every 12 hours:** `./canary-rollout.sh status`
- **Look for:** No new error categories, latency stable, audit integrity > 99.9%
- **Prepare:** Team for potential 100% rollout on Day 5

**Risk Assessment:**

| Risk Level | Signal | Action |
|------------|--------|--------|
| **LOW** | error_rate 0.08%, latency 420ms, all SLOs ✓ | Proceed normally |
| **MEDIUM** | latency trending up (400→450→490ms), audit lag | Monitor closely; don't panic |
| **HIGH** | error spike to 2-3%, latency > 600ms, failing SLOs | Escalate to on-call eng; consider rollback |
| **CRITICAL** | error > 5%, latency > 1000ms, audit integrity < 99% | **AUTOMATIC ROLLBACK** happens |

### Day 5: Health Gate Assessment #2

**Time:** 09:00 UTC (48 hours after ramp start)  
**Gate:** RAMP_50 → FULL_100 (if healthy)

**Same Logic as Day 3:**

```bash
# Operator workflow:
./canary-rollout.sh status         # Review gate status
# System auto-promotes if SLOs pass, else holds or rolls back

# If auto-promoted:
# 100% traffic now flows through Phase 6
# Final 7-day stability window begins
```

### Days 6–11: Full Production + Stabilization

**Time:** Continuous  
**Stage:** FULL_100 (7-day observation)

**What Happens:**
- 100% of users on Phase 6
- Same SLO monitoring (but higher statistical confidence)
- 7-day minimum before marking as COMPLETE
- Incident response still active (on-call)

**Operator Cadence:**

| Frequency | Activity | Time |
|-----------|----------|------|
| Every 4h | `./canary-rollout.sh status` | 5 min |
| Daily | Review Prometheus graphs | 30 min |
| Daily | Check decision log: `tail decisions.jsonl` | 5 min |
| Daily | Incident review (if any alerts fired) | 15 min |

**Day 11 Gate (automatic):**

At 09:00 UTC on Day 11, the system transitions:

```
IF all_7_days_healthy:
    PROMOTE to COMPLETE
    stage = PRODUCTION
    SEND final success alert
ELSE:
    HOLD in FULL_100
    EXTEND observation window by 1-7 days
```

---

## Incident Response Procedures

### Procedure 1: Error Spike (>0.1% SLO Warning)

**Trigger:** Alert `CanaryErrorRateSpike` (error_rate > 0.1% for 2+ min)

**Investigation (5 min):**
```bash
# 1. Check alert severity
./canary-rollout.sh logs | tail -20

# 2. Query Prometheus for error breakdown
# Open http://localhost:9090 → Query: increase(canary_errors_total[5m])
# Look for: which error type increased? (validation, network, internal)

# 3. Check application logs
tail -100 ~/.corvin/tenants/_default/sessions/*/logs.jsonl | grep "ERROR"

# 4. Assess: is this expected variance or a real issue?
```

**Decision Tree:**

```
IF error_rate < 0.5%:
  → This is a warning (SLO alert fired)
  → Investigate root cause
  → If recovers naturally within 10 min: NO ACTION (expected variance)
  → If persists: Escalate to on-call eng
  
ELIF 0.5% ≤ error_rate < 5%:
  → This is a concern (SLO failing, not critical yet)
  → Root cause analysis required
  → Can wait 30 min if recovering
  → If not recovering: Escalate
  
ELIF error_rate ≥ 5%:
  → AUTOMATIC ROLLBACK TRIGGERED
  → System will rollback stage automatically
  → Operator: Monitor and document
```

**If Manual Rollback Needed:**
```bash
# (Only if system didn't auto-rollback and you need to act)
./canary-rollout.sh rollback FORCE

# This requires FORCE flag to prevent accidents
# Logs all details: operator, timestamp, reason
```

**Post-Incident:**
1. Document root cause in decisions.jsonl
2. Create bug ticket if code issue
3. Update monitoring if new error type discovered
4. Decide: restart canary from current stage or roll back to Phase 5

### Procedure 2: Latency Degradation (>500ms Warning)

**Trigger:** Alert `CanaryLatencyDegradation` (p99 > 500ms for 5+ min)

**Investigation (10 min):**
```bash
# 1. Check latency breakdown by component
# Prometheus query: histogram_quantile(0.99, rate(canary_latency_ms_bucket{component=~".*"}[5m]))
# Look for: which component is slow? (context_engine, audit_chain, etc.)

# 2. Check CPU/Memory usage
# Node exporter query: 100 - (avg by (instance) (irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)
# If CPU > 80%: system is resource-constrained

# 3. Check ContextBus backlog
tail -5 ~/.corvin/tenants/_default/sessions/*/audit.jsonl | grep "contextbus_latency"
```

**Decision Tree:**

```
IF latency 500–700ms:
  → Warning state (SLO alert, not yet critical)
  → Investigate trend: is it climbing or stabilizing?
  → If stabilizing: NO ACTION
  → If climbing: May need to reduce traffic (manual ramp-down)
  
ELIF latency 700–1000ms:
  → Concerning (SLO failing, warning alert fired)
  → Check if ContextBus is backed up
  → If fixable (e.g., restart a subsystem): Try fix
  → If not fixable: Escalate to on-call
  
ELIF latency > 1000ms for 2+ min:
  → CRITICAL (Auto-rollback trigger)
  → System will rollback automatically
  → Operator: Monitor, document, stand by for manual recovery
```

**If You Need to Stop the Bleed:**
```bash
# Emergency: reduce traffic manually
# (Only if system is unresponsive and auto-rollback failed)

# Option 1: Disable Phase 6 feature flag
corvin config set features.unified_architecture v0.2-rc1 --enabled=false

# Option 2: Use fallback (Phase 5)
corvin config set canary_deployment.stage RAMP_50  # Drop back to 50%
systemctl restart corvin-service  # Reload

# Option 3: Full emergency stop
corvin config set canary_deployment.stage INITIAL  # Stop canary completely
systemctl restart corvin-service
```

### Procedure 3: Audit Chain Integrity Loss (<99.9%)

**Trigger:** Alert `CanaryAuditIntegrityLoss` (integrity < 99.9% for 5+ min)

**Audit chain health is critical — immediate investigation.**

**Investigation (2 min):**
```bash
# 1. Check audit trail write latency
curl -s http://localhost:8765/metrics/audit | grep "audit_write_latency"
# If > 100ms: disk I/O bottleneck

# 2. Check disk space
df -h ~/.corvin/tenants/_default/

# 3. Verify chain continuity
tail -10 ~/.corvin/tenants/_default/audit.jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    event = json.loads(line)
    if 'hash_chain_valid' in event:
        print(f\"Valid: {event['hash_chain_valid']}, Hash: {event.get('hash', 'N/A')[:8]}...\")
"
```

**Decision Tree:**

```
IF integrity 99.0–99.9%:
  → Warning (SLO failing)
  → Check disk I/O, restart audit writer if needed
  → Monitor recovery
  
ELIF integrity < 99.0%:
  → CRITICAL (Chain may be corrupted)
  → AUTOMATIC ROLLBACK TRIGGERED
  → Post-mortem required (this is rare)

IF disk is full:
  → Delete old audit logs (keep last 30 days only)
  → Restart audit writer
  → Re-enable canary if recovery successful
```

### Procedure 4: Severe Latency (>1000ms — Automatic Rollback)

**Trigger:** `CanaryLatencySevere` alert OR system auto-triggers

**What happens automatically:**
```
IF latency_p99 > 1000ms FOR 2 min:
  orchestrator.rollback_stage("Severe latency > 1000ms")
  state.json updated
  Slack alert sent: "ROLLBACK to previous stage"
  decisions.jsonl logged
```

**Operator Response (after auto-rollback):**
```bash
# 1. Verify rollback succeeded
./canary-rollout.sh status
# Expected: stage = previous (e.g., RAMP_50 if was FULL_100)

# 2. Monitor for recovery
# Watch Prometheus for latency to drop back below 600ms
# If recovered: can try promoting again later

# 3. Document
# Add note to decisions.jsonl with context:
# "Rollback successful. Cause: ContextBus queue buildup. Restarting subsystem X."
```

### Procedure 5: Severe Error Spike (>5% — Automatic Rollback)

**Trigger:** `CanaryErrorSpikeSevere` alert OR system auto-triggers

**Similar to latency procedure:**
```bash
# Verify rollback
./canary-rollout.sh status

# Investigate error source
tail -50 ~/.corvin/tenants/_default/sessions/*/logs.jsonl | grep -i "error"

# Look for: What changed at the moment errors spiked?
# - New error message type
# - Cascading failures (error A → error B)
# - Dependency failure (external API down?)
```

---

## Health Monitoring Dashboard

### Key Metrics to Watch

Open: `http://localhost:3000/d/canary-dashboard` (Grafana) or `http://localhost:9090` (Prometheus)

**Per-Minute Snapshots:**

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| **Error Rate** | >0.1% | >5% | Check logs; escalate if >1% |
| **Latency p99** | >500ms | >1000ms | Check CPU/memory; consider rollback |
| **Audit Integrity** | <99.9% | <99% | Check disk I/O; restart if needed |
| **Throughput** | <3000 rps | <1500 rps | Check for cascading failures |
| **Feature Promotions** | None | >3 stuck | Investigate feature system |

### Recommended Dashboard Panels

1. **Error Rate Trend** (last 48h)
   - Query: `rate(canary_errors_total[5m])`
   - Threshold line at 0.001 (0.1%)
   - Threshold line at 0.05 (5%)

2. **Latency P99** (last 48h)
   - Query: `histogram_quantile(0.99, rate(canary_latency_ms_bucket[5m]))`
   - Threshold line at 500ms
   - Threshold line at 1000ms

3. **Audit Integrity** (last 48h)
   - Query: `(canary_audit_chain_valid / canary_audit_chain_total)`
   - Threshold line at 0.999 (99.9%)

4. **Gate Status** (current)
   - Manual text panel showing current stage + time healthy
   - Updated every 15 seconds

5. **Incident Log** (last 7 days)
   - Table of decisions.jsonl entries
   - Color-coded: GREEN=PROMOTE, RED=ROLLBACK

---

## Rollback Procedures (Manual)

### Full Rollback to Phase 5

If something goes seriously wrong and auto-rollback didn't trigger:

```bash
# 1. Immediate: Disable Phase 6
./deploy/canary-rollout.sh rollback FORCE

# 2. Verify rollback
./deploy/canary-rollout.sh status
# Should show: stage = previous (or INITIAL)

# 3. Monitor Phase 5 recovery
# Watch error_rate, latency drop back to baseline
# Expected: 5–10 minute recovery

# 4. Post-mortem
# Document in decisions.jsonl: what went wrong, when, by whom
# Create incident ticket
# Schedule RCA (root-cause analysis) for next day
```

### Partial Rollback (stay in previous stage)

```bash
# If at FULL_100 and want to stay at RAMP_50:
./deploy/canary-rollout.sh rollback FORCE
./deploy/canary-rollout.sh status  # Verify

# If at RAMP_50 and want to stay at CANARY_10:
./deploy/canary-rollout.sh rollback FORCE
./deploy/canary-rollout.sh status
```

---

## Success Criteria & Sign-Off

### Day 11 Success Checklist

After the system transitions to `COMPLETE`, verify:

- [ ] All 7 days in FULL_100 showed error_rate ≤ 0.1%
- [ ] All 7 days showed latency_p99 ≤ 500ms
- [ ] All 7 days showed audit_integrity ≥ 99.9%
- [ ] No manual rollbacks during FULL_100 period
- [ ] No security incidents reported
- [ ] User feedback (if any) is positive
- [ ] Performance baselines documented
- [ ] Incident log (decisions.jsonl) reviewed and archived

### Final Report

Generate final summary:
```bash
# Capture final metrics
tail -100 ~/.corvin/canary-deployment/decisions.jsonl > canary_week2_final_report.jsonl

# Email to stakeholders:
# Subject: "Week 2 Canary Deployment: SUCCESS"
# Body:
#   - Stage progression: 10% → 50% → 100% → COMPLETE
#   - SLO satisfaction: 7d @ 100% SLO pass rate
#   - Incidents: [count] (list any, all resolved)
#   - Rollbacks: [count] (all auto-triggered, none critical)
#   - Recommendation: APPROVE Phase 6 as stable production
```

---

## Troubleshooting

### Script Fails to Create State File

```bash
# Check CORVIN_HOME
echo $CORVIN_HOME  # Default: ~/.corvin

# Verify directory exists
mkdir -p ~/.corvin/canary-deployment

# Try again
./canary-rollout.sh status
```

### Prometheus Connection Failed

```bash
# Verify Prometheus is running
curl -s http://localhost:9090/api/v1/query?query=up | jq '.status'
# Should return "success"

# If not running:
systemctl start prometheus
# or
docker run -d -p 9090:9090 prom/prometheus:latest
```

### Metrics Not Updating

```bash
# Check metric freshness
./canary-rollout.sh health-check | jq '.timestamp'

# If stale (> 5 min old), restart collector daemon:
systemctl restart corvin-metrics-collector
# or manually:
pkill -f "collector_daemon" && sleep 2 && python3 -m core.monitoring.collector_daemon &
```

### Can't Read Decision Log

```bash
# Verify file exists
ls -lh ~/.corvin/canary-deployment/decisions.jsonl

# Check permissions
cat ~/.corvin/canary-deployment/decisions.jsonl | head

# Parse as JSON
tail -5 ~/.corvin/canary-deployment/decisions.jsonl | python3 -m json.tool
```

---

## References

- **ADR-0461:** Phase 6 Production Rollout Framework (Corvin-ADR repo)
- **ADR-0423:** Unified 7-Layer Architecture (technical spec)
- **Prometheus Docs:** https://prometheus.io/docs/
- **Phase 5 Runbook:** docs/PHASE_5_OPERATOR_RUNBOOK.md (baseline reference)

---

## Appendix: CLI Reference

```bash
# Show current status
./canary-rollout.sh status

# Automatically promote (if health gates pass)
./canary-rollout.sh promote

# Force promotion (requires audit)
./canary-rollout.sh manual-promote FORCE

# Emergency rollback
./canary-rollout.sh rollback FORCE

# Show recent logs
./canary-rollout.sh logs [lines]  # Default: 50

# Run single health check
./canary-rollout.sh health-check

# Show help
./canary-rollout.sh help
```

---

**Document Status:** READY FOR DEPLOYMENT  
**Last Updated:** 2026-08-30  
**Operator Signature:** ___________________________ (on printout)
