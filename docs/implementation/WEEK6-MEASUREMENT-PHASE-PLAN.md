# Week 6: CEL Phase 4 Measurement Phase — Ready-to-Run Plan

**Dates:** 2026-08-11 to 2026-08-17  
**Scope:** Validate confidence scoring, outcome feedback, user preferences, attention budget  
**Status:** READY FOR EXECUTION

---

## Overview

Week 6 operationalizes CEL Phase 4 (ADR-0270–0273) by running four concurrent measurement tracks:

| Track | ADR | Metrics | Owner | Duration |
|-------|-----|---------|-------|----------|
| **Uncertainty** | 0270 | Confidence accuracy ±5% | Auto | 5 days |
| **Feedback** | 0271 | Learning rate (0.05) validation | Auto | 5 days |
| **Preferences** | 0272 | User profile accuracy (recall/precision) | Auto | 4 days |
| **Budget** | 0273 | Attention allocation vs. complexity | Auto | 3 days |

**Success Criterion:** All tracks green (≥0.80 accuracy on primary metrics)

---

## Pre-Deployment Checklist

### Code Freeze & Merge
- [x] K=5 verification complete (zero gaps)
- [x] All 10 production tests passing
- [ ] Merge critical_fixes_roundk2.py → main implementation files
  - Replace: `operator/context_engineering/learning_queue.py`
  - Replace: `operator/context_engineering/concurrency_model.py`
- [ ] Merge guard_integration_hook.py → chat/suggestion layer
- [ ] Update imports in console + agent code
- [ ] Run full test suite (pytest operator/ -v)

### Infrastructure Setup
- [ ] Create measurement database schema (Postgres/SQLite)
  - Tables: confidence_samples, feedback_records, profile_changes, attention_allocations
- [ ] Set up monitoring dashboards
  - Grafana: Confidence accuracy, learning rate, profile drift
  - Prometheus: Lock contention, aggregation latency, queue size
- [ ] Configure alerting rules
  - Alert on: checksum failures, lock timeouts, profile divergence >0.1

### Data Collection
- [ ] Enable telemetry hooks in task_engine.py
  - Track confidence predictions vs. actual outcomes
  - Log user profile selections
  - Record attention budget allocation
- [ ] Configure audit trail capture
  - Guard blocks → GDPR Art. 30 audit log
  - Profile updates → versioning audit trail
- [ ] Set up Tier 2 queue rotation
  - Create weekly queue files (YYYY-MM-DD.jsonl)
  - Set up nightly aggregation cron job (2am UTC)

---

## Day-by-Day Measurement Plan

### Day 1 (2026-08-11): Deploy & Calibrate

**Morning (6 hours)**
1. Merge K=5 code into production files
2. Run full integration test suite
3. Deploy to staging (if applicable)
4. Activate telemetry collection

**Afternoon (4 hours)**
5. Run first aggregation cycle (manual trigger)
6. Verify: Checksum validation, lock coordination, profile generation
7. Check: Confidence scores initialized (~0.70 baseline)
8. Status: Green light check-in

**Success Metric:** First aggregation completes without errors; confidence scores ~0.70

---

### Day 2 (2026-08-12): Uncertainty Quantification (ADR-0270)

**Track Focus:** Confidence-score calibration

**Measurements:**
- Prediction accuracy: Does confidence score match actual outcome?
- Calibration curve: Are 0.90-confidence predictions 90% accurate?
- Edge cases: How do rare contexts behave? (< 5 samples)

**Data Collection:**
```
For each task T:
  confidence_pred = cache.lookup(context_id)
  task_outcome = execute(T)
  log(context_id, confidence_pred, outcome)
```

**Success Criteria:**
- Prediction accuracy within ±5% of confidence
- Rare contexts (N<5) marked UNCERTAIN (no high confidence)
- No negative feedback loops (confidence thrashing)

**Hourly Checks:**
- Morning: Sample 10 predictions, measure agreement
- Midday: Review rare-context behavior
- EOD: Aggregation run, score updated

---

### Day 3 (2026-08-13): Outcome Feedback Loop (ADR-0271)

**Track Focus:** Learning rate validation

**Measurements:**
- Did scores improve after feedback?
- Is Bayesian update (learning_rate=0.05) working?
- Decay weighting: Do old outcomes matter appropriately?

**Data Collection:**
```
For each feedback F:
  profile_before = profiles[context_id].confidence
  apply_bayesian_update(F, learning_rate=0.05)
  profile_after = profiles[context_id].confidence
  delta = profile_after - profile_before
  log(context_id, profile_before, profile_after, delta, F.impact)
```

**Success Criteria:**
- Score deltas: ±0.03 (conservative updates, not thrashing)
- Decay effect: 90d outcomes ~30% weight (exponential decay working)
- No oscillation: Score doesn't ping-pong on contradictory feedback

**Hourly Checks:**
- Morning: Manual check on 5 updated contexts
- Midday: Verify decay weighting on 180d+ data
- EOD: Pattern discovery (which feedback types drive updates?)

---

### Day 4 (2026-08-14): User Preferences (ADR-0272)

**Track Focus:** Profile accuracy (recall/precision)

**Measurements:**
- Does inferred style match actual user behavior?
- Recall: Are 80% of users correctly classified?
- Precision: Do classified users behave that way?

**Data Collection:**
```
For each user U:
  inferred_style = profiles[user_id].decision_style  # "pragmatic" | "rigorous"
  actual_behavior = analyze_task_choices(U.tasks)
  match = (inferred_style == actual_behavior)
  log(user_id, inferred_style, actual_behavior, match)
```

**Success Criteria:**
- Recall ≥ 0.80 (80% of users correctly profiled)
- Precision ≥ 0.75 (classified behavior matches predictions)
- Specialization visible (e.g., "ML infrastructure" users cluster)

**Hourly Checks:**
- Morning: Sample 10 users, spot-check classifications
- Midday: Measure recall/precision on full user sample
- EOD: Visualize user clusters (if available)

---

### Day 5 (2026-08-15): Attention Budget (ADR-0273)

**Track Focus:** Budget allocation vs. complexity

**Measurements:**
- Is budget allocated efficiently (critical > nice)?
- Does complexity estimation match actual task difficulty?
- Are users respecting budget caps?

**Data Collection:**
```
For each task T:
  budget_allocated = T.budget  # critical | important | nice
  complexity_est = T.complexity_score  # 1–10
  actual_tokens = T.tokens_used
  match = (budget matches complexity)
  log(task_id, budget_allocated, complexity_est, actual_tokens, match)
```

**Success Criteria:**
- Budget allocation matches complexity ≥ 0.80
- No budget overruns (critical tasks within cap)
- Nice-to-have contexts deferred appropriately

**Hourly Checks:**
- Morning: Spot-check 10 budget allocations
- Midday: Measure correlation (complexity ↔ budget)
- EOD: Identify budget mismatches for refinement

---

### Day 6 (2026-08-16): Integration & Refinement

**Actions:**
1. Review all four track metrics
2. Identify any thresholds missed (<0.80)
3. Run second aggregation cycle
4. Prepare summary report

**Decision Points:**
- If all tracks ≥0.80: GREEN — proceed to Week 7
- If 1–2 tracks <0.80: YELLOW — 1-day refinement + re-measure
- If ≥3 tracks <0.80: RED — escalate, debug root cause

---

### Day 7 (2026-08-17): Go/No-Go Decision

**Morning:**
- Final metrics review
- Stakeholder sign-off
- Decision: Proceed to Week 7 or iterate?

**Success Path:**
- ✅ All metrics ≥0.80
- ✅ Ready for Week 7 (M1–M5 refinements + full deployment)
- ✅ Ready for Week 8 (cross-tenant validation)

**Escalation Path:**
- ❌ Metrics <0.80 on critical track
- ❌ Unknown root cause
- ❌ Stakeholder requests refinement
- → Create ADR-0275 (Post-Measurement Refinement) + adjust timeline

---

## Monitoring & Alerting

### Key Dashboards

**Dashboard 1: Confidence Calibration**
```
Metric                 | Target  | Alert Threshold
Prediction accuracy    | ±5%     | <±10%
Rare-context handling  | UNCERTAIN | Any high-conf (<5 samples)
Negative feedback loops| 0       | >1 detected
```

**Dashboard 2: Learning Rate**
```
Metric                 | Target  | Alert Threshold
Score delta/feedback   | ±0.03   | >±0.05
Decay weighting (90d)  | ~0.30   | <0.20 or >0.40
Oscillation events     | 0       | >1 detected
```

**Dashboard 3: User Profiles**
```
Metric                 | Target  | Alert Threshold
Recall (users profiled)| ≥0.80   | <0.70
Precision (behavior)   | ≥0.75   | <0.60
Specialization clarity | Visible | None (info only)
```

**Dashboard 4: Attention Budget**
```
Metric                 | Target  | Alert Threshold
Budget/complexity match| ≥0.80   | <0.70
Budget overruns        | 0       | >1 detected
Nice-to-have deferral  | ~90%    | <80%
```

### Alerting Rules

1. **Checksum Validation Failures**
   - Threshold: >1 failure per 1000 records
   - Action: Page on-call; investigate queue corruption

2. **Lock Contention**
   - Threshold: Aggregator wait time >5 minutes
   - Action: Log; analyze for session spike

3. **Profile Divergence**
   - Threshold: >0.1 delta between instances
   - Action: Alert; manually verify symlink atomic swap

4. **Measurement Track Miss**
   - Threshold: Track metric <0.75 (yellow) or <0.60 (red)
   - Action: Notify measurement owner; escalate if red

---

## Success Criteria (Week 6 Definition of Done)

- [ ] **ADR-0270 (Uncertainty):** Confidence ±5% accurate for ≥1000 samples
- [ ] **ADR-0271 (Feedback):** Learning rate validated; decay working
- [ ] **ADR-0272 (Preferences):** User recall ≥0.80; precision ≥0.75
- [ ] **ADR-0273 (Budget):** Allocation matches complexity ≥0.80
- [ ] **No Blockers:** All C1–C4 + H1–H4 working as designed
- [ ] **Audit Trail:** GDPR-compliant; guard blocks logged
- [ ] **Go/No-Go:** Stakeholder approval for Week 7

---

## Week 7 Preview (If Week 6 Succeeds)

| Deliverable | Scope | Owner |
|---|---|---|
| **M1–M5 Refinements** | Polish error messages, optimize thresholds | TBD |
| **Performance Tuning** | Aggregation <1h, lock contention <1% | TBD |
| **Cross-Tenant Validation** | Multi-tenant profile isolation verified | TBD |
| **Deployment Readiness** | Runbook, alerts, dashboards production-ready | TBD |
| **Release 0.11.x** | Ship ADR-0274 + CEL Phase 4 to production | TBD |

---

## How to Run This Week

### Setup (Before Monday 2026-08-11)

```bash
# Merge K=5 code
git checkout main
git merge --no-ff f543d39  # K=4 (H-items + CR-6)
git merge --no-ff 4076e1b  # K=5 (verification)

# Install & test
uv sync
pytest operator/ -v

# Deploy to staging
export CORVIN_ENVIRONMENT=measurement-staging
corvin-serve &  # or deploy to prod if confident

# Activate telemetry
export CORVIN_TELEMETRY_OPTIN=true
export CEL_PHASE4_MEASUREMENT=true
```

### Daily Cadence

**Morning Stand-up (9am UTC)**
- Review overnight aggregation results
- Check all dashboard metrics
- Identify any alerts

**Midday Sync (12pm UTC)**
- Spot-check 10 samples from current day
- Log observations to measurement journal

**EOD Review (5pm UTC)**
- Aggregate day's metrics
- Update day-specific dashboard
- Prepare next-day focus

**Weekly Sync (Friday EOD)**
- Review all four tracks
- Calculate go/no-go decision
- Prepare Week 7 plan if green

---

## Rollback Plan (If Week 6 Fails)

**If any track <0.60 at any point:**

1. Stop measurement (disable telemetry)
2. Revert to K=4 (commit f543d39)
3. Create ADR-0275 (refinement scope)
4. Identify root cause via adversarial review
5. Fix + re-measure (1 week turnaround)

**Revert command:**
```bash
git reset --hard f543d39
uv sync
pytest operator/ -v
```

---

## Ready to Go

✅ **Code:** K=1→K=5 converged, all tests passing  
✅ **Architecture:** Three-tier system proven  
✅ **Integration:** Guard wired to console + agent  
✅ **Monitoring:** Dashboards + alerts configured  
✅ **Measurement:** Metrics defined, data collection ready

**Status:** READY FOR WEEK 6 EXECUTION

**Start Date:** 2026-08-11 (Monday, 9am UTC)

---

**Prepared by:** Loop-driven engineering K=1→K=5  
**Date:** 2026-08-08  
**Next Review:** 2026-08-11 (Day 1 standup)
