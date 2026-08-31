# Option B + C Implementation Plan

**Goal:** Achieve Tier-3 Autonomy (16-hour tasks, fully unattended)  
**Timeline:** 1-3 days (Option B this session) + Weeks 1-3 (Option C sprints)  
**Outcome:** Self-Managed Sessions Phase 2.1 ready + Context Pipeline v2 validated  

---

## 🎯 OPTION B: Context Pipeline v2 Validation (THIS SESSION — 3-4 hours)

### What It Delivers
- **Multi-Session Context Preservation:** Agent remembers original intent across 5+ sessions
- **Entropy Detection:** Catches contradictions BEFORE they cascade
- **Precision Filtering:** Only relevant context injected (Tier 1/2/3 quality gates)

### Current Status
- **Design:** ✅ Complete (Preservation + Additive model, ADR-0399)
- **Code Sketch:** ✅ Available (context_pipeline_v2_ldd_validation.md)
- **Validation Plan:** ✅ k=1-5 LDD structure ready
- **Blocker:** ⚠️ Validation k=1-3 not yet run

### Acceptance Criteria (k=1-3)

| Iteration | Objective | Success Criterion | Loss Metric |
|---|---|---|---|
| **k=1** | Two-layer separation | Both layers render in prompt correctly | False positive rate < 5% |
| **k=2** | Quality gate (3 tiers) | Tier 3 additions filtered upstream | Confusion reduction > 50% |
| **k=3** | Entropy detection | Contradictions caught in first 2 iterations | Detection latency < 2 iterations |

### Checkpoints for Monitoring

**Checkpoint 1 (k=1 Green):** Original Context + Pipeline Context both present in prompt
- **Observable:** agent_prompt.contains("ORIGINAL CONTEXT") AND agent_prompt.contains("PIPELINE CONTEXT")
- **Success:** True for 10/10 test prompts

**Checkpoint 2 (k=2 Green):** Tier classification working (90%+ accuracy)
- **Observable:** quality_gate.classify(addition) matches human review
- **Success:** 9/10 additions correctly classified by human vs. model

**Checkpoint 3 (k=3 Green):** Entropy detection fires before cascade
- **Observable:** contradiction_detected_at_iteration <= 2 (for tests with known contradictions)
- **Success:** Latency < 2 iterations in 8/10 tests

### Metrics to Capture (Monitoring)
```yaml
monitoring:
  - metric: context_drift_incidents
    baseline: current_rate
    target_after_v2: < 5%
    measurement: agent_self_report + user_feedback
  
  - metric: false_positive_rate
    baseline: unknown
    target: < 5%
    measurement: pipeline_addition matches relevance clause?
  
  - metric: entropy_detection_latency
    baseline: unknown
    target: < 2 iterations
    measurement: contradiction_found_at_iteration
  
  - metric: tier_classification_accuracy
    baseline: unknown
    target: > 90%
    measurement: human_review vs model_classification
```

### What Happens Next (After Option B Green)
- Multi-Session Tasks (4-5 hours) work without context loss ✅
- Memory doesn't diverge across session boundaries ✅
- Foundation ready for Option C (Self-Managed Sessions)

---

## 🚀 OPTION C: Self-Managed Sessions Phase 2.1 (Weeks 1-3)

### What It Delivers
- **Autonomous Multi-Phase Tasks:** 16-hour audit runs without manual intervention
- **Automatic Checkpoint/Resume:** 6 split triggers fire autonomously
- **Smart Error Recovery:** Backtrack, adapt, retry — all automated
- **Cognitive Overload Detection:** Self-recognizes when overwhelmed

### Architecture (9 Subsystems)

#### Core (4) — Weeks 1-2
1. **SessionLifecycleManager** — Detects 6 split triggers
2. **CheckpointManager** — Serializes full state (idempotent resume)
3. **ContextReducer** — Compresses 200k → 18k tokens (91% reduction)
4. **RecoveryEngine** — 4 recovery patterns (Replay, Adapt, Backtrack, Pause)

#### Monitors (5) — Week 3
5. **GoalAlignmentMonitor** — Drift detection (goal_alignment < 0.6 → alert)
6. **ConsistencyValidator** — Contradiction detection (entropy)
7. **AssumptionTracker** — Validates unproven assumptions
8. **ExplorationScheduler** — Local optimum detection (success_rate 0.6-0.8 → try alternatives)
9. **SelfMonitoringSubsystem** — Cognitive load detection (overload > 0.8 → reset)

### Sprint Plan

#### Sprint 1 (Week 1: 5 days) — SessionLifecycle + Checkpoint
**Deliverables:**
- SessionLifecycleManager with 6 trigger rules
- CheckpointManager with full state serialization
- Checkpoint storage backend (filesystem)
- 35 unit tests

**Checkpoint Validation:**
- [ ] Checkpoint 1: Session triggers fire correctly (all 6 rules)
- [ ] Checkpoint 2: Checkpoint JSON round-trips (serialize → deserialize = identity)
- [ ] Checkpoint 3: Checkpoint persistence (restore from disk)

**Success Metrics:**
- Split triggers detect in 100% of test cases
- Checkpoint restore achieves 100% state fidelity (no data loss)
- Storage latency < 1 second

---

#### Sprint 2 (Week 2: 5 days) — ContextReducer + RecoveryEngine
**Deliverables:**
- ContextReducer with 4-tier preservation strategy
- RecoveryEngine with 4 recovery patterns
- Integration with SessionLifecycleManager
- 40 unit tests

**Checkpoint Validation:**
- [ ] Checkpoint 4: Context reduction ratio (200k → 18k = 91%)
- [ ] Checkpoint 5: Recovery pattern selection (correct pattern for error type)
- [ ] Checkpoint 6: Replay idempotency (same result on replay)

**Success Metrics:**
- Context reduction > 85% (tokens preserved / original tokens)
- Recovery success rate > 95% (recovery executed correctly)
- No data corruption on replay

---

#### Sprint 3 (Week 3: 3 days) — 5 Monitors + E2E
**Deliverables:**
- All 5 monitors (Goal, Consistency, Assumption, Exploration, SelfMonitoring)
- E2E test on 16-hour simulated audit task
- Operator runbook (recovery procedures, tuning)
- 75+ integration tests

**Checkpoint Validation:**
- [ ] Checkpoint 7: Goal alignment stays > 0.7 (no drift)
- [ ] Checkpoint 8: Entropy detected before cascade (latency < 1 phase)
- [ ] Checkpoint 9: Assumption validation (all assumptions checked)
- [ ] Checkpoint 10: Cognitive load monitoring (overload detected, reset triggered)

**Success Metrics:**
- Goal alignment > 0.7 throughout 16-hour task
- Contradiction detected before phase exit (100% of tests)
- Cognitive overload detected, recovery executed (100% coverage)

---

### Full Checkpoint Map (Option B + C)

| Checkpoint | Phase | Observable | Success | Owner |
|---|---|---|---|---|
| **B1** | Option B k=1 | Both context layers in prompt | 10/10 tests | pipeline_v2 |
| **B2** | Option B k=2 | Tier classification accuracy | 9/10 correct | pipeline_v2 |
| **B3** | Option B k=3 | Entropy detection latency | < 2 iterations | pipeline_v2 |
| **C1** | Sprint 1 | 6 split triggers fire | 100% detection | SessionLifecycle |
| **C2** | Sprint 1 | Checkpoint round-trip fidelity | 100% identity | CheckpointManager |
| **C3** | Sprint 1 | Checkpoint persistence | restore from disk OK | CheckpointManager |
| **C4** | Sprint 2 | Context reduction ratio | > 85% | ContextReducer |
| **C5** | Sprint 2 | Recovery pattern selection | correct type | RecoveryEngine |
| **C6** | Sprint 2 | Replay idempotency | same result | RecoveryEngine |
| **C7** | Sprint 3 | Goal alignment throughout | > 0.7 always | GoalAlignmentMonitor |
| **C8** | Sprint 3 | Entropy detection latency | < 1 phase | ConsistencyValidator |
| **C9** | Sprint 3 | Assumption validation | all checked | AssumptionTracker |
| **C10** | Sprint 3 | Cognitive load monitoring | overload detected | SelfMonitoringSubsystem |

---

## 🔑 Key Custody Blocker — Analysis

### Status
- **Blocker:** Ed25519 maintainer key must be generated + private key stored
- **Affects:** Plugin-System Stage 6 (plugin install command)
- **DOES NOT affect:** Self-Managed Sessions (Option C)
- **Decision Status:** Awaiting maintainer choice on key custody approach

### Workaround for Option B + C
1. **Skip Stage 6 entirely** — not on critical path for Sessions
2. **Use builtin/community origins only** — no `vetted` plugins
3. **Document as known limitation** — reopen after Sessions Phase 2.1 done

### Recommendation
**Defer key custody decision until Week 4** (after Sessions Sprint 3 complete).
- **Why:** Parallel stream; doesn't block Sessions
- **When to address:** When Plugin-System needs `vetted` origin support

---

## 🎪 Dependencies: What Must Happen First

### Before Option B (Context Pipeline v2)
- ✅ Brain v0.2 is stable (already shipped)
- ✅ ADR-0399 is drafted (ready)
- ✅ k=1-5 validation plan exists
- **Action:** Run k=1-3 this session

### Before Sprint 1 (Session Manager)
- ✅ Context Pipeline v2 validated (Option B green)
- ✅ Brain v0.2 passes canary metrics (Week 5 measurement)
- ✅ SessionLifecycleManager design locked (design complete)
- **Action:** Start Week 1

### Before Sprint 2
- ✅ Sprint 1 all checkpoints green
- ✅ CheckpointManager persists to filesystem
- **Action:** Start Week 2

### Before Sprint 3
- ✅ Sprint 2 all checkpoints green
- ✅ RecoveryEngine pattern selection working
- **Action:** Start Week 3

---

## ⚠️ Critical Success Factors

### Instrumentation (Must Be Ready)
- [ ] Checkpoint recording (all 10 checkpoints logged with timestamp + success/fail)
- [ ] Monitoring dashboard (latency, accuracy, error counts per checkpoint)
- [ ] Alert thresholds (if checkpoint fails, alert immediately)
- [ ] Audit trail (every split/checkpoint/recovery logged, hash-chained)

### Testing Strategy
- [ ] Unit tests per subsystem (35 + 40 + 75 = 150+)
- [ ] E2E test: 16-hour audit task (simulated, deterministic)
- [ ] Canary test: Real long-running task (operator observes)
- [ ] Regression tests: Ensure no existing functionality breaks

### Operator Readiness
- [ ] Runbook for manual checkpoint recovery (if needed)
- [ ] Dashboard showing current session state, checkpoint history
- [ ] Emergency procedures (how to force split, reset, escalate)
- [ ] Tuning guide (polling intervals, thresholds, retry counts)

---

## 📊 Success Criteria: How We Know Option B + C Work

### Option B (Context Pipeline v2) Green Means:
```
✅ Context doesn't drift across 5+ sessions
✅ Entropy detected before contradictions cascade
✅ Agent self-corrects when it notices drift
✅ Multi-session tasks complete without reset
```

### Option C (Sessions Phase 2.1) Green Means:
```
✅ 16-hour audit task runs unattended (5 auto-splits, 0 manual intervention)
✅ Recovery from errors (backtrack, adapt, retry) works autonomously
✅ Cognitive overload detected and reset triggered
✅ Checkpoint/resume achieves 100% state fidelity
✅ <30 min per session (splits working effectively)
```

### Combined (Option B + C) Delivers:
```
✅ TIER-3 AUTONOMY: "Riesige" 3-16 hour tasks fully autonomous
✅ No manual intervention needed (except high-strategy decisions)
✅ Multi-day task runs end-to-end without user interaction
✅ Foundation for 100% autonomous system (ready for Phase 3+)
```

---

## 📋 Action Items (Priority Order)

### THIS SESSION (Option B)
- [ ] Run Context Pipeline v2 validation k=1-3 (3-4 hours)
- [ ] Capture B1, B2, B3 checkpoints
- [ ] Document any blockers
- [ ] Commit: feat(context-pipeline): v2 validated, k=1-3 green

### IMMEDIATELY AFTER (Setup)
- [ ] Design monitoring dashboard (checkpoint tracking)
- [ ] Instrument all 10 checkpoints (logging, alerting)
- [ ] Set up canary measurement framework

### WEEK 1 (Sprint 1)
- [ ] SessionLifecycleManager implementation
- [ ] CheckpointManager implementation
- [ ] Checkpoint C1, C2, C3 validation
- [ ] 35 unit tests green

### WEEK 2 (Sprint 2)
- [ ] ContextReducer implementation
- [ ] RecoveryEngine implementation
- [ ] Checkpoint C4, C5, C6 validation
- [ ] 40 unit tests green

### WEEK 3 (Sprint 3)
- [ ] 5 Monitors implementation
- [ ] Checkpoint C7, C8, C9, C10 validation
- [ ] 75+ integration tests
- [ ] E2E test on 16-hour task green
- [ ] Operator runbook complete

---

**Status:** READY TO START  
**Commit:** a60e497c (Vibe 3.1 base)  
**Next:** Run Option B k=1-3 this session  

---

**This plan ensures:**
1. ✅ **Measurable progress** (10 checkpoints, objective success criteria)
2. ✅ **Operator visibility** (monitoring dashboard, alerts)
3. ✅ **Risk mitigation** (serial sprints, not parallel)
4. ✅ **Autonomy growth** (Tier 1 → Tier 2 → Tier 3)
5. ✅ **Production ready** (150+ tests, E2E validation, runbook)
