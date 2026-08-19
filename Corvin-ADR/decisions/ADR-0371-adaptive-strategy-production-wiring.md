---
id: ADR-0371
status: PROPOSED
supersedes: []
depends_on: [ADR-0370]
related: [ADR-0358, ADR-0369]
commits: []
paths:
  - core/learning/adaptive_strategy.py
  - core/orchestration/subsystems/loop_engineer.py
  - core/orchestration/subsystems/strategy_advisor.py
docs:
  - docs/claude-ref/quality-discipline.md
---

# ADR-0371 — Adaptive Strategy Production Wiring & Docstring Fix

**Status:** PROPOSED  
**Date:** 2026-08-19  
**Deciders:** Claude Code (agent), Shumway (operator)

## Context

ADR-0370 (Adaptive Strategy Ladder) introduced `StrategyAdvisor.get_strategy()` for fingerprint-aware strategy ranking, but left the method without a production call site. Code-review adversarial gate (k=1, LDD loop) identified two critical findings:

1. **E2E Wiring Violation**: `get_strategy()` existed as tested code but was unreachable from production paths. Per CLAUDE.md mandatory gate, new entry points require live call sites outside tests.
2. **Docstring-Code Inconsistency**: Three docstrings specified `confidence > 0.7` (strictly greater) while implementation used `>= 0.7` (ADR-0370 spec).

## Decision

**Wiring Strategy Selection into LoopEngineer:**

1. Inject `StrategyAdvisor` into `LoopEngineer` during hub startup
2. In `LoopEngineer._apply_strategy()` (error recovery path):
   - Build available strategies as `StrategyOption` objects from the static ladder
   - Retrieve operator fingerprint from ExecutionContext if available
   - Call `StrategyAdvisor.get_strategy()` with available strategies + fingerprint
   - Fall back to static ladder if adaptive selection fails or StrategyAdvisor unavailable
   - Record selection mode (adaptive vs. static_ladder) in audit trail for transparency

**Docstring Corrections:**
- `adaptive_strategy.py:8` (module): `> 0.7` → `>= 0.7`
- `adaptive_strategy.py:76` (class): `> 0.7` → `>= 0.7`
- `strategy_advisor.py:157` (method): `> 0.7` → `>= 0.7`

All docstrings now match code behavior and ADR-0370.

## Three-Level Analysis

### Conceptual

Adaptive strategy selection is load-bearing for Phase 2, Improvement 4. The decision is: **when a task errors, should we use operator-aware strategy selection, or static ladder?**

Adaptive ranking improves strategy fit by considering operator expertise, speed preference, and risk tolerance. But only when fingerprint confidence is high (≥0.7). Below that threshold, empirical fallback prevents misclassification.

The new call site (LoopEngineer error recovery) is where this decision matters most—errors are high-stakes, operator context is available, and selecting the right recovery strategy improves task success.

### Structural

- **StrategyAdvisor** is now invoked in a real production path: `LoopEngineer._apply_strategy()`.
- **LoopEngineer** gains a new dependency: StrategyAdvisor (injected via hub).
- **Fallback chain** is maintained: adaptive → static ladder → no change.
- **Audit trail** records which mode was used, enabling future analysis of adaptive vs. static performance.
- **Backward compatibility** is preserved: if StrategyAdvisor is unavailable, system degrades to static ladder without error.

### Implementation

- `LoopEngineer.startup()`: Retrieve StrategyAdvisor from hub.subsystems["strategy_advisor"]
- `LoopEngineer._apply_strategy()`:
  - Build list of `StrategyOption(name, required_steps, avg_latency_ms, avg_cost_cents, success_rate, operator_preference_score)` for each strategy in `strategy_ladder`
  - Retrieve fingerprint from context state (`.operator_fingerprint`)
  - Call `strategy_advisor.get_strategy(available_strategies, fingerprint, task_type="error_recovery")`
  - If successful, use returned strategy; else fall back to static ladder
  - Log mode (adaptive/static) in decision record

## Rationale

**Why LoopEngineer?**
- Error recovery is decision-heavy: strategy choice directly affects task success probability
- Operator fingerprint is available in ExecutionContext during task execution
- LoopEngineer already owns strategy selection logic (static ladder)
- Integration point is natural: `_apply_strategy()` is called on error_detected event

**Why fallback?**
- StrategyAdvisor may not be initialized (graceful degradation)
- Fingerprint may be unavailable or low-confidence (fallback to empirical)
- Prevents single point of failure

**Why record mode?**
- Operator visibility: decisions show whether adaptive or static was used
- Learning signal: future analysis can compare adaptive vs. static success rates per operator
- Compliance: audit trail documents the decision-making process

## Alternatives Considered

1. **Create a separate "adaptive strategy selector" subsystem**: Over-engineered; reuse LoopEngineer's existing integration
2. **Always use adaptive ranking, no fallback**: Risky if fingerprint unavailable or low-confidence; breaks graceful degradation
3. **Inline get_strategy() calls in StrategyAdvisor.handle_request()**: Limited reach; handle_request is not invoked in production code paths
4. **Leave get_strategy() as internal API, only expose via handle_request**: Violates E2E wiring gate; dead code shipped

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| StrategyAdvisor injection fails silently, cascades to fallback | Log at INFO level; audit trail records fallback; no system failure |
| Fingerprint unavailable mid-task | Check with hasattr/getattr; gracefully pass None to get_strategy() |
| Building StrategyOption list adds latency to error path | StrategyOption is frozen dataclass, construction is <1ms; negligible vs. error recovery time |
| Audit trail bloat from strategy selection records | Records are small (decision_type + value + reasoning); append-only log; no performance impact |

## Acceptance Criteria

- [x] `StrategyAdvisor.get_strategy()` has at least one production call site outside tests
- [x] Docstrings match code behavior (>= 0.7 across all three locations)
- [x] LoopEngineer.startup() successfully injects StrategyAdvisor from hub
- [x] `_apply_strategy()` calls get_strategy() and logs selection mode
- [x] Fallback to static ladder works when StrategyAdvisor unavailable
- [x] Audit trail records strategy selection with mode tag
- [x] E2E wiring proof gate passes: method is reachable, callable from live trigger

## Follow-up

Measurement (Week 1): Observe adaptive vs. static strategy selection rates, success distributions per mode, in canary rollout. If adaptive shows consistent improvement, promote to default in ADR-0370 v2.

---

## Amendment (k=4 — Constants & Fresh-Install Handling)

**Date:** 2026-08-19 (k=4, LDD iteration)  
**Status:** PROPOSED → ACCEPTED (after k=3 empirical wiring + k=4 constant extraction)

Extracted hardcoded cost/latency formulas to module-level constants in adaptive_strategy.py:
- STRATEGY_BASE_COST_CENTS, STRATEGY_COST_INCREMENT_CENTS
- STRATEGY_BASE_LATENCY_MS, STRATEGY_LATENCY_INCREMENT_MS
- STRATEGY_DEFAULT_SUCCESS_RATE = 0.5 (fresh install default)

Enables design validation, prevents silent formula drift, and clarifies default behavior for systems with no empirical history.

## Amendment (k=3 — Empirical Data Wiring)

**Date:** 2026-08-19 (k=3, LDD iteration)  
**Status:** PROPOSED → ACCEPTED (after adversarial review k=1, k=2 fixes applied)

Initial wiring used hardcoded success rates (0.8 - i*0.15) and metrics. K=2 code review identified that adaptive ranking was using phantom data instead of empirical measurements.

**k=3 Fix:** Added `StrategyAdvisor.build_strategy_options()` method that constructs StrategyOption list with REAL empirical success rates from `strategy_scores`, plus derived cost/latency estimates. Rewired `LoopEngineer._apply_strategy()` to use this method instead of hardcoded formulas. Fixed silent exception handling in fingerprint retrieval.

Result: Adaptive ranking now uses real data across both adaptive and empirical paths, eliminating the data-source inconsistency.

## Operator Notes

*None yet.*
