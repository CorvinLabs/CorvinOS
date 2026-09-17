# Model Selection Tier 3 Deployment Guide

**Document Version:** 1.0  
**Target Release:** CorvinOS v0.11.0+  
**Deployment Type:** Feature Flag Rollout (Low Risk)  
**Estimated Duration:** 2-4 hours

---

## SECTION 1: Pre-Deployment Checklist

### 1.1 Code Review & Merge

- [ ] All 4 ADRs accepted: ADR-0165, ADR-0641, ADR-0642, ADR-0644
- [ ] All tests passing: `pytest tests/skills/ tests/e2e/ tests/compliance/ -v`
- [ ] Coverage >= 85%: `coverage report --min-coverage=85`
- [ ] Code review signed off (2+ reviewers)
- [ ] No TODOs/FIXMEs: `grep -r "TODO\|FIXME" core/skills/os_skills/ core/learning/ core/console/` (should be empty)
- [ ] Commit hash documented for rollback

### 1.2 Performance Validation

- [ ] P99 latency <50ms: ✅ [date: __________]
- [ ] Memory footprint <10MB: ✅ [date: __________]
- [ ] Audit write latency <100ms: ✅ [date: __________]
- [ ] No memory leaks (24h sustained test): ✅ [date: __________]

### 1.3 Security & Compliance

- [ ] Audit chain verified (hash integrity): ✅
- [ ] PII scrubbing validated (_assert_safe): ✅
- [ ] Tenant isolation tested: ✅
- [ ] GDPR Art. 5/6/30/32 compliance sign-off: ✅
- [ ] No cross-tenant leakage: ✅
- [ ] Boot tripwire passes: ✅

### 1.4 Documentation & Training

- [ ] Operator runbook complete: MODEL_SELECTION_OPERATOR_RUNBOOK.md
- [ ] Console help text updated: ✅
- [ ] Rollback procedure documented: ✅
- [ ] Support contact list updated: ✅
- [ ] On-call team briefed: ✅

### 1.5 Staging Environment Test

- [ ] Deploy to staging: ✅
- [ ] Run smoke tests: ✅
- [ ] Monitor for 24h: ✅
- [ ] Verify learning loop converges: ✅
- [ ] Check cost savings: 30-40%
- [ ] Zero regressions: ✅

---

## SECTION 2: Deployment Steps (2-4 Hours)

### 2.1 Pre-Production Verification (30 minutes)

```bash
# Step 1: Verify current production state
./scripts/pre_deployment_check.sh
# Expected output:
# ✅ All services running
# ✅ Audit chain healthy (12800+ events)
# ✅ No outstanding incidents
# ✅ Database connectivity OK

# Step 2: Create deployment snapshot (for rollback)
corvin system snapshot --name=pre_tier3_deployment
# Output: snapshot_id: snap_2026091700001
# Store this ID: __________________
```

### 2.2 Feature Flag Deploy (30 minutes)

```bash
# Step 1: Deploy code to production (database migrations included)
git checkout main
git pull origin main  # Ensure latest merged code
git checkout v0.11.0-rc1  # Or current release branch

# Deploy
docker pull corvinOS:v0.11.0-rc1
docker-compose down
docker-compose up -d
# Wait for services to start (check logs)
docker-compose logs -f corvin-console

# Verify: http://localhost:8765/health (should return 200 OK)
# Check: curl http://localhost:8765/v1/console/status

# Step 2: Enable feature flag (MODEL_SELECTION_v2_ENABLED = false initially)
# Set to false to stage deployment without activating
corvin config set spec.model_selection_v2_enabled false

# Verify disabled state
curl http://localhost:8765/v1/console/model-selection/status
# Expected: {"status": "disabled", "fallback": "opus"}
```

### 2.3 Canary Rollout (1-2 hours)

```bash
# Step 1: Enable for 5% of traffic (canary phase)
# Syntax: spec.model_selection_v2_enabled = 0.05  (5% canary)
corvin config set spec.model_selection_v2_enabled 0.05

# Step 2: Monitor canary metrics (real-time)
# Open new terminal window:
watch -n 5 'curl -s http://localhost:8765/v1/console/model-selection/status | jq'

# Expected output (every 5 seconds):
# {
#   "status": "active",
#   "canary_percentage": 0.05,
#   "haiku_usage_rate": 0.05,
#   "error_rate": 0.0,
#   "latency_p99_ms": 42,
#   "task_count_last_hour": 150
# }

# Step 3: Monitor for 10 minutes (watch for errors)
# ✅ No errors: latency_p99 <50ms, error_rate ~0%
# ❌ Errors: See Section 4: Incident Response

# Step 4: Increase to 25% (if canary clean)
corvin config set spec.model_selection_v2_enabled 0.25
# Monitor for 10 more minutes

# Step 5: Full rollout (100%)
corvin config set spec.model_selection_v2_enabled true
# Monitor for 30 minutes

# Verify: Dashboard should show real data
# Navigate to: http://localhost:8765/console/#/model-selection-analytics
```

### 2.4 Post-Deployment Verification (30 minutes)

```bash
# Step 1: Verify audit chain integrity
corvin audit verify-chain
# Expected: ✅ Chain height XXX, all hashes verified

# Step 2: Verify learning loop active
corvin learning query --metric=haiku_success_rate
# Expected: >0.85 (or recent data)

# Step 3: Verify dashboard data
curl http://localhost:8765/v1/console/model-selection/analytics
# Expected: Model distribution, success rates (real data)

# Step 4: Run end-to-end test
pytest tests/e2e/test_model_selection_tier3_wiring_e2e.py -v
# Expected: All tests pass

# Step 5: Check cost savings
corvin analytics model-selection --period=1h
# Expected: Cost savings visible (should increase as Haiku is used)

# Step 6: Operator sign-off
# Operator confirms via: http://localhost:8765/console/#/model-selection-overrides
# Can override, audit is working, dashboard visible
```

---

## SECTION 3: Monitoring & Validation (First 24-48 Hours)

### 3.1 Real-Time Monitoring (Every 15 minutes for 2 hours)

```bash
# Automated monitoring script
./scripts/monitor_tier3_deployment.sh  # Runs for 2 hours

# Manual checks:
while true; do
  echo "=== $(date) ==="
  curl -s http://localhost:8765/v1/console/model-selection/status | jq '.{status, haiku_usage_rate, error_rate, latency_p99_ms}'
  sleep 15
done

# Expected:
# {
#   "status": "active",
#   "haiku_usage_rate": 0.30,    ← Increasing
#   "error_rate": 0.0,            ← 0% errors
#   "latency_p99_ms": 42          ← <50ms
# }
```

### 3.2 Learning Loop Verification (Every 1-4 hours)

```bash
# Check convergence
corvin learning query --metric=haiku_success_rate --limit=4

# Expected (upward trend):
# 2026-09-17T12:00:00Z | 0.80
# 2026-09-17T16:00:00Z | 0.82
# 2026-09-17T20:00:00Z | 0.84
# 2026-09-17T00:00:00Z | 0.85  ← Converging

# If stalled or declining → Investigate (see Section 4)
```

### 3.3 Cost Tracking (Every 4 hours)

```bash
# Monitor cost savings
corvin analytics model-selection --period=4h

# Expected trend (increasing):
# Hour 0-4:   Cost savings 10% (learning phase)
# Hour 4-8:   Cost savings 25% (Haiku usage increasing)
# Hour 8-24:  Cost savings 35-40% (convergence)
# Day 2-7:    Cost savings 40-45% (stable state)

# If <10% after 4 hours → Check if Haiku is being selected
```

### 3.4 Audit Trail Validation (Every 8 hours)

```bash
# Verify audit events are being logged
corvin audit trace --event=skill_executed --skill=model_selector --limit=10

# Expected: 10 recent events with:
# - event_type: "skill_executed"
# - skill_id: "model_selector"
# - model: "claude-haiku-4-5" or "claude-sonnet-5" or "claude-opus-4"
# - confidence: 0.75-0.99
# - No PII

# Verify hash-chain integrity
corvin audit verify-chain --quick
# Expected: ✅ Chain healthy
```

### 3.5 Incident Response Matrix

| Metric | Healthy | Warning | Critical | Action |
|--------|---------|---------|----------|--------|
| Error Rate | 0% | 0-1% | >1% | Disable (Section 4) |
| P99 Latency | <50ms | 50-75ms | >75ms | Investigate cache |
| Haiku Usage | 25-35% | 15-25% | <15% | Check heuristics |
| Haiku Success | >85% | 75-85% | <75% | Lower threshold |
| Cost Savings | 35-45% | 25-35% | <25% | Check overfitting |

---

## SECTION 4: Incident Response

### 4.1 If Error Rate Spikes (>1%)

```bash
# Step 1: Immediate action
corvin config set spec.model_selection_v2_enabled 0.5  # Reduce to 50% canary
# OR
corvin config set spec.model_selection_v2_enabled false  # Full disable

# Step 2: Investigate
corvin audit trace --event=skill_executed --skill=model_selector --error=true --limit=20
# Look for error patterns in output

# Step 3: Fix and redeploy
# (See Section 4 of Operator Runbook for detailed troubleshooting)

# Step 4: Gradual re-enable
corvin config set spec.model_selection_v2_enabled 0.05  # 5% canary
# Monitor for 30 minutes
corvin config set spec.model_selection_v2_enabled true   # Full rollout
```

### 4.2 If Latency Exceeds 75ms P99

```bash
# Step 1: Reduce computational load
corvin config set spec.model_selection.use_decomposition_hints false

# Step 2: Clear cache
corvin learning cache clear

# Step 3: Check if specific task types are slow
curl http://localhost:8765/v1/console/model-selection/latency-by-task-type
# Disable for slow types:
corvin config set spec.model_selection.disabled_for_task_types '["orchestration"]'

# Step 4: Monitor
watch -n 5 'curl -s http://localhost:8765/v1/console/model-selection/status | jq .latency_p99_ms'
```

### 4.3 If Haiku Is Not Being Selected

```bash
# Step 1: Check heuristic thresholds
corvin learning query --metric=haiku_success_rate

# Step 2: If <0.85, manually lower threshold
corvin config set spec.model_selection.haiku_success_threshold 0.80

# Step 3: Verify change took effect
corvin audit trace --event=skill_executed --skill=model_selector --limit=20
# Check if any haiku appears in output

# Step 4: If still 0%, restart service
docker-compose restart corvin-console
docker-compose logs corvin-console | grep -i model_selection
```

### 4.4 Full Rollback (If Critical)

```bash
# IMMEDIATE: Disable
corvin config set spec.model_selection_v2_enabled false

# Verify rollback
curl http://localhost:8765/v1/console/model-selection/status
# Expected: {"status": "disabled", "fallback": "opus"}

# Restore from snapshot (if needed)
corvin system restore --snapshot-id=snap_2026091700001

# Notify team
corvin support create --issue-type=incident --priority=p1 --message="Model Selection v2 rolled back due to [reason]"

# Post-incident review
# Schedule: 24-48 hours after incident
# Attendees: Architecture, LDD, Compliance, SRE
# Objective: Root cause analysis, prevent recurrence
```

---

## SECTION 5: Rollback Procedure (Detailed)

### 5.1 Non-Disruptive Rollback (Preferred)

```bash
# Step 1: Disable via config (no restart)
corvin config set spec.model_selection_v2_enabled false

# Step 2: Wait for in-flight tasks to complete (60 seconds)
sleep 60

# Step 3: Verify disabled
curl http://localhost:8765/v1/console/model-selection/status

# Step 4: Check logs for errors
docker-compose logs corvin-console | tail -100 | grep -i error

# Result: All new tasks use Opus (safe default)
# Previous overrides remain in audit trail (immutable)
# No data loss, no compliance violations
```

### 5.2 Code Rollback (If Config Not Sufficient)

```bash
# Step 1: Revert code
git checkout [previous_commit_hash]

# Step 2: Rebuild & redeploy
docker build -t corvinOS:v0.10.5 .
docker-compose down
docker-compose up -d

# Step 3: Verify
curl http://localhost:8765/v1/console/status

# Step 4: Validate audit chain still intact
corvin audit verify-chain
```

### 5.3 Data Recovery (If Audit Chain Corrupted)

```bash
# ⚠️ EMERGENCY ONLY — Contact compliance team first

# Restore from snapshot
corvin system restore --snapshot-id=snap_2026091700001

# Verify chain integrity
corvin audit verify-chain --verbose

# If successful:
#   ✅ Chain restored to pre-incident state
#   ✅ All events intact and verified
#   ✅ Hash-chain links validated

# If failed:
#   ❌ Escalate to compliance + architect immediately
#   ❌ This is a GDPR-critical failure (Art. 30/32)
```

---

## SECTION 6: Production Handoff Checklist

### 6.1 Sign-Off (Checklist)

- [ ] All deployment steps completed: ✅
- [ ] All tests passing: ✅
- [ ] All monitoring alerts configured: ✅
- [ ] On-call team trained: ✅ (signature: ____________)
- [ ] Operator runbook distributed: ✅
- [ ] Support contact list updated: ✅
- [ ] Incident response procedure tested: ✅
- [ ] Rollback procedure tested: ✅
- [ ] GDPR/compliance sign-off: ✅

### 6.2 Documentation Hand-Off

- [ ] MODEL_SELECTION_OPERATOR_RUNBOOK.md (Section 1: Quick Start)
- [ ] ADR-0165, ADR-0641, ADR-0642, ADR-0644 (for reference)
- [ ] Monitoring dashboard URL: http://localhost:8765/console/#/model-selection-analytics
- [ ] Escalation contacts: [on-call schedule link]

### 6.3 Team Signoff

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Deployment Lead | __________ | __________ | __________ |
| SRE Lead | __________ | __________ | __________ |
| Compliance Lead | __________ | __________ | __________ |
| Architecture Lead | __________ | __________ | __________ |

---

## SECTION 7: Post-Deployment (Days 1-7)

### Day 1 (Operational Monitoring)

```bash
# Morning (24h post-deploy)
corvin model-selection report --period=24h
# Expected:
# - Haiku usage: 28% (within 25-35% target)
# - Haiku success: 87% (within 85%+ target)
# - Cost savings: 38% (within 35-45% target)
# - Zero errors, zero audit issues

# Afternoon (check learning convergence)
corvin learning query --metric=haiku_success_rate
# Should show upward trend

# Evening (check for incidents)
corvin audit trace --event=skill_executed --error=true --since=24h
# Should be empty (no errors)
```

### Days 2-7 (Weekly Validation)

```bash
# Daily monitoring (5 min)
./scripts/monitor_tier3_daily.sh

# Weekly report (30 min)
corvin model-selection report --period=week

# Expected milestone:
# - End of Week 1: Haiku usage 30-35%, cost savings 40%+
# - Learning loop converged (confidence trend flat/positive)
# - Zero regressions in other systems
# - Operator overrides minimal (<2% of tasks)
```

---

## SECTION 8: Success Criteria (Sign-Off)

### Phase 1: Deployment (Day 0)
- ✅ Code deployed to production
- ✅ Feature flag enabled
- ✅ Monitoring active
- ✅ Audit chain healthy

### Phase 2: Operational (Day 1-7)
- ✅ Haiku selected for 25-35% of tasks
- ✅ Haiku success rate >85%
- ✅ Cost savings >35%
- ✅ P99 latency <50ms
- ✅ Zero compliance issues

### Phase 3: Stabilization (Week 2+)
- ✅ Learning loop converged (confidence stable)
- ✅ Cost savings 40%+ (stable)
- ✅ Operator interventions rare (<1%)
- ✅ Model Selection v2 becomes SOP (invisible)

---

## References

- **ADR-0165:** Model Selection Routing Injection
- **ADR-0641:** Model Selector Skill Design
- **ADR-0642:** Skills Registry Hardening
- **ADR-0644:** Learning Routes Real Data Audit-First
- **Operator Runbook:** MODEL_SELECTION_OPERATOR_RUNBOOK.md
- **Compliance Baseline:** docs/compliance-baseline.md

---

**Document Status:** ✅ READY FOR PRODUCTION  
**Release Target:** CorvinOS v0.11.0  
**Deployment Date:** [TBD]  
**Deployment Lead:** [Name]  
**Contact:** #corvinOS-model-selection (Slack)
