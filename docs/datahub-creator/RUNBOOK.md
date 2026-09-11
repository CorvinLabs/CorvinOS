# DataHub Creator Phase 4 Runbook

**Last Updated:** 2026-09-11  
**Status:** Production Ready (All 4 Phases Complete)

## Overview

DataHub Creator is a unified skill/tool creation system with integrated learning loops. Phase 4 adds production-grade monitoring, audit trails, and compliance reporting.

**Phases:**
- **Phase 1:** DataHub Skill (data ingestion + quality scoring)
- **Phase 2:** Creator 2.0 (12-phase skill generation model)
- **Phase 3:** Background Daemon (learning from usage + weight optimization)
- **Phase 4:** Dashboard + Audit Trail + Compliance (production readiness)

---

## Deployment

### Prerequisites

- Python 3.10+
- CorvinOS installed (`~/.corvin/` or `$CORVIN_HOME`)
- Tenant configured (`CORVIN_TENANT_ID=_default` or similar)

### Installation

```bash
# Install Phase 4 components
cd /home/shumway/projects/CorvinOS

# Ensure audit trail directory exists
mkdir -p ~/.corvin/tenants/_default/audit

# No additional dependencies beyond Phases 1-3
```

### Boot Sequence

On startup, Phase 4 performs fail-closed verification:

```python
# Boot tripwire (ADR-0232)
1. Verify audit trail directory exists
2. Verify audit chain integrity (hash-chain unbroken)
3. Verify tenant_id non-null
4. Initialize AuditTrail(tenant_id, chain_path)
5. Initialize ComplianceReporter(audit_trail)
6. Initialize PrometheusExporter(audit_trail)
7. Mount dashboard API endpoints
```

If any check fails: **HALT** (fail-closed). Do NOT proceed with degraded audit.

---

## Console Dashboard

### Access

1. **URL:** `http://localhost:8765/console/#/learning-dashboard`
2. **CLI:** `corvin console learning` (if CLI tooling exists)

### Dashboard Tabs

| Tab | Purpose | Refresh |
|-----|---------|---------|
| **Overview** | KPIs + Feedback Distribution + Convergence | 5s |
| **Skills** | Skill generation timeline (table) | 5s |
| **Weights** | Weight update history (line chart) | 5s |
| **Feedback** | User feedback impact (bar chart) | 5s |

### KPI Cards

```
┌─────────────────────────────────────────────┐
│ Skills Generated  │ Avg Improvement │ Feedback  │ Convergence │
│      47          │      23.5%       │   156    │    78%      │
└─────────────────────────────────────────────┘
```

- **Skills Generated:** Count of unique skills created (Phase 2)
- **Avg Improvement:** Average loss reduction (Phase 2 metric)
- **Feedback:** Total feedback signals received (Phase 3 metric)
- **Convergence:** Learning daemon convergence confidence (0-100%)

### Real-Time Updates

Dashboard polls every 5 seconds:
- Fetches `/api/v1/learning/skills?limit=50`
- Fetches `/api/v1/learning/weights?limit=100`
- Fetches `/api/v1/learning/feedback?limit=100`
- Fetches `/api/v1/learning/convergence`

If polling fails: console shows stale data (last successful fetch timestamp in header).

---

## Audit Trail Management

### Location

```
~/.corvin/tenants/_default/audit/datahub.jsonl
```

Format: JSONL (one immutable event per line).

### Events Captured

| Event Type | Triggered By | Frequency | Payload Example |
|---|---|---|---|
| `skill_generated` | Creator 2.0 finish | Per skill | `{skill_name, loss_before, loss_after, phase_count}` |
| `weight_updated` | Daemon optimize | Per update | `{source_id, weight_before, weight_after, reason}` |
| `feedback_received` | User/system | Per signal | `{signal: positive\|negative\|neutral, impact_on_loss}` |

### Querying

```python
from core.skills.os_skills.audit.trail import AuditTrail

trail = AuditTrail(
    tenant_id="_default",
    chain_path=Path("~/.corvin/tenants/_default/audit/datahub.jsonl")
)

# Query by event type
events = trail.query_events(event_type="feedback_received", limit=100)

# Query with time filter
since = datetime.now() - timedelta(days=7)
events = trail.query_events(since=since, limit=999999)
```

### Integrity Verification

```python
# Verify chain on boot
is_valid, message = trail.verify_integrity()
if not is_valid:
    raise ValueError(f"Audit chain broken: {message}")
    # This causes boot failure (fail-closed)
```

---

## Compliance Reporting

### GDPR Export

```python
from core.skills.os_skills.audit.reporter import ComplianceReporter

reporter = ComplianceReporter(trail, retention_days=90)

# Export for compliance auditor
export_text = reporter.export_for_compliance(
    since=datetime.now() - timedelta(days=30),
    redact=True  # PII redacted, user IDs masked
)

# Save to file
with open("compliance_report_sept_2026.jsonl", "w") as f:
    f.write(export_text)
```

### What Gets Redacted

- Emails: `user@example.com` → `[REDACTED_email]`
- Phone: `555-1234` → `[REDACTED_phone]`
- SSN: `123-45-6789` → `[REDACTED_ssn]`
- User IDs: `user_123` → `a7f8b2c4d9e1f6a3` (consistent hash)

### Retention Policy

Old events are deleted after `retention_days`:

```python
# Enforce 90-day retention
deleted_count = reporter.enforce_retention()
print(f"Deleted {deleted_count} events older than 90 days")
```

**Important:** This is GDPR Art. 5 (Storage Limitation). Events older than 90 days are permanently deleted.

### Bias Detection

```python
# Detect biased feedback patterns
alerts = reporter.detect_bias()

# Returns:
# {
#     "skewed_feedback": [
#         "skill_123: 95% positive feedback (N=20)",
#         "skill_456: 88% neutral feedback (N=50)"
#     ],
#     "low_observation_skills": [
#         "skill_789: only 3 observations"
#     ],
#     "high_variance": [...]
# }
```

**Action:** If high-bias skill detected, review skill generation (Phase 2) for issues.

---

## Monitoring & Alerts

### Prometheus Metrics

Endpoint: `http://localhost:8765/api/v1/learning/metrics`

```
# HELP datahub_skill_generation_count Total skills created
# TYPE datahub_skill_generation_count counter
datahub_skill_generation_count 47

# HELP datahub_weight_updates_total Total weight changes
# TYPE datahub_weight_updates_total counter
datahub_weight_updates_total 156

# HELP datahub_feedback_signals_total Total feedback received
# TYPE datahub_feedback_signals_total counter
datahub_feedback_signals_total 892

# HELP datahub_daemon_convergence_status Learning daemon convergence (0-1)
# TYPE datahub_daemon_convergence_status gauge
datahub_daemon_convergence_status 0.78

# HELP datahub_audit_chain_height Number of events in audit trail
# TYPE datahub_audit_chain_height gauge
datahub_audit_chain_height 1095

# HELP datahub_audit_chain_verified Chain integrity status (0-1)
# TYPE datahub_audit_chain_verified gauge
datahub_audit_chain_verified 1.0
```

### Critical Alerts

| Alert | Condition | Action |
|---|---|---|
| **AuditChainBroken** | `datahub_audit_chain_verified == 0` | HALT immediately. Investigate tampering. |
| **DaemonStalled** | No feedback > 1 hour | Check daemon logs. Restart if needed. |
| **LearningNotConverging** | `convergence_status < 0.5` after 1000 samples | Review feedback quality. May need data rebalancing. |

### Grafana Dashboard

Create dashboard from metrics:

```yaml
# datasource: Prometheus (http://localhost:9090)
panels:
  - title: "Skill Generation Rate"
    target: "rate(datahub_skill_generation_count[5m])"
  - title: "Feedback Velocity"
    target: "rate(datahub_feedback_signals_total[5m])"
  - title: "Convergence Progress"
    target: "datahub_daemon_convergence_status"
  - title: "Audit Chain Status"
    target: "datahub_audit_chain_verified"
```

---

## Troubleshooting

### Dashboard Shows "Loading..." Forever

**Diagnosis:**
- Check browser console (F12 → Console tab)
- Look for 404 or 500 errors in network tab

**Fix:**
```bash
# Ensure backend endpoints exist
curl -s http://localhost:8765/api/v1/learning/skills | jq .

# If 404: restart console
systemctl --user restart corvin-webui.service

# If 500: check backend logs
journalctl --user -u corvin-console.service -n 50
```

### Audit Trail Broken (Hash Chain Fails)

**Diagnosis:**
```python
trail = AuditTrail(tenant_id="_default", chain_path=...)
is_valid, msg = trail.verify_integrity()
# Output: (False, "Chain broken at line 42: expected prev_hash=abc123, got xyz789")
```

**Root Causes:**
1. Manual edit of `audit.jsonl` (never do this)
2. Filesystem corruption
3. Process crash mid-write

**Recovery:**
```bash
# Option 1: Restore from backup
cp ~/.corvin/tenants/_default/audit/datahub.jsonl.bak \
   ~/.corvin/tenants/_default/audit/datahub.jsonl

# Option 2: Start fresh (delete all audit data — only if absolutely necessary)
rm ~/.corvin/tenants/_default/audit/datahub.jsonl
systemctl --user restart corvin-console.service
# Writes a new chain starting from event 1
```

### High Memory Usage from Dashboard

**Diagnosis:**
```bash
# Check memory per process
ps aux | grep corvin-console
# Look for >500MB usage
```

**Root Cause:** Dashboard loads 1000+ events into React state.

**Fix:**
- Reduce query limit in dashboard code (default 50 skills, 100 weights)
- Implement pagination in frontend (not yet done in Phase 4)
- Archive old audit events:
  ```python
  reporter.enforce_retention(days=30)  # Keep only 30 days
  ```

---

## Upgrade Path

### From Phase 3 to Phase 4

No breaking changes. Phase 4 is **backward-compatible**:
- Phases 1-3 continue unchanged
- New audit trail starts fresh (no migration needed)
- Dashboard added as new optional panel

**Migration Steps:**
1. Deploy Phase 4 code
2. Restart console: `systemctl --user restart corvin-webui.service`
3. Audit trail initialized on first skill generation
4. Dashboard available at `/console/#/learning-dashboard`

---

## SLOs & Performance

| Metric | Target | Measurement |
|---|---|---|
| Dashboard Load | <1000ms | Page load (waterfall chart in DevTools) |
| Audit Write | <50ms | Event persisted to disk |
| Compliance Export (1000 events) | <500ms | Time to generate JSONL |
| Chain Verification (1000 events) | <100ms | Time to verify hash chain |

**Monitoring:**
```python
import time
start = time.time()
trail.write_event(...)
elapsed = (time.time() - start) * 1000
print(f"Audit write: {elapsed:.1f}ms")
```

---

## Support & Escalation

**Phase 4 Issues:**
1. Check `/var/log/corvin/console.log` (or journalctl)
2. Verify audit trail integrity
3. Run compliance export to confirm PII redaction working
4. Check Prometheus metrics for anomalies

**Escalation Path:**
- Audit chain broken → **CRITICAL** (fail-closed)
- Dashboard unreachable → **HIGH** (no visibility)
- Bias detection alerts → **MEDIUM** (investigate learning quality)
- Retention policy issues → **MEDIUM** (GDPR risk)

---

## References

- **ADR-0661:** DataHub Creator Architecture (Phases 1-4)
- **ADR-0314:** Learning Infrastructure (event schema, persistence)
- **ADR-0232:** Boot Tripwire (fail-closed audit verification)
- **GDPR Art. 5:** Storage Limitation (retention policy)
- **GDPR Art. 30:** Records of Processing (audit trail)
