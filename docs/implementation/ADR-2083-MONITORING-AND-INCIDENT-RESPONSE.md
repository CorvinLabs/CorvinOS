# ADR-2083: Monitoring & Incident Response Plan

> **Implementation status (2026-09-27, adversarial review) — the runtime pieces this document relies on are NOT implemented and fail closed:**
> - **Background workflow runner** (`corvin_operator/workflows/workflow_background_runner.py`): there is no background executor. `WorkflowBackgroundRunner.start()` returns `status="blocked"` with `reason_code="not_implemented"` for every workflow ("no background workflow executor is wired on this install") and records the attempt on the tenant audit chain as `workflow.background_start` (content-free: `run_id`, `status`, `bridge_type`, `reason_code`). It never answers `queued`, and no workflow runs.
> - **Interactive loop** (`corvin_operator/autonomy/loop_executor_bridge_aware.py`, CLI/Web path): nothing schedules the loop — `LoopExecutor.run()` returns `reason_complete="not_implemented"`. The non-interactive (Discord/Slack) path runs the iterations in the CALLING thread, blocking the caller; it is not a background job. `get_audit_trail()` is an in-memory trace, not the audit chain.
> - **No bridge calls any of it**: `autonomy_detector`, `loop_executor_bridge_aware` and `workflow_background_runner` are imported only by `scripts/adr2083_staging_validation.py` (an in-process simulation that exits 3, "simulated only — no staging evidence", when all its checks pass) and its tests; the runner and the loop executor carry the "NOT WIRED: no production caller" marker. `/loop` on Discord and Slack workflow starts behave exactly as before ADR-2083.
> - Checklists, rollout gates, monitoring signals and log lines below describe the DESIGN, not observed behaviour.

**Project:** ADR-2083 Non-Interactive Bridge Autonomy  
**Scope:** Canary (Phase 2) + GA (Phase 3) + Monitor (Phase 4)  
**Owner:** Release Team + Support Team  
**Date:** 2026-09-27

---

## Monitoring Stack

### Real-Time Dashboards

**Dashboard 1: Canary vs. Stable (Phase 2)**

| Metric | Stable (Baseline) | Canary (Target) | Alert |
|---|---|---|---|
| Error Rate | < 0.5% | < 1.0% | > 1.5% |
| p50 Latency | ~200ms | < 300ms | > 400ms |
| p99 Latency | ~400ms | < 1000ms | > 1500ms |
| Discord Uptime | > 99.9% | > 99% | < 99% |
| Slack Uptime | > 99.9% | > 99% | < 99% |
| Queue Depth | < 50 | < 150 | > 300 |
| Audit Trail Gaps | 0 | 0 | > 0 |

**Refresh:** Every 60 seconds  
**Audience:** Release Eng, On-Call, Leadership

---

### Dashboard 2: GA Health (Phase 3–4)

**Real-Time Metrics:**

```
┌─────────────────────────────────────────────────────────────┐
│ ADR-2083 GA Health Dashboard                                 │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  📊 Error Rate:         0.42% ━━━━╋━━━━ < 0.8% target     ✅ │
│  ⏱️  p99 Latency:       620ms ━━━╋━━━━━ < 800ms target    ✅ │
│  🔄 Discord Uptime:     99.87% ━━━━╋━━━━ > 99% target      ✅ │
│  🔄 Slack Uptime:       99.91% ━━━━╋━━━━ > 99% target      ✅ │
│                                                               │
│  Incidents (24h):                                            │
│    🟢 SEV1: 0                                                │
│    🟡 SEV2: 1 (resolved)                                    │
│    🟠 SEV3: 2 (in progress)                                  │
│    🔵 SEV4: 4 (logged)                                       │
│                                                               │
│  Audit Trail:                                                │
│    Chain Height:        142857                               │
│    Hash Integrity:      ✅ Verified                         │
│    Gaps:                0                                    │
│                                                               │
│  Last Updated: 2026-09-27 14:32:15 UTC                      │
└─────────────────────────────────────────────────────────────┘
```

**Key Metrics Collected:**
- Error rate (by component: autonomy_detector, loop_executor, workflow_runner)
- Latency (p50, p95, p99)
- Throughput (requests/sec by bridge)
- Queue depth (pending workflows, loops)
- Incident log (all SEV1/2 events)
- Audit trail (events/hour, hash verification)

---

## Alert Rules

### Tier 1: Automatic Escalation (Page On-Call)

```
if error_rate > 5%:
    alert_severity = CRITICAL
    notify = pagerduty_oncall
    action = investigate immediately
    
if p99_latency > 2000ms:
    alert_severity = CRITICAL
    notify = pagerduty_oncall
    
if discord_uptime < 95%:
    alert_severity = CRITICAL
    notify = pagerduty_oncall
    
if slack_uptime < 95%:
    alert_severity = CRITICAL
    notify = pagerduty_oncall
    
if audit_trail_gaps > 0:
    alert_severity = CRITICAL  (data integrity!)
    notify = pagerduty_oncall
```

### Tier 2: High Priority (Slack #incidents)

```
if error_rate > 1%:
    alert_severity = HIGH
    notify = slack_incidents_channel
    action = investigate within 15min
    
if p99_latency > 1000ms:
    alert_severity = HIGH
    notify = slack_incidents_channel
    
if queue_depth > 500:
    alert_severity = HIGH
    notify = slack_incidents_channel
    action = scale workers or pause deployments
```

### Tier 3: Medium Priority (Email digest)

```
if error_rate > 0.7%:
    alert_severity = MEDIUM
    notify = team_email
    action = investigate within 1 hour
    
if p99_latency > 600ms:
    alert_severity = MEDIUM
    notify = team_email
```

### Tier 4: Info (Logging only)

```
if error_rate > 0.4%:
    action = log, no alert
    
All operational events logged to: 
    /var/log/corvinOS/bridge/adr2083.log
```

---

## Incident Classification & Response

### Severity Levels

```
┌─────────────┬──────────────────┬──────────────────┬──────────┐
│ Severity    │ Example           │ Response Time    │ Escalate │
├─────────────┼──────────────────┼──────────────────┼──────────┤
│ SEV1        │ Data loss         │ < 5 min          │ Immediate│
│ (Critical)  │ Auth bypass       │                  │ Page     │
│             │ Cascading outage  │                  │ CEO      │
├─────────────┼──────────────────┼──────────────────┼──────────┤
│ SEV2        │ 50% error rate    │ < 15 min         │ Page     │
│ (High)      │ p99 > 5s          │                  │ On-Call  │
│             │ One bridge down   │                  │          │
├─────────────┼──────────────────┼──────────────────┼──────────┤
│ SEV3        │ Error rate 2–5%   │ < 1 hour         │ Notify   │
│ (Medium)    │ p99 1–2s          │                  │ Team     │
│             │ Partial degradation│                 │          │
├─────────────┼──────────────────┼──────────────────┼──────────┤
│ SEV4        │ Error rate 0.5–1% │ < 4 hours        │ Log only │
│ (Low)       │ Isolated impact   │                  │          │
└─────────────┴──────────────────┴──────────────────┴──────────┘
```

### Incident Response Flow

```
┌─ Alert fires
│
├─→ Is it ADR-2083 related?
│   ├─ YES → proceed
│   └─ NO  → escalate to appropriate team
│
├─→ Classify severity
│   ├─ SEV1/2 → Page on-call (immediate)
│   ├─ SEV3   → Notify team (15 min SLA)
│   └─ SEV4   → Log only
│
├─→ Assess root cause (5-layer diagnosis)
│   ├─ Layer 1: Metrics (error spike, latency jump?)
│   ├─ Layer 2: Code (any commits in last 1h?)
│   ├─ Layer 3: Config (any recent changes?)
│   ├─ Layer 4: Infrastructure (capacity? dependencies?)
│   └─ Layer 5: External (third-party API? rate limits?)
│
├─→ Decide: Rollback or Fix Forward
│   ├─ Rollback (< 5 min) if:
│   │  - SEV1 (always)
│   │  - SEV2 + root cause unclear
│   │  - Data integrity compromised
│   │
│   └─ Fix Forward if:
│      - Root cause identified + fix simple
│      - No customer impact
│      - SLA allows (SEV3/4)
│
├─→ Execute
│   ├─ Rollback: revert commits, restart services, verify
│   └─ Fix:      apply hotfix, re-test, monitor closely
│
├─→ Verify resolution (metrics return to baseline)
│
└─→ RCA (within 24h of SEV2+)
    ├─ Timeline
    ├─ Impact (errors, data, users)
    ├─ Root cause (5-layer)
    ├─ Fix
    └─ Prevention (what monitoring gap, design flaw?)
```

---

## Rollback Procedure

### SEV1/2 Automatic Rollback (< 5 min)

```bash
#!/bin/bash
# Rollback ADR-2083 in emergency
set -e

echo "🚨 EMERGENCY ROLLBACK INITIATED"
echo "Timeline: $(date)"

# Step 1: Stop traffic to canary/GA version
echo "Step 1: Routing 100% to stable version..."
kubectl set traffic deployment/discord-bridge stable=100 canary=0
kubectl set traffic deployment/slack-bridge stable=100 canary=0

# Wait for connections to drain
sleep 10

# Step 2: Revert code commits
echo "Step 2: Reverting ADR-2083 commits..."
git revert e350c14e7  # CorvinOS commit
git revert 51c7142    # Corvin-ADR commit
git push origin main

# Step 3: Restart services
echo "Step 3: Restarting services..."
systemctl restart corvin-discord-bridge
systemctl restart corvin-slack-bridge
systemctl restart corvin-gateway

# Step 4: Verify baseline metrics
echo "Step 4: Verifying baseline..."
sleep 30
ERROR_RATE=$(curl -s http://monitoring/api/error_rate)
if [[ $ERROR_RATE > 0.5 ]]; then
    echo "❌ ROLLBACK INCOMPLETE - error rate still elevated"
    exit 1
fi

echo "✅ ROLLBACK COMPLETE"
echo "Status: Stable version running"
echo "Next: RCA within 24h"
```

### SEV3/4 Fix Forward (if identified + simple)

```bash
#!/bin/bash
# Apply hotfix for SEV3/4 issue
set -e

echo "🔧 HOTFIX DEPLOYMENT"

# 1. Identify issue (from RCA)
ISSUE="Discord loop timeout not enforced"

# 2. Apply fix
git checkout -b hotfix/adr2083-timeout-fix
# ... make code changes ...
git commit -m "fix(adr2083): enforce timeout on Discord loops"

# 3. Test in canary
git push origin hotfix/adr2083-timeout-fix
# PR review + merge
git checkout main && git pull

# 4. Re-test in staging (Phase 1 mini)
pytest tests/e2e/test_bridge_autonomy_e2e.py::TestLoopExecutionModes -v

# 5. Deploy to canary (10% traffic)
kubectl set image deployment/discord-bridge \
  corvin-bridge=corvinos:e350c14e7-fixed

# 6. Monitor 1h
# ... check metrics ...

# 7. If stable, scale to GA (50% → 100%)
echo "✅ Hotfix stable, scaling to GA"
```

---

## Post-Incident RCA Template

**File:** `outputs/incident_reports/INC-2026-09-27-ADR2083-timeout-issue.md`

```markdown
# Incident Report: ADR-2083 Timeout Issue

**Incident ID:** INC-2026-09-27-001  
**Severity:** SEV2  
**Duration:** 2026-09-27 14:00 UTC → 2026-09-27 14:25 UTC (25 min)  
**Owner:** Release Team

## Timeline

| Time | Event |
|---|---|
| 14:00 UTC | Alert: p99 latency spike to 2.5s |
| 14:02 UTC | On-call paged |
| 14:05 UTC | Determined: Discord loop timeout not enforced |
| 14:10 UTC | Decision: Rollback (root cause unclear) |
| 14:15 UTC | Rollback complete, metrics back to baseline |
| 14:25 UTC | Investigation begins |

## Impact

- Duration: 25 minutes
- Affected: 100% Discord bridge traffic
- Errors: 1,248 timeouts (5.2% of requests)
- Data Loss: 0 (audit trail complete)
- Customers: ~50 active loops interrupted

## Root Cause (5-Layer Diagnosis)

| Layer | Finding |
|---|---|
| **L1: Metrics** | Latency spike coincided with canary deploy |
| **L2: Code** | Commit e350c14e7: timeout not enforced in loop_executor |
| **L3: Config** | Default timeout was 3600s, but in test was 0.3s |
| **L4: Design** | No input validation on LoopConfig.timeout_seconds |
| **L5: Process** | E2E tests passed but didn't validate realistic timeout |

**Root Cause:** Timeout parameter had no lower bound, allowing tests to pass with unrealistic values (0.3s). When deployed to production with realistic timeouts, loop_executor hit the timeout immediately and failed.

## Fix

```python
# Added validation in LoopConfig
@dataclass
class LoopConfig:
    timeout_seconds: int
    
    def __post_init__(self):
        if self.timeout_seconds < 60:
            raise ValueError("timeout_seconds must be >= 60")
```

Commit: `abc1234` (hotfix/adr2083-timeout-validation)

## Prevention

1. **Add schema validation** to LoopConfig (minimum values for all parameters)
2. **Expand E2E tests** to validate with production-realistic timeout values
3. **Add audit schema** to catch timeout violations (log every timeout with reason)

## Lessons Learned

- Test with production-realistic parameters (not edge cases in isolation)
- Validate input bounds at schema level (not just at runtime)
- Add observability: timeout reason (exceeded? completed?) to audit trail

---

**RCA Completed:** 2026-09-27 15:30 UTC  
**Approved By:** Release Lead  
**Follow-Up:** ADR-XXXX (Input Validation Standards)
```

---

## Hourly Monitoring Export

**Automated every hour:**

```bash
#!/bin/bash
# Export monitoring data to S3 for audit + compliance

TIMESTAMP=$(date -u +%Y-%m-%d_%H-%M-%S)
EXPORT_DIR="/tmp/monitoring_export_$TIMESTAMP"
mkdir -p "$EXPORT_DIR"

# 1. Audit trail (last 60 min)
tail -c 50M ~/.corvin/tenants/_default/global/forge/audit.jsonl > \
    "$EXPORT_DIR/audit_trail_60min.jsonl"

# 2. Error logs
journalctl -u corvin-discord-bridge -u corvin-slack-bridge \
    --since "1 hour ago" > "$EXPORT_DIR/error_logs.txt"

# 3. Metrics snapshot
curl -s http://monitoring/api/export?window=1h > \
    "$EXPORT_DIR/metrics_60min.json"

# 4. Incident log
grep "incident_" ~/.corvin/tenants/_default/global/forge/audit.jsonl | \
    tail -100 > "$EXPORT_DIR/incident_log.jsonl"

# 5. Upload to S3
aws s3 sync "$EXPORT_DIR" \
    s3://corvinOS-audit-trail-prod/2026-09-27/

# 6. Cleanup
rm -rf "$EXPORT_DIR"

echo "✅ Hourly export complete"
```

---

## 7-Day Review Meeting

**Scheduled:** 2026-10-04 10:00 AM UTC  
**Duration:** 1 hour  
**Attendees:** Release Eng, Support, Product, Platform Lead

**Agenda:**

1. **Metrics Summary** (10 min)
   - Uptime %, error rate, p99 latency
   - Incident count by severity
   - Comparison: baseline vs. ADR-2083

2. **Incident Review** (20 min)
   - Top 3 incidents (if any)
   - Root causes + fixes
   - Prevention improvements

3. **Audit Trail Verification** (10 min)
   - Hash-chain integrity: ✅
   - Tenant isolation: ✅
   - Data completeness: ✅

4. **Decision** (10 min)
   - Move to standard monitoring (weekly reviews)
   - Continue monitoring (if issues)
   - Rollback (if critical failures)

5. **Action Items & Follow-Up** (10 min)
   - Document lessons learned
   - Update runbooks / SLOs
   - Plan improvements

---

## Monitoring Metrics Export (End of Phase 4)

**7-Day Report Template:**

```markdown
# ADR-2083 Production Monitoring Report (7 Days)

**Period:** 2026-09-27 — 2026-10-04  
**Status:** ✅ SUCCESSFUL

## Executive Summary
- Uptime: 99.87% (0:31:20 downtime total)
- Incidents: 3 (1 SEV2, 2 SEV3, 0 SEV1)
- Data Integrity: 100% (audit trail complete)
- Error Rate: 0.62% average (within target)

## Key Metrics

| Metric | Target | Actual | Status |
|---|---|---|---|
| Uptime | > 99.5% | 99.87% | ✅ |
| Error Rate | < 0.8% | 0.62% | ✅ |
| p99 Latency | < 800ms | 680ms | ✅ |
| SEV1 Incidents | 0 | 0 | ✅ |
| Audit Trail Gaps | 0 | 0 | ✅ |

## Incidents

- **INC-2026-09-27-001** (SEV2): Discord timeout not enforced [RESOLVED]
- **INC-2026-09-28-001** (SEV3): Slack rate limit spike [RESOLVED]
- **INC-2026-09-30-001** (SEV3): Queue backlog warning [RESOLVED]

## Recommendations

1. Update timeout validation (merged)
2. Tune Slack rate limiter thresholds (testing)
3. Add queue metrics to hourly exports (backlog)

## Conclusion

ADR-2083 is stable and ready for standard operations. Recommend monitoring weekly (vs. continuous) and moving to standard on-call rotation.

**Sign-Off:** Release Lead, Platform Lead  
**Date:** 2026-10-04
```

---

## Sign-Off

**Monitoring Plan:** ✅ Complete  
**Incident Response:** ✅ Procedures defined  
**7-Day Review:** ✅ Scheduled (2026-10-04)

Ready for Phase 1 Staging → Phase 4 Production Monitoring.
