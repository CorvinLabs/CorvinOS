# Phase 6: Complete E2E Implementation — Learning Integration Gaps

**Status:** PHASE 1 (Gap 1) IN PROGRESS — Day 1 Complete  
**Date:** 2026-08-19  
**Objective:** Implement all 7 learning integration gaps (ADR-0321-0327) with full E2E proof, 80-120 tests, and production-ready code.

---

## PHASE 1: Gap 1 (Tool Execution Telemetry) — Days 1-7

### Iteration 1 ✅ COMPLETE

**Deliverables:**
- ✅ `core/learning/tool_execution.py` — ToolExecutionTelemetry frozen dataclass (ADR-0321)
  - Immutable telemetry capture
  - __post_init__ validation (fail-fast)
  - PII sanitization (_sanitize_error_message)
  - Fail-closed validator (_assert_safe)
  - Event payload conversion (to_event_payload)

- ✅ `core/learning/event_schema.py` — Event types and payloads
  - Added LearningEventType.TOOL_EXECUTED enum
  - Added LearningEventType.OPERATOR_RATED_TOOL enum
  - Added ToolExecutedPayload frozen dataclass
  - Added OperatorRatedToolPayload frozen dataclass

- ✅ `tests/unit/test_learning_tool_execution.py` — 21 unit tests (exceeds 8 required)
  - TestToolExecutionTelemetry: 11 tests
  - TestPiiSanitization: 5 tests
  - TestAssertSafe: 3 tests
  - TestToolExecutionStatus: 2 tests
  - **Result: 21/21 passing ✅**

**Gates Status:**
- Tier-1 (schema/lint): ✅ PASSED (imports, type checks)
- Tier-2 (unit tests): ✅ PASSED (21/21)

---

### Iteration 2: ToolForgeSubsystem Integration (Days 3-5)

**In Progress — Requires:**

1. **EventEmitter Integration**
   - Understand how EventEmitter is initialized in the system
   - Get tenant_id, instance_id from execution context
   - Initialize EventEmitter in ToolForgeSubsystem.startup()
   - Emit TOOL_EXECUTED events after tool execution

2. **Context Flow Analysis**
   - Trace how session_id, turn_id, task_id flow through ToolForgeSubsystem
   - Understand where to capture model_id, task_type
   - Map cost estimation to estimated_cost_cents

3. **Integration Code Required:**
   - Add to ToolForgeSubsystem.__init__: event_emitter, tenant_id, instance_id
   - Extend ToolForgeSubsystem.startup(): Initialize EventEmitter
   - Add method: _emit_tool_executed_event(telemetry)
   - Add method: _emit_operator_rated_tool_event(rating)
   - Modify _forge_exec: Capture ToolExecutionTelemetry, emit event
   - Subscribe to operator_rated_tool events in startup()

4. **Integration Tests Required (4 tests):**
   - test_tool_executed_event_emitted_on_success
   - test_tool_executed_event_emitted_on_failure
   - test_audit_trail_entry_created
   - test_operator_rating_attached

**Blockers:**
- Need to understand EventEmitter initialization pattern in orchestration subsystems
- Need to map context flow (where does session_id come from?)

---

### Iteration 3: Feature Flag + ADR (Days 5-6)

**Deliverables:**
- [ ] Add `learning_gap_1_tool_telemetry` feature flag to tenant.corvin.yaml schema
- [ ] Gate event emission behind feature flag
- [ ] Update ADR-0321 status: PROPOSED → ACCEPTED
- [ ] Commit with ADR-0321 reference

---

### Iteration 4: E2E Test + Docs (Days 6-7)

**Deliverables:**
- [ ] Create E2E test: tool execution → event emission → EventStore query
- [ ] Update docs/DETAILED_DESIGN_ALL_INTEGRATIONS.md with Gap 1 complete
- [ ] Migration guide: "How to enable Gap 1 in existing tenant"
- [ ] Operator runbook: "Understanding tool execution events"
- [ ] Canary rollout: 10% of instances on Day 7

---

## PHASE 2: Gaps 4 + 2 (PARALLEL) — Days 8-20

### Gap 4: Performance Aggregation Pipeline
- [ ] `core/learning/performance_aggregation.py`
- [ ] `core/learning/aggregation_queries.py`
- [ ] Background cron job (hourly metric aggregation)
- [ ] 15 unit tests
- [ ] Cache strategy + TTL management

### Gap 2: Tool Ranking & Reuse Decision
- [ ] `core/learning/tool_ranking.py`
- [ ] `core/orchestration/tool_selection.py`
- [ ] `core/learning/tool_ranking_cache.py`
- [ ] 12 unit tests + 1 integration test
- [ ] Scoring formula + reuse threshold

**Blocking:** Gap 1 must be complete (provides TOOL_EXECUTED events)

---

## PHASE 3: Gap 3 (Skill Attribution) — Days 21-25

**Deliverables:**
- [ ] `core/learning/skill_attribution.py`
- [ ] 4 attribution models: EQUAL (default), WEIGHTED, FIRST, LAST
- [ ] 10 unit tests
- [ ] Audit trail integration

**Blocking:** Gaps 4+2 must be complete

---

## PHASE 4: Gaps 5, 6, 7 (PARALLEL) — Days 26-33

### Gap 5: Context Coherence (Cross-Session Learning)
- [ ] `core/orchestration/context_coherence.py`
- [ ] 10 unit tests

### Gap 6: Cost Learning (Budget Refinement)
- [ ] `core/learning/tool_cost_learning.py`
- [ ] 8 unit tests

### Gap 7: Operator Feedback Loop
- [ ] `core/learning/operator_feedback.py`
- [ ] API endpoints: POST /api/tools/{id}/rating, /api/skills/{id}/rating
- [ ] 12 unit tests

**Blocking:** Gap 3 must be complete

---

## PHASE 5: E2E Integration + Rollout — Days 34-43

**Deliverables:**
- [ ] 4 comprehensive E2E scenarios
  - Scenario 1: Tool Learning Loop
  - Scenario 2: Multi-Session Learning
  - Scenario 3: Skill Attribution + Feedback
  - Scenario 4: Cost Refinement

- [ ] Performance benchmarks
  - Event emission latency: <50ms (p99)
  - Aggregation job: <1s for 10k events
  - Ranking query: <100ms (p99)
  - Tool reuse decision: <5ms
  - Overall task latency impact: <100ms

- [ ] Monitoring dashboard + metrics
- [ ] Rollout plan (canary → beta → GA)
- [ ] Documentation + operator onboarding

---

## TEST COVERAGE SUMMARY

| Gap | Unit Tests | Integration | E2E | Total |
|-----|-----------|------------|-----|-------|
| 1   | 21 ✅     | 0 (2 planned) | 0 (1 planned) | 23 planned |
| 4   | 15        | 0          | 0   | 15 |
| 2   | 12        | 1          | 0   | 13 |
| 3   | 10        | 0          | 0   | 10 |
| 5   | 10        | 0          | 0   | 10 |
| 6   | 8         | 0          | 0   | 8 |
| 7   | 12        | 0          | 0   | 12 |
| E2E | -         | -          | 4   | 4 |
| **TOTAL** | **88** | **1** | **4** | **93** |

---

## QUALITY GATES (ALL PHASES)

**Gate 1: Unit Test Coverage**
- [ ] Line coverage >95%
- [ ] Branch coverage >90%
- [ ] All test cases passing

**Gate 2: Code Review**
- [ ] 2 independent reviewers approve
- [ ] All findings addressed
- [ ] Comments reference ADRs

**Gate 3: ADR Compliance**
- [ ] Code reflects ADR decisions
- [ ] Structural constraints honored
- [ ] ADR status: PROPOSED → ACCEPTED

**Gate 4: Audit Trail**
- [ ] All mutations logged
- [ ] Hash chain verified
- [ ] Tenant isolation enforced

**Gate 5: Performance**
- [ ] No regressions in critical path
- [ ] Latency budgets met
- [ ] Memory overhead bounded

**Gate 6: E2E Testing**
- [ ] Reachability proof (all gaps called)
- [ ] E2E scenarios pass
- [ ] Feature flags toggle without errors

**Gate 7: Documentation**
- [ ] All public APIs documented
- [ ] Integration guide complete
- [ ] Examples provided

---

## NEXT STEPS

**Immediate (Next Session):**
1. Understand EventEmitter initialization pattern
2. Trace context flow (session_id, task_id, turn_id)
3. Complete Iteration 2 (ToolForgeSubsystem integration)
4. Run integration tests

**Within 1 Week:**
1. Complete Phase 1 (Gap 1)
2. Start Phase 2 (Gaps 4+2 parallel)
3. Establish performance baseline

**Within 2 Weeks:**
1. Complete Phases 1-3 (Gaps 1-4, 1-3)
2. Start Phase 4 (Gaps 5-7)
3. Full test coverage >90%

---

## KEY DEPENDENCIES

- **Gap 1 BLOCKS all others** (provides TOOL_EXECUTED events)
- **Gaps 4+2 parallel** (after Gap 1)
- **Gap 3 after Gaps 4+2** (requires aggregated metrics)
- **Gaps 5+6+7 parallel** (after Gap 3, mostly independent)
- **Phase 5 E2E** (after all gaps)

---

## ADR STATUS

| ADR | Title | Status | Blocker |
|-----|-------|--------|---------|
| 0321 | Tool Execution Learning Events | PROPOSED → ACCEPTED (Phase 1 Day 7) | Gap 1 |
| 0322 | Tool Performance Ranking & Reuse | PROPOSED | Gap 2 |
| 0323 | Skill Attribution Model | PROPOSED | Gap 3 |
| 0324 | Performance Aggregation Pipeline | PROPOSED | Gap 4 |
| 0325 | Context Coherence (Cross-Session) | PROPOSED | Gap 5 |
| 0326 | Cost Learning (Budget Refinement) | PROPOSED | Gap 6 |
| 0327 | Operator Feedback Loop Integration | PROPOSED | Gap 7 |

---

**Updated:** 2026-08-19 23:00 UTC  
**Owner:** Claude Code (Haiku 4.5)  
**Scope:** Phase 6 Complete E2E Implementation
