# Model Selection Operator Runbook — Tier 3 Variant D

**Document Version:** 1.0  
**Last Updated:** 2026-09-17  
**Maintainer:** CorvinOS Compliance Team  
**Audience:** Operators, Site Reliability Engineers (SREs)

---

## SECTION 1: Quick Start (5 Minutes)

### 1.1 Enable Model Selection v2

```bash
# Via console: http://localhost:8765/console
# Settings → Model Selection → Enable v2 (toggle)
# Or via CLI:
corvin config set spec.model_selection_v2_enabled true

# Verify it's enabled
curl http://localhost:8765/v1/console/model-selection/status
# Expected: {"status": "active", "haiku_usage_rate": 0.28, ...}
```

### 1.2 Check Dashboard

```bash
# Navigate to: http://localhost:8765/console/#/model-selection-analytics
# Should show:
# - Last 7 days: Haiku 25-35%, Sonnet 45-55%, Opus 10-20%
# - Success rates: Haiku 85%, Sonnet 94%, Opus 97%
# - Cost savings: ~40% vs "all Opus" baseline
```

### 1.3 Override a Single Task

```bash
# Via console: Model Selection Overrides panel
# Click [Force Haiku] or [Force Sonnet]
# Enter reason: "Testing Haiku quality on this specific task"
# Click "Apply Override"
# Result: Task routed to chosen model, audit event created

# Via CLI:
corvin model-selection override --task-id=task_abc123 --model=haiku --reason="Test override"
```

---

## SECTION 2: Daily Operations

### 2.1 Monitor Learning Loop Convergence

```bash
# Check if heuristics are improving
corvin learning query --metric=model_selection_haiku_success_rate --limit=7

# Expected output (upward trend):
# 2026-09-10: 0.82
# 2026-09-11: 0.83
# 2026-09-12: 0.85
# 2026-09-13: 0.86  ← Converging upward (good)
# ...

# If trend is flat or declining, investigate (see Section 4: Troubleshooting)
```

### 2.2 Check Daily Cost Report

```bash
# View cost savings vs baseline
corvin analytics model-selection --period=24h

# Expected: Cost Savings: 38-42% vs "all Opus"
# If <30%: Haiku usage may be too low, check Section 4

# Breakdown:
# - Haiku tasks: 28 (avg $0.08 each, total $2.24)
# - Sonnet tasks: 48 (avg $0.25 each, total $12.00)
# - Opus tasks: 24 (avg $0.60 each, total $14.40)
# - Baseline (all Opus): $28.80
# - Actual: $28.64
# - Savings: 0.56% (modest, expected for first week)
```

### 2.3 Audit Trail Queries

```bash
# Show all model selection events in past 24h
corvin audit trace --event=skill_executed --skill=model_selector --since=24h

# Expected output (one line per task):
# evt_001 | 2026-09-17T10:00:00Z | model_selector | task_abc | haiku | confidence=0.89
# evt_002 | 2026-09-17T10:05:00Z | model_selector | task_def | sonnet | confidence=0.92
# ...

# Verify no PII in output
corvin audit trace --event=skill_executed --skill=model_selector | grep -i "user\|email\|ssn"
# Expected: (empty — no PII should appear)
```

### 2.4 Weekly Review

```bash
# Generate weekly report
corvin model-selection report --period=week --output=pdf

# Check:
# ✅ Haiku usage: 25-35% of tasks
# ✅ Haiku success: >85%
# ✅ Cost savings: >40% vs baseline
# ✅ No regressions: P99 latency <50ms
# ✅ Audit chain: Clean (no hash mismatches)

# If any check fails, escalate to Section 4: Troubleshooting
```

---

## SECTION 3: Per-Task Overrides (Operator Control)

### 3.1 When to Override

Override automatic model selection when:

| Scenario | Override To | Reason |
|----------|---|---------|
| Task failed with Haiku | Sonnet | Quality issue, higher confidence needed |
| Operator prefers consistency | Opus | Cost not a concern for this task |
| Testing Haiku limits | Haiku | Evaluate if Haiku works for this task type |
| Emergency (high priority) | Opus | Safety first, accept higher cost |
| Sensitive task (PII) | Opus | Better quality on safety-critical tasks |

### 3.2 Recording an Override

**Via Console:**

```
1. Navigate to: http://localhost:8765/console/#/model-selection-overrides
2. Select task from "Recent Tasks" list
3. Click [Force Haiku/Sonnet/Opus]
4. Enter reason (required): "Operator noticed poor quality with Haiku"
5. Click "Apply Override"
6. ✅ Audit event created: task_xyz | operator=you | reason="..." | timestamp=...
```

**Via CLI:**

```bash
corvin model-selection override \
  --task-id=task_abc123 \
  --model=sonnet \
  --reason="Haiku output quality too low for this code review"
```

### 3.3 Verify Override Was Recorded

```bash
# Check audit trail
corvin audit trace --event=skill_config_updated --task=task_abc123

# Expected output:
# event_type: skill_config_updated
# skill_id: model_selector
# operator: [your name]
# reason: "Haiku output quality too low for this code review"
# timestamp: 2026-09-17T14:30:00Z
# prev_model: claude-haiku-4-5
# new_model: claude-sonnet-5
# cost_delta: +$0.17

# Cost delta should appear in dashboard within 5 minutes
```

### 3.4 Reset Override (Use Default)

```bash
# Via console: Click [Reset to Default] button
# Or CLI:
corvin model-selection reset --task-id=task_abc123

# Verifies: Task will use automatic selection next time (if similar)
```

---

## SECTION 4: Troubleshooting

### Problem 4.1: Haiku Success Rate Too Low (<80%)

**Symptoms:**
- Dashboard shows Haiku success: 75%
- Learning loop convergence stalled
- Cost savings below expected

**Diagnosis:**
```bash
# Check which task types have low Haiku success
corvin learning query --metric=haiku_success_by_task_type

# Check recent Haiku failures
corvin audit trace --skill=model_selector --model=haiku --feedback=failure --limit=10

# Look for patterns in failures
```

**Resolution:**

1. **Adjust Haiku success threshold:**
   ```bash
   # Current threshold: 85% (use Haiku if success_rate > 0.85)
   # Increase to 90% to be more conservative:
   corvin config set spec.model_selection.haiku_success_threshold 0.90
   
   # Decrease to 80% to be more aggressive:
   corvin config set spec.model_selection.haiku_success_threshold 0.80
   ```

2. **Disable Haiku for specific task types:**
   ```bash
   corvin config set spec.model_selection.haiku_disabled_types ["orchestration", "system_design"]
   ```

3. **Re-run learning optimizer with fresh data:**
   ```bash
   corvin model-selection reset-learning --keep-last-n-samples=100
   ```

### Problem 4.2: Haiku Not Being Used (0% Selection)

**Symptoms:**
- Haiku selected for 0% of tasks
- Learning loop not activating
- No Haiku success/failure events

**Diagnosis:**
```bash
# Check if learning store has data
corvin learning query --metric=haiku_success_rate

# Check if model selector is running
corvin audit trace --event=skill_executed --skill=model_selector --limit=1

# Check classification results
curl http://localhost:8765/v1/console/model-selection/latest?limit=5
```

**Resolution:**

1. **Verify model selector is enabled:**
   ```bash
   corvin config get spec.model_selection_v2_enabled
   # Should return: true
   ```

2. **Check heuristic thresholds:**
   ```bash
   # Current Haiku success rate (should be >85%)
   corvin learning query --metric=haiku_success_rate_video_production
   
   # If <50% (untested task type), Haiku won't be recommended
   # Manually set a baseline:
   corvin learning set --task-type=video_production --haiku-success=0.85 --sample-count=10
   ```

3. **Restart model selector service:**
   ```bash
   systemctl restart corvin-model-selector  # If running as service
   # Or restart console server
   systemctl restart corvin-console
   ```

### Problem 4.3: Audit Chain Integrity Failure

**Symptoms:**
- Boot tripwire fails: "Audit chain verification failed"
- Server won't start
- Hash mismatch error

**Diagnosis:**
```bash
# Check audit chain health
corvin audit verify-chain --verbose

# Output should show:
# ✅ Chain height: 12847 events
# ✅ All hashes verified: 12846/12846 links
# ❌ If hashes mismatch, investigate further
```

**Resolution:**

1. **Rollback to last known-good backup:**
   ```bash
   # Restore from snapshot (before corruption occurred)
   corvin audit restore --snapshot=2026-09-16T00:00:00Z
   ```

2. **Contact compliance team immediately:**
   - Audit chain is GDPR-critical (Art. 30/32)
   - Tampering is a compliance violation
   - Escalate to security team

### Problem 4.4: High Latency (>50ms P99)

**Symptoms:**
- Dashboard reports model selection latency >50ms P99
- Requests slow down
- Learning loop queries timeout

**Diagnosis:**
```bash
# Measure current latency
corvin perf measure --metric=model_selection_latency --samples=100

# Check memory usage
top -p $(pgrep -f "corvin")  # Look for RES column

# Check disk I/O
iostat -x 1 5  # Check model_selection cache file
```

**Resolution:**

1. **Clear learning store cache:**
   ```bash
   corvin learning cache clear  # Rebuilds on next query
   ```

2. **Disable decomposition hints (use fast path):**
   ```bash
   corvin config set spec.model_selection.use_decomposition_hints false
   ```

3. **Reduce learning store sample retention:**
   ```bash
   corvin config set spec.learning.retention_days 30  # From 90
   ```

### Problem 4.5: Cost Savings Below Expected

**Symptoms:**
- Dashboard shows cost savings: 15%
- Expected: 40%

**Diagnosis:**
```bash
# Check Haiku usage rate
corvin analytics model-selection --metric=usage_rate_by_model

# Check if Haiku tasks are actually cheaper
corvin analytics model-selection --metric=cost_per_model

# Check if there's a mix issue (e.g., many expensive Opus fallbacks)
```

**Resolution:**

1. **Increase Haiku success threshold (more conservative):**
   ```bash
   corvin config set spec.model_selection.haiku_success_threshold 0.80
   ```

2. **Add manual Haiku override for high-volume task types:**
   ```bash
   corvin config set spec.model_selection.override_for_task_type \
     '{"documentation": "haiku", "summarization": "haiku"}'
   ```

3. **Verify cost estimates are accurate:**
   ```bash
   # Recalculate from actual tokens (not estimates)
   corvin analytics recalculate-costs --include-actual-tokens
   ```

---

## SECTION 5: Rollback Procedure (If Critical Issues)

### 5.1 Disable Model Selection v2 (Fallback to Opus)

```bash
# Immediate disable (no restart needed)
corvin config set spec.model_selection_v2_enabled false

# Verify disabled
curl http://localhost:8765/v1/console/model-selection/status
# Expected: {"status": "disabled", "fallback_model": "opus"}

# All new tasks route to Opus (safe default)
# Previous overrides remain in audit trail (immutable)
```

### 5.2 Restore to Previous Learning State

```bash
# If learning loop converged incorrectly, restore backup
corvin learning restore --snapshot=2026-09-15T00:00:00Z

# Verify restored
corvin learning query --metric=haiku_success_rate --limit=1
# Should match previous state
```

### 5.3 Purge Override History (If Needed)

```bash
# ⚠️ CAUTION: This affects GDPR audit trail
# Contact compliance before running

corvin audit delete --event-type=skill_config_updated --keep-after=2026-09-01
# This keeps all model selection overrides but removes technical debug events
```

---

## SECTION 6: Operator Checklist

### Daily (5 minutes)

- [ ] Check model selection dashboard: cost savings visible?
- [ ] Verify learning loop active: haiku_success_rate increasing?
- [ ] Audit trail clean: `corvin audit verify-chain` passes?

### Weekly (30 minutes)

- [ ] Generate weekly report: `corvin model-selection report --period=week`
- [ ] Review override history: any patterns?
- [ ] Check performance: P99 latency <50ms?
- [ ] Verify compliance: no PII in audit trail?

### Monthly (1 hour)

- [ ] Review all ADRs (ADR-0165, 0641, 0642, 0644) — still current?
- [ ] Update thresholds if needed: haiku_success_threshold, cost limits
- [ ] Test rollback procedure (without actually rolling back)
- [ ] Update this runbook if procedures changed

### On Incident

- [ ] Check Section 4: Troubleshooting
- [ ] Consult ADRs for design constraints
- [ ] Gather audit trail data for root cause
- [ ] Escalate to architecture team if root cause is structural

---

## SECTION 7: Escalation Matrix

| Issue | Severity | Escalate To | Contact | Response Time |
|-------|----------|---|---|---|
| Model selection disabled | P1 (Critical) | Architect + SRE | On-call | <5 min |
| Audit chain corrupted | P1 (Critical) | Compliance + Architect | Legal team | <1 min |
| Haiku success <70% | P2 (High) | LDD Agent | Architecture | <1 hour |
| Latency >50ms P99 | P2 (High) | Performance team | SRE | <1 hour |
| Cost savings <20% | P3 (Medium) | CorvinOS team | Team lead | <1 day |
| Dashboard not updating | P3 (Medium) | Frontend team | Console lead | <4 hours |

---

## SECTION 8: Support Contact

**Model Selection Lead:** [name]  
**Email:** [email]  
**Slack:** #corvinOS-model-selection  
**On-Call Escalation:** [link to on-call schedule]

For urgent issues:
```bash
corvin support create --issue-type=model-selection --priority=critical
```

---

## SECTION 9: References

- **ADR-0165:** Model Selection Routing Injection
- **ADR-0641:** Model Selector Skill Design
- **ADR-0642:** Skills Registry Hardening
- **ADR-0644:** Learning Routes Real Data Audit-First
- **ADR-0232:** Boot Tripwire (Audit Compliance)
- **ADR-0233:** Plugin Security (Audit Backend)
- **ADR-0314:** Learning Infrastructure
- **COMPLIANCE:** docs/compliance-baseline.md
- **GDPR:** Art. 5 (minimization), 6 (legal basis), 30 (record keeping), 32 (security)
- **EU AI Act:** Art. 50 (transparency, disclosure)

---

**Document Status:** ✅ PRODUCTION READY  
**Last Review:** 2026-09-17  
**Next Review:** 2026-10-17
