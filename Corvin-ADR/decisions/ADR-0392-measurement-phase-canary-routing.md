---
id: ADR-0392
status: accepted
supersedes: []
depends_on: [ADR-0387, ADR-0388, ADR-0391]
related: []
commits: []
paths:
  - operator/measurement/canary_router.py
  - operator/measurement/token_metrics.py
  - operator/measurement/analysis.py
  - core/console/corvin_core/feature_flags.py
docs:
  - docs/measurement/canary-rollout-week1.md
---

# ADR-0392 — Measurement Phase: Canary Routing & A/B Testing Infrastructure

**Date:** 2026-08-19  
**Deciders:** shumway (Claude)  
**Status:** Accepted

## Context

Phases 1-3 of context optimization claim 40-50% token reduction. Before full rollout, we need to validate these savings in production on real traffic, compare against a control group, and establish the measurement infrastructure for phases 4-5.

## Decision

Implement a stateless, deterministic canary routing system:

1. **CanaryRouter** — Hash-based tenant routing (90% control, 10% canary)
   - Deterministic: same tenant always assigned to same group
   - Stateless: no database, no state initialization
   - Performance: ~10µs per call

2. **MetricsCollector** — Non-blocking token/latency tracking
   - Fire-and-forget JSON-lines writes (~1ms per record)
   - Thread-safe, immutable TokenMetric dataclass
   - CSV export for analysis

3. **Analysis Pipeline** — Statistical comparison of groups
   - Load metrics from JSON-lines
   - Split by control/canary
   - Calculate reduction_improvement_pct, latency_delta_ms
   - Decision recommendations (CONTINUE/INVESTIGATE/MARGINAL)

4. **Feature Flag Integration** — Canary-aware flag resolution
   - `canary_percentage_routing(tenant_id, flag_id, canary_pct=10) → bool`
   - Graceful degradation if measurement module unavailable

## Rationale

- **Determinism:** Same tenant always gets same experiment assignment (stable results, reproducibility)
- **Scalability:** Stateless routing works at any scale (no bottleneck)
- **Safety:** Control group runs baseline, canary group runs optimizations (can always roll back)
- **Measurement:** Isolate optimization impact from other variables (A/B testing best practice)

## Constraints

- Sample size: 1000+ turns (900+ baseline, 100+ canary) required for statistical power
- Duration: 1 week minimum (captures diurnal patterns)
- Decision threshold: Canary must show ≥25% improvement over baseline to continue

## Compliance

✅ No PII leakage (metrics are tokens/latency only)  
✅ Audit trail records measurement events  
✅ No user-visible changes during measurement phase  

## Files

| File | LoC | Purpose |
|------|-----|---------|
| canary_router.py | 131 | Deterministic routing |
| token_metrics.py | 250 | Metrics collection |
| analysis.py | 249 | Statistical comparison |
| test_canary_adr0392.py | 615 | 29 comprehensive tests |
| canary-rollout-week1.md | 369 | Deployment guide |

**Total: 1,706 LoC, 29 tests, 0 breaking changes**

## Timeline

- Week 1: Measurement phase (canary 10%)
- Week 2+: Rollout decision based on data
