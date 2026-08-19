# Iteration 2: ToolForgeSubsystem Integration — Blocking Analysis

**Status:** READY FOR IMPLEMENTATION  
**Date:** 2026-08-19  
**Phase:** Phase 1B (ADR-0321, Gap 1)  

---

## Executive Summary

All 5 blocking questions have clear answers. **Phase 1A (data structures) is COMPLETE.** Phase 1B can proceed immediately with these integration patterns:

| Question | Answer | Status | Action |
|---|---|---|---|
| Context Flow | ExecutionContext not yet passed to `_forge_exec()`; must be added via kwargs | 🟡 BLOCKER | Add `context: Optional[ExecutionContext]` parameter |
| EventEmitter Registration | EventEmitter exists but NOT a subsystem; must be created in ToolForgeSubsystem.startup() | 🟡 BLOCKER | Initialize in startup(), use hub tenant_id |
| Cost Estimation | Simple heuristic exists for `_forge_tool()`; `_forge_exec()` needs measurement | 🟡 BLOCKER | Use token count + model cost table |
| Error Context / error_class | Not currently captured; must infer from exception type or accept as kwarg | 🟠 WORKAROUND | Accept in kwargs for now; defer inference to Gap 2 |
| Operator Rating Event | UI not built; only event type defined | 🟢 DEFER | Just subscribe to event handler; emit happens in Gap 7 |

---

## Blocking Question Answers

### Question 1: Context Flow in `_forge_exec()`

**Current State:**
```python
async def _forge_exec(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Handle forge_exec request.
    Request schema: { name: str, input_data: dict }
    """
    tool_name = payload["name"]
    start = time.time()
    # ❌ NO ExecutionContext extraction
    output = await self.async_registry.forge_exec(tool_name, payload["input_data"])
```

**Finding:** ExecutionContext is initialized in `brain_startup.py` via MemoryCoordinator but NOT passed to subsystem request handlers.

**Required Change:**
```python
async def _forge_exec(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    # NEW: Extract context from kwargs
    context: Optional[ExecutionContext] = payload.get("context")
    session_id = context.session_id if context else "unknown"
    tenant_id = context.tenant_id if context else "_default"
    task_id = context.task_id if context else None
    turn_id = context.turn_id if context else None
```

**Files to Modify:**
- `core/orchestration/subsystems/tool_forge_subsystem.py`: Add context extraction in `_forge_exec()`
- Callers of `_forge_exec()` must pass `context` in payload

---

### Question 2: EventEmitter Registration

**Current State:**
- EventEmitter is instantiated in `core/console/corvin_console/standalone.py:L130-145` for token measurement
- It is **NOT** registered as a subsystem in SubsystemHub
- Pattern: `_emitter = EventEmitter(Path(_tenant_dir), _tenant_id)`

**Finding:** EventEmitter is a standalone component, not part of subsystem architecture. Each subsystem that needs it must initialize it.

**Required Pattern for ToolForgeSubsystem:**
```python
def startup(self, hub: Any) -> None:
    """Initialize subsystem and subscribe to events."""
    self.hub = hub
    
    # NEW: Initialize EventEmitter for learning events
    try:
        from core.learning.event_emitter import EventEmitter
        from pathlib import Path
        
        # Get tenant info (must be extracted from context or hub)
        tenant_id = getattr(hub, 'tenant_id', '_default')  # or get from session
        tenant_home = Path.home() / '.corvin' / 'tenants' / tenant_id
        
        self.event_emitter = EventEmitter(tenant_home, tenant_id, max_queue_size=1000)
        asyncio.create_task(self.event_emitter.start())
        logger.info("ToolForgeSubsystem: EventEmitter initialized")
    except Exception as e:
        logger.warning(f"ToolForgeSubsystem: EventEmitter init failed: {e}")
        self.event_emitter = None
```

**Files to Modify:**
- `core/orchestration/subsystems/tool_forge_subsystem.py`: Add EventEmitter initialization in `startup()`
- `core/orchestration/subsystems/tool_forge_subsystem.py`: Add `shutdown()` hook to clean up EventEmitter

**Dependency:** Must know tenant_id at startup time. This may come from:
1. `hub` attributes (if tenant_id is stored there)
2. First request context (deferred initialization)
3. Constructor parameter

---

### Question 3: Cost Estimation for `_forge_exec()`

**Current State (forge_tool):**
```python
@staticmethod
def _estimate_forge_cost(impl: str) -> float:
    """Estimate cost of forging a tool.
    Simple heuristic: 1 cost unit per 1000 characters.
    """
    return max(1.0, len(impl) / 1000.0)
```

**Finding:** forge_tool estimates cost from implementation size. forge_exec needs cost from execution tokens.

**Required Pattern for forge_exec:**
```python
async def _forge_exec(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    # ... execution ...
    # NEW: Extract token counts from result
    input_tokens = output.get('input_tokens', 0)
    output_tokens = output.get('output_tokens', 0)
    
    # Calculate cost: pricing_table[model][tokens]
    estimated_cost_cents = self._calculate_execution_cost(
        model_id=output.get('model', 'claude-opus-5'),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    
    # Record for telemetry
    self.last_execution_cost = estimated_cost_cents
```

**Implementation:** Need to create a cost table or defer to CostController:
```python
_COST_TABLE = {
    "claude-opus-5": {
        "input_per_1k_tokens": 15,   # $0.015 / 1K input
        "output_per_1k_tokens": 45,  # $0.045 / 1K output
    },
    # ... other models ...
}

def _calculate_execution_cost(self, model_id: str, input_tokens: int, output_tokens: int) -> int:
    """Calculate cost in cents."""
    pricing = _COST_TABLE.get(model_id, _COST_TABLE["claude-opus-5"])
    input_cost = (input_tokens / 1000) * pricing["input_per_1k_tokens"]
    output_cost = (output_tokens / 1000) * pricing["output_per_1k_tokens"]
    return int((input_cost + output_cost) * 100)  # Convert to cents
```

**Files to Modify:**
- `core/orchestration/subsystems/tool_forge_subsystem.py`: Add `_calculate_execution_cost()` method
- Update `_forge_exec()` to extract and calculate cost

---

### Question 4: Error Context (error_class)

**Current State:**
```python
async def _forge_exec(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        output = await self.async_registry.forge_exec(tool_name, payload["input_data"])
    except Exception as e:
        # ❌ No error_class captured; only logs
        logger.error(f"Failed to execute tool {tool_name}: {e}")
        raise
```

**Finding:** `error_class` is meant to track "what problem was this tool trying to solve?" (e.g., "MissingDependency", "PermissionDenied"). Not available from exception alone.

**Solution (Iteration 2):**
1. **Accept as kwarg:** Pass `error_class` from caller (LoopEngineer, Strategy Advisor, etc.)
   ```python
   error_class = payload.get("error_class")  # From caller
   ```

2. **Inference (defer to Gap 2):** Later, analyze tool description + error type to infer error_class

**Pattern:**
```python
async def _forge_exec(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    error_class = payload.get("error_class", None)  # From caller, or None
    
    try:
        output = await self.async_registry.forge_exec(tool_name, payload["input_data"])
        # ... success ...
    except Exception as e:
        # error_class passed by caller if known; None otherwise
        # Telemetry will capture both the exception type and error_class (if provided)
```

**Files to Modify:**
- `core/orchestration/subsystems/tool_forge_subsystem.py`: Extract `error_class` from payload in `_forge_exec()`
- Document in docstring that callers may pass `error_class` kwarg

---

### Question 5: Operator Rating Event & UI

**Current State:**
- Event type defined: `OPERATOR_RATED_TOOL = "operator.rated_tool"` in `event_schema.py`
- Payload type defined: `OperatorRatedToolPayload` (tool_id, rating 1-5, feedback_text, etc.)
- **No UI component** that emits this event
- **No handler** in ToolForgeSubsystem

**Finding:** This is **Gap 7** (Operator Feedback Loop Integration). For Iteration 2, just set up the subscription handler.

**Pattern:**
```python
def startup(self, hub: Any) -> None:
    """..."""
    # NEW: Subscribe to operator feedback
    hub.subscribe("operator.rated_tool", self.on_operator_rated_tool)

async def on_operator_rated_tool(
    self, event_name: str, event_data: Dict[str, Any]
) -> None:
    """Handle operator rating of a tool (Gap 7).
    
    For now: just log. When Gap 7 implements UI, this will store rating retroactively.
    """
    tool_id = event_data.get("tool_id")
    rating = event_data.get("rating", -1)
    feedback_text = event_data.get("feedback_text", "")
    
    logger.info(f"Operator rated tool {tool_id}: {rating}/5 — {feedback_text}")
    
    # TODO (Gap 7): Store rating in EventStore retroactively
    # This will correlate with prior TOOL_EXECUTED event via tool_id + session_id
```

**Files to Modify:**
- `core/orchestration/subsystems/tool_forge_subsystem.py`: Add subscription in `startup()`
- `core/orchestration/subsystems/tool_forge_subsystem.py`: Add `on_operator_rated_tool()` handler stub

---

## Integration Checklist for Iteration 2

### Phase 1B: ToolForgeSubsystem Integration (Days 3–5)

**Prerequisite: Phase 1A Complete ✅**
- [x] ToolExecutionTelemetry dataclass implemented
- [x] _sanitize_error_message() function implemented
- [x] _assert_safe() validator implemented
- [x] 8+ unit tests passing

**Phase 1B Tasks:**

- [ ] **Task 1: Context Flow** (Day 3)
  - Add `context: Optional[ExecutionContext]` extraction in `_forge_exec()`
  - Extract session_id, tenant_id, task_id, turn_id from context
  - Pass context to all helpers that need it
  - [ ] Test: `test_forge_exec_extracts_execution_context()`

- [ ] **Task 2: EventEmitter Initialization** (Day 3)
  - Import EventEmitter in `tool_forge_subsystem.py`
  - Initialize in `startup()` with tenant_id and tenant_home
  - Store reference: `self.event_emitter`
  - Add to `shutdown()` hook to stop event processing
  - [ ] Test: `test_tool_forge_subsystem_initializes_event_emitter()`

- [ ] **Task 3: Cost Estimation** (Day 3–4)
  - Create cost lookup table with model pricing (cents per 1K tokens)
  - Implement `_calculate_execution_cost()` method
  - Extract input/output tokens from forge_exec() result
  - Calculate estimated_cost_cents
  - [ ] Test: `test_calculate_execution_cost_matches_model_pricing()`

- [ ] **Task 4: Emit TOOL_EXECUTED Event** (Day 4)
  - Create ToolExecutionTelemetry in `_forge_exec()` after execution
  - Call `_assert_safe()` to validate
  - Wrap in LearningEvent with event_type = TOOL_EXECUTED
  - Call `event_emitter.emit(event)` (async, non-blocking)
  - [ ] Test: `test_forge_exec_emits_tool_executed_event()`
  - [ ] Test: `test_tool_executed_event_has_correct_payload()`

- [ ] **Task 5: Error Handling & Sanitization** (Day 4)
  - Catch exceptions in `_forge_exec()` before emitting
  - Capture error type, message, and status
  - Pass through ToolExecutionTelemetry validation (sanitization happens in __post_init__)
  - [ ] Test: `test_tool_execution_error_event_sanitized()`
  - [ ] Test: `test_tool_execution_timeout_status()`

- [ ] **Task 6: Operator Rating Handler** (Day 4–5)
  - Subscribe to "operator.rated_tool" in `startup()`
  - Implement `on_operator_rated_tool()` handler (stub for now)
  - Log operator feedback
  - [ ] Test: `test_tool_forge_subsystem_subscribes_to_operator_rated_tool()`

- [ ] **Task 7: Integration Tests** (Day 5)
  - [ ] `test_tool_execution_end_to_end()` — forge + exec + event emission
  - [ ] `test_event_emission_latency_under_50ms()` — overhead measurement
  - [ ] `test_cross_tenant_isolation()` — no leakage between tenants
  - [ ] `test_event_emitter_queue_full_graceful_degrade()` — drop handling

- [ ] **Task 8: Documentation & ADR** (Day 5)
  - Update ADR-0321 status: PROPOSED → ACCEPTED
  - Document event payload examples
  - Add operator runbook: "Understanding tool execution events"
  - [ ] ADR-0321 published
  - [ ] Integration guide written

---

## Files to Create/Modify

| File | Action | Reason |
|---|---|---|
| `core/orchestration/subsystems/tool_forge_subsystem.py` | **Modify** | Add context extraction, EventEmitter init, telemetry emission, rating handler |
| `tests/test_tool_forge_subsystem.py` | **Modify** | Add 4+ integration tests for event emission |
| `core/learning/tool_execution.py` | **No change** | Already complete (Phase 1A) |
| `core/learning/event_schema.py` | **No change** | Already has TOOL_EXECUTED, OPERATOR_RATED_TOOL |
| `docs/implementation/PHASE6_LEARNING_INTEGRATION_STATUS.md` | **Update** | Record Phase 1B completion |
| `Corvin-ADR/decisions/ADR-0321-tool-execution-learning-events.md` | **Update** | Change status to ACCEPTED |

---

## Success Criteria

✅ **Phase 1B Exit Criteria:**
- TOOL_EXECUTED events emitted at 100% rate (no drops for non-overloaded systems)
- Latency overhead <50ms p99 (measured with benchmark)
- Error sanitization verified (no PII in audit trail)
- Operator rating handler in place (even if UI is Gap 7)
- 4+ integration tests passing
- Cross-tenant isolation verified
- ADR-0321 marked ACCEPTED
- Zero regressions in tool execution

✅ **Unblocking Downstream:**
Once Phase 1B passes, Gaps 2–7 can proceed in parallel:
- Gap 2: Query TOOL_EXECUTED events for tool ranking
- Gap 4: Aggregate success rates
- Gap 3: Attribute outcomes
- Gap 5: Track tool coherence
- Gap 6: Learn cost models
- Gap 7: Operator feedback UI

---

## Timeline

| Day | Task | Status |
|---|---|---|
| Day 3 | Context flow + EventEmitter + Cost estimation | Ready to start |
| Day 4 | Event emission + Error handling + Rating handler | Dependent on Day 3 |
| Day 5 | Integration tests + Documentation + ADR acceptance | Final gates |

---

## Known Unknowns (To Be Resolved)

1. **Tenant ID at Startup:** How to get tenant_id in `startup()` if hub doesn't store it?
   - **Option A:** Hub stores tenant_id as attribute
   - **Option B:** Defer EventEmitter init until first request (lazy init)
   - **Option C:** Pass tenant_id as constructor parameter

2. **Async Context Propagation:** Are asyncio tasks losing ContextVars when EventEmitter worker runs?
   - **Mitigation:** EventEmitter queue is isolated; no context propagation needed

3. **Cost Table Versioning:** When pricing changes, how to handle existing events?
   - **Mitigation:** Store model_id + timestamp; lookup pricing at query time (not storage)

---

## References

- ADR-0321: Tool Execution Learning Events (PROPOSED → ACCEPTED pending Phase 1B)
- ADR-0314: Learning Infrastructure (EventEmitter, EventStore)
- ADR-0358: Context Engineering v2 (ExecutionContext)
- ADR-0361: API Registry (Loose coupling for subsystems)
- ADR-0365: Forge Quota Enforcement

---

**Next:** Address any unknowns above, then proceed to Day 3 implementation.
