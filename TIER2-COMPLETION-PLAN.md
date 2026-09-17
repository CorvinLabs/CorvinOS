# Phase C Tier 2: C-T2-1 Completion Plan

**Date:** 2026-09-17  
**Initiative:** Skill Learning Bridge Integration (ADR-0693B)  
**Status:** 60% COMPLETE → TARGET: 100% COMPLETE  
**Effort:** 6-8 hours (tight sprint)  
**Blocker:** Upstream dependencies ALL done (ADR-0314, ADR-0675, ADR-0232)

---

## What's Done (60%)

✅ SkillLearningBridge class: core/learning/skill_learning_bridge.py (413 LoC)
✅ SkillFeedbackEvent + SkillConfig + ConfigUpdateEvent dataclasses
✅ E2E test sketch: tests/test_phase4_skill_learning_e2e.py
✅ Console routes: routes/skill_learning_routes.py (assumed wired)
✅ EventStore + audit infrastructure (ADR-0314 Phase 1-2)

---

## What's Needed (40%)

### 1. LearningOptimizer Implementation (2h)
**File:** core/learning/skill_optimizer.py (exists, ~13KB, needs completion)

**Requirements:**
- [ ] Deterministic algorithm (replay-able for tests + audit)
- [ ] Input: skill_id, feedback_events (10+ minimum batch), current_config
- [ ] Process:
  - Compute gradient: ∂loss/∂param = (loss_after - loss_before) / param_delta
  - Apply momentum: delta_new = 0.9 * delta_old + 0.1 * gradient
  - Bounds enforcement: ±1σ on each parameter (prevent drastic changes)
  - Convergence check: stop if |delta| < 0.001 for 5 consecutive iterations
- [ ] Output: updated_config, confidence_score (0-1), convergence_signal
- [ ] Error handling: fail gracefully on malformed input

**Tests needed:**
- test_gradient_computation: ∂loss/∂param calculation correct
- test_bounds_enforcement: delta never > ±1σ
- test_convergence_detection: stops at correct threshold
- test_deterministic: same input → same output (replay-able)

### 2. E2E Wiring Proof (2h)
**Requirement:** Prove real orchestrator calls SkillLearningBridge.execute()

**Test structure:**
```python
def test_e2e_skill_execution_with_learning_feedback():
    """
    Part A: CALL CHAIN
    - Call real skill executor (e.g., os.model_selector.execute(test_input))
    - Verify execution completes
    
    Part B: OUTPUT
    - Verify result contains expected fields
    
    Part C: AUDIT TRAIL
    - Query audit chain for skill_executed + skill_config_updated events
    - Verify events logged with correct tenant_id + hash
    - Verify chain: event2.prev_hash == event1.hash
    """
```

**Entry point to prove:**
- Real HTTP request: POST /v1/console/skills/execute (if exists)
- Or: Real function call: from core.skills.os_skills.model_selector import ModelSelectorSkill; skill.execute()
- OR: Real CLI: corvin skill-execute model_selector (if exists)

### 3. Audit Integration (1h)
**Requirements:**
- [ ] skill_executed event logged BEFORE feedback loop starts
- [ ] skill_config_updated event logged WITH previous_config + new_config + confidence_delta
- [ ] No PII in events (scrub emails, user names, API keys)
- [ ] Hash chain verified (each event refs prev_hash)
- [ ] Tenant_id isolation (all events carry correct tenant_id)

**Test:**
```python
def test_audit_events_logged_correctly():
    """Verify skill execution emits 2+ events in hash chain."""
    bridge = SkillLearningBridge(...)
    result = bridge.execute_with_learning(test_input)
    
    # Read audit events
    events = read_audit_chain(tenant_id)
    our_events = [e for e in events if e["skill_id"] == "test_skill"]
    
    assert len(our_events) >= 2
    assert our_events[0]["event_type"] == "skill_executed"
    assert our_events[1]["event_type"] == "skill_config_updated"
    assert our_events[1]["prev_hash"] == our_events[0]["hash"]
    assert "[EMAIL]" not in json.dumps(our_events)  # No PII
```

### 4. PII Scrubbing Validation (1h)
**Requirement:** Audit events must not contain:
- Email addresses (user@domain.com)
- Phone numbers (+1-234-567-8900)
- API keys / tokens
- Credit card numbers
- User names (if PII)

**Test:**
```python
def test_no_pii_in_audit_events():
    """Scrub patterns tested + verified."""
    test_inputs = [
        {"email": "user@example.com", "feedback": "good"},
        {"phone": "+1-555-1234", "feedback": "bad"},
        {"api_key": "sk_live_123456789abcdef", "feedback": "ok"},
    ]
    
    for inp in test_inputs:
        result = bridge.execute_with_learning(inp)
        events = read_audit_chain()
        events_str = json.dumps(events)
        
        # Verify patterns scrubbed
        assert "user@example.com" not in events_str
        assert "555-1234" not in events_str
        assert "sk_live_" not in events_str
```

### 5. Convergence Detection (1h)
**Requirement:** Learning stops when confidence reaches 95% or loss slope plateaus

**Test:**
```python
def test_convergence_detection():
    """Optimizer stops when converged."""
    # Simulate feedback loop: iterate until convergence
    config = initial_config
    for iteration in range(100):
        feedback = generate_feedback(config)
        config, converged, confidence = optimizer.process(feedback, config)
        if converged:
            assert iteration < 50  # Should converge within 50 iterations
            assert confidence >= 0.95  # OR slope plateau detected
            break
    assert converged, "Did not converge after 100 iterations"
```

### 6. Console Learning Health Panel (2h)
**Requirement:** Dashboard shows Skill Learning metrics

**Components:**
- [ ] Route: GET /v1/console/learning-health/{skill_id}
  - Returns: skill_id, confidence_score, last_updated, learning_status
- [ ] React component: pages/learning-health.tsx (or similar)
  - Chart: confidence_score over time
  - Chart: loss over time
  - Metrics: convergence_status, update_count, last_feedback_at
- [ ] Audit trail viewer: show recent events for this skill

**Test:**
```python
def test_learning_health_panel_renders():
    """HTTP endpoint returns learning metrics."""
    response = requests.get("http://localhost:8765/v1/console/learning-health/test_skill")
    assert response.status_code == 200
    data = response.json()
    assert "confidence_score" in data
    assert "learning_status" in data
```

### 7. Comprehensive Tests (30+ cases)
**Coverage target:**
- [ ] Unit tests: 12+ (LearningOptimizer, bounds, convergence, PII scrubbing)
- [ ] E2E tests: 8+ (full workflow, different skill types, error cases)
- [ ] Adversarial: 10+ (malformed input, race conditions, memory limits)

**Test file locations:**
- core/learning/tests/test_skill_learning_bridge_complete.py (new, primary)
- core/learning/tests/test_learning_optimizer_complete.py (new, for optimizer)
- tests/e2e/test_skill_learning_e2e_complete.py (E2E + wiring proof)

---

## Acceptance Criteria (ALL REQUIRED)

- [ ] ADR-0693B marked ACCEPTED in Corvin-ADR/decisions/
- [ ] LearningOptimizer fully implemented + all logic deterministic
- [ ] E2E wiring proof: real call (HTTP/function) proves reachability + audit trail
- [ ] Audit integration: events logged + hash-chained + no PII
- [ ] PII scrubbing: regex patterns tested + verified
- [ ] Bounds enforcement: parameter deltas capped ±1σ
- [ ] Convergence detection: stops at 95% confidence or slope plateau
- [ ] Console panel: Learning Health metrics render
- [ ] 30+ tests passing (unit + E2E + adversarial)
- [ ] Commit: "feat(learning): ADR-0693B Skill Learning Bridge completion [ADR-0693]"

---

## Execution Order

1. **Hour 1-2:** Audit + complete LearningOptimizer (deterministic algorithm)
2. **Hour 2-3:** Write LearningOptimizer tests (unit + adversarial)
3. **Hour 3-4:** E2E wiring proof (real call + audit trail verification)
4. **Hour 4-5:** PII scrubbing + bounds enforcement validation
5. **Hour 5-6:** Console Learning Health panel (route + React component)
6. **Hour 6-7:** Comprehensive test suite (30+ cases)
7. **Hour 7-8:** Final audit + commit

---

## Critical Files

**Core implementation:**
- core/learning/skill_learning_bridge.py (413 LoC, 60% done)
- core/learning/skill_optimizer.py (~13KB, needs completion)
- core/console/corvin_console/routes/skill_learning_routes.py (HTTP routes)

**Tests:**
- core/learning/tests/test_skill_learning_bridge_complete.py (NEW)
- core/learning/tests/test_learning_optimizer_complete.py (NEW)
- tests/e2e/test_skill_learning_e2e_complete.py (E2E wiring proof)

**Documentation:**
- docs/claude-ref/learning-loop.md (update with convergence algorithm)
- Inline docstrings (LearningOptimizer.process() + bounds checking)

---

## No Blockers

All upstream dependencies are done:
- ✅ ADR-0314 (Learning Infrastructure + EventStore)
- ✅ ADR-0675 (Skill Forge v2.0 Phase 1)
- ✅ ADR-0232 (Boot tripwire + audit chain)

---

## Success Metrics

| Metric | Target | Threshold |
|--------|--------|-----------|
| Tests Passing | 30+ | ≥25 |
| E2E Wiring Proof | Complete | 3-part proof present |
| Audit Integration | 100% | All events logged + hash-chained |
| PII Scrubbing | 100% | Zero PII in audit events |
| Bounds Enforcement | Verified | Tests show ±1σ enforcement |
| Convergence | <50 iterations | Stops before 100 iterations |
| ADR Status | ACCEPTED | In Corvin-ADR/decisions/ |

---

**Status:** 🚀 READY TO IMPLEMENT (60% → 100% completion sprint)
