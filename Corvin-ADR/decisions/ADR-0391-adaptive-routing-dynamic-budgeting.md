# ADR-0391: Adaptive Context Routing & Dynamic Budget Allocation (Phase 3 Optimization)

**Status:** PROPOSED  
**Date:** 2026-08-19  
**Author:** Claude (Haiku 4.5) for CorvinOS  
**Depends on:** ADR-0388 (Phase 2 Token Budgeting)  
**Related:** ADR-0275 (Vibe Engineering), ADR-0282 (Active Brain)

---

## Problem

Phases 1–2 of Context Optimization (Confidence-Gated Memory + Bounded Preview, Per-Stage Token Budgeting) reduced context consumption via fixed allocation rules. However, **uniform budgets waste tokens on tasks that don't need expensive stages**:

- A simple rename/fix task doesn't need ADR graph traversal or skill injection — only memory + synthesis.
- A complex architectural design needs all stages, but Phase 2's fixed percentages don't adapt.
- Stage performance varies across tasks (e.g., memory relevance 0.95 one turn, 0.3 the next), yet budget stays static.

**Measurable waste today:**
- Simple tasks consume graph/skills tokens even when zero relevant results exist.
- High-utilization stages can't request more budget; low-utilization stages hog allocation.
- No feedback loop: performance metrics are collected (Phase 2) but never used.

**Expected impact:** 40–50% additional context reduction (Phase 1+2+3 combined) via adaptive routing + dynamic rebalancing.

---

## Solution

Three components working together:

### 1. Task Complexity Classifier (Phase 3a)
A lightweight keyword-based classifier runs on every task:

```
classify(task_text: str) → ClassificationResult:
  - complexity: SIMPLE | MODERATE | COMPLEX
  - confidence: 0.0–1.0 (keyword density)
  - keyword_matches: int
```

**Heuristics:**
- **SIMPLE:** "rename", "delete", "format", "typo", "fix", "strip", "syntax" → skip graph + skills (Memory 60%, Synthesis 40%)
- **MODERATE:** mixed keywords or no strong signal → balanced allocation (Memory 35%, Graph 15%, Skills 15%, Synthesis 35%)
- **COMPLEX:** "refactor", "design", "architect", "implement", "optimize" → all stages (Memory 30%, Graph 20%, Skills 20%, Synthesis 30%)

Confidence score increases with keyword density; boosters like "just", "simple", "complex" add weight. **Degrade gracefully:** no keywords → MODERATE with low confidence (0.1).

### 2. Adaptive Budget Allocation (Phase 3b)
Extends Phase 2's fixed TokenBudget with task-aware allocation:

```
AdaptiveBudget.allocate_for_task(complexity: TaskComplexity) → AdaptiveBudget:
  - memory: int
  - graph: int (0 for SIMPLE)
  - skills: int (0 for SIMPLE)
  - synthesis: int
  - stage_adjustments: dict[str, float] (±10% cap per stage)
```

**Rebalancing logic** (fires when delta > 15% from baseline):
- **Low utilization (<30%)** → reduce allocation by 5%
- **High confidence (>0.8)** → increase allocation by 3%
- **Poor quality (<0.5)** → reduce allocation by 7%
- **Bounds:** per-stage deltas capped at ±10% per rebalance cycle; total tokens conserved via normalization

Example: If graph stage has 20% utilization + 0.3 confidence for three turns running, rebalance reduces graph by 5% + 7% = 12% (capped at 10%), redistributing to higher-performing stages.

### 3. Performance Metrics Collection (Phase 3c)
PerformanceTracker maintains rolling windows (default 10 metrics) per stage:

```
PerformanceMetric:
  - utilization: float (actual_tokens / allocated_tokens)
  - confidence: float (mean relevance score)
  - quality: float (LLM success rate)
  - latency_ms: float (execution time)

PerformanceTracker.record_stage_execution(stage_id, metric)
PerformanceTracker.should_rebalance() → bool
```

**Drift detection:** compares rolling average to baseline; if Δ ≥ threshold (default 15%), signals rebalancing.

---

## Integration with Pipeline

**Build Context Pipeline (operator/context_engineering/pipeline.py)**

1. **Classify task:**
   ```python
   classification = TaskComplexity.classify(task_text)
   ```

2. **Allocate adaptive budget:**
   ```python
   budget = AdaptiveBudget.allocate_for_task(classification.complexity)
   ```

3. **Use adaptive allocation in stage execution:**
   - Each stage receives its allocation limit (e.g., memory gets 60% of total for SIMPLE).
   - Stage runs and records metrics.

4. **Periodic rebalancing (every N turns or on explicit trigger):**
   ```python
   tracker.record_stage_execution("memory", metric)
   if tracker.should_rebalance():
       budget.rebalance_from_metrics(tracker.get_all_metrics())
       tracker.reset_baseline()
   ```

5. **Fallback:** If classification flag is OFF, allocations default to Phase 2 uniform distribution (no adaptive routing).

---

## Feature Flag

**Flag ID:** `adaptive_context_routing`  
**Label:** "Adaptive Context Routing & Dynamic Budgeting"  
**Default:** OFF (ship-dark)  
**Target Release:** 0.13.x  
**Tags:** context-engineering, performance, optimization  
**Tier:** alpha

When OFF, pipeline uses Phase 2 uniform budgeting (backward compatible). When ON, classification + adaptive allocation active.

---

## Testing Strategy

**Unit Tests (12 total):**
1. TaskClassification (4):
   - Simple/complex/moderate keyword detection
   - Confidence scoring (density correlation)
   - Empty task degradation

2. AdaptiveBudget (4):
   - Per-complexity allocation percentages
   - Rebalancing from metrics
   - Bounds enforcement (±10%)
   - Export to dict/percentages

3. PerformanceTracker (2):
   - Record and aggregate metrics
   - Window size enforcement
   - Rebalance trigger detection
   - Baseline reset

4. Integration (2):
   - Full pipeline with adaptive budget
   - Feature flag disabled → Phase 2 behavior

**E2E Coverage:**
- Pipeline classification + budget allocation on real tasks
- Rebalancing cascade across multiple turns
- Metrics collection + reporting

---

## Constraints & Trade-offs

**Constraints:**
- Simple/Moderate/Complex boundary is heuristic, not exact. False negatives (mislabeling a complex task as simple) degrade quality; cost is acceptable given keyword coverage and fallback to MODERATE.
- Rebalancing delay: metrics lag allocation changes by 1–2 turns (window size default 10). Acceptable for batch optimization; not suitable for per-turn reactivity.
- No ML model training; heuristics locked to shipped keywords. Future phases could add learned classifiers.

**Trade-offs:**
- **Simplicity vs. Accuracy:** Keyword heuristics are 99 LoC vs. a multi-model ensemble, but misclassify edge cases. Mitigated by high MODERATE default (safest allocation).
- **Rebalancing Frequency:** Fewer cycles = stale feedback; more cycles = overhead + oscillation. Default 15% drift threshold + 10-metric window balances these.
- **Budget Conservation:** Rebalancing normalizes to maintain total tokens, preventing runaway allocation. Simple quadratic fit is O(1).

---

## Compliance & Safety

- **No structural changes:** existing stages unchanged; classifier + budget are *additive layers* above Phase 2.
- **Fail-safe:** classifier returns MODERATE on parse error; rebalancing is optional (feature-flagged).
- **Audit trail:** metrics collection + rebalancing decisions logged for transparency (future enhancement).
- **No PII:** classifies task *text only* (keywords); no model state, no user data storage.

---

## Metrics & Success Criteria

**Target Savings:** 40–50% context reduction (Phase 1+2+3 combined).

**Measurable outcomes:**
1. **Latency:** simple tasks 200–300ms faster (skip graph/skills stages).
2. **Token efficiency:** avg context size reduced by 300–500 tokens/turn.
3. **Confidence:** rebalanced stage allocations improve top-stage quality by 5–10%.
4. **Adoption:** alpha → beta requires >90% "on" enablement in canary; no reported misclassifications blocking real workflows.

---

## Deployment Plan

1. **Phase 3.0 (v0.13-alpha):**
   - TaskClassifier, AdaptiveBudget, PerformanceTracker modules (new).
   - Feature flag registered (default OFF).
   - 12 unit tests (100% green).

2. **Phase 3.1 (v0.13-beta):** (10% canary)
   - Enable flag for 10% of users.
   - Collect latency + context-size metrics.
   - Operator feedback on misclassifications.

3. **Phase 3.2 (v0.13-stable):** (50% rollout)
   - Adjust keyword heuristics if needed.
   - Graduate to beta tier.

4. **Phase 3.3 (v0.14):**
   - Learned classifiers (optional advanced feature).

---

## Open Questions / Future Work

1. **Keyword Coverage:** should "machine learning", "api design", "debugging" be COMPLEX? (Currently neutral → MODERATE). Operator feedback drives updates.

2. **Granular Rebalancing:** today per-stage; future could be per-stage-per-task-type (e.g., coding tasks vs. writing).

3. **Learned Classifiers:** post-v0.13, use historical performance + user feedback to train lightweight classifier (e.g., naive Bayes over task words + prior context).

4. **Cross-Tenant Metrics:** aggregate stats across tenants to refine keyword sets? (Privacy: only stats, no task text).

---

## References

- ADR-0388: Per-Stage Token Budgeting (Phase 2)
- ADR-0387: Confidence-Gated Memory (Phase 1)
- ADR-0275: Vibe Engineering (Context Engineering umbrella)
- Phase 3 Implementation: `operator/context_engineering/{task_classifier,adaptive_budget,performance_tracker}.py`
- Tests: `operator/context_engineering/tests/test_adaptive_routing_adr0391.py` (22 tests, 100% pass)
- Feature Flag: `core/console/corvin_core/feature_flags.py::adaptive_context_routing`
