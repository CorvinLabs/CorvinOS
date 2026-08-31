# Phase 1-3 Context Engineering Measurement — Week 1 Canary Rollout

**Document:** Phase 1-3 Measurement Infrastructure (ADR-0392)
**Status:** Ready for Week 1 deployment
**Timeframe:** 1 week (1000+ turns per group)
**Measurement Window:** 2026-08-19 to 2026-08-26

---

## Overview

This document describes the measurement infrastructure for quantifying token savings from Phase 1-3 context engineering optimizations (adaptive context routing, per-stage token budgeting, memory confidence gating).

A 10% canary deployment will measure real token/context savings vs. a control baseline, with deterministic per-tenant group assignment to ensure stable A/B comparison.

---

## Deployment Strategy

### ✅ CHANGED: Direct 100% Deployment (Single-User Environment)

**Previous:** Canary rollout (10% → 50% → 100% over 3 weeks)
**Current:** Direct 100% deployment (immediate rollout)

Reason: Single user (operator only), no need for gradual rollout.

### Configuration

```yaml
# OLD (Canary Strategy)
canary_pct: 10  # 10% get new features
# 90% control, 10% canary

# NEW (Direct Deployment)
canary_pct: 100  # 100% get new features
# All tenants in "active" group
```

### Group Assignment

- **100% Active:** All tenants (and the single user) get Phase 1-3 flags ON
- Deterministic assignment logic still works (compatible with future multi-user deployment)
- No A/B testing needed (focus on monitoring, not measurement)

### Feature Flags (100% Deployment)

```python
# ALL flags enabled by default (direct deployment)
# No measurement/control group needed (single user)

# Phase 1: Memory confidence gating
memory_confidence_gate_enabled = True  # ✅ default ON

# Phase 2: Per-stage token budgeting  
per_stage_token_budgeting = True  # ✅ default ON

# Phase 3: Adaptive context routing
adaptive_context_routing = True  # ✅ default ON

# Vibe Engineering v0.2
vibe_engineering_v0_2 = True  # ✅ default ON

# Dashboard (Phase 3.1)
monitoring_dashboard = True  # ✅ default ON
```

---

## Measurement Infrastructure

### 1. CanaryRouter (`operator/measurement/canary_router.py`)

Stateless, deterministic routing:
- No database dependency
- No state to initialize
- Deterministic hash-based assignment
- ~10 microsecond latency per call

**Public API:**
```python
from operator.measurement.canary_router import CanaryRouter

router = CanaryRouter()

# Check if a tenant is in the canary group
is_canary = router.is_canary_tenant("user_42", canary_pct=10)

# Route feature flags
routed_flags = router.route_by_tenant_percentage(
    tenant_id="user_42",
    feature_flags={"adaptive_context_routing": True, "per_stage_token_budgeting": True},
    canary_pct=10
)
# Returns: {"adaptive_context_routing": False, "per_stage_token_budgeting": False}
# if user_42 is in control group, flags unchanged if in canary group
```

### 2. TokenMetric & MetricsCollector (`operator/measurement/token_metrics.py`)

Non-blocking fire-and-forget metrics collection:
- Thread-safe JSON lines appends
- Immutable `TokenMetric` dataclass
- Automatic validation (context_size_after ≤ context_size_before)
- ~1ms latency per record (async, non-blocking)

**Per-turn metrics captured:**
```python
TokenMetric(
    timestamp="2026-08-19T13:00:00Z",  # ISO 8601
    turn_id="turn_abc123",
    tenant_id="user_42",
    feature_flags_enabled={"adaptive_context_routing": True},
    context_size_before=15000,  # tokens in context before optimization
    context_size_after=8000,    # tokens in context after optimization
    tokens_saved=7000,          # reduction
    latency_ms=1250,            # turn latency
)
```

**Integration point:** Called after each turn in `chat_runtime.py`:
```python
# After each turn
collector.record(TokenMetric(...))
```

### 3. Analysis Pipeline (`operator/measurement/analysis.py`)

Loads metrics and compares control vs. canary groups:
```python
from operator.measurement.analysis import load_metrics, generate_report

metrics = load_metrics("/tmp/metrics.jsonl")
report = generate_report("/tmp/metrics.jsonl")

# Returns
{
    "timestamp": 1724070000,
    "baseline_summary": {
        "turns": 500,
        "avg_latency_ms": 1200,
        "avg_tokens_saved": 2000,  # baseline has low savings
    },
    "canary_summary": {
        "turns": 50,
        "avg_latency_ms": 1250,
        "avg_tokens_saved": 5000,  # canary has high savings
    },
    "comparison": {
        "baseline_turns": 500,
        "canary_turns": 50,
        "baseline_avg_reduction_pct": 15.0,  # 15% context reduction
        "canary_avg_reduction_pct": 45.0,    # 45% context reduction (+ 30%)
        "reduction_improvement_pct": 30.0,   # KEY METRIC
        "latency_delta_ms": 50,  # 50ms slower (acceptable)
        "tokens_saved_improvement": 3000,
    },
    "recommendation": "CONTINUE"  # or INVESTIGATE_LATENCY_IMPACT, etc.
}
```

### 4. Feature Flag Integration (`core/console/corvin_core/feature_flags.py`)

New function: `canary_percentage_routing(tenant_id, flag_id, canary_pct=10)`

```python
from core.console.corvin_core.feature_flags import canary_percentage_routing

# During feature flag resolution
enabled = canary_percentage_routing("user_42", "adaptive_context_routing", canary_pct=10)

# Returns:
# - False if user_42 is in control group (independent of flag setting)
# - is_enabled("adaptive_context_routing", "user_42") if in canary group
```

---

## Measurement Schedule

### Week 1 Timeline

| Time | Activity | Expected Data |
|------|----------|---|
| Day 1–2 | Deployment stabilization | 100+ turns |
| Day 3–7 | Normal measurement window | 1000+ baseline + 100+ canary |
| End of Week 1 | Analysis & decision | Recommendation (CONTINUE / REFINE / ABORT) |

### Sample Size Requirements

- **Minimum:** 100 turns per group
- **Target:** 500+ baseline, 50+ canary (to hit ~10% split on normal traffic)
- **Decision gate:** If < 100 total turns, extend measurement week by 1 week

---

## Decision Framework

### Key Metrics

1. **Context Reduction (PRIMARY):** Canary must show ≥ 25% more context reduction than baseline
   - Baseline: ~15% reduction (Phase 0 + hand-tuned)
   - Target: ~40%+ reduction (Phase 1–3)

2. **Latency Impact:** Canary must not degrade latency by > 100ms (p95)
   - Baseline: ~1200ms (p95)
   - Threshold: < 1300ms (p95)

3. **Stability:** Canary standard deviation of latency must be < 20% of baseline

### Recommendation Logic

```
If canary_turns < 100:
    → COLLECT_MORE_DATA
Elif reduction_improvement_pct < 10%:
    → MARGINAL_IMPROVEMENT (may still proceed)
Elif latency_delta_ms > 100:
    → INVESTIGATE_LATENCY_IMPACT
Else:
    → CONTINUE (to full rollout)
```

---

## Console Integration

### Settings Panel (`Settings → Features → Phase 1-3`)

Shows per-flag status:
- **ID:** `per_stage_token_budgeting`
- **Status:** "Canary measurement" (read-only during test)
- **Group:** "Control" or "Canary" (inferred from tenant hash)
- **Expected savings:** 35% context reduction

### Audit View

Each turn shows:
- `feature_flags_enabled`: which Phase 1-3 flags ran
- `group`: "control" or "canary"
- `tokens_saved`: actual reduction for that turn
- `latency_ms`: turn latency

### Metrics Dashboard (Console → Telemetry → Phase 1-3)

Real-time dashboard showing:
- Current canary vs. baseline comparison
- Rolling 6-hour averages
- Recommendation status

---

## Data Collection

### Files Written

```
~/.corvin/sessions/*/metrics.jsonl
  ↑ Metrics append on every turn (non-blocking)
  ↑ ~500 bytes per metric (JSON compressed)
  ↑ After 1 week: ~5MB per active user
```

### Privacy & Retention

- **Collected:** turn_id, tenant_id, timestamps, token counts, latency (NO prompts, NO user data)
- **Retention:** 14 days (after decision, metrics are deleted)
- **Audit trail:** All metric writes logged (ADR-0232 audit chain)

---

## Rollback Plan

If measurement discovers serious issues:

1. **Latency regression** (> 100ms p95):
   - Disable `per_stage_token_budgeting` immediately
   - Keep `memory_confidence_gate_enabled` (low overhead)
   - Investigate graph traversal cost

2. **Quality regression** (context reduction < 5%):
   - Disable all Phase 1-3 flags
   - Backtrack to Phase 0 (deterministic brief only)

3. **Crash or infinite loops:**
   - Kill canary group's feature flags via emergency flag override
   - Full log dump + debugging session

**Rollback latency:** < 5 minutes (operator edits `tenant.corvin.yaml`)

---

## Test Coverage

**12 comprehensive tests** in `operator/measurement/tests/test_canary_adr0392.py`:

1. Deterministic routing (same tenant always same group)
2. Percentage distribution (10% assignment)
3. Route by percentage (flags disabled for control)
4. TokenMetric creation & validation
5. MetricsCollector recording (thread-safe, non-blocking)
6. CSV export & parsing
7. File I/O roundtrip
8. Group splitting
9. Group comparison (canary vs. baseline)
10. Report generation
11. Routing + metrics integration
12. Complete workflow (100 turns, analysis pipeline)

**Status:** All 12 tests passing ✅

---

## Next Steps (Week 2+)

### If CONTINUE (metrics show improvement):

1. **Day 8:** Expand canary to 25% (100 more tenants)
2. **Day 15:** Expand to 50% (half all users)
3. **Day 22:** Full rollout (100%, flags default ON)
4. **Day 29:** Measure Phase 4 (closed-loop learning)

### If INVESTIGATE_LATENCY_IMPACT:

1. Profile `ContextBridge.build_brief()` cost
2. Optimize graph traversal (ADR-0328)
3. Restart measurement with optimized Phase 2
4. Retarget Phase 3 release to Week 6

### If MARGINAL_IMPROVEMENT:

1. Investigate why context reduction is < 25%
2. Audit Phase 1 confidence gate thresholds
3. Consider combining with Phase 4 (learning-driven optimization)
4. Re-measure with learned models

---

## Appendix: Command Reference

### View Canary Assignment

```bash
# Check if a tenant is in canary
python3 -c "
from operator.measurement.canary_router import CanaryRouter
router = CanaryRouter()
print('Is canary:', router.is_canary_tenant('user_42', 10))
print(router.report_assignment('user_42', 10))
"
```

### View Metrics

```bash
# Live metrics summary
python3 -c "
from operator.measurement.token_metrics import MetricsCollector
from pathlib import Path
collector = MetricsCollector(Path.home() / '.corvin/sessions/metrics.jsonl')
metrics = collector.load_from_file()
print(f'Total turns: {len(metrics)}')
print(collector.summary())
"

# Full report
python3 -c "
from operator.measurement.analysis import generate_report
report = generate_report('/path/to/metrics.jsonl')
print(report['recommendation'])
print(report['comparison']['reduction_improvement_pct'])
"
```

### Disable Canary (Emergency)

```bash
# Edit tenant config
corvin config set features.per_stage_token_budgeting false
corvin config set features.adaptive_context_routing false
corvin config set features.memory_confidence_gate_enabled false

# Restart
systemctl restart corvin-service
```

---

**Document ID:** ADR-0392 § Phase 1: Measurement  
**Author:** Claude Code  
**Date:** 2026-08-19  
**Status:** READY FOR DEPLOYMENT
