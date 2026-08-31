# TreeOfThoughts Phase 7c — Live Wiring Guide

**Status:** ✅ PRODUCTION READY  
**Date:** 2026-08-17  
**Scope:** Wire active learning into chat_runtime.py + TTS paths

## Overview

TreeOfThoughts is built and deployed (Phase 1-7d complete). Phase 7c activates **live confidence tracking** by wrapping execution paths with `ChatLearningWrapper` and TTS metrics collection.

After Phase 7c, every chat turn and TTS call automatically updates pattern confidence in the dashboard.

## Architecture

### Chat Turn Path

```
user message
  ↓
chat_runtime.py::stream_turn()
  ↓
ChatLearningWrapper.stream_turn_with_learning()  [PHASE 7C]
  ├── tracks latency_ms
  ├── counts tokens (heuristic: len / 4)
  ├── records success/error_type
  └── emits ExecutionMetrics → confidence update
  ↓
dashboard shows updated confidence
```

### TTS Path (say.py)

```
speak(text, provider, voice)
  ↓
execute_with_learning("pattern_tts_{provider}")  [PHASE 7C]
  ├── calls say.py subprocess
  ├── tracks provider success rates
  └── updates TTS pattern confidence
  ↓
dashboard shows "OpenAI TTS (0.88)", "Edge TTS (0.72)", etc.
```

## Implementation Steps (Phase 7c)

### Step 1: Wire ChatLearningWrapper into stream_turn

**File:** `core/console/corvin_console/chat_runtime.py`  
**Location:** Around line 4432 (stream_turn function)

```python
# At top of file, add import:
from .chat_learning_wrapper import get_chat_learning_wrapper

# Inside stream_turn, wrap the execution:
async def stream_turn(...):
    wrapper = get_chat_learning_wrapper(session.tenant_id)
    
    # Instead of:
    # async for event in _stream_claude_turn(...):
    #     yield event
    
    # Do:
    async for event in wrapper.stream_turn_with_learning(
        stream_turn_fn=_stream_claude_turn,
        chat_key=chat_key,
        messages=messages,
        system_prompt=system_prompt,
        # ... other kwargs
    ):
        yield event
```

**Impact:** Every chat turn now emits learning events. Pattern `pattern_chat_turn_execution` confidence updates based on success/failure.

### Step 2: Wire TTS metrics into say.py caller

**File:** `core/console/corvin_console/chat_runtime.py` or wherever TTS is called  
**Pattern:** Wrap subprocess call with metrics collection

```python
from core.learning import LearningIntegration

async def synthesize_with_learning(text, lang, voice, provider):
    integration = LearningIntegration()
    
    try:
        result = await run_tts_subprocess(text, lang, voice, provider)
        success = True
    except Exception as e:
        success = False
        result = None
    
    # Record metrics
    metrics = ExecutionMetrics(
        subject_id=f"pattern_tts_{provider}",
        latency_ms=elapsed,
        success=success,
        context={"lang": lang, "provider": provider}
    )
    integration.metrics.record(metrics)
    
    return result
```

**Impact:** Each provider's confidence tracked independently. Dashboard shows which providers are most reliable.

### Step 3: E2E Test

Create `tests/test_learning_phase7c_live.py`:

```python
@pytest.mark.asyncio
async def test_chat_turn_emits_learning_event():
    """E2E: Chat turn execution updates confidence."""
    wrapper = get_chat_learning_wrapper()
    
    # Mock stream_turn
    async def mock_stream():
        yield {"type": "delta", "text": "Hello"}
    
    # Wrap and execute
    events = []
    async for event in wrapper.stream_turn_with_learning(
        stream_turn_fn=mock_stream,
        chat_key="test-chat",
        messages=[],
        system_prompt="test"
    ):
        events.append(event)
    
    # Verify event was recorded
    node = wrapper.integration.store.get_node("pattern_chat_turn_execution")
    assert node is not None
    assert node.calls_in_production >= 1
```

## Rollout Plan

### Week 1 (Canary)
- Deploy Phase 7c to 10% of instances
- Monitor confidence updates in dashboard
- Verify no performance degradation

### Week 2 (Ramp)
- 50% rollout
- Operators start seeing real data
- Adjust metrics collection if needed

### Week 3 (Full)
- 100% production
- All patterns have live confidence scores
- Dashboard shows real usage patterns

## Metrics to Monitor

After Phase 7c deployment, watch:

- **Chat turn confidence:** Should converge toward 0.7-0.85 for stable patterns
- **TTS provider confidence:** OpenAI ~0.88, Edge ~0.72 (depends on usage)
- **Error rate:** Should stay < 5% (confidence delta = -0.15 per error)
- **Dashboard latency:** Should stay < 100ms (lazy-load on demand)

## Rollback Plan

If issues arise:
1. Disable `ChatLearningWrapper` calls (comment out wrapper code)
2. Dashboard still works (frozen confidence scores)
3. No data loss (all events in audit log)
4. Can re-enable at any time

## Success Criteria

Phase 7c is complete when:
- ✅ Chat turns emit learning events
- ✅ TTS calls tracked per provider
- ✅ Dashboard shows updated confidence scores
- ✅ No performance degradation (< 5ms overhead per turn)
- ✅ E2E test passes (live confidence update proven)

## References

- **ADR-0365:** TreeOfThoughts Unified Learning Hierarchy
- **ADR-0366:** Reachability Proof & E2E Integration
- **ADR-0367:** Console Dashboard & Active Learning Loop
- **Code:** `core/learning/chat_learning_wrapper.py`, `core/console/corvin_console/routes/learning.py`
