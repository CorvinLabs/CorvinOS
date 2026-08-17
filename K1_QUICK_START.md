# Phase 2.K=1 Quick Start — WorkerEngine Instrumentation

**Status:** Skeleton implementation complete. Ready for wiring into chat_runtime.py.

**Deliverables:**
- `K1_IMPLEMENTATION_SKELETON.md` — Full design, insertion points, code samples
- `tests/unit/test_token_instrumentation_k1_live_SKELETON.py` — Unit test outline
- `tests/integration/test_chat_stream_metrics_k1_SKELETON.py` — E2E test outline

---

## 3-Minute Summary

**Goal:** Wire TokenInstrumentationHooks into `stream_turn()` so every turn records token usage.

**Three Hook Points:**

```python
# 1. START (line ~4520 in stream_turn)
counter = TokenInstrumentationHooks.on_worker_engine_start(
    turn_id="...", engine="claude_code", engine_tier="cloud"
)
set_current_token_counter(counter)

# 2. RECORD (line ~6220, inside subprocess stdout loop)
TokenInstrumentationHooks.on_llm_response(counter, input_tokens=1000, output_tokens=500)

# 3. END (line ~6450, in finally block)
TokenInstrumentationHooks.on_worker_engine_end(counter, outcome_quality="good", required_followup=False)

# PERSIST (after finalize)
if metrics_store:
    event = counter.to_event(tenant_id="default", instance_id="...", session_id="...")
    metrics_store.event_emitter.emit(event)  # Fire-and-forget
```

**Three Paths to Instrument:**
1. Claude Code (`stream_turn()` → subprocess path)
2. Hermes (`_stream_hermes_turn()` → WorkerEngine path)
3. Delegation (`_stream_tde_turn()` → TDE/ACS paths)

---

## Files to Create/Modify

| # | File | Action | Lines | Effort |
|---|------|--------|-------|--------|
| 1A | `core/console/corvin_console/chat_runtime.py` | MODIFY | 4520, 6220, 6450 | 50 LOC |
| 1A | `core/console/corvin_console/chat_runtime.py` | MODIFY (Hermes) | 3531+ | 30 LOC |
| 1B | `core/console/corvin_console/app.py` | MODIFY | ~70 | 15 LOC |
| 1C | `core/console/corvin_console/chat_runtime.py` | ADD (helper) | N/A | 10 LOC |
| 1D | `tests/unit/test_token_instrumentation_k1_live_SKELETON.py` | NEW | 200 | Copy & rename |
| 1E | `tests/integration/test_chat_stream_metrics_k1_SKELETON.py` | NEW | 150 | Copy & rename |

**Total:** ~105 LOC of actual implementation + 350 LOC tests.

---

## Step-by-Step

### Step 1: Read Reference Files

1. Open `K1_IMPLEMENTATION_SKELETON.md`
2. Locate the three insertion points in `chat_runtime.py` (lines 4520, 6220, 6450)
3. Review the skeleton code samples

### Step 2: Wire stream_turn() (5 min)

**Point 1 (line ~4520):** After `_exec_ctx_builder = ExecutionContextBuilder(...)`

Copy the "Phase 2b: Initialize TokenInstrumentation" block from the skeleton:

```python
_token_counter: "TokenCounter | None" = None
try:
    from core.learning.token_instrumentation import (
        TokenInstrumentationHooks, TokenCounter, set_current_token_counter
    )
    _token_counter = TokenInstrumentationHooks.on_worker_engine_start(
        turn_id=f"{sess.tenant_id}:{sess.sid}:t{sess.turn_count}",
        engine=_os_engine or "unknown",
        engine_tier="cloud",  # TODO: fetch from feature_flags
    )
    set_current_token_counter(_token_counter)
except Exception as _tok_exc:
    _log.debug("[token_instrumentation] init failed: %s", _tok_exc)
    _token_counter = None
```

**Point 2 (line ~6220):** Inside `async for raw in proc.stdout:` loop, after `evt = json.loads(line)`

Add (before the `etype == "system"` check):

```python
# Token recording: capture LLM response metadata
if etype == "system" and evt.get("subtype") == "init" and _token_counter:
    if evt.get("usage"):
        TokenInstrumentationHooks.on_llm_response(
            _token_counter,
            input_tokens=evt["usage"].get("input_tokens", 0),
            output_tokens=evt["usage"].get("output_tokens", 0),
        )
```

**Point 3 (line ~6450):** In the `finally:` block of the subprocess handling

Add (before existing finally cleanup):

```python
# Phase 2b: Finalize TokenInstrumentation
if _token_counter is not None:
    try:
        TokenInstrumentationHooks.on_worker_engine_end(
            _token_counter,
            outcome_quality="good" if _stdout_drained_normally else "bad",
            required_followup=False,
        )
        # Persist metrics if store available
        if hasattr(sess, "app_state") and hasattr(sess.app_state, "metrics_store"):
            metrics_store = sess.app_state.metrics_store
            if metrics_store is not None:
                try:
                    event = _token_counter.to_event(
                        tenant_id=sess.tenant_id,
                        instance_id=sess.sid[:8],
                        session_id=sess.sid,
                    )
                    metrics_store.event_emitter.emit(event)
                except Exception:
                    pass  # Best-effort; don't break turn on persist failure
    except Exception as _final_exc:
        _log.debug("[token_instrumentation] finalization failed: %s", _final_exc)
```

### Step 3: Mirror Hermes Path (3 min)

**In `_stream_hermes_turn()` (line 3531):**

Wrap the hermes spawn call with the same three hooks:

```python
_token_counter = None
try:
    from core.learning.token_instrumentation import TokenInstrumentationHooks
    _token_counter = TokenInstrumentationHooks.on_worker_engine_start(
        turn_id=f"{sess.tenant_id}:{sess.sid}:th{sess.turn_count}",
        engine="hermes",
        engine_tier="local",
    )
except Exception:
    pass

try:
    # ... existing _HermesEngine.spawn() call ...
    response = ...
    
    # Record tokens if available
    if _token_counter and hasattr(response, "usage"):
        TokenInstrumentationHooks.on_llm_response(
            _token_counter,
            input_tokens=response.usage.get("input_tokens", 0),
            output_tokens=response.usage.get("output_tokens", 0),
        )
finally:
    if _token_counter:
        TokenInstrumentationHooks.on_worker_engine_end(_token_counter, "good", False)
```

### Step 4: Initialize Store in app.py (2 min)

**In `core/console/corvin_console/app.py`, in the lifespan context manager:**

```python
try:
    from core.learning.event_emitter import EventEmitter
    from core.learning.token_metrics_store import TokenMetricsStore
    
    event_emitter = EventEmitter(audit_writer=audit_writer)
    metrics_store = TokenMetricsStore(event_emitter, db=None)
    app.state.metrics_store = metrics_store
except Exception:
    app.state.metrics_store = None
```

### Step 5: Run Tests

```bash
# Unit tests (Phase 1 components, no chat_runtime dependency)
pytest tests/unit/test_token_instrumentation_k1_live_SKELETON.py::TestTokenCounterLifecycle -v

# Copy skeleton to real test file after implementation
cp tests/unit/test_token_instrumentation_k1_live_SKELETON.py tests/unit/test_token_instrumentation_k1_live.py

# Then update real test file and run:
pytest tests/unit/test_token_instrumentation_k1_live.py -v
pytest tests/integration/test_chat_stream_metrics_k1_SKELETON.py::TestStreamTurnInstrumentation -v

# Verify Phase 1 tests still pass
pytest tests/unit/test_token_instrumentation_k1.py tests/unit/test_token_metrics_phase1_complete.py -v
```

---

## Verification Checklist

**After wiring, verify:**

- [ ] `pytest tests/unit/test_token_instrumentation_k1_live.py::TestTokenCounterLifecycle -v` → PASS
- [ ] `pytest tests/integration/test_chat_stream_metrics_k1_SKELETON.py::TestStreamTurnInstrumentation::test_stream_turn_starts_counter -v` → PASS
- [ ] `pytest tests/unit/test_token_metrics_phase1_complete.py -v` → PASS (Phase 1 unbroken)
- [ ] Run console: `npm run dev` (in web-next), then chat normally
- [ ] Check workdir `chat_debug.jsonl` for events: `token_instrumentation.started`, `llm_response`, `finalized`
- [ ] Verify no performance regression (<1ms per hook call)

**Manual E2E:**

1. Start console: `npm run dev` (web-next directory)
2. Login, start new chat
3. Send prompt: "Hello, how are you?"
4. Check `~/.corvin/tenants/_default/sessions/web:<sid>/chat_debug.jsonl` for:
   ```json
   {"ts": "...", "event": "token_instrumentation.started", "turn_id": "...", "engine": "claude_code"}
   {"ts": "...", "event": "token_instrumentation.llm_response", "input_tokens": 123, "output_tokens": 456}
   {"ts": "...", "event": "token_instrumentation.finalized", "total_tokens": 579}
   ```

---

## Known Limitations (Phase 1 → Phase 2.K=1)

1. **No DB persistence yet** — metrics stay in memory only (cache-only)
   - Solution: K=2 adds SQLite backend
2. **No console panel yet** — metrics not visible in UI
   - Solution: K=3 adds React components
3. **No API endpoints yet** — metrics not queryable via REST
   - Solution: K=4 adds endpoints
4. **Hermes usage metadata TBD** — may not always be available from HermesEngine
   - Workaround: record as 0 if unavailable, upgrade when available

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `AttributeError: 'NoneType' has no attribute 'event_emitter'` | metrics_store not initialized in app.py | Add DI in lifespan (Step 4) |
| Import error: `ModuleNotFoundError: core.learning` | Phase 1 modules not installed | Verify `core/learning/*.py` exists |
| Debug events not in `chat_debug.jsonl` | Hooks not called | Verify insertion points (Step 2) |
| `pytest: test_token_instrumentation_k1_live.py not found` | Skeleton not renamed | `cp test_token_instrumentation_k1_live_SKELETON.py test_token_instrumentation_k1_live.py` |
| Test FAILED: `Fixture 'mock_metrics_store' not found` | pytest fixtures not loaded | Ensure conftest.py or fixtures in same file |

---

## References

- Full skeleton: `K1_IMPLEMENTATION_SKELETON.md`
- Phase 2 design: `TMF_PHASE2_DESIGN.md`
- Phase 2 quick ref: `TMF_PHASE2_QUICK_REF.md`
- Token instrumentation (Phase 1): `core/learning/token_instrumentation.py`
- Event schema (Phase 1): `core/learning/event_schema.py`

---

## Timeline

- **Skeleton complete:** Ready for implementation (this document)
- **Step 1–5 duration:** ~20 minutes
- **Unit test duration:** ~10 minutes
- **E2E manual test:** ~5 minutes
- **Total effort:** ~35 minutes
- **Definition of done:** All checks green + `chat_debug.jsonl` has instrumentation events

---

## Next: K=2

Once K=1 is complete and tested:

```bash
git commit -m "feat(token_instrumentation): K=1 WorkerEngine hooks wired

- stream_turn() initializes TokenCounter at start
- claude/hermes paths record LLM tokens
- All paths finalize and emit to EventEmitter
- chat_debug.jsonl captures instrumentation events

Ref: K1_IMPLEMENTATION_SKELETON.md
Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

Then proceed to K=2 (DB backend + schema):
- `core/learning/token_metrics_db.py` (SqliteMetricsDB)
- `core/learning/token_metrics_db_factory.py` (environment-aware backend selection)
- Upgrade TokenMetricsStore to use DB backend
- Create tests: `test_token_metrics_db_k2.py`
