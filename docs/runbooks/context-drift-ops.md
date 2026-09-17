# Context-Drift Operations Runbook

**Document Date:** 2026-09-17  
**Status:** Production-Ready ✅  
**On-Call Alert:** #corvin-prod-alerts  
**Escalation:** infrastructure@corvin.ai

---

## Quick Start

### System Status

```bash
# Health check
curl http://localhost:8765/v1/context-drift/health
# Expected: {"status": "healthy", ...}

# Metrics
curl http://localhost:8765/v1/context-drift/metrics | jq .

# Recent alerts
curl http://prometheus:9090/api/v1/query?query='ALERTS{job="context-drift"}'
```

### Common Actions

| Action | Command | Time |
|--------|---------|------|
| Check system health | `curl /v1/context-drift/health` | <1s |
| View current threshold | `curl /v1/context-drift/metrics \| jq .threshold` | <1s |
| View feedback quality | `curl /v1/context-drift/metrics \| jq .feedback_quality` | <1s |
| View active goals | `kubectl get deployment -n production -l app=context-drift-prod` | <5s |
| View logs (last 100 lines) | `kubectl logs -f deployment/context-drift-prod -n production \| head -100` | <5s |
| View Grafana dashboard | Open: http://grafana:3000/d/context-drift-overview | <5s |

---

## Alert Response Procedures

### Alert: HighDriftDetectionRate (>50%)

**Severity:** Warning  
**Response Time:** 15 minutes

#### 1. Assess Situation

```bash
# Query drift rate
curl http://prometheus:9090/api/v1/query?query='increase(corvin_context_drift_goal_alignment_checks_total{result="drifted"}[5m]) / increase(corvin_context_drift_goal_alignment_checks_total[5m])'

# Check if legitimate (many goals really are drifting) or threshold issue
curl http://localhost:8765/v1/context-drift/metrics | jq '.feedback_quality'
# If quality < 70%: threshold is wrong
# If quality > 85%: drift alerts are correct
```

#### 2. Check Recent Changes

```bash
# Any code deploys?
git log --oneline -5

# Any threshold changes?
kubectl logs -n production deployment/context-drift-prod | grep -i "threshold" | tail -5

# Any Prometheus scrape issues?
curl http://prometheus:9090/api/v1/targets | jq '.data.activeTargets[] | select(.job=="context-drift")'
```

#### 3. Decide Action

**If feedback quality > 85%:**  
→ Drift alerts are correct. Notify users to review/fix drifting goals.  
→ No system action needed.  
→ Escalate to product team if pattern continues.

**If feedback quality < 70%:**  
→ Threshold needs tuning. Manually trigger tuning:

```bash
curl -X POST http://localhost:8765/v1/context-drift/tune-threshold \
  -H "Content-Type: application/json" \
  -d '{"target_accuracy": 0.90}'

# Verify new threshold
curl http://localhost:8765/v1/context-drift/metrics | jq '.threshold'
```

#### 4. Follow-up

- [ ] Document decision in Slack (#corvin-prod-alerts)
- [ ] Set reminder to re-check in 1 hour
- [ ] If tuning was needed, monitor for improvement

---

### Alert: LowFeedbackQuality (<70%)

**Severity:** Warning  
**Response Time:** 30 minutes

#### 1. Investigate Quality Drop

```bash
# Get feedback statistics
curl http://localhost:8765/v1/context-drift/feedback/stats | jq .

# Sample recent feedback
curl http://localhost:8765/v1/context-drift/feedback?limit=20 | jq '.[] | {score: .alignment_score, correct: .was_drift_correct}'
```

#### 2. Identify Root Cause

**Pattern A: Feedback ratings degrading**  
→ Users less satisfied with drift detection  
→ Action: Review recent threshold tuning, may have overcorrected

**Pattern B: Feedback inconsistent**  
→ Users giving conflicting feedback for same score  
→ Action: Check if drift detection logic is unstable (e.g., due to randomness in LLM responses)

**Pattern C: No feedback collected**  
→ Users not providing feedback  
→ Action: Check if feedback UI working, enable feedback reminders

#### 3. Resolution

```bash
# Option 1: Trigger immediate tuning with more aggressive params
curl -X POST http://localhost:8765/v1/context-drift/tune-threshold \
  -H "Content-Type: application/json" \
  -d '{"target_accuracy": 0.95, "aggressive": true}'

# Option 2: Temporarily disable high-confidence predictions
curl -X POST http://localhost:8765/v1/context-drift/config \
  -H "Content-Type: application/json" \
  -d '{"confidence_threshold": 0.8}'  # Only alert when very confident

# Option 3: Investigate user feedback mechanism
kubectl logs deployment/context-drift-prod -n production | grep -i "feedback" | tail -20
```

#### 4. Document

- [ ] Post resolution in Slack
- [ ] Update metrics dashboard
- [ ] Monitor for improvement over next 24h

---

### Alert: AlignmentCheckSloViolation (p95 latency >5ms)

**Severity:** Warning  
**Response Time:** 10 minutes

#### 1. Check Current Latency

```bash
curl http://prometheus:9090/api/v1/query?query='histogram_quantile(0.95, rate(corvin_context_drift_alignment_check_latency_ms_bucket[5m]))'

# If < 5ms: fluke, dismiss
# If > 5ms: real issue, investigate
```

#### 2. Check Resource Usage

```bash
# CPU usage
kubectl top pod -n production -l app=context-drift-prod

# Memory usage
kubectl top pod -n production -l app=context-drift-prod --containers

# Expected: CPU <200m, Memory <500Mi
```

#### 3. Identify Bottleneck

```bash
# Check for errors
kubectl logs deployment/context-drift-prod -n production | grep -i "error" | tail -10

# Check for slow queries
kubectl logs deployment/context-drift-prod -n production | grep -i "slow\|timeout" | tail -10

# Check for lock contention (multiple goals checked concurrently)
curl http://localhost:8765/v1/context-drift/metrics | jq '.active_goals'
# If high (>100), expected slowdown
```

#### 4. Remediation

**If CPU high (>200m):**
```bash
# Increase resource requests
kubectl patch deployment context-drift-prod -n production -p \
  '{"spec": {"template": {"spec": {"containers": [{"name": "context-drift", "resources": {"requests": {"cpu": "500m"}}}]}}}}'
```

**If memory high (>500Mi):**
```bash
# Restart pods (clears any leaks)
kubectl rollout restart deployment/context-drift-prod -n production
```

**If due to heavy load:**
```bash
# Temporary: disable feedback collection (lighter load)
curl -X POST http://localhost:8765/v1/context-drift/config \
  -H "Content-Type: application/json" \
  -d '{"collect_feedback": false}'  # Restart collection after load drops
```

#### 5. Verify Fix

```bash
# Re-check latency
curl http://prometheus:9090/api/v1/query?query='histogram_quantile(0.95, rate(corvin_context_drift_alignment_check_latency_ms_bucket[5m]))'
# Should return < 5.0
```

---

### Alert: EscalatedDriftDetected (Critical)

**Severity:** Critical  
**Response Time:** 5 minutes (Page on-call)

#### 1. Immediate Action

```bash
# Confirm alert is real
curl http://localhost:8765/v1/context-drift/metrics | jq '.alerts'

# Get count
curl http://prometheus:9090/api/v1/query?query='increase(corvin_context_drift_drift_alerts_total{severity="critical"}[5m])'
```

#### 2. If Real Emergency

```bash
# Option A: Reduce threshold sensitivity (fewer false alarms)
curl -X POST http://localhost:8765/v1/context-drift/config \
  -H "Content-Type: application/json" \
  -d '{"threshold": 0.25}'  # More permissive

# Option B: Disable drift detection temporarily (if system unstable)
curl -X POST http://localhost:8765/v1/context-drift/config \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Option C: Roll back to previous version
kubectl rollout undo deployment/context-drift-prod -n production
```

#### 3. Notify Team

```
@ops-team CRITICAL: Context-Drift escalated alert fired. {link to Slack thread}
Action taken: {describe above}
ETA for resolution: {estimate}
```

#### 4. Post-Incident

- [ ] Root cause analysis
- [ ] Review thresholds/config
- [ ] Add test case to prevent recurrence
- [ ] Post retro in Slack

---

## Manual Operations

### Manual Threshold Tuning

```bash
# View current tuning history
curl http://localhost:8765/v1/context-drift/tuning-history | jq '.[-5:]'

# Trigger tuning manually
curl -X POST http://localhost:8765/v1/context-drift/tune-threshold \
  -H "Content-Type: application/json" \
  -d '{"target_accuracy": 0.85}'

# Verify new threshold
curl http://localhost:8765/v1/context-drift/metrics | jq '{threshold: .threshold, last_tuning: .tuning.last_tuning}'
```

### View Audit Trail

```bash
# Get recent audit events
curl http://localhost:8765/v1/context-drift/audit?limit=50&sort=desc | jq '.events'

# Filter by event type
curl 'http://localhost:8765/v1/context-drift/audit?event_type=goal_created&limit=10' | jq '.events[]'

# Verify chain integrity
curl http://localhost:8765/v1/context-drift/audit/verify-chain | jq '.status'
# Expected: "valid"
```

### View Feedback Samples

```bash
# Get recent feedback
curl http://localhost:8765/v1/context-drift/feedback?days=7&limit=20 | jq '.[]'

# Get feedback for specific goal
curl 'http://localhost:8765/v1/context-drift/feedback?goal_id=goal_123' | jq '.[]'

# Get feedback statistics
curl http://localhost:8765/v1/context-drift/feedback/stats | jq .
```

### Query Goals

```bash
# Get active goals
curl http://localhost:8765/v1/context-drift/goals?status=active | jq '.goals[] | {id, task, created_at}'

# Get recently drifted goals
curl http://localhost:8765/v1/context-drift/goals?status=drifted&sort=recent | jq '.goals[] | {id, task, drift_score}'

# Get specific goal
curl http://localhost:8765/v1/context-drift/goals/goal_123 | jq .
```

---

## Maintenance Tasks

### Daily (Automated)

- Threshold auto-tuning (if ≥10 feedback samples)
- Feedback quality scoring
- Audit chain verification
- Metrics export to Prometheus

### Weekly (Manual)

```bash
# 1. Review Grafana dashboard
# Open: http://grafana:3000/d/context-drift-overview
# Check: All panels have data, trends look normal

# 2. Check disk usage (audit trail)
du -sh ~/.corvin/tenants/*/global/audit.jsonl

# 3. Verify goal restoration working
curl http://localhost:8765/v1/context-drift/metrics | jq '.goals.restored'

# 4. Review recent errors
kubectl logs deployment/context-drift-prod -n production --since=7d | grep -c "ERROR"
# Should be close to 0

# 5. Check alert firing appropriately
# View Prometheus alerts page: http://prometheus:9090/alerts
# Verify no stuck alerts
```

### Monthly (Manual)

```bash
# 1. Generate compliance report
python3 /home/shumway/projects/CorvinOS/core/compliance/context_drift_compliance_report.py

# 2. Review feedback quality trend
# Chart: feedback_quality over past 30 days in Grafana

# 3. Audit user feedback for PII
# Spot-check 10 recent feedback entries
# Action: If PII found, run erasure workflow for affected goals

# 4. Capacity planning
# Check: CPU/memory trends over past month
# If trending up, plan scaling

# 5. Review ADR updates
# Verify: No new findings that should trigger ADR amendments
```

### Quarterly (With Team)

```bash
# Conduct retrospective:
# 1. What alerts fired? Were they actionable?
# 2. What was the mean time to resolution (MTTR)?
# 3. Any missed edge cases?
# 4. Should we adjust alert thresholds?
# 5. Any process improvements?
```

---

## Emergency Contacts

| Role | Name | Slack | Phone |
|------|------|-------|-------|
| On-Call Engineer | [TBD] | #corvin-prod-alerts | +1-XXX-XXX-XXXX |
| Infrastructure Lead | [TBD] | @infra-lead | +1-XXX-XXX-XXXX |
| Product Manager | [TBD] | @pm-corvin | +1-XXX-XXX-XXXX |

---

## Related Documentation

- [Deployment Guide](context-drift-deployment-guide.md) — How to deploy
- [Troubleshooting Guide](../troubleshooting/context-drift.md) — Common issues
- [API Documentation](../api/context-drift-api.md) — API reference
- [ADR-0407](../../corvin_decisions/decisions/ADR-0407-session-context-drift-prevention.md) — Architecture

---

*Last Updated: 2026-09-17*  
*Next Review: 2026-10-17*
