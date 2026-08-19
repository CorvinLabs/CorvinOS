---
id: ADR-0374
status: PROPOSED
supersedes: []
depends_on: [ADR-0358, ADR-0372]
related: [ADR-0373]
commits: []
paths:
  - core/orchestration/subsystems/safety_validator.py
docs: []
---

# ADR-0374 — Safety Gate Hardening: Multi-Layer Enforcement

**Status:** PROPOSED | **Phase 2, Improvement 7**

## Context

SafetyValidator currently checks strategy validity. With learning feedback loop (Improvement 5) + cost optimization (Improvement 6), we need stronger safety gates to prevent:
- Cascading failures (repeated bad strategies)
- Resource exhaustion (runaway cost/token burn)
- Unsafe state transitions (invalid strategy chains)

## Decision

Three safety-hardening mechanisms:

### 1. Failure Circuit Breaker
Stop using a strategy after N consecutive failures:
- Track consecutive_failures per strategy
- If N ≥ 5: mark strategy as "temporarily disabled" (48h cooldown)
- Prevent repeated failure loops

### 2. Resource Exhaustion Guard
Enforce hard budget limits per task:
- If budget_remaining < min_recovery_cost → forbid expensive strategies
- Force fallback to low-cost strategies only
- Prevent token/cost death spirals

### 3. State Safety Validation
Validate strategy transitions are safe:
- Not all strategy chains are valid (e.g., direct_fix → direct_fix → direct_fix is suspicious)
- Check strategy sequence against known-safe patterns
- Reject unsafe transitions

## Three-Level Analysis

**Conceptual:** Protect the system from cascading failures and resource exhaustion by enforcing hard limits and safe state transitions.

**Structural:** SafetyValidator gains circuit breaker, resource guard, state validation.

**Implementation:** New methods in SafetyValidator:
- `check_circuit_breaker(strategy) → bool`
- `check_resource_exhaustion(budget, strategy) → bool`
- `validate_strategy_transition(prev_strategy, next_strategy) → bool`

---

## Operator Notes

*None yet.*
