# Phase B: Learning Loop Integration Master Plan

**Date:** 2026-09-16 (Session 5+ Kickoff)  
**Parent ADR:** ADR-0689 (Tier-2 Orchestration)  
**Blocker:** Phase A Complete ✅ (Track 1 OS-Skills merged)  
**Timeline:** 2–3 sessions (parallel with Phase A cleanup)

---

## 🎯 PHASE B VISION

**Connect the Learning Feedback Loop (ADR-0314) to OS-Skills (ADR-0675).**

Today: OS-Skills execute deterministically; no feedback, no learning.  
After Phase B: Every skill execution generates a confidence score + feedback signal → Optimizer tunes parameters → next invocation learns.

**Three-Tier Architecture:**
1. **L5 (Routing):** `os.delegation_router` skill shadow-learns from routing feedback
2. **L10 (Context):** `os.context_adapter` learns which context shapes improve decisions
3. **L22 (Workflow):** `os.workflow_optimizer` learns execution chains from user preferences

---

## 📋 PHASE B SCOPE (3 Sessions)

### Session 5: Learning Integration ADR Draft + Wiring Proof
- **ADR-0693:** Learning Integration (proposal: wire ADR-0314 EventStore into OS-Skills execution path)
- **ADR-0694:** Optimizer Loop (proposal: feedback + convergence detection)
- E2E wiring proof: Skill execution → audit event → feedback ingestion → param update

### Session 6: Optimizer Loop Implementation
- Implement `LearningOptimizer` class (reads feedback events, updates skill config)
- Feedback types: `outcome_feedback` (right/wrong), `confidence_score` (0–100), `metric_observed` (latency, cost)
- Convergence detection (stop when slope < 0.01 for N consecutive iterations)
- Safety: fail-closed, no parameter drifts > 1 std dev

### Session 7: E2E Learning Loop + Console Observability
- Full loop: skill decision → user feedback → optimizer update → verify next execution uses tuned config
- Vibe Dashboard panel: Learning health (convergence rate, feedback lag, parameter deltas)
- Compliance: Audit all optimizer decisions (ADR-0232)

---

## 🔗 DEPENDENCIES & COMPLIANCE

### Load-Bearing Invariants (ADR-0232 Compliance Hardening)
```
✅ Every learning event → audit.jsonl (appendix-only, hash-chain)
✅ Optimizer changes → logged as skill_config_updated event
✅ Feedback validation → PII scrubbed, shape-checked before ingestion
✅ Convergence halt → when confidence >= 95% or slope plateaus
✅ Parameter bounds → never exceed ±1σ from prior distribution
```

### Related ADRs (Load-Bearing)
- **ADR-0314:** Learning Infrastructure (EventStore, LearningEvent types)
- **ADR-0675:** OS-Skills Phase 1 (skill lifecycle, manifest)
- **ADR-0840-0683:** Skill Forge V2 Phase7 Learning Feedback (feedback schema)
- **ADR-0848-0770:** Phase2 K1 Model Selection + Learning (routing optimization)
- **ADR-0231:** Compartmentalization System (skill isolation)
- **ADR-0232:** Compliance Hardening (audit trail integrity)

---

## 📐 ADR DRAFTS (Session 5 Deliverables)

### ADR-0693: Learning Integration (OS-Skills + EventStore)

**Conceptual Level:**
Every OS-Skill execution is a **decision point**. Learning means:
1. Record what decision was made (input, output, latency, confidence)
2. Wait for user feedback (outcome, preference, metric observation)
3. Update skill parameters (router thresholds, context weights, workflow priors)
4. Verify next execution uses tuned config

**Structural Level:**
```
Skill.execute(input)
  → SkillExecutedEvent (audit log + skill_executed topic)
  → [wait for user feedback or auto-metric]
  → LearningOptimizer.process_feedback(FeedbackEvent)
    → Validator.scrub_pii() [fail-closed]
    → Optimizer.update_params(skill_id, delta)
    → EventStore.write_event(skill_config_updated) [audit trail]
  → next Skill.execute() uses tuned config
```

**Implementation Level:**
- `core/learning/skill_integration.py`: `SkillLearningBridge` class
- EventStore query: `events_for_skill(skill_id, since=epoch)` → feedback loop
- Optimizer: stateless, re-entrant, safe to call from async handlers

---

### ADR-0694: Optimizer Loop Convergence & Safety

**Problem:** Skill parameters drift aimlessly if feedback is sparse, stale, or adversarial.

**Solution:**
1. **Confidence Intervals** (ADR-0315): Track uncertainty in each parameter
2. **Convergence Detection** (new): Stop learning when:
   - Confidence ≥ 95% (high certainty, unlikely to improve)
   - Slope < 0.01 for N iterations (diminishing returns)
   - Human explicitly marks as "good" (confidence_score ≥ 90)
3. **Bounds Enforcement** (new): Parameter delta capped at ±1σ; fail-closed on out-of-range

**Algorithm Sketch:**
```python
for feedback in feedback_stream:
    delta = compute_delta(feedback, prior_config)
    if abs(delta) > 1 * std_dev:
        skip(reason="out_of_bounds")  # fail-closed
        continue
    
    config.update(delta)
    confidence = estimate_confidence(feedback_count, variance)
    
    if confidence >= 0.95 or slope < 0.01:
        stop_learning()
        lock_config(skill_id)
    
    audit_log(skill_config_updated event)
```

**Compliance:**
- All optimizer decisions logged (ADR-0232)
- Feedback sourced from EventStore (immutable)
- Config changes write through to audit chain
- No silent optimization (every step is observable)

---

## 🧪 E2E WIRING PROOF (Session 5 Acceptance Criterion)

**Gate:** New entry point = `LearningOptimizer.process_feedback()` must be reachable and tested.

**Phase 1 — Reachability Proof:**
```
Find real call site:
  EventStore.query(event_type="feedback") 
    → LearningOptimizer.process_feedback(event)
    → skill.config.update(...)
    ✅ Real call site: SkillLearningBridge.run() [not test file]
    ✅ Traceable to trigger: Feedback event ingestion (WebSocket /learning/feedback)
```

**Phase 2 — E2E Test:**
```python
# Not a unit test calling optimizer directly
# Real E2E: POST /learning/feedback → EventStore ingests → Optimizer runs → config changes

async def test_learning_loop_e2e():
    # 1. Execute skill with mock input
    skill = os.delegation_router()
    result = await skill.execute({"request": "route me"})
    
    # 2. Post feedback
    response = await client.post(
        "/learning/feedback",
        json={
            "skill_id": "os.delegation_router",
            "feedback_type": "outcome_feedback",
            "signal": "correct",  # user says routing was right
            "timestamp": now()
        }
    )
    assert response.status_code == 200
    
    # 3. Verify skill config was updated
    updated_config = await skill.get_config()
    assert updated_config["version"] > prior_version
    
    # 4. Verify audit event logged
    events = store.query(event_type="skill_config_updated")
    assert len(events) > 0
```

---

## 📊 METRICS (Phase B Success = All 3 Conditions)

| Metric | Target | Verification |
|---|---|---|
| **Feedback Ingestion** | ≥100 events/skill/hour | Load test: POST /learning/feedback rate |
| **Optimizer Latency** | <100ms per update | Measure timestamp(event) → timestamp(config_updated) |
| **Config Convergence** | Slope < 0.01 in <50 iterations | Optimizer telemetry dashboard |
| **Audit Trail** | 100% feedback + optimizer decisions logged | `grep skill_config_updated audit.jsonl \| wc -l` |
| **PII Scrubbing** | 0 PII in feedback events | Validator.scrub_pii() must run fail-closed |
| **Fail-Closed Safety** | No parameter drift > ±1σ | Bounds checker in Optimizer |

---

## 🔄 PARALLEL EXECUTION STRATEGY

**Sessions 5–7 can overlap:**

| Session | Track 1 | Track 2 | Track 3 |
|---|---|---|---|
| **5** | Phase B ADR draft (0693/0694) | Phase A cleanup (if needed) | (async) |
| **6** | Optimizer impl. + unit tests | Console (Vibe dashboard) | (async) |
| **7** | E2E loop + convergence tests | Learning health panel | Phase B acceptance |

---

## ✅ PHASE B COMPLETION CRITERIA

**Phase B = DONE when ALL hold:**

- [ ] ADR-0693 (Learning Integration) `status: ACCEPTED`
- [ ] ADR-0694 (Optimizer Loop) `status: ACCEPTED`
- [ ] SkillLearningBridge wired to EventStore (reachability proof ✅)
- [ ] E2E test: feedback → optimizer → config update (end-to-end)
- [ ] Audit trail: 100% feedback + optimizer decisions logged
- [ ] Convergence detection: working and tested
- [ ] Fail-closed bounds: no parameter drift > ±1σ
- [ ] Console panel: Learning health dashboard live
- [ ] 0 PII in feedback events (validator scrubbing verified)

---

## 📌 SESSION 5 DELIVERABLES (Today)

By end of Session 5:
1. **ADR-0693 PROPOSED** (Learning Integration)
2. **ADR-0694 PROPOSED** (Optimizer Loop)
3. **Reachability proof** (find + document LearningOptimizer call site)
4. **E2E test skeleton** (test_learning_loop_e2e.py, not yet passing)
5. **PHASE-B-LEARNING-PLAN.md** (this file, expanded with feedback)

**No code implementation today** (validation before coding).

---

## 🚀 BLOCKED UNTIL

- ✅ Phase A complete (Track 1 OS-Skills merged) — **DONE**
- ✅ ADR-0314 EventStore wired + tested — **DONE (Phase 3)**
- ✅ Skill manifest + lifecycle (ADR-0675) ready — **DONE (Phase 1)**

**Phase B is UNBLOCKED. Proceed with ADR drafting.**

---

## 📝 REFERENCES

- **ADR-0314:** Learning Infrastructure (EventStore, types)
- **ADR-0675:** OS-Skills Phase 1 (manifest, lifecycle)
- **ADR-0232:** Compliance Hardening (audit trail baseline)
- **ADR-0840-0683:** Skill Forge V2 Phase7 Learning Feedback
- **ADR-0848-0770:** Phase2 K1 Model Selection + Learning
- **PHASE-A-EXECUTION-STATUS.md:** Track 1 merged status
- **SESSION-5-PHASE-A-COMPLETION-REPORT.md:** Phase A recap

---

**Status:** 🟢 **READY FOR PHASE B KICKOFF — ADR DRAFTING STARTS NOW**
