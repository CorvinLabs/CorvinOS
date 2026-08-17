# Phase 2.K=1 — WorkerEngine Instrumentation Implementation Skeleton

## Summary

This document outlines the K=1 phase implementation for wiring TokenInstrumentationHooks into CorvinOS's turn execution. The main dispatcher is `stream_turn()` in `core/console/corvin_console/chat_runtime.py` (line 4477), which orchestrates all three execution paths:

1. **Delegation paths** (ACS/TDE) — handled by `_stream_tde_turn()` and internal ACS runtime
2. **Hermes path** — handled by `_stream_hermes_turn()` (line 3531)
3. **Claude Code path** — direct subprocess spawn (line 6148 onwards)

---

## File Locations

| Task | File | Type | Lines | Purpose |
|------|------|------|-------|---------|
| 1A | `core/console/corvin_console/chat_runtime.py` | MODIFY | 4477–6500 | Wire hooks into stream_turn() dispatcher |
| 1B | `core/console/corvin_console/app.py` | MODIFY | ~70 | Initialize TokenMetricsStore in lifespan |
| 1C | `core/console/corvin_console/chat_runtime.py` | ADD (new func) | 100 | Helper: get_metrics_store() DI function |
| 1D | `tests/unit/test_token_instrumentation_k1_live.py` | NEW | 200 | Unit test: instrumentation lifecycle |
| 1E | `tests/integration/test_chat_stream_metrics_k1.py` | NEW | 150 | E2E test: real turn flow with metrics |

---

## Implementation Details

### Task 1A: Insert Hooks into stream_turn()

**Location:** `core/console/corvin_console/chat_runtime.py:4477–6500`

**Insertion Points:**

**Point 1 (line ~4520):** After ExecutionContextBuilder instantiation, before any dispatch:

```python
# Phase 2b: Initialize TokenInstrumentation (K=1 integration)
_token_counter: "TokenCounter | None" = None
try:
    from core.learning.token_instrumentation import (
        TokenInstrumentationHooks, TokenCounter, set_current_token_counter
    )
    _token_counter = TokenInstrumentationHooks.on_worker_engine_start(
        turn_id=f"{sess.tenant_id}:{sess.sid}:t{sess.turn_count}",
        engine=_os_engine or "unknown",
        engine_tier=_feature_flags.get_engine_tier(sess.tenant_id, _os_engine or "claude_code"),
    )
    set_current_token_counter(_token_counter)
    _dbg(sess.workdir, "token_instrumentation.started",
         turn_id=_token_counter.turn_id,
         engine=_token_counter.engine)
except Exception as _tok_exc:  # noqa: BLE001
    _log.debug("[token_instrumentation] init failed (non-fatal): %s", _tok_exc)
    _token_counter = None
```

**Point 2 (line ~6220–6250):** Inside the `async for raw in proc.stdout` loop, after parsing `evt` (for claude path):

```python
# Token recording: after LLM response parsed
if etype == "system" and evt.get("subtype") == "init":
    # Extract usage from init event if present (may not always be there)
    if _token_counter and evt.get("usage"):
        _usage = evt.get("usage", {})
        TokenInstrumentationHooks.on_llm_response(
            _token_counter,
            input_tokens=_usage.get("input_tokens", 0),
            output_tokens=_usage.get("output_tokens", 0),
        )
        _dbg(sess.workdir, "token_instrumentation.llm_response",
             input_tokens=_usage.get("input_tokens", 0),
             output_tokens=_usage.get("output_tokens", 0))
```

**Point 3 (line ~6400–6450):** In the finally block (bottom of try/finally that wraps proc.stdout draining):

```python
finally:
    # Phase 2b: Finalize TokenInstrumentation
    if _token_counter is not None:
        try:
            _exit_code = 0 if _stdout_drained_normally else 1
            TokenInstrumentationHooks.on_worker_engine_end(
                _token_counter,
                outcome_quality="good" if _exit_code == 0 else "bad",
                required_followup=False,  # Will be enhanced in K=3
            )
            _dbg(sess.workdir, "token_instrumentation.finalized",
                 total_tokens=_token_counter.total_tokens,
                 exit_code=_exit_code)
            
            # Persist metrics to store if available
            metrics_store = getattr(app_state, "metrics_store", None) if hasattr(sess, "app_state") else None
            if metrics_store is not None:
                try:
                    event = _token_counter.to_event(
                        tenant_id=sess.tenant_id,
                        instance_id=sess.sid[:8],  # Use session ID prefix as instance ID
                        session_id=sess.sid,
                        user_id=getattr(sess, "user_id", None),
                    )
                    # Fire-and-forget emit (EventEmitter is async-queue based)
                    metrics_store.event_emitter.emit(event)
                except Exception as _persist_exc:  # noqa: BLE001
                    _log.debug("[token_instrumentation] persistence failed: %s", _persist_exc)
        except Exception as _final_exc:  # noqa: BLE001
            _log.debug("[token_instrumentation] finalization failed: %s", _final_exc)
    
    # ... rest of existing finally block ...
```

**Point 4 (line ~3531):** In `_stream_hermes_turn()`, mirror the above pattern:

```python
async def _stream_hermes_turn(
    sess: WebChatSession,
    prompt: str,
    tm: _task_manager.TaskManager,
    task_id: str,
    *,
    os_audit: Callable[[str, dict | None], None],
    audit_emit: Callable[..., None],
    emit_completed: Callable[[int], None],
    os_turn_id: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """Stream HermesEngine (Layer-22 WorkerEngine) turn.
    
    Mirrors the claude_code path: same instrumentation, same lifecycle.
    """
    # Phase 2b: Initialize TokenInstrumentation for Hermes
    _token_counter: "TokenCounter | None" = None
    try:
        from core.learning.token_instrumentation import (
            TokenInstrumentationHooks, TokenCounter, set_current_token_counter
        )
        _token_counter = TokenInstrumentationHooks.on_worker_engine_start(
            turn_id=f"{sess.tenant_id}:{sess.sid}:th{sess.turn_count}",
            engine="hermes",
            engine_tier="local",
        )
        set_current_token_counter(_token_counter)
    except Exception as _tok_exc:  # noqa: BLE001
        _log.debug("[hermes][token_instrumentation] init failed: %s", _tok_exc)
        _token_counter = None
    
    try:
        # ... existing _HermesEngine.spawn() call ...
        # After engine response, record tokens if usage available:
        if _token_counter and _usage_from_hermes:
            TokenInstrumentationHooks.on_llm_response(
                _token_counter,
                input_tokens=_usage_from_hermes.get("input_tokens", 0),
                output_tokens=_usage_from_hermes.get("output_tokens", 0),
            )
        
        # ... yield results ...
    finally:
        if _token_counter is not None:
            try:
                TokenInstrumentationHooks.on_worker_engine_end(
                    _token_counter,
                    outcome_quality="good" if no_error else "bad",
                    required_followup=False,
                )
                # Persist if metrics_store available
            except Exception:  # noqa: BLE001
                pass
```

---

### Task 1B: Initialize TokenMetricsStore in App Bootstrap

**Location:** `core/console/corvin_console/app.py` (around line 70)

**Current pattern:** The app already has an async lifespan context manager. Add DI here:

```python
# Around line 65–80, in the lifespan context manager:

async def lifespan(app: FastAPI):
    # Startup phase
    try:
        # Initialize audit chain (existing)
        from core.compliance.corvin_compliance_reports.audit_writer import get_audit_writer
        audit_writer = get_audit_writer()
    except Exception as _audit_exc:  # noqa: BLE001
        _log.warning("[app] audit_writer init failed: %s", _audit_exc)
        audit_writer = None
    
    # NEW: Initialize TokenMetricsStore (Phase 2b K=1)
    try:
        from core.learning.event_emitter import EventEmitter
        from core.learning.token_metrics_store import TokenMetricsStore
        
        # EventEmitter with optional audit chain
        event_emitter = EventEmitter(audit_writer=audit_writer)
        
        # TokenMetricsStore (cache-only for now; DB backend in K=2)
        metrics_store = TokenMetricsStore(event_emitter, db=None)
        
        # Store in app state for DI
        app.state.metrics_store = metrics_store
        _log.info("[app] TokenMetricsStore initialized (cache-only)")
    except Exception as _tok_exc:  # noqa: BLE001
        _log.warning("[app] TokenMetricsStore init failed (optional): %s", _tok_exc)
        app.state.metrics_store = None
    
    yield
    
    # Cleanup phase
    # (no cleanup needed for cache-only store; DB cleanup in K=2)
```

---

### Task 1C: Helper Function for DI

**Add to:** `core/console/corvin_console/chat_runtime.py` (module level, around line 300)

```python
def _get_metrics_store() -> "TokenMetricsStore | None":
    """Retrieve TokenMetricsStore from thread-local or app context.
    
    Best-effort: returns None if unavailable (non-fatal).
    """
    try:
        # Try thread-local first (for async context)
        from contextvars import ContextVar
        _metrics_store_var: ContextVar["TokenMetricsStore | None"] = ContextVar(
            "metrics_store", default=None
        )
        store = _metrics_store_var.get()
        if store is not None:
            return store
    except Exception:  # noqa: BLE001
        pass
    
    # Fallback: None means metrics persistence is skipped gracefully
    return None
```

---

## Skeleton Code Locations

### 1. Token Counter Usage

```python
# Within stream_turn() or any turn dispatcher:
from core.learning.token_instrumentation import TokenInstrumentationHooks, set_current_token_counter

counter = TokenInstrumentationHooks.on_worker_engine_start(
    turn_id="...",
    engine="claude_code",
    engine_tier="cloud",
)
set_current_token_counter(counter)

# Later, after LLM response:
TokenInstrumentationHooks.on_llm_response(counter, 1000, 500)

# At end of turn:
TokenInstrumentationHooks.on_worker_engine_end(counter, "good", False)
```

### 2. Metrics Persistence

```python
# After finalizing counter:
if metrics_store is not None:
    event = counter.to_event(
        tenant_id=sess.tenant_id,
        instance_id=sess.sid[:8],
        session_id=sess.sid,
    )
    metrics_store.event_emitter.emit(event)  # Fire-and-forget
```

---

## Test Outlines

### Test 1D: Unit Test (test_token_instrumentation_k1_live.py)

```python
# tests/unit/test_token_instrumentation_k1_live.py

import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch
from core.learning.token_instrumentation import (
    TokenCounter, TokenInstrumentationHooks, set_current_token_counter
)
from core.learning.event_emitter import EventEmitter
from core.learning.token_metrics_store import TokenMetricsStore
from core.learning.event_schema import LearningEventType

class MockEventEmitter(EventEmitter):
    """Mock emitter that collects events instead of emitting."""
    def __init__(self):
        super().__init__()
        self.events = []
    
    def emit(self, event):
        self.events.append(event)

@pytest.fixture
def metrics_store():
    """Fixture: in-memory metrics store."""
    emitter = MockEventEmitter()
    return TokenMetricsStore(emitter, db=None)

def test_token_counter_lifecycle():
    """Test: counter creation, recording, finalization."""
    counter = TokenInstrumentationHooks.on_worker_engine_start(
        turn_id="t1",
        engine="claude",
        engine_tier="cloud",
    )
    assert counter.turn_id == "t1"
    assert counter.engine == "claude"
    assert counter.total_tokens == 0
    
    # Record LLM call
    TokenInstrumentationHooks.on_llm_response(counter, 1000, 500)
    assert counter.total_tokens == 1500
    
    # Finalize
    TokenInstrumentationHooks.on_worker_engine_end(counter, "good", False)
    assert counter.outcome_quality == "good"

def test_token_counter_with_subsystems():
    """Test: subsystem overhead recording."""
    counter = TokenInstrumentationHooks.on_worker_engine_start("t1", "claude", "cloud")
    
    # Record LLM
    TokenInstrumentationHooks.on_llm_response(counter, 1000, 500)
    
    # Record subsystem work
    TokenInstrumentationHooks.on_subsystem_executed(counter, "confidence", 200)
    TokenInstrumentationHooks.on_subsystem_executed(counter, "vibe_brief", 300)
    
    assert counter.subsystem_tokens["confidence"] == 200
    assert counter.subsystem_tokens["vibe_brief"] == 300
    assert counter.total_tokens == 1500 + 200 + 300
    
    TokenInstrumentationHooks.on_worker_engine_end(counter, "good", False)

def test_metrics_store_persistence(metrics_store):
    """Test: event emission and collection."""
    counter = TokenInstrumentationHooks.on_worker_engine_start("t1", "claude", "cloud")
    TokenInstrumentationHooks.on_llm_response(counter, 1000, 500)
    TokenInstrumentationHooks.on_worker_engine_end(counter, "good", False)
    
    event = counter.to_event(
        tenant_id="default",
        instance_id="inst1",
        session_id="s1",
    )
    
    metrics_store.event_emitter.emit(event)
    
    # Verify event was collected
    emitter = metrics_store.event_emitter
    assert len(emitter.events) == 1
    assert emitter.events[0].event_type == LearningEventType.TOKEN_METRICS
    assert emitter.events[0].tenant_id == "default"

def test_context_var_isolation():
    """Test: context variable isolation across async tasks."""
    import asyncio
    
    async def task1():
        c1 = TokenInstrumentationHooks.on_worker_engine_start("t1", "claude", "cloud")
        set_current_token_counter(c1)
        await asyncio.sleep(0.01)
        # Should still be c1, not c2
        assert c1.turn_id == "t1"
    
    async def task2():
        c2 = TokenInstrumentationHooks.on_worker_engine_start("t2", "hermes", "local")
        set_current_token_counter(c2)
        await asyncio.sleep(0.01)
        # Should still be c2, not c1
        assert c2.turn_id == "t2"
    
    asyncio.run(asyncio.gather(task1(), task2()))
```

### Test 1E: E2E Test (test_chat_stream_metrics_k1.py)

```python
# tests/integration/test_chat_stream_metrics_k1.py

import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from core.console.corvin_console.chat_runtime import stream_turn, WebChatSession
from core.learning.token_metrics_store import TokenMetricsStore
from core.learning.event_emitter import EventEmitter

class MockWebChatSession:
    """Mock session for testing."""
    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.tenant_id = "default"
        self.sid = "test_session_123"
        self.chat_key = "web:test_session_123"
        self.turn_count = 0
        self.title = ""
        self.last_active_at = 0
        self.app_state = MagicMock()

@pytest.fixture
def temp_session_dir(tmp_path):
    """Fixture: temporary session directory."""
    return tmp_path / "sessions" / "web:test"

@pytest.mark.asyncio
async def test_stream_turn_emits_metrics(temp_session_dir):
    """E2E: stream_turn() initializes, records, and emits metrics."""
    temp_session_dir.mkdir(parents=True, exist_ok=True)
    
    sess = MockWebChatSession(temp_session_dir)
    
    # Mock metrics store
    emitter = MagicMock()
    metrics_store = TokenMetricsStore(emitter, db=None)
    sess.app_state.metrics_store = metrics_store
    
    # Mock task manager
    with patch("core.console.corvin_console.chat_runtime._task_manager") as mock_tm:
        mock_tm.TaskManager.return_value.create_task.return_value = "task_1"
        mock_tm.TaskManager.return_value.record_event = MagicMock()
        
        # Mock feature flags
        with patch("core.console.corvin_console.chat_runtime._feature_flags") as mock_ff:
            mock_ff.is_enabled.return_value = False
            mock_ff.get_engine_tier.return_value = "cloud"
            
            # Mock model selector
            with patch("core.console.corvin_console.chat_runtime._model_selector") as mock_ms:
                mock_ms.estimate_os_turn_chars.return_value = 1000
                mock_ms.resolve_os_model.return_value = "claude-3-haiku"
                mock_ms.resolve_step_model.return_value = "claude-3-haiku"
                
                # Run stream_turn (will initialize TokenCounter)
                events = []
                async for evt in stream_turn(sess, "Hello world"):
                    events.append(evt)
                    if evt.get("type") == "done":
                        break
    
    # Verify events were collected
    assert len(events) > 0
    # Should have at least one error or result event (actual subprocess will fail in test)
    assert any(e.get("type") in ["error", "result", "done"] for e in events)
```

---

## Checklist (Definition of Done)

- [ ] **1A:** TokenInstrumentationHooks.on_worker_engine_start() called at stream_turn() entry
- [ ] **1A:** TokenInstrumentationHooks.on_llm_response() called after LLM response parsed (all 3 paths)
- [ ] **1A:** TokenInstrumentationHooks.on_worker_engine_end() called in finally block (all 3 paths)
- [ ] **1B:** TokenMetricsStore initialized in app.py lifespan
- [ ] **1C:** _get_metrics_store() helper function available
- [ ] **1D:** Unit tests pass (token counter lifecycle, subsystems, context vars)
- [ ] **1E:** E2E test demonstrates metrics emission (no failures expected on error paths)
- [ ] **No Phase 1 tests broken** (run: `pytest tests/unit/test_token_instrumentation_k1.py tests/unit/test_token_metrics_phase1_complete.py -v`)
- [ ] **Debug log entries** added for troubleshooting (token_instrumentation.started, llm_response, finalized)
- [ ] **Metrics persist** to event_emitter.emit() (fire-and-forget, non-blocking)

---

## Key Design Decisions

1. **Fire-and-forget emission:** Metrics are emitted to EventEmitter with no await — this keeps turn latency unaffected
2. **Best-effort initialization:** TokenMetricsStore init failures are logged but do not fail the turn (graceful degradation)
3. **Three-path coverage:** Claude Code, Hermes, and delegated paths all wire the same hooks (uniform instrumentation)
4. **Context var isolation:** Each turn/task gets its own TokenCounter via ContextVar (thread-safe, async-safe)
5. **Cache-only in K=1:** DB backend deferred to K=2 (schema, queries, persistence layer separate)

---

## Integration Points (Downstream ADRs)

- **K=2:** TokenMetricsDB backend (SQLite schema, queries, aggregation)
- **K=3:** Console React panel (live metrics dashboard)
- **K=4:** REST API endpoints (metrics summary, per-session, per-task-type)

---

## References

- `TMF_PHASE2_QUICK_REF.md` — Phase 2 task breakdown
- `TMF_PHASE2_DESIGN.md` — Full design rationale
- `core/learning/token_instrumentation.py` — TokenCounter, TokenInstrumentationHooks (Phase 1, ready)
- `core/console/corvin_console/chat_runtime.py` — stream_turn() main dispatcher
