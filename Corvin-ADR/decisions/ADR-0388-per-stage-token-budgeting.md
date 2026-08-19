---
id: ADR-0388
status: PROPOSED
depends_on: [ADR-0314, ADR-0255]
relates_to: [ADR-0024, ADR-0043, ADR-0119]
paths:
  - core/orchestration/subsystems/token_budget.py
  - core/orchestration/tests/test_token_budget_adr0388.py
  - operator/context_engineering/tests/test_token_budget_pipeline_adr0388.py
  - core/console/corvin_core/feature_flags.py
  - operator/context_engineering/pipeline.py
  - operator/context_engineering/stages/
docs:
  - docs/implementation/PERFORMANCE_SLOS.md
---

# ADR-0388: Per-Stage Token Budgeting in Context Pipeline

**Decision Date:** 2026-08-19  
**Author:** Claude Code (Haiku 4.5)  
**Status:** PROPOSED

## Problem

The CorvinOS context pipeline executes in fixed order: Memory → ADR → Skills → Synthesis.
Each stage consumes tokens without constraint, leading to two pathologies:

1. **Tail waste:** Later stages starve because earlier stages consume their budget indiscriminately.
   Example: Memory stage extracts 1000 tokens of irrelevant session history; ADR stage gets 500 tokens left,
   cannot import decision records needed for the task.

2. **Priority inversion:** Low-value stages (e.g., Skills disambiguation when the user is explicit) consume
   tokens that Synthesis needs for the core reasoning loop, degrading output quality.

3. **Unpredictable cost:** Operator cannot reason about token burn per pipeline run; a 2000-token model call
   becomes a 2500–3500-token call depending on stochastic earlier-stage decisions.

**Impact:** Reduced reasoning depth, higher cost variance, unpredictable latency (token overspill forces
model re-run or truncation).

## Options Considered

### Option A: No Budgets (Status Quo)
- **Pros:** Zero implementation cost, maximum stage autonomy.
- **Cons:** Tail waste, priority inversion, cost unpredictability persist.

### Option B: Single Global Budget Only
- **Pros:** Operator specifies one budget; simple to explain.
- **Cons:** No allocation strategy; stages compete for the same pool, requiring complex arbitration logic.
  Doesn't solve priority inversion if Memory stage reserves 80% and starves downstream.

### Option C: Per-Stage Budgets with Cascade Logic (CHOSEN)
- **Pros:** Clear ownership (each stage knows its token ceiling), predictable cost, priority alignment
  (Synthesis gets largest slice), cascading spill enables efficiency (unused Memory tokens flow to ADR).
- **Cons:** More complex context manager; requires feature flag for backward compatibility.

### Option D: Dynamic Budgeting with ML Predictor
- **Pros:** Learns optimal allocation per task type over time.
- **Cons:** Requires months of telemetry data, adds latency (predictor inference), risk of adversarial
  gaming (user prompt that tricks predictor into starving Synthesis). Defer to v0.3.

**Decision:** **OPTION C** — per-stage budgets with cascade (waterfall) logic.

## Decision

### Allocation Strategy

Stages receive fixed percentages of the remaining token budget, left-to-right:

```
Input budget: 4000 tokens (example)

Memory stage:       30% of 4000 = 1200 tokens available
  Actual use:       800 tokens
  Surplus:          400 tokens

ADR stage:          20% of 4000 = 800 tokens available
  + cascaded:       400 tokens (from Memory)
  Ceiling:          1200 tokens available
  Actual use:       600 tokens
  Surplus:          600 tokens

Skills stage:       15% of 4000 = 600 tokens available
  + cascaded:       600 tokens (from ADR)
  Ceiling:          1200 tokens available
  Actual use:       400 tokens
  Surplus:          800 tokens

Synthesis stage:    35% of 4000 = 1400 tokens available
  + cascaded:       800 tokens (from Skills)
  Ceiling:          2200 tokens available
  Actual use:       2100 tokens (core reasoning loop)
  Status:           ✅ sufficient depth
```

### New Components

1. **TokenBudget Context Manager** (`core/orchestration/token_budget.py`):
   - Immutable configuration: `stage_name`, `base_budget`, `cascade_enabled`, `strict_mode`.
   - Instance tracking: remaining balance per stage, cascade pool, audit trail.
   - Query API: `remaining()`, `claim(n_tokens)`, `available_with_cascade()`.
   - Fail semantics: strict mode raises `TokenBudgetExceeded`; soft mode truncates and logs.

2. **Pipeline Integration** (`operator/context_engineering/pipeline.py`):
   - ExecutionContext carries `token_budget: TokenBudget` alongside `task_data`.
   - Each stage wrapper calls `token_budget.claim(n_tokens)` before assembly, after rendering.
   - Stages implement `SoftTokenLimit` interface: accept a budget parameter in `execute()`.

3. **Feature Flag** (`spec.features.per_stage_token_budgeting`):
   - Default: `false` (backward compatible; global budget behavior).
   - When `true`: per-stage budgets enforced; stages must respect budget contracts.
   - Gating logic in `ContextBridge.execute()`: wraps stage calls conditionally.

### Configuration

```yaml
# tenant.corvin.yaml
spec:
  context_pipeline:
    token_budget:
      global_budget: 4000
      cascade_enabled: true
      stages:
        memory:
          allocation: 0.30
          strict_mode: false  # soft truncation
        adr:
          allocation: 0.20
          strict_mode: false
        skills:
          allocation: 0.15
          strict_mode: false
        synthesis:
          allocation: 0.35
          strict_mode: true   # must not truncate reasoning
  features:
    per_stage_token_budgeting: false  # until operator enables
```

## Consequences

### Breaking Changes
- Stages that ignore budget constraints will see truncated input in strict mode.
- Operator must enable feature flag and set allocations before enforcement kicks in.
- Cost projections will improve but require re-baseline of existing telemetry.

### Mitigation
- Feature flag defaults to `false`; zero behavior change on existing installs.
- New stages added after launch must implement `SoftTokenLimit` interface.
- Audit trail records `token_claim(stage, requested, granted, cascaded, reason)` for every stage.

### Benefits
1. **Predictable cost:** Operator controls max spend per pipeline run (feature-flag tuning).
2. **Prioritized reasoning:** Synthesis gets larger allocation; low-value stages cannot starve it.
3. **Debuggability:** Audit log shows token flow; operators can identify tail-waste stages by querying `token_claim` events.
4. **Cascade efficiency:** Unused tokens flow downstream; no per-stage starvation.

## Alternatives Considered and Rejected

| Alternative | Reason for Rejection |
|---|---|
| Single operator-tuned knob per stage (no cascade) | Doesn't solve cascade efficiency; operator must micro-tune four knobs simultaneously. |
| ML predictor (Option D) | No production data yet; adds latency. Viable Phase 0.4 enhancement. |
| Token limits per model call only (not per stage) | Too late in the pipeline; stages have already assembled large prompts. |

## Implementation Plan

### Phase 1: Core (Week 1)
- Implement `TokenBudget` context manager.
- Add `SoftTokenLimit` interface to stage protocol.
- Wire into `ContextBridge.execute()` with feature flag.
- Unit tests: 12 (budget arithmetic, cascade logic, fail modes).

### Phase 2: Integration (Week 2)
- Refactor Memory, ADR, Skills stages to use `token_budget.claim()`.
- Audit trail integration: emit `token_claim` events.
- E2E test: full pipeline with 4000-token budget, verify allocation and cascade.

### Phase 3: Tuning (Week 3)
- Telemetry: measure actual token use per stage across 100 tasks.
- Adjust allocation % based on empirical data.
- Documentation and operator playbook.

### Phase 4: Rollout (Week 4)
- Enable feature flag in canary (10% users).
- Monitor cost variance metric; target <5% drift from projected budget.
- Rollout to 100% if variance < 5%; else adjust allocations and re-run.

## Metrics

- **Tail waste reduction:** Compare (spend on Synthesis + Memory + ADR) before/after.
  Target: 10% improvement (more tokens available for core reasoning).
- **Cost predictability:** Std dev of actual spend vs. projected budget across 100 runs.
  Target: <2% (tight control).
- **Stage truncation rate:** % of runs where strict-mode stages hit budget ceiling.
  Target: <1% (allocations are generous enough).

## Risks

1. **Cascade deadlock:** if a stage always overshoots, downstream stages starve.
   *Mitigation:* Monitor truncation rate; adjust allocations within 24h if >5%.

2. **Operator confusion:** four configurable knobs; risk of misconfiguration.
   *Mitigation:* Ship default allocations validated on 1000 real tasks. Operator playbook with examples.

3. **Backward-compat regression:** an operator enables feature flag without updating stages.
   *Mitigation:* Stages implement `SoftTokenLimit` in parallel during Phase 1; flag is inert
   until all stages are ready.

## References

- ADR-0314: Learning Infrastructure (event schema for token telemetry)
- ADR-0255: Worker Engine Delegation (orchestrator context)
- PERFORMANCE_SLOS.md: Benchmarks and cost targets
- Layer 20 (Forge context routing): docs/claude-ref/layer-20-*.md

## Questions for Review

1. Should cascade pool have per-stage reserve (e.g., Skills keeps 100 tokens minimum)?
   *Recommendation:* No; cascade is simple-by-design. Add guardrails in v0.3 if needed.

2. Should operators be able to define custom allocations per task type (e.g., long-form requires 50% for Synthesis)?
   *Recommendation:* Not in v0.1. Add task-type routing in v0.2 once we have telemetry.

3. Should synthesis stage get a hard floor (e.g., minimum 2000 tokens, regardless of config)?
   *Recommendation:* Yes; add `stages.<name>.min_budget: 2000` to config schema.

---

**Status:** Ready for Concept Gate and E2E Wiring Proof gate (following token_budget.py implementation).
