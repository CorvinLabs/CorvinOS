---
id: ADR-0370
status: PROPOSED
supersedes: []
depends_on: [ADR-0358]
related: [ADR-0369]
commits: []
paths:
  - core/learning/adaptive_strategy.py
  - core/orchestration/subsystems/strategy_advisor.py
docs:
  - docs/claude-ref/layer-28-conversation-recall.md
---

# ADR-0370: Adaptive Strategy Ladder — Fingerprint-Gated Ranking

**Status:** PROPOSED  
**Date:** 2026-08-19  
**Deciders:** shumway (via Claude Code, coder persona)

---

## Context

### Problem: One-Size-Fits-All Strategy Ranking

StrategyAdvisor ranks strategies by empirical success rate only. It ignores operator
fingerprint (expertise, speed/risk preferences) and cost efficiency. Result: tasks select
strategies poorly matched to operator profile, increasing latency and cost.

**Example:** Conservative operator (risk_tolerance=0.3) gets a risky strategy because it has
the highest raw success rate. Conservative operator fails, retries cost-inefficiently.

**Current Loss:** Suboptimal strategy selection; 5–15% of tasks pick wrong strategy.  
**Impact:** Longer task duration, higher token cost, operator dissatisfaction

### Dependencies

- **ADR-0358:** OperatorFingerprint (learns operator profile from 20+ signals)
- **Layer 28:** Task context persistence (coherence manager)

---

## Design: Adaptive Strategy Engine

### 1. Confidence-Gated Ranking

**Algorithm:** Three-factor weighted score with confidence gate:

```
if fingerprint.confidence >= 0.7:
    # Adaptive ranking (fingerprint-informed)
    score = success_rate * 0.5
          + operator_preference * 0.3
          + cost_efficiency * 0.2
else:
    # Empirical fallback (success_rate only)
    score = success_rate
```

**Weights:**
- `success_rate` (50%): Empirical success from historical outcomes
- `operator_preference` (30%): Alignment with operator profile + task expertise
- `cost_efficiency` (20%): Normalized inverse cost (1.0 - cost/100)

### 2. Operator Preference Score

Three sub-factors (normalized to [0, 1]):

1. **Task-type expertise** (40% of preference):  
   `fingerprint.expertise_profile[task_type]` (default 0.5)

2. **Speed alignment** (30% of preference):  
   Operator fast (speed_pref > 0.7) → prefer low-latency strategies  
   Operator slow (speed_pref < 0.3) → prefer careful strategies  
   Score = `1.0 - |speed_pref - (1.0 - normalized_latency)|`

3. **Risk alignment** (30% of preference):  
   Operator aggressive (risk_tol > 0.7) → prefer high-success strategies  
   Operator conservative (risk_tol < 0.3) → accept lower-success, safer strategies  
   Score = `1.0 - |risk_tol - success_rate|`

### 3. Integration: StrategyAdvisor.get_strategy()

```python
def get_strategy(
    available_strategies: List[StrategyOption],
    fingerprint: Optional[OperatorFingerprint] = None,
    task_type: str = "general",
) -> Optional[StrategyOption]:
    if fingerprint and fingerprint.confidence >= 0.7:
        # Use adaptive ranking
        ranked = adaptive_engine.rank_strategies_by_fingerprint(
            fingerprint, available_strategies, task_type
        )
        return ranked[0] if ranked else None
    else:
        # Empirical fallback
        return max_by_success_rate(available_strategies)
```

---

## Loss Function

**Strategy Suboptimality Loss:**

```
loss = 1.0 - selected_strategy.success_rate
```

**Closed-loop:**
- Task outcome recorded → success_rate updated
- Next same task uses improved empirical rate
- If fingerprint confidence increases, adaptive ranking engages at 0.7 threshold

---

## Testing

**Unit tests (42 total):**
- StrategyOption.weighted_score() with various weight combinations
- AdaptiveStrategyEngine preference scoring (task expertise, speed, risk alignment)
- Confidence gate (adaptive vs empirical at 0.7 boundary)
- Fallback behavior when fingerprint missing

**Integration tests (20+ total):**
- StrategyAdvisor.get_strategy() with live fingerprint objects
- Ranking consistency across multiple calls
- Cache invalidation on score updates

**E2E tests (5 scenarios):**
- End-to-end strategy selection and outcome feedback
- Loss = 0.0% (no regression on baseline)
- Fingerprint confidence progression (0.1 → 0.8)

---

## Constraints & Assumptions

1. **Fingerprint confidence required for adaptive ranking:** Must be ≥ 0.7 to engage  
   Rationale: Below 0.7, operator profile too noisy; empirical fallback is safer

2. **Cost normalization:** Assumes typical cost range [1, 100] cents  
   Outside this range: clamped to [0, 1] via `min(1.0, cost / 100.0)`

3. **No in-flight fingerprint updates:** Fingerprint passed at strategy selection time  
   Cannot re-rank mid-task if fingerprint updates. Addressed in v0.3.

4. **Task-type expertise default:** 0.5 if task_type not in fingerprint  
   Conservative: assumes unknown expertise = neutral

---

## Rollout

**Feature flag:** `FEATURE_ADAPTIVE_STRATEGIES` (default: `true`)  
**Confidence threshold:** 0.7 (not configurable in v0.2)  
**Fallback:** Always available empirical-only ranking

---

## Alternatives Considered

1. **Weighted fingerprint only (no fallback):**  
   Risk: Unreliable fingerprint early in task life (confidence < 0.7) → poor ranking  
   Decision: Confidence gate + fallback is safer

2. **Dynamic confidence threshold:**  
   Risk: Adds complexity; operator profile varies per domain  
   Decision: Fixed 0.7 for v0.2; revisit in v0.3

3. **Four factors (include task-duration preference):**  
   Risk: Overfit to operator temporal preference; less stable  
   Decision: Stick with three (success, expertise/speed/risk, cost)

---

## Success Criteria

- [ ] Adaptive ranking selects strategy matching operator profile (qualitative review)
- [ ] E2E loss = 0.0% (no regression vs baseline)
- [ ] Fingerprint confidence gate respected (adaptive only when ≥ 0.7)
- [ ] Fallback works when fingerprint missing or low confidence
- [ ] Feature flag toggles adaptive behavior (on/off)

---

## Timeline

- **Week 1:** Code review + integration testing
- **Week 2:** Canary rollout (10% of new tasks)
- **Week 3:** Measure loss + strategy selection quality
- **Week 4:** Full rollout or pivot to v0.3 refinement

