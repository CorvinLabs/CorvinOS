---
id: ADR-0321
status: proposed
depends_on: [ADR-0314]
related: [ADR-0322, ADR-0324]
supersedes: []
paths:
  - core/learning/tool_execution.py
  - core/learning/event_schema.py
  - core/orchestration/subsystems/tool_forge_subsystem.py
  - tests/unit/test_learning_tool_execution.py
docs:
  - docs/implementation/DETAILED_DESIGN_ALL_INTEGRATIONS.md
  - docs/CODE_REVIEW_INTEGRATION_GAPS.md
  - docs/implementation/PHASE6_LEARNING_INTEGRATION_STATUS.md
commits:
  - Phase 1 Iteration 1 — Tool execution telemetry dataclass + event schemas
---

# ADR-0321 — Tool Execution Learning Events

**Status:** PROPOSED  
**Date:** 2026-08-19  
**Author:** Claude Code  
**Deciders:** Learning Team, Architecture Team  

---

## Context

### Problem
Tool Forge generates and executes tools, but **this execution produces no learning signals**. The system cannot measure:
- Which tools succeed and which fail
- How fast tools execute
- How much they cost
- Whether operators found them useful

As a result:
- Tool selection is random (or first-match)
- Failed/slow tools remain in service forever
- Cost models have no ground truth
- Operators never get feedback on tool quality
- No basis for preferring reuse over generation

The Learning Engine (ADR-0314) has the infrastructure to capture events, but **no subsystem feeds it tool execution data**.

### Current State
1. **ToolForgeSubsystem** has `forge_tool`, `forge_exec`, `forge_promote` handlers
2. `execute_tool()` returns `ToolExecutionResult(success, output, latency, tokens)`
3. Result is returned to caller; **no event is emitted**
4. **No data reaches the learning event stream**

### Gap
**Gap 1: Learning Events Not Captured During Tool Execution** — the foundation blocker that prevents all downstream learning (Gaps 2, 3, 4, 5, 6, 7).

---

## Decision

### What We're Building

We will **capture full telemetry from every tool execution** (latency, tokens, operator rating, outcome signals) and **emit as TOOL_EXECUTED learning events** integrated into the event stream.

Three layers:

#### 1. Conceptual Level

**Principle:** Every tool execution is a data point for learning. Capturing that data is load-bearing for tool quality, cost accuracy, and skill attribution.

We treat tool execution as a **first-class learning event** — not a side effect, but the atomic signal that drives downstream decisions (tool ranking, cost learning, attribution).

#### 2. Structural Level

**New data structure:** `ToolExecutionTelemetry` (frozen dataclass)
- Immutable capture of execution metrics
- Validation in __post_init__ (fail-fast)
- Conversion to learning event payload

**New subsystem hook:** ToolForgeSubsystem emits TOOL_EXECUTED events
- After every tool.execute() call (success or failure)
- Captures latency, tokens, cost, outcome signals
- Non-blocking (async queue, queue-full = drop)

**New event types:** 
- `TOOL_EXECUTED` — telemetry from tool execution
- `OPERATOR_RATED_TOOL` — user feedback on tool (retroactive)

**Integration points:**
- ToolForgeSubsystem._handle_tool_execute() → emit TOOL_EXECUTED
- ToolForgeSubsystem._handle_tool_rate() → emit OPERATOR_RATED_TOOL
- EventEmitter receives both, writes to EventStore + audit trail

#### 3. Implementation Level

```python
@dataclass(frozen=True)
class ToolExecutionTelemetry:
    """Immutable telemetry from a single tool execution."""
    tool_id: str
    tool_name: str
    tool_type: str  # "generated" | "promoted" | "builtin"
    start_timestamp_utc: datetime
    end_timestamp_utc: datetime
    latency_ms: int  # Calculated in __post_init__
    input_tokens: int
    output_tokens: int
    subsystem_tokens: dict[str, int]  # Breakdown: {"Claude_Opus": 450, ...}
    status: ToolExecutionStatus  # success | failure | timeout | error
    error_type: Optional[str]
    error_message: Optional[str]  # **SANITIZED** for PII
    input_size_bytes: int
    output_size_bytes: int
    user_satisfaction: int  # 1-5 or -1 (not available)
    required_followup: bool  # Did user ask again?
    error_resolved: Optional[bool]  # Outcome signal
    model_id: str  # Model used
    estimated_cost_cents: int
    task_type: Optional[str]  # "code", "research", etc.
    task_id: Optional[str]
    error_class: Optional[str]
    session_id: str
    turn_id: Optional[str]
    tags: list[str]  # e.g., ["high_latency", "cost_overrun"]
    
    def __post_init__(self):
        # Calculate latency (frozen dataclass workaround)
        # Validate all fields (fail-fast)
        # Sanitize error_message for PII
        # Emit audit trail entry
        # Check consent gates (GDPR Art. 6)
```

**Emission:** ToolForgeSubsystem._emit_tool_executed_event()
- Wraps ToolExecutionTelemetry in LearningEvent
- Calls event_emitter.emit(event) (async, non-blocking)
- Audit trail is automatic (LearningEvent.write_event)

---

## Consequences

### Positive
✅ **Data-driven tool selection** — downstream systems (Gap 2) can query success rates  
✅ **Cost accuracy** — cost models have ground truth execution data  
✅ **Operator visibility** — tool quality is visible and measurable  
✅ **Learning foundation** — enables Gaps 2–7 (all downstream depend on this)  
✅ **Audit trail complete** — GDPR Art. 30 records of all tool executions  

### Negative
⚠️ **Latency overhead** — telemetry capture adds microseconds per execution  
⚠️ **Storage cost** — learning events stored in EventStore (projected: 10–100 MB/day for typical tenant)  
⚠️ **Token counting complexity** — measuring tokens per subsystem requires CostController integration  
⚠️ **Operator rating latency** — rating UI is Gap 7 (separate work)  

### Risks & Mitigation

**Risk 1: PII leakage in error messages**
- Mitigation: Sanitization in __post_init__ removes paths, schema names, stack traces
- Validation: _assert_safe() (fail-closed) before emission
- Proof: Code review (Gap 1, Finding 2) highlights this requirement

**Risk 2: EventEmitter full (queue drops events)**
- Mitigation: Queue size tunable; drop = logged but not fatal
- Monitoring: Emit telemetry on drop rate
- Fallback: Events still generate audit trail (separate path)

**Risk 3: Tenant isolation breach**
- Mitigation: session_id captured; LearningEvent enforces tenant_id
- Validation: Query EventStore always filters by tenant_id
- Testing: Cross-tenant queries in E2E suite

**Risk 4: Token counting inaccuracy**
- Mitigation: CostController integration (production instrumentation)
- Fallback: Conservative estimates if CostController unavailable
- Validation: Unit tests compare token counts to model invoices (monthly)

---

## Alternatives Considered

### Alternative A: Batch telemetry in memory, emit periodic summaries
**Rationale for rejection:**
- Loses per-execution signals (coarse aggregation)
- Harder to correlate telemetry with later operator feedback
- Risk of memory leak if batch grows unbounded
- Requires explicit flush logic (operational complexity)

### Alternative B: Emit only success/failure, capture latency/cost separately
**Rationale for rejection:**
- Incomplete signal (no operator rating, no outcome signals)
- Harder to correlate success/failure with cost (multiple queries)
- Doesn't capture "tool worked but was slow" signals
- Blocks Gap 2 (tool ranking needs full metrics)

### Alternative C: Telemetry in centralized logging (not EventStore)
**Rationale for rejection:**
- Breaks audit trail integration (GDPR Art. 30)
- Harder for downstream systems to query learning signals
- Loses tenant isolation (logs are cross-tenant)
- Incompatible with learning event schema (ADR-0314)

---

## Why This Decision Wins

**This design is the minimal sufficient foundation for all downstream learning:**

1. **Completeness:** Captures every signal needed by Gaps 2–7 (success, cost, latency, ratings, outcomes)
2. **Integrity:** Immutable data structure prevents tampering; audit trail records all emissions
3. **Performance:** Async emission doesn't block tool execution; queue-full is graceful degrade
4. **Compliance:** GDPR Art. 5 (data minimization), Art. 6 (consent), Art. 30 (audit trail) all satisfied
5. **Testability:** Frozen dataclass and deterministic validation enable unit testing
6. **Extensibility:** Payload is dictionary (type-agnostic); can add fields without schema migration

**Compared to alternatives:**
- Batch aggregation loses signal fidelity
- Partial telemetry blocks downstream gaps
- Centralized logging breaks audit trail and tenant isolation

---

## Implementation Plan

### Phase 1A: Data Structures & Validation (Days 1–2)
- [ ] Implement `ToolExecutionTelemetry` dataclass
- [ ] Implement `_sanitize_error_message()` function
- [ ] Implement `_validate_tokens()` helper
- [ ] Unit tests (8 cases): happy path, failures, ratings, token breakdown, validation, outcome signals
- [ ] Code review approval

### Phase 1B: ToolForgeSubsystem Integration (Days 3–5)
- [ ] Add `_handle_tool_execute()` method (wraps tool execution, captures telemetry)
- [ ] Add `_handle_tool_rate()` method (captures operator rating)
- [ ] Add `_emit_tool_executed_event()` method (wraps telemetry in LearningEvent)
- [ ] Subscribe to `operator_rated_tool` events
- [ ] Integration tests (4 cases): event emission, error handling, audit trail, rating attachment
- [ ] Feature flag: `learning_gap_1_tool_telemetry` (default: false)

### Phase 1C: Event Schema & ADR (Days 5–6)
- [ ] Add `TOOL_EXECUTED` and `OPERATOR_RATED_TOOL` to `LearningEventType` enum
- [ ] Add payload dataclasses (`ToolExecutedPayload`, `OperatorRatedToolPayload`)
- [ ] Publish ADR-0321
- [ ] Document event schema in `docs/implementation/LEARNING_EVENT_SCHEMA.md`

### Phase 1D: Documentation & Rollout (Days 6–7)
- [ ] Update `docs/DETAILED_DESIGN_ALL_INTEGRATIONS.md` with fixes from code review
- [ ] Migration guide: "How to enable Gap 1 in existing tenant"
- [ ] Operator runbook: "Understanding tool execution events"
- [ ] Canary flag: 10% of tenants enabled on day 7

---

## Metrics & Success Criteria

### Phase 1 Success (Blocking for Gap 2)
- [ ] `test_tool_execution_emits_learning_event` passing
- [ ] `test_operator_rating_event_emitted` passing
- [ ] Audit trail contains ≥1 tool.executed entry per executed tool
- [ ] Error message sanitization validated (no paths, schema names in logs)
- [ ] EventEmitter queue not dropping events (drop rate < 0.1%)

### Phase 2–7 Unblocks
Once Phase 1 passes, Gaps 2–7 can proceed in parallel:
- [ ] Gap 2 can query TOOL_EXECUTED events for ranking
- [ ] Gap 4 can aggregate success rates from events
- [ ] Gap 3 can attribute outcomes with ground truth
- [ ] Gap 5 can track tool coherence across sessions
- [ ] Gap 6 can learn cost multipliers
- [ ] Gap 7 can correlate operator ratings with outcomes

---

## Code Review Findings & Mitigations

**Finding 1: Subsystem tokens validation unclear**
- Mitigation: ADR clarifies that `subsystem_tokens` is a breakdown (subset of total), not overhead
- Assertion updated: `sum(subsystem_tokens.values()) <= (input_tokens + output_tokens)`
- Test case added: `test_subsystem_tokens_consistency`

**Finding 2: error_message PII sanitization missing**
- Mitigation: Implement `_sanitize_error_message()` in __post_init__
- Removes: absolute paths, database schema, internal service names, stack traces
- Validation: _assert_safe() (fail-closed) before emission

**Finding 3: required_followup never populated**
- Mitigation: Document as "future work for Gap 7" (operator feedback loop)
- Current: Always false; when Gap 7 wires UI, populate this signal
- Default behavior: Safe (conservative estimate)

**Finding 4: Audit trail integration missing**
- Mitigation: Emit audit event in ToolExecutionTelemetry.__post_init__
- Event: `("tool.execution_captured", {"tool_id": ..., "status": ..., "session_id": ...})`
- Verification: Audit trail contains entry for every TOOL_EXECUTED event

**Finding 5: EventEmitter initialization validation**
- Mitigation: Add assertion in ToolForgeSubsystem.startup()
- Fails fast if EventEmitter not registered
- Blocks startup rather than silently degrading

---

## Compliance & Security

### GDPR Art. 5 (Lawfulness, Fairness, Transparency)
✅ **Data minimization:** Only telemetry necessary for learning (latency, cost, status, outcome)  
✅ **Consent gates:** Tied to `/pass` consent (operator can opt out of learning)  
✅ **Purpose limitation:** Data used only for tool quality feedback  

### GDPR Art. 6 (Legal basis)
✅ **Legitimate interest:** Learning tool quality benefits the operator  
✅ **Consent:** Operator consent checked (via `consent_granted` flag)  

### GDPR Art. 30 (Records of processing)
✅ **Audit trail:** Every TOOL_EXECUTED event audited (timestamp, tool_id, status)  
✅ **Hash-chained:** Each entry includes prior entry's hash  

### PII Risk Mitigation
- ✅ Error messages sanitized (no paths, schema, stack traces)
- ✅ No user data in payload (only tool metadata and metrics)
- ✅ Tenant isolation enforced (query filters by tenant_id)
- ✅ Fail-closed _assert_safe() drops any record with PII shape

---

## Tenant Isolation & Multi-Tenant Safety

**Isolation enforced at three levels:**

1. **Data level:** LearningEvent.tenant_id is immutable and part of audit trail
2. **Query level:** EventStore.query_events() filters by tenant_id (no cross-tenant leaks possible)
3. **Storage level:** Event records are partitioned by date + tenant_id in database

**Verification:**
- Unit tests include cross-tenant scenario (Tenant A's tools don't appear in Tenant B's queries)
- E2E test: Tool A executed in Tenant 1, ranked in Tenant 2 → should NOT appear

---

## Feature Flag & Rollout Strategy

**Flag:** `learning_gap_1_tool_telemetry` (schema: `spec.features.learning_gap_1_tool_telemetry: bool`)

**Default:** `false` (off on fresh install, off after upgrade until enabled)

**Rollout:**
- Week 1: 10% canary (internal tenants only)
- Week 2: 25% if no errors
- Week 3: 50% (customer tenants opt-in)
- Week 4: 100% (enabled by default, can be disabled per tenant)

**Telemetry on learning_gap_1:**
- If flag=false: ToolForgeSubsystem skips event emission (silent no-op)
- If flag=true: All executions emit events (no silent failures)

---

## References

- **ADR-0314:** Learning Infrastructure (EventEmitter, EventStore, event schema)
- **ADR-0322:** Tool Performance Ranking (depends on Gap 1 events)
- **ADR-0324:** Performance Aggregation (aggregates Gap 1 events)
- **GDPR Art. 5, 6, 30, 32:** Compliance baseline (docs/claude-ref/compliance-baseline.md)
- **E2E Wiring Proof Standard:** docs/claude-ref/e2e-wiring-proof-standard.md

---

**Status:** PROPOSED (awaiting Architecture Team approval)  
**Next:** Address code review findings (5 blockers), then implement Phase 1A.
