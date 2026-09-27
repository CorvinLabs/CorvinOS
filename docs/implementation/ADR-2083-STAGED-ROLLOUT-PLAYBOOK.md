# ADR-2083 Staged Rollout Playbook

**Project:** Non-Interactive Bridge Autonomy Design (ADR-2083)  
**Timeline:** 4 phases over 10 days (Staging 48h → Canary 24h → GA 48h → Monitor 7d)  
**Owner:** CorvinOS Release Team  
**Status:** 🟡 **IN PROGRESS** (Phase 1 starting)

---

## Phase 1: Staging Validation (48 hours)

### Objective
Validate Discord loops + Slack workflows in **staging environment** before any live traffic.

**Success Criteria:**
- ✅ Discord loops execute 100+ test messages, latency < 500ms
- ✅ Slack workflows process 50+ diverse payloads
- ✅ Webhook responses + rate-limit recovery working
- ✅ 100% pass rate across 3 independent test runs
- ✅ Zero audit trail anomalies (hash-chain integrity)

### Phase 1 Timeline

| Time | Task | Owner | Status |
|---|---|---|---|
| **T+0h** | Deploy to staging | Release Eng | ⏳ |
| **T+1h** | Discord validation suite 1 | QA | ⏳ |
| **T+6h** | Slack validation suite 1 | QA | ⏳ |
| **T+12h** | Discord validation suite 2 (independent run) | QA | ⏳ |
| **T+24h** | Slack validation suite 2 (independent run) | QA | ⏳ |
| **T+30h** | Full 12h load test (Discord) | QA | ⏳ |
| **T+42h** | Full 12h load test (Slack) | QA | ⏳ |
| **T+48h** | Final audit trail verification + gate decision | Release Eng | ⏳ |

### Phase 1 Validation Checklist

#### Discord Loops (100+ test messages)

```markdown
### Discord Loop Validation Suite

Test Category: Basic Functionality
- [ ] Loop starts in background mode (detect CORVIN_BRIDGE_TYPE=discord)
- [ ] Audit event logged: { event: "loop_scheduled", autonomy_mode: "background" }
- [ ] Each iteration completes within 500ms
- [ ] @mentions in loop prompt processed correctly
- [ ] Reactions to loop messages work (delete, add emoji)
- [ ] 10x retry on rate-limit works (backoff: 1s, 2s, 4s...)

Test Category: Edge Cases
- [ ] Loop with 100 iterations completes correctly
- [ ] Loop with short interval (10s) under load
- [ ] Loop with long prompt (>2000 chars) handled
- [ ] Loop with special chars (emoji, unicode) not corrupted
- [ ] Loop timeout (3600s) enforced correctly
- [ ] Loop cancellation (manual stop) works

Test Category: Audit Trail
- [ ] Every loop iteration logged to audit.jsonl
- [ ] Audit events chain correctly (hash_prev → hash_current)
- [ ] Tenant_id present in all events
- [ ] Bridge type = "discord" in all events
- [ ] No audit gaps (complete iteration sequence)

Execution: Run each test 5 times
Expected: All pass ≥5/5 runs
```

#### Slack Workflows (50+ diverse payloads)

```markdown
### Slack Workflow Validation Suite

Test Category: Webhook Integration
- [ ] Workflow start via webhook returns async ack (run_id format: wf_*)
- [ ] Ack message: "Workflow gestartet: wf_***, läuft im Hintergrund"
- [ ] run_id is unique across 50 concurrent workflow starts
- [ ] Workflow state persisted (queryable via get_workflow_result)

Test Category: Payload Handling
- [ ] JSON payload (100 bytes) processed
- [ ] Large payload (10MB) processed or rejected with clear error
- [ ] Malformed JSON returns 400 (not 500)
- [ ] Missing required fields returns 400
- [ ] Extra fields in payload ignored (not error)

Test Category: Rate Limits & Resilience
- [ ] 50 workflows/min: all queued (no drops)
- [ ] 200 workflows/min: rate-limit triggered (429), backoff works
- [ ] Webhook retry on timeout (3s timeout, 3 retries)
- [ ] Queue persistence: if scheduler crashes mid-run, workflows restart

Test Category: Audit Trail
- [ ] Every workflow start logged: event=workflow_started
- [ ] Async ack doesn't wait for completion (non-blocking)
- [ ] Workflow result (when complete) logged: event=workflow_complete
- [ ] Bridge type = "slack" in all events
- [ ] Tenant_id = _default in staging

Execution: Run each test 3 times with different payloads
Expected: All pass ≥3/3 runs
```

### Phase 1 Load Test (12h continuous per bridge)

**Discord Load Test (12h continuous):**
```bash
# Simulate 20 active loops, 5 iterations each, 60s interval
# Expected: 100 total iterations, 1200 messages
# Success: 0% data loss, 0% duplicates, p99 latency <500ms
```

**Slack Load Test (12h continuous):**
```bash
# Simulate 10 concurrent workflows, 5 payloads each
# Expected: 50 total workflow starts, all queued
# Success: 0% queue drops, rate-limit handling correct, 0% audit gaps
```

### Phase 1 Gate Decision

**Gate 1: All validation suites pass (3 independent runs)**
```
if (discord_suite_pass_rate >= 100% AND slack_suite_pass_rate >= 100%) {
    gate = PASS
} else {
    gate = FAIL
    escalate to root-cause-by-layer
}
```

**Gate 2: Audit trail integrity**
```
if (audit_chain_verified AND no_hash_gaps AND tenant_isolation_ok) {
    gate = PASS
} else {
    gate = FAIL → investigate ADR-0232 compliance
}
```

**Gate 3: Load test stability**
```
if (p99_latency < 500ms AND error_rate < 0.1% AND zero_data_loss) {
    gate = PASS → proceed to Canary
} else {
    gate = FAIL → tune performance or identify bottleneck
}
```

### Phase 1 Rollback Plan

**If Gate Fails:**
1. Revert ADR-2083 commits (both Corvin-ADR + CorvinOS)
2. Restart staging environment
3. Investigation: root-cause-by-layer (audit logs, test output, metrics)
4. Decision: fix forward (code change) or defer (re-plan phase 1)

---

## Phase 2: Canary Deployment (24 hours)

### Objective
Deploy ADR-2083 to **10% of live Discord/Slack traffic**, monitor for anomalies.

**Success Criteria:**
- ✅ Error rate < 1% (vs. baseline < 0.5%)
- ✅ p99 latency < 1s (vs. baseline ~300ms)
- ✅ Zero data loss (audit trail complete)
- ✅ Discord + Slack uptime > 99%
- ✅ Incident severity ≤ SEV3 (no SEV1/SEV2)

### Phase 2 Timeline

| Time | Task | Owner | Status |
|---|---|---|---|
| **T+0h** | Canary traffic: 10% | DevOps | ⏳ |
| **T+1h** | Alert: error rate spike? | Monitoring | ⏳ |
| **T+6h** | Mid-phase metrics review | Release Eng | ⏳ |
| **T+12h** | Metrics stable checkpoint | Release Eng | ⏳ |
| **T+24h** | Gate decision: scale to 50% or full GA? | Release Eng | ⏳ |

### Phase 2 Canary Routing

```yaml
# Load Balancer Config
discord-bridge:
  version_stable: "e350c14e7"   # Previous commit (before ADR-2083)
  version_canary: "e350c14e7"   # New commit (ADR-2083)
  canary_traffic_percent: 10    # 10% to new version
  
  routing:
    - match: random(0-100) < 10 → version_canary
    - match: random(0-100) >= 10 → version_stable
    
slack-bridge:
  version_stable: "e350c14e7"
  version_canary: "e350c14e7"
  canary_traffic_percent: 10
```

### Phase 2 Monitoring Dashboard

**Real-Time Metrics (update every 60s):**

| Metric | Alert Threshold | Baseline | Target |
|---|---|---|---|
| **Error Rate** | > 1% | < 0.5% | < 0.8% |
| **p50 Latency** | > 500ms | ~200ms | < 300ms |
| **p99 Latency** | > 1s | ~400ms | < 600ms |
| **Discord Uptime** | < 99% | > 99.9% | > 99% |
| **Slack Uptime** | < 99% | > 99.9% | > 99% |
| **Audit Trail Gap** | Any | 0 | 0 |
| **Queue Depth** | > 500 | < 100 | < 150 |

**Dashboards to maintain:**
1. **Canary vs Stable** — side-by-side error rate, latency, throughput
2. **Audit Trail** — real-time hash-chain verification
3. **Incidents** — severity, component, duration

### Phase 2 Incident Response

**Classification (by severity):**

| Severity | Example | Response |
|---|---|---|
| **SEV1 (Critical)** | Data loss, auth bypass, cascading outage | Immediate rollback (< 5 min) |
| **SEV2 (High)** | 50% error rate, p99 > 5s | Page on-call, assess rollback vs fix |
| **SEV3 (Medium)** | Error rate 2–5%, p99 1–2s, single bridge affected | Monitor, log, plan fix |
| **SEV4 (Low)** | Error rate 0.5–1%, isolated to one user | Document, no rollback |

**Incident Response SOP:**
```
1. Detect: Alert fires (error > 1% or p99 > 1s)
2. Classify: SEV1/2/3/4 based on metrics + customer impact
3. Assess:
   - Is this ADR-2083 or pre-existing issue?
   - Grep: git log --since=1h (changes deployed recently?)
   - Query audit trail for anomalies
4. Decide:
   - SEV1/2: Rollback within 5 minutes
   - SEV3: Fix forward (1h SLA) or rollback
5. Execute: Rollback script OR hotfix + re-deploy
6. Verify: Metrics return to baseline within 15 min
7. RCA: Post-incident review within 24h
```

### Phase 2 Gate Decision

**At T+24h, check:**

```
metrics = fetch_metrics(canary_start, now)

if metrics.error_rate < 1% AND 
   metrics.p99_latency < 1s AND 
   metrics.audit_trail_intact AND 
   incident_count <= 1:
    gate = PASS → proceed to Full GA
else:
    gate = HOLD → extend canary 12h, investigate
    gate = ROLLBACK → revert if incident_count >= 2
```

---

## Phase 3: General Availability (48 hours)

### Objective
Roll out ADR-2083 to **100% of live Discord/Slack traffic**.

**Success Criteria:**
- ✅ Error rate stable < 0.7% (within baseline variance)
- ✅ p99 latency < 800ms (stable)
- ✅ Zero SEV1/SEV2 incidents
- ✅ User-facing success rate ≥ 99.5%
- ✅ Audit trail 100% complete (no gaps)

### Phase 3 Timeline

| Time | Task | Owner | Status |
|---|---|---|---|
| **T+0h** | Scale to 50% traffic | DevOps | ⏳ |
| **T+2h** | Metrics checkpoint | Monitoring | ⏳ |
| **T+6h** | Scale to 100% traffic | DevOps | ⏳ |
| **T+8h** | Full rollout stable checkpoint | Release Eng | ⏳ |
| **T+24h** | 24h stable gate | Release Eng | ⏳ |
| **T+48h** | Proceed to 7d monitoring phase | Release Eng | ⏳ |

### Phase 3 Rollout Ramp

```
T+0h:   10% → 50%
        Wait 2h for metric stability
T+2h:   Check error rate, latency, incidents
        If OK: continue
T+6h:   50% → 100%
        Wait 2h for stabilization
T+8h:   Checkpoint: all green?
        If OK: full GA complete
        If issues: partial rollback to 50%, investigate
T+24h:  24h stability gate
        If OK: declare GA success
```

### Phase 3 Monitoring

Same dashboards as Canary, but now monitoring **100% traffic**:
- Error rate < 0.7%
- p99 latency < 800ms
- Zero SEV1 incidents
- Audit trail complete

### Phase 3 Rollback

**If critical issue discovered:**
```
if incident_severity == SEV1 or error_rate > 5%:
    traffic_routed_to_stable = 100%  # Immediate
    post_incident_review() → root cause
    fix_deployed_to_canary() → re-validate
    re_attempt_ga_with_fix()
else:
    continue monitoring
```

---

## Phase 4: 7-Day Production Monitoring

### Objective
Continuous monitoring + incident response for 7 days post-GA.

**Success Criteria:**
- ✅ Uptime ≥ 99.5% (Discord + Slack combined)
- ✅ Error rate < 0.8%
- ✅ Zero unplanned rollbacks
- ✅ Audit trail 100% complete + hash-chained verified
- ✅ Customer complaints = 0 or ≤ 1 low-severity

### Phase 4 Monitoring Stack

**Hourly Exports:**
```bash
# Every hour, export:
1. Audit trail (last 60min) → s3://corvinOS-audit-trail-prod/2026-09-27/
2. Error logs (severity >= warning)
3. Performance metrics (p50/p95/p99 latency)
4. Incident log (if any)
```

**Dashboards (continuous):**
- Discord/Slack success rate %
- Error rate (by component: autonomy_detector, loop_executor, workflow_runner)
- Latency distribution (p50, p95, p99)
- Queue depth + backlog
- Audit trail gaps (0 expected)

**Alerts (real-time, multi-channel):**
- PagerDuty: SEV1/2 incidents
- Slack #incidents: all incidents ≥ SEV3
- Email: daily summary (uptime %, incident count, top issues)

### Phase 4 Incident Response (7d SOP)

**On-Call Rotation:**
- CorvinOS Release Team (primary)
- Support Team (secondary)
- Platform Team (escalation)

**Response Times:**
- SEV1: page on-call, respond within 5 min
- SEV2: respond within 15 min, page on-call if needed
- SEV3: log, respond within 1 hour
- SEV4: document, no page

**Post-Incident RCA (within 24h of SEV2+):**
1. Timeline (what happened, when)
2. Impact (errors, data, affected users)
3. Root cause (5-layer diagnosis)
4. Fix (code or config change)
5. Prevention (monitoring gap? design weakness?)

### Phase 4 Success Metrics

**At 7-day mark, evaluate:**

```
uptime = (total_time - downtime) / total_time
sev1_count = incidents_with_severity == 1
sev2_count = incidents_with_severity == 2
audit_trail_gaps = count(missing_events_in_chain)

if uptime >= 99.5% AND sev1_count == 0 AND sev2_count <= 1 AND audit_trail_gaps == 0:
    status = SUCCESS → ADR-2083 is STABLE
    recommend_next_action = "Monitor ongoing (normal ops)"
else:
    status = PARTIAL_SUCCESS or FAILURE
    recommend_next_action = "Investigate + Fix Forward or Rollback"
```

### Phase 4 Post-Monitoring Actions

**After 7 days, decision:**

| Outcome | Action |
|---|---|
| **Success (all metrics green)** | Move to standard monitoring (weekly reviews), update docs |
| **Partial Success (1–2 SEV3 incidents, stable)** | Continue monitoring 7d more, document known issues |
| **Failure (SEV1/2 or > 3 incidents, uptime < 99%)** | RCA + fix, plan re-rollout after fix validation |

---

## Cross-Phase Artifact Handoff

### Deliverables by Phase

| Phase | Deliverable | Format | Owner |
|---|---|---|---|
| **1. Staging** | Test Results + Audit Trail Verification | JSON + PDF report | QA |
| **2. Canary** | Incident Log + Metrics Snapshot | CSV + Dashboard export | Release Eng |
| **3. GA** | Rollout Timeline + Error Logs | Timeline chart + CSV | Release Eng |
| **4. Monitor** | 7-day Report (uptime %, incidents, RCAs) | PDF + Slack post | Release Eng |

### Documentation Updates

**Upon successful GA (Phase 3):**
- [ ] Update CLAUDE.md with ADR-2083 rule (no explicit token warnings)
- [ ] Update Bridge Adapter docs (CORVIN_BRIDGE_TYPE usage)
- [ ] Update Runbooks (discord-bridge + slack-bridge startup)
- [ ] Update SLOs (if error budget changed)

---

## Escalation Path (if gate fails)

### Phase 1 Failure → Phase 1 Rework

```
If validation suite fails:
1. root-cause-by-layer (audit logs, test output)
2. Classify: code bug vs. test issue vs. environment issue
3. Fix + re-run Phase 1 suite
4. Re-attempt gate after 2 independent passes
```

### Phase 2 Failure (Canary) → Rollback + Rework

```
If canary metrics degrade:
1. Rollback to previous version (< 5 min)
2. RCA: incident log + audit trail analysis (24h)
3. Fix identified issue
4. Re-validate in staging (Phase 1 mini)
5. Re-attempt canary
```

### Phase 3 Failure (GA) → Rollback + Redesign

```
If GA encounters SEV1/2:
1. Rollback to previous version (< 5 min)
2. RCA: full investigation (48h)
3. Architectural fix (may require ADR change)
4. Re-plan rollout (Phase 1–4 restart)
```

---

## Runbook: Phase-to-Phase Transition

### Staging → Canary (T+48h)

**Prerequisites:**
- [ ] All Phase 1 gates PASS
- [ ] Audit trail verified (no gaps)
- [ ] Load test completed (12h continuous)

**Release Engineer Checklist:**
- [ ] Create canary branch from main
- [ ] Update load balancer config (10% traffic to canary)
- [ ] Deploy canary version to staging first (verify deploy works)
- [ ] Deploy canary version to production (10% slice)
- [ ] Verify dashboards show canary vs stable metrics
- [ ] Page on-call to monitor Phase 2

**Communication:**
- [ ] Slack #releases: "ADR-2083 canary deployed (10% traffic)"
- [ ] Email: team + stakeholders

### Canary → GA (T+72h)

**Prerequisites:**
- [ ] Phase 2 gate PASS (error < 1%, latency < 1s, zero SEV1/2)
- [ ] 24h stable metrics

**Release Engineer Checklist:**
- [ ] Approve scale-up in change-review system
- [ ] Update load balancer: 50% traffic to canary
- [ ] Wait 2h, verify metrics
- [ ] Update load balancer: 100% traffic to canary
- [ ] Decommission stable version
- [ ] Run Phase 3 load test (24h continuous)

**Communication:**
- [ ] Slack #releases: "ADR-2083 rolled out to 100%"
- [ ] Update status page (if public-facing)

### GA → Monitor (T+120h)

**Prerequisites:**
- [ ] Phase 3 gate PASS (24h stable, uptime > 99%)

**Release Engineer Checklist:**
- [ ] Handoff to support team (7d monitor phase)
- [ ] Update on-call runbook
- [ ] Configure hourly metric exports (S3)
- [ ] Set up 7d review meeting (sync)

**Communication:**
- [ ] Calendar invite: 7d review (stakeholders)
- [ ] Slack: "ADR-2083 Phase 4 monitoring begins"

---

## Metrics & Reporting

### Real-Time Dashboard
**URL:** (to be filled in by DevOps)  
**Update Frequency:** 60s  
**Audience:** Release Eng, On-Call, Leadership

### Phase Completion Report Template

```markdown
# ADR-2083 Rollout: Phase [N] Report

**Phase:** [Staging / Canary / GA / Monitor]
**Duration:** [T+0h to T+Xh]
**Status:** ✅ PASS / ⚠️ HOLD / ❌ ROLLBACK

## Metrics

| Metric | Target | Actual | Status |
|---|---|---|---|
| Error Rate | < X% | Y% | ✅ / ⚠️ |
| p99 Latency | < Xms | Yms | ✅ / ⚠️ |
| Uptime | > X% | Y% | ✅ / ⚠️ |
| Incidents | ≤ X | Y | ✅ / ⚠️ |
| Audit Trail | 0 gaps | Z gaps | ✅ / ⚠️ |

## Incidents

| Severity | Count | Resolution |
|---|---|---|
| SEV1 | X | [action] |
| SEV2 | X | [action] |
| SEV3 | X | [action] |

## Next Step

- [ ] Proceed to Phase [N+1]
- [ ] Hold Phase [N] (reason: ...)
- [ ] Rollback (reason: ...)

## Approvers

- [ ] Release Engineer
- [ ] Platform Lead
- [ ] Product Lead
```

---

## Success Declaration (7-day gate)

**When all of these are true, declare ADR-2083 STABLE:**

- ✅ Uptime ≥ 99.5% (7 days)
- ✅ SEV1 incidents: 0
- ✅ SEV2 incidents: ≤ 1 (and resolved)
- ✅ Error rate < 0.8% (stable)
- ✅ Audit trail 100% complete (no gaps)
- ✅ Customer complaints ≤ 1 (non-critical)
- ✅ Team confidence: "Ready for standard ops"

**Declaration template:**
```markdown
# ADR-2083 Rollout COMPLETE ✅

**Project:** Non-Interactive Bridge Autonomy Design  
**Timeline:** [start date] — [end date]  
**Final Status:** STABLE

## Summary
- Staging: ✅ All tests passed
- Canary: ✅ Metrics stable (10% → 100%)
- GA: ✅ 48h stable rollout
- Monitor: ✅ 7d production monitoring complete

## Metrics (7-day final)
- Uptime: 99.X%
- Error Rate: 0.X%
- SEV1 Incidents: 0
- Audit Trail: 100% complete

## Next Actions
1. Update CLAUDE.md with ADR-2083 rules
2. Transition to standard monitoring
3. Plan quarterly review (ADR-0297 link)

**Approved By:** [Release Engineer], [Platform Lead]  
**Date:** [2026-10-04]
```

---

## Appendix: Automation Scripts

### Script: Phase 1 Staging Validator

**File:** `scripts/adr2083_staging_validator.sh`

```bash
#!/bin/bash
# Run Discord + Slack validation suites
# Usage: ./adr2083_staging_validator.sh

set -e

echo "=== ADR-2083 Staging Validation ==="

# Discord Loop Tests
echo "Running Discord loop validation (suite 1)..."
python3 tests/e2e/test_bridge_autonomy_e2e.py::TestLoopExecutionModes -v
python3 tests/e2e/test_bridge_autonomy_e2e.py::TestDiscordIntegration -v

# Slack Workflow Tests
echo "Running Slack workflow validation (suite 1)..."
python3 tests/e2e/test_bridge_autonomy_e2e.py::TestWorkflowBackgroundExecution -v
python3 tests/e2e/test_bridge_autonomy_e2e.py::TestSlackIntegration -v

# Audit Trail Verification
echo "Verifying audit trail integrity..."
python3 scripts/verify_audit_chain.py --tenant=_default

echo "=== Staging Validation Complete ==="
```

### Script: Phase 2 Canary Monitoring

**File:** `scripts/adr2083_canary_monitor.sh`

```bash
#!/bin/bash
# Monitor canary metrics (10% traffic)
# Usage: ./adr2083_canary_monitor.sh

set -e

echo "=== ADR-2083 Canary Monitoring ==="

# Fetch metrics
METRICS=$(curl -s http://monitoring.internal/api/metrics \
  --data-urlencode 'query=canary_error_rate')

ERROR_RATE=$(echo "$METRICS" | jq '.value')
LATENCY_P99=$(echo "$METRICS" | jq '.p99_latency')

echo "Error Rate: $ERROR_RATE%"
echo "P99 Latency: ${LATENCY_P99}ms"

# Gate decisions
if (( $(echo "$ERROR_RATE < 1.0" | bc -l) )); then
    echo "✅ Error rate gate PASS"
else
    echo "❌ Error rate gate FAIL"
    exit 1
fi

if (( $(echo "$LATENCY_P99 < 1000" | bc -l) )); then
    echo "✅ Latency gate PASS"
else
    echo "❌ Latency gate FAIL"
    exit 1
fi

echo "=== Canary Monitoring Complete ==="
```

---

## Sign-Off

**Playbook Owner:** CorvinOS Release Team  
**Last Updated:** 2026-09-27  
**Status:** 🟡 **ACTIVE** (Phase 1 starting)

This playbook is **executable** and **versioned**. Any changes must go through ADR-XXXX (Rollout Process Improvements).
