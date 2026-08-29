---
id: ADR-0407
status: IMPLEMENTED (Phase 1 complete)
date: 2026-08-30
depends_on: [ADR-0399, ADR-0080, ADR-0347, ADR-0348]
supersedes: []
related: [ADR-0404, ADR-0405, ADR-0406]
paths:
  - core/session_manager/goal_context.py (Phase 1: GoalContext)
  - core/session_manager/checkpoint.py (Phase 1: goal persistence)
  - core/orchestration/subsystems/session_manager.py (Phase 1: goal lifecycle)
  - core/learning/ (Phase 2: TBD)
  - core/context_engineering/ (Phase 2-3: TBD)
docs:
  - docs/implementation/TASK_CONTEXT_DRIFT_PREVENTION.md
---

# ADR-0407 — Task-Context-Drift Prevention System (Master)

**Status:** PROPOSED  
**Date:** 2026-08-30  
**Deciders:** Claude Code (Autonomous Implementation)

## Problem Statement

CorvinOS experiences **Context Drift** — agent loses original task context mid-session and diverges to unrelated tasks. Root causes:

1. **Gap 1:** No goal-alignment check after context reduction (91% token loss)
2. **Gap 2:** Task goal not persisted across session splits  
3. **Gap 3:** LDD outer loop has no goal re-synchronization protocol

**Impact:** User sees "Why is Corvin working on logging?" when they asked for "refactor payment"

## Solution

**Unified 3-part system** closing all architectural gaps simultaneously:

| Component | ADR | Purpose | Status |
|-----------|-----|---------|--------|
| **Part 1: Validation Gate** | ADR-0404 | Prevent context reduction from erasing goal | PROPOSED |
| **Part 2: Goal Persistence** | ADR-0405 | Restore goal across session splits | PROPOSED |
| **Part 3: LDD Re-Sync** | ADR-0406 | Detect and correct drift during outer loop | PROPOSED |

## Architecture

```
SESSION START: Goal = "Refactor payment processing"
  ↓
Goal Context Initialized (GoalContext, hash-chained)
  ├─ Registered in ExecutionContext
  ├─ Logged to audit trail
  ↓
ITERATION 1-10: Strategy aligns with goal
  ├─ LDD Re-Sync checks: similarity = 0.85 (OK)
  ├─ Drift counter = 0
  ↓
CONTEXT LIMIT REACHED: Need to reduce 200k → 18k
  ├─ [NEW] Validation Gate checks reduction
  ├─ Goal alignment score = 0.72 (safe to reduce)
  ├─ Reduced context used
  ↓
CHECKPOINT CREATED (Phase end)
  ├─ [NEW] GoalContext included in checkpoint
  ├─ Hash-chained, audit logged
  ↓
SESSION SPLIT / RESTART
  ↓
Resume from Checkpoint
  ├─ [NEW] GoalContext restored + integrity verified
  ├─ Re-registered in ExecutionContext
  ├─ New session starts with SAME goal
  ↓
ITERATION 11-30: New session continues
  ├─ LDD Re-Sync checks: similarity = 0.78 (OK)
  ├─ Drift counter = 0
  ├─ Goal alignment maintained
  ↓
ITERATION 31+: Strategy drifts
  ├─ LDD Re-Sync checks: similarity = 0.42
  ├─ Drift counter = 1 → 2 → 3
  ├─ [NEW] DRIFT DETECTED at iteration 33
  ├─ Enter correction phase
  ├─ Re-align strategy to original goal
  ↓
ITERATION 34+: Back to alignment
  ├─ Similarity = 0.76 (OK)
  ├─ Drift counter resets

Result: Feedback loop prevents drift accumulation forever
```

## Implementation Roadmap

### Phase 1: Data Model & Persistence (3-4 days, 120 LoC)

**Deliverable:** Goal persists 100% across session boundaries

**Files:**
- Create: `core/session_manager/goal_context.py` (50 LoC)
- Modify: `core/session_manager/session_manager.py` (40 LoC)
- Modify: `core/session_manager/checkpoint.py` (15 LoC)
- Tests: 50 LoC

**Exit Criteria:**
- ✅ 50+ unit tests (goal_context, persistence)
- ✅ E2E test: split & resume, goal unchanged
- ✅ Audit trail: every goal event logged
- ✅ No regressions in existing tests

---

### Phase 2: Validation Gate (4-5 days, 160 LoC)

**Deliverable:** Context reduction validated for goal coherence

**Files:**
- Create: `core/session_manager/goal_validation_gate.py` (120 LoC)
- Modify: `core/session_manager/context_reducer.py` (35 LoC)
- Tests: 100 LoC

**Exit Criteria:**
- ✅ 50+ unit tests (validation, similarity scoring)
- ✅ E2E test: reduction with goal alignment check
- ✅ Performance: <5ms per validation call
- ✅ No goal loss on safe reductions

---

### Phase 3: LDD Re-Sync (5-6 days, 200 LoC)

**Deliverable:** LDD outer loop detects and corrects goal drift

**Files:**
- Create: `core/session_manager/ldd_goal_resync.py` (150 LoC)
- Modify: `core/learning/loss_driven_development.py` (~30 LoC)
- Tests: 80 LoC

**Exit Criteria:**
- ✅ 50+ unit + integration tests
- ✅ E2E test: 100-iteration simulation, drift detection
- ✅ Drift detected within 2-3 iterations
- ✅ No false negatives (all real drifts caught)

---

### Production-Ready Gates (All Phases)

**Before ANY promotion:**

| Gate | Criteria | Enforcement |
|------|----------|------------|
| **Test Pass Rate** | ≥99% (0 failures) | Automated, blocks merge |
| **Code Coverage** | ≥85% (goal/session modules) | SonarQube check |
| **Security** | 0 high/critical findings | Security review checklist |
| **Compliance** | GDPR Art. 30/32, EU AI Act Art. 50 | Audit trail verification |
| **Performance** | <5ms overhead per reduction | Benchmark tests |
| **E2E Validation** | All integration paths proven | Reachability + functional tests |

## Effort Estimate

| Phase | New LoC | Modified LoC | Test LoC | Duration | Tests | Blocker |
|-------|---------|--------------|----------|----------|-------|---------|
| **Phase 1** | 120 | 95 | 50 | 3-4 days | 50+ | None |
| **Phase 2** | 160 | 35 | 100 | 4-5 days | 50+ | Phase 1 pass |
| **Phase 3** | 200 | 30 | 80 | 5-6 days | 50+ | Phase 1+2 pass |
| **TOTAL** | **480** | **160** | **230** | **12-15 days** | **150+** | None |

## Compliance & Audit Trail

### GDPR Art. 30 (Documentation of Processing)

Every goal context change logged:
```json
{
  "event": "goal_context_initialized|restored|validation_checked",
  "task_id": "...",
  "goal_hash": "sha256(...)",
  "timestamp": "2026-08-30T...",
  "tenant_id": "...",
  "validation_score": 0.72
}
```

### GDPR Art. 32 (Data Protection Measures)

- Hash-chained goal context (integrity)
- Immutable GoalContext (frozen dataclass)
- Fail-closed validation (reject unsafe reductions)
- Audit trail (every event logged)

### EU AI Act Art. 50 (Transparency)

- Goal alignment scores visible in audit trail
- Drift detection logged with reason
- Recovery actions documented
- User escalations transparent

## Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Goal Persistence** | 100% across splits | E2E test: split & resume, goal unchanged |
| **Validation Coverage** | 100% of reductions | Every reduce() call validated |
| **Drift Detection Speed** | <2 iterations | E2E test: 100-iteration simulation |
| **False Positives** | <5% | Unit tests on threshold calibration |
| **User Escalations** | <1 per 100 tasks | Metric collection (post-deployment) |

## Design Decisions & Trade-offs

| Decision | Pro | Con | Rationale |
|----------|-----|-----|-----------|
| **Fail-Closed on Drift** | Safety guaranteed | Conservative (full context fallback) | Correctness > Performance |
| **Immutable GoalContext** | Integrity protected | Tiny memory overhead | Acceptable trade-off |
| **Per-Iteration Checks** | Early drift detection | O(n) overhead acceptable at 50-100 iter | Safety > Performance |
| **User Escalation** | Safety net | May interrupt automation | Acceptable UX trade-off |

## Dependency Map

```
ADR-0399 (Context-Pipeline v2)
  ├─→ ADR-0405 (Goal Persistence — uses ContextReducer)
  
ADR-0347/0348 (Brain Hub + EventBus)
  ├─→ ADR-0406 (LDD Re-Sync — hooks into LDD outer loop)
  
ADR-0080 (Task Engine)
  ├─→ ADR-0405 (Goal stored in TaskExecution + Checkpoint)

NEW: ADR-0404 (Goal-Alignment Validation Gate)
NEW: ADR-0405 (Cross-Session Goal Persistence)
NEW: ADR-0406 (LDD Goal Re-Sync Protocol)
MASTER: ADR-0407 (Task-Context-Drift Prevention System)
```

## Decision

✅ **APPROVED FOR FULL IMPLEMENTATION**

Implement Task-Context-Drift Prevention System with all 3 phases to production-ready state, autonomous execution, zero manual gates.

**Execution Model:**
- Phase 1: Data Model (ADR-0405) — days 1-3
- Phase 2: Validation Gate (ADR-0404) — days 4-8
- Phase 3: LDD Re-Sync (ADR-0406) — days 9-14
- Production-Ready Verification: day 15

**Next:** Spawn 3 autonomous agents (one per phase), all gates enforced automatically.

---

**ADR:** Task-Context-Drift Prevention System  
**Owner:** Autonomous Implementation (Full Phases 1-3)  
**Timeline:** 12-15 days autonomous execution
