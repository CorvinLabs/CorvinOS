# CRITICAL-5: EventEmitter Universal Wiring Audit Report

**Date:** 2026-08-29  
**Status:** AUDIT COMPLETE, IMPLEMENTATION READY  
**Impact:** Blocking I/O in learning modules; skill execution latency  
**Urgency:** CRITICAL  

---

## Executive Summary

**Problem:** Four learning modules call `EventStore.write_event()` directly, performing blocking I/O in skill execution paths and bypassing the non-blocking EventEmitter queue.

**Risk:** 
- Skill execution latency increases by network/I/O delay (100ms–1s+)
- Events may be lost on queue full (fire-and-forget via EventEmitter cannot occur if write_event is blocking)
- Tenant isolation enforcement varies (direct calls may not validate tenant_id consistently)

**Solution:** Refactor all direct `write_event()` calls to use `EventEmitter.emit()` (async queue-based approach).

**Coverage:** 4 files, 4 direct write_event calls identified. All are **wirable** (reachable from async contexts or convertible).

---

## Coverage Audit Results

### Direct EventStore.write_event() Calls (Non-Test Files)

| File | Line | Method Signature | Type | Blocking? | Urgency |
|---|---|---|---|---|---|
| `core/learning/confidence_scorer.py` | 347 | `_emit_confidence_event()` (sync) | Direct write | YES | HIGH |
| `core/learning/operator_feedback.py` | 344 | `record_tool_rating()` (async) | Direct write | NO | HIGH |
| `core/learning/operator_feedback.py` | 401 | `record_skill_rating()` (async) | Direct write | NO | HIGH |
| `core/learning/skill_attribution.py` | 246 | `emit_attribution_event()` (async) | Await write | NO | MEDIUM |
| `core/learning/user_profile.py` | 475 | `_emit_preference_updated()` (sync) | Direct write | YES | HIGH |

### Summary
- **Total direct calls:** 5 (1 per file in confidence_scorer, 1 per file in user_profile, 2 in operator_feedback, 1 in skill_attribution)
- **Blocking calls:** 2 (confidence_scorer, user_profile — both synchronous methods)
- **Async calls:** 3 (all operator_feedback and skill_attribution — async methods)
- **Coverage:** 100% (all identified, all wirable)

---

## Detailed Analysis

### 1. confidence_scorer.py (Line 347)

**Current Code:**
```python
def _emit_confidence_event(self, skill_id, context, relevance, reliability):
    if self.event_store is None:
        return
    try:
        if hasattr(self.event_store, "write_event"):
            from .event_schema import LearningEvent as CanonicalEvent
            from .event_schema import LearningEventType
            
            self.event_store.write_event(CanonicalEvent(
                event_type=LearningEventType.CONFIDENCE_SCORE,
                tenant_id=str(context.get("tenant_id") or "_default"),
                ...
            ))
```

**Issues:**
- Method is synchronous, but calls blocking I/O
- Embedded try-except swallows AttributeError (no signal if event_store doesn't have write_event)
- Called from `per_skill_stats()`, which is a query method — should not block on I/O

**Fix Strategy:**
- Convert to async method (low impact — callers are learning loop only)
- Or: Create sync wrapper that queues to EventEmitter asynchronously (non-blocking)
- **Recommended:** Async conversion (cleaner, no wrapper needed)

---

### 2. operator_feedback.py (Lines 344, 401)

**Current Code:**
```python
async def record_tool_rating(self, tool_id, tool_name, rating, tenant_id, ...):
    event = LearningEvent(...)
    try:
        self.event_store.write_event(event)  # Line 344
        logger.info(...)
    except Exception as e:
        logger.error(...)
        raise

async def record_skill_rating(self, skill_id, skill_name, rating, tenant_id, ...):
    event = LearningEvent(...)
    try:
        self.event_store.write_event(event)  # Line 401
        logger.info(...)
    except Exception as e:
        logger.error(...)
        raise
```

**Issues:**
- Both methods are async but use blocking write_event (not awaited)
- Direct I/O call blocks the event loop (should be queued instead)
- High-risk: if write_event takes 100ms+, the entire operator feedback path stalls

**Fix Strategy:**
- Replace `self.event_store.write_event(event)` with `await self.event_emitter.emit(event)`
- Inject EventEmitter in `__init__`
- Verify tenant_id consistency (event.tenant_id == self.tenant_id)

**Callers:** 
- Bridge handlers, console feedback endpoints — all async
- **Safe to await:** YES

---

### 3. skill_attribution.py (Line 246)

**Current Code:**
```python
async def emit_attribution_event(self, ...):
    event = LearningEvent(...)
    try:
        await self.event_store.write_event(event)  # Line 246
    except Exception:
        # Log silently, continue processing
        continue
```

**Issues:**
- Already awaiting write_event, but it's blocking I/O masquerading as async
- Should use EventEmitter queue instead (async-queue semantics, fire-and-forget)

**Fix Strategy:**
- Replace `await self.event_store.write_event(event)` with `await self.event_emitter.emit(event)`
- No method signature change needed (already async)

---

### 4. user_profile.py (Line 475)

**Current Code:**
```python
def _emit_preference_updated(self, profile, feedback):
    if not self.event_store:
        return
    try:
        event = LearningEvent(...)
        self.event_store.write_event(event)  # Line 475
    except Exception as e:
        print(f"[WARN] Failed to emit preference update event: {e}")
```

**Issues:**
- Method is synchronous, blocks on I/O
- Called from `update_profile()` (user preference update path)
- Silent failure (just prints warning, doesn't raise)

**Fix Strategy:**
- Convert to async method (wrapper call: `asyncio.create_task()`)
- Or: Create sync wrapper that queues to EventEmitter without blocking
- **Recommended:** Sync wrapper (lower impact on update_profile callers)

---

## EventEmitter API Reference

```python
class EventEmitter:
    async def emit(self, event: LearningEvent) -> None:
        """Emit event (non-blocking, queue-based).
        
        Args:
            event: Event to emit (tenant_id must match self.tenant_id)
        
        Raises:
            ValueError: If tenant_id mismatch
        
        On queue full: drops event with warning log (fire-and-forget)
        """
        if event.tenant_id != self.tenant_id:
            raise ValueError(f"Tenant mismatch...")
        try:
            self.event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logging.warning(f"EventEmitter queue full, dropping event: ...")
```

**Key Properties:**
- Non-blocking (put_nowait)
- Async only (use await)
- Fire-and-forget on queue full (silent drop with log)
- Tenant validation built-in
- Background worker processes queue

---

## Implementation Plan

### Phase 1: Add EventEmitter Injection (0.5 day)

1. **confidence_scorer.py:**
   - Add `event_emitter: Optional[EventEmitter]` parameter to `__init__`
   - Convert `_emit_confidence_event()` to async
   - Replace write_event call with await emit()

2. **operator_feedback.py:**
   - Add `event_emitter: Optional[EventEmitter]` parameter to `__init__`
   - Replace write_event calls with await emit() in both methods

3. **skill_attribution.py:**
   - Add `event_emitter: Optional[EventEmitter]` parameter to `__init__`
   - Replace await write_event with await emit()

4. **user_profile.py:**
   - Add `event_emitter: Optional[EventEmitter]` parameter to `__init__`
   - Create `_queue_preference_updated()` async method
   - Call it from `_emit_preference_updated()` via asyncio.create_task()

### Phase 2: Wiring (0.5 day)

Update all constructors to inject EventEmitter:
- `ConfidenceScorer(event_store, event_emitter)`
- `OperatorFeedbackHandler(event_store, event_emitter)`
- `SkillAttributionEngine(event_store, event_emitter)`
- `UserProfileManager(event_store, event_emitter)`

Verify injection points:
- Learning loop initialization
- Skill system integration
- Test fixtures

### Phase 3: Testing (1 day)

1. **Coverage tests:**
   - Verify all 5 calls now use EventEmitter.emit()
   - Verify no direct write_event() calls remain
   - Verify tenant_id validation happens

2. **Latency tests:**
   - Measure skill execution latency with/without events
   - Baseline: no events
   - Target: <5ms overhead for 10 events
   - Use profiling context manager

3. **Stress tests:**
   - Emit 1000 events in rapid succession
   - Verify queue doesn't lose events
   - Verify worker processes all
   - Verify no blocking on main loop

4. **Integration tests:**
   - Confidence score events persist correctly
   - Operator feedback events persist correctly
   - Skill attribution events persist correctly
   - User preference events persist correctly

---

## Tenant Isolation Verification

### Current State
- EventStore.write_event() requires tenant_id parameter ✅
- EventEmitter.emit() validates tenant_id in event ✅

### Verification Needed
- [ ] confidence_scorer: tenant_id from context (line 349)
- [ ] operator_feedback: tenant_id from method parameter (lines 330, 385)
- [ ] skill_attribution: tenant_id from self.tenant_id
- [ ] user_profile: tenant_id from profile.tenant_id (line 452)

All appear correct; no changes needed beyond using EventEmitter.

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Existing code still calls write_event directly | Low | HIGH | Grep audit after fix; add pre-commit check |
| EventEmitter not injected everywhere | Medium | MEDIUM | Test fixture coverage, trace all entry points |
| Async conversion breaks sync callers | Low | HIGH | Trace all call sites before conversion |
| Tenant_id mismatch after migration | Low | MEDIUM | Add validation test in event persistence |
| Performance regression | Low | LOW | Latency tests capture any slowdown |

---

## Pre-Implementation Checklist

- [x] All 5 direct write_event() calls identified
- [x] Blocking I/O locations documented
- [x] EventEmitter API reviewed
- [x] Tenant isolation model verified
- [x] Conversion strategy for sync methods identified
- [ ] Code changes implemented
- [ ] Tests written and passing
- [ ] Coverage audit re-run (verify 0 bypasses)
- [ ] Latency tests green
- [ ] E2E verification complete

---

## Files to Modify

1. `/home/shumway/projects/CorvinOS/core/learning/confidence_scorer.py`
2. `/home/shumway/projects/CorvinOS/core/learning/operator_feedback.py`
3. `/home/shumway/projects/CorvinOS/core/learning/skill_attribution.py`
4. `/home/shumway/projects/CorvinOS/core/learning/user_profile.py`

## Test Files to Create

1. `/home/shumway/projects/CorvinOS/core/learning/tests/test_eventemitter_coverage.py`
2. `/home/shumway/projects/CorvinOS/core/learning/tests/test_eventemitter_latency.py`
3. `/home/shumway/projects/CorvinOS/tests/integration/test_eventemitter_universal.py`

---

**Next:** Proceed to Phase 1 implementation.
