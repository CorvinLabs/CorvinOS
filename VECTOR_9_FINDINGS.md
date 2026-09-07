# Adversarial Vector #9: Feedback Contradiction (with Consistency Check)

## Status: EXPLOITABLE ❌

**Severity: HIGH** (Allows contradictory feedback to bypass mitigations and corrupt learning loop)

**Exploitability: YES** (Trivial to exploit via numeric loss sorting bug)

---

## Vulnerability Overview

The FeedbackConsistencyValidator was designed to detect and downweight contradictory feedback by comparing user feedback against recent loss trends. However, a critical bug in the implementation **completely reverses loss trends**, allowing contradictory feedback to bypass the consistency check.

### Root Cause

**File:** `core/learning/consistency_checker.py`, Line 233

```python
def _fetch_recent_losses(self, skill_id: str, tenant_id: str) -> List[float]:
    # ...
    if losses:
        return sorted(losses)  # ❌ BROKEN: Numeric sort destroys chronological order
```

The code sorts losses by numeric value instead of preserving their temporal order. This **inverts the trend** for decreasing loss sequences.

### Attack Scenario

**Step 1: System operates with improving loss (decreasing)**
```
Chronological losses: [0.6, 0.58, 0.56, 0.54, 0.52]
Trend: DECREASING (loss improving - good)
Expected feedback: GOOD
```

**Step 2: Bug applies numeric sort**
```
After sorted(): [0.52, 0.54, 0.56, 0.58, 0.6]
Trend detected: INCREASING (wrong!)
Expected feedback: BAD
```

**Step 3: Attacker submits contradictory feedback**
```
User feedback: BAD
Consistency check: score = 1.0 ✅ (appears consistent with inverted trend)
Contradiction event: NOT LOGGED
Downweighting: NOT APPLIED
```

**Step 4: Corrupted learning**
```
- Contradictory feedback influences backprop normally (full weight)
- Learning loop receives inverted signals
- Optimizer tuning corrupted
```

---

## Proof of Exploit

Running the exploit test (`test_vector_9_exploit.py`):

### Test 1: Trend Reversal Exploit
```
Input losses: [0.6, 0.58, 0.56, 0.54, 0.52] (DECREASING)
User feedback: BAD (contradictory)
Detected trend: INCREASING (inverted!)
Consistency score: 1.0 (SHOULD BE < 0.5)
Contradiction event: NOT logged
```

**Result: EXPLOITABLE** ❌

### Test 2: Feedback Cascade Attack
```
Submit 3 contradictory feedback signals
All marked as: is_consistent=True, score=1.0
Expected: is_consistent=False, score < 0.5 for all
```

**Result: All bypass mitigations** ❌

### Test 3: Audit Trail Bypass
```
Contradictory feedback marked as consistent
Contradiction event: NOT emitted
Audit trail: Shows false "consistent" status
```

**Result: Silent corruption** ❌

---

## Attack Vector Details

| Aspect | Status |
|--------|--------|
| **Exploitability** | ✅ YES - Trivial (just requires feedback on improving system) |
| **Discoverability** | ✅ High - First feedback batch will trigger |
| **Impact** | ✅ HIGH - Corrupts learning loop with inverted gradients |
| **Detectability** | ❌ Low - Audit trail shows false "consistent" status |
| **Repeatability** | ✅ YES - 100% reproducible |

---

## Impact Assessment

1. **Learning Loop Corruption**: Contradictory feedback influences backprop with full weight
2. **Gradient Inversion**: Loss trends are inverted, causing training to worsen
3. **Silent Failure**: No contradiction event logged, audit trail shows false status
4. **Cascade Attack**: Multiple contradictory feedback signals all appear consistent

---

## Mitigation Required

**Fix:** Preserve chronological order instead of numeric sorting

### Option A: Remove the sort (simplest)
```python
def _fetch_recent_losses(self, skill_id: str, tenant_id: str) -> List[float]:
    if self.event_store:
        try:
            events = self.event_store.get_events(
                event_type="unified_loss_computed",
                skill_id=skill_id,
                tenant_id=tenant_id,
                limit=self.LOSS_WINDOW_SAMPLES,
            )
            losses = []
            for event in events:
                if "total_loss" in event.get("payload", {}):
                    losses.append(event["payload"]["total_loss"])
            # ✅ FIX: Remove sorted() to preserve event store order
            return losses  # Chronological order as provided by event store
    return []
```

### Option B: Sort by timestamp (if available)
```python
# Extract losses with timestamps, sort by timestamp (not by numeric value)
events_with_loss = [
    (event.get("timestamp"), event["payload"]["total_loss"])
    for event in events
    if "total_loss" in event.get("payload", {})
]
return [loss for _, loss in sorted(events_with_loss, key=lambda x: x[0])]
```

---

## Test Coverage Gap

The test suite (`test_security_fix_9.py`) has a critical gap:

❌ **Missing:** Tests do NOT verify that losses maintain chronological order  
❌ **Missing:** Tests do NOT simulate event store returning unsorted losses  
❌ **Missing:** Tests do NOT verify trend reversal scenarios  

The tests pass because they provide losses in a specific order that happens to work, but they don't catch the sorting bug.

### Required Test Addition
```python
def test_chronological_order_preserved():
    """Losses must maintain chronological order, not numeric value order."""
    validator = FeedbackConsistencyValidator()
    
    # Decreasing loss (improvement)
    chronological_losses = [0.6, 0.58, 0.56, 0.54, 0.52]
    
    class MockEventStore:
        def get_events(self, **kwargs):
            # Return in chronological order (as real event store would)
            return [{"payload": {"total_loss": loss}} for loss in chronological_losses]
    
    validator.event_store = MockEventStore()
    
    result = validator.validate_consistency(
        feedback_id="fb_chrono",
        skill_id="os.router",
        task_id="task_chrono",
        feedback_signal=FeedbackSignal.BAD,  # Contradictory
        tenant_id="_default",
    )
    
    assert result.loss_trend == "decreasing", "Must preserve chronological order"
    assert not result.is_consistent, "Contradictory feedback must be detected"
    assert result.consistency_score < 0.5, "Score must reflect contradiction"
```

---

## Compliance Impact

**GDPR Art. 32 (Data Security):** Audit trail falsely reports contradictory feedback as consistent, violating integrity guarantees.

**Audit Chain (ADR-0232):** Trailing contradiction event is not emitted, breaking the immutability promise.

---

## Recommendation

**CRITICAL:** Fix the sorting bug before vector #9 is considered mitigated.

1. Apply Option A (remove sort) immediately
2. Add chronological order test to prevent regression
3. Run full adversarial test suite (`test_vector_9_exploit.py`)
4. Verify no contradiction events are being swallowed
5. Confirm audit trail shows correct trend detection

**Timeline:** Blocker for learning loop deployment until fixed.
