---
id: ADR-0373
status: PROPOSED
supersedes: []
depends_on: [ADR-0358, ADR-0370, ADR-0372]
related: [ADR-0371]
commits: []
paths:
  - core/orchestration/subsystems/cost_controller.py
docs: []
---

# ADR-0373 — Cost Optimization Tuning: Adaptive Budget Allocation

**Status:** PROPOSED | **Phase 2, Improvement 6**

## Context

CostController currently allocates budget uniformly across strategies. With Improvement 4 (adaptive strategy selection) + Improvement 5 (learning feedback loop), we can now optimize cost allocation based on observed efficiency.

## Decision

Three cost-optimization mechanisms:

### 1. Cost-Efficiency Ranking
Track cost_per_success for each strategy:
- cost_efficiency = total_cost / total_successes
- Rank strategies by efficiency (lowest cost wins)
- Use in strategy selection (tie-breaker after success rate)

### 2. Adaptive Budget Per Strategy
Allocate more budget to high-efficiency strategies:
- If strategy A costs 0.5¢/success, strategy B costs 2.0¢/success → allocate 4:1 budget ratio
- Reallocate periodically (weekly) as strategies prove themselves

### 3. Cost Spike Detection & Recovery
Detect when a strategy suddenly becomes expensive (regression):
- If cost_per_success doubles, downgrade its priority
- Trigger emergency strategy switch if budget burn > threshold

## Three-Level Analysis

**Conceptual:** Allocate resources efficiently — spend more on cheap strategies, less on expensive ones.

**Structural:** CostController gains cost-efficiency tracking, adaptive budget allocation, spike detection.

**Implementation:** New methods in CostController:
- `track_cost_per_strategy(strategy, cost, success)`
- `get_cost_efficiency(strategy) → float`
- `detect_cost_spike(strategy) → bool`
- `reallocate_budget() → Dict[strategy, allocation]`

---

## Operator Notes

*None yet.*
