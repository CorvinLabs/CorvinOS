---
id: ADR-0326
status: proposed
depends_on: [ADR-0314, ADR-0321, ADR-0324]
related: [ADR-0322, ADR-0325, ADR-0327]
supersedes: []
paths:
  - core/learning/cost_learner.py
  - core/orchestration/subsystems/cost_controller.py
docs:
  - docs/implementation/DETAILED_DESIGN_ALL_INTEGRATIONS.md
commits: []
---

# ADR-0326 — Cost Learning & Budget Refinement

**Status:** PROPOSED  
**Date:** 2026-08-19  
**Author:** Claude Code  

---

## Context

### Problem
Cost estimates are static (model pricing list). But **actual tool costs vary** based on:
- Input complexity (more tokens = higher cost)
- Model backend (Opus vs Haiku vs local)
- Tool subsystem overhead (retrieval, reranking, etc)

**Impact:**
- Budget predictions are inaccurate
- Cost-aware tool selection (Gap 2) uses wrong estimates
- Operator can't trust budget forecasts

### Gap
**Gap 6: Cost-Aware Scheduling Not Integrated** — enables cost-optimized tool selection.

---

## Decision

### Conceptual Level
**Principle:** Cost estimates improve over time by **learning actual cost deltas**. We observe: estimated_cost_cents vs actual_cost_cents, compute delta, and adjust multiplier.

### Structural Level

**Cost model:**
```
actual_cost_cents = base_model_cost_cents * task_complexity_multiplier * subsystem_overhead_multiplier
```

**Learning:**
- Track (estimated, actual) pairs per tool + model
- Compute deltas (actual - estimated)
- Apply exponential moving average (EMA) to multipliers
- Emit COST_ESTIMATE_ADJUSTED events

**Data structure:**
```python
@dataclass(frozen=True)
class CostLearnerMetrics:
    tool_id: str
    model_id: str
    estimated_cost_cents_median: int
    actual_cost_cents_median: int
    task_complexity_multiplier: float  # avg(actual / estimated)
    subsystem_overhead_multiplier: float
    samples: int
    trend: float  # +1 = cost increasing, -1 = decreasing
```

---

## Implementation Level

```python
class CostLearner:
    """Learn cost multipliers from execution history."""
    
    def __init__(self, event_store: EventStore):
        self.event_store = event_store
        self.multipliers = {}  # {(tool_id, model_id): multiplier}
    
    async def observe_execution(
        self,
        tool_id: str,
        model_id: str,
        estimated_cost_cents: int,
        actual_cost_cents: int,
    ) -> None:
        """Observe actual cost for a tool execution.
        
        Updates multiplier via EMA.
        """
        if estimated_cost_cents == 0:
            return  # Skip division by zero
        
        # Actual multiplier
        actual_multiplier = actual_cost_cents / estimated_cost_cents
        
        # EMA update (learning rate = 0.1)
        key = (tool_id, model_id)
        current = self.multipliers.get(key, 1.0)  # Default: no correction
        new_multiplier = 0.9 * current + 0.1 * actual_multiplier
        
        self.multipliers[key] = new_multiplier
    
    def get_cost_estimate(
        self,
        tool_id: str,
        model_id: str,
        base_cost_cents: int,
    ) -> int:
        """Get corrected cost estimate for a tool."""
        key = (tool_id, model_id)
        multiplier = self.multipliers.get(key, 1.0)
        
        return int(base_cost_cents * multiplier)
    
    async def aggregate_multipliers(
        self,
        time_window_days: int = 7,
        tenant_id: str = "_default",
    ) -> dict[tuple, CostLearnerMetrics]:
        """Aggregate cost multipliers from execution history."""
        # Query TOOL_EXECUTED events
        # Group by (tool_id, model_id)
        # Compute median estimated, median actual
        # Compute task_complexity_multiplier = median_actual / median_estimated
        # Return metrics
        pass
```

---

## Consequences

### Positive
✅ **Accurate budgets:** Cost estimates improve with data  
✅ **Cost-optimized selection:** Gap 2 uses corrected estimates  
✅ **Outlier detection:** Large cost deltas flagged  

### Negative
⚠️ **Multiplier lag:** Takes time to accumulate samples (cold-start)  
⚠️ **Drift:** If actual costs change (e.g., model pricing update), multipliers are stale  

### Risks & Mitigation

**Risk 1: Outlier execution inflates multiplier**
- Mitigation: Use median (robust to outliers), not mean
- Outlier detection: Flag executions where actual > 2x estimated

**Risk 2: Model pricing changes not reflected**
- Mitigation: Reset multipliers on model update
- Recommendation: Version multipliers with model_id

**Risk 3: Tool-specific costs vary widely**
- Mitigation: Track per (tool_id, model_id) pair (fine-grained)
- Recommendation: Consider task_type slicing (future work)

---

## Implementation Plan

### Phase 4 (Parallel with Gap 5): Cost Learning (Days 33–36)
- [ ] Implement `CostLearner` (EMA multiplier updates)
- [ ] Implement aggregation over execution history
- [ ] Wire into CostController (observe executions, adjust estimates)
- [ ] Unit tests (10+ cases): EMA updates, edge cases, outlier handling
- [ ] Feature flag: `learning_gap_6_cost_learning` (default: false)

---

## References

- ADR-0314: Learning Infrastructure
- ADR-0321: Tool Execution Events (provides actual costs)
- ADR-0322: Tool Ranking (uses corrected cost estimates)

---

**Status:** PROPOSED  
**Next:** Implement after Gap 4 stabilizes
