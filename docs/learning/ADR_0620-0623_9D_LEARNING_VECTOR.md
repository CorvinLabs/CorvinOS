# ADR-0620–0623: 9D Learning Vector System

**Status:** Proposed (Ready for Design Review)  
**Date:** 2026-09-06  
**Deciders:** shumway, Claude  
**Related:** ADR-0614, ADR-0615, ADR-0616

---

## ADR-0620: Infrastructure Loops (Tier 2)

**Decision:** Add three learnable infrastructure feedback loops (memory, plugins, security) to extend 6D core loss → 9D system.

### Context
Current 6D system (ADR-0614–0616) learns at task execution level. But three system components are static:
- Memory plugin: Preservation weights fixed (always preserve task_id, conditionally preserve user_prefs)
- Plugin ecosystem: Config static (cache_size, ttl never adapt)
- Security gates: Compliance thresholds fixed (PII detection threshold constant)

### Decision
Add Tier 2 loops to learn these configurations:
- **L_memory:** Preservation weights adapt based on user feedback ("Was context relevant?")
- **L_plugins:** Plugin config adapts based on metrics (latency, error rate)
- **L_security:** Compliance thresholds adapt based on false positive/negative rates

### Consequences
- ✅ System components self-tune (better performance)
- ✅ Audit-logged (every adaptation → event)
- ⚠️ Slower convergence (Tier 2 has fewer feedback signals; use α₂=5%)
- ⚠️ New event types (3 per loop, total 9 new events)

### Constraints
- Tier 2 updates at different rates than Tier 1 (hourly for plugins, daily for security, per-request for memory)
- No Tier 2 loop can break Tier 1 guarantees (fail-closed, consent, house-rules)
- All updates audit-logged + hash-chained

**Ref:** CONCEPT-0032, Implementation Roadmap Phase 1

---

## ADR-0621: Meta Loop Hyperparameter Tuning (Tier 3)

**Decision:** Add meta-optimizer (Tier 3) that learns optimal weight vector w₁..w₆ via tiered damping (α₃=1% update rate).

### Context
Operator manually tunes weights w₁..w₆ via console slider (slow, subjective). Meta-optimizer learns from loss trends.

### Decision
Tier 3 meta-optimizer:
- Observes: L_total trend over ≥100 samples
- Learns: What weights minimize L_total?
- Updates: w_new = (1 - α₃)·w_old + α₃·w'_optimized (α₃ = 1%)
- Damping: Prevents oscillation (proved in simulation)
- Operator control: Manual weights still work (hybrid tuning)

### Consequences
- ✅ Weights self-optimize (faster convergence than manual tuning)
- ✅ Operator can still override (set w_target, meta learns around it)
- ✅ Stable (damping α₃=1% prevents feedback loops)
- ⚠️ Very slow (needs ~10K samples to adapt weights)
- ⚠️ New risk: divergence if meta learns wrong direction (requires monitoring)

### Constraints
- Tiered damping: α₁=10%, α₂=5%, α₃=1% (faster → slower)
- Oscillation detection: if Δw variance >0.1 over 5 steps → pause meta learning
- Divergence recovery: Manual operator intervention required (no auto-reset)

**Ref:** ADR-0621-Hyperparameter-Tuning.md, Implementation Roadmap Phase 2

---

## ADR-0622: 9D Loss Function Schema

**Decision:** Unify all 9 dimensions into single loss computation: 6D core + 3 Tier 2 + penalty term.

### Context
Current 6D loss sums six independent loops:
```
L_total = w₁·L₁ + w₂·L₂ + ... + w₆·L₆
```

With Tier 2 + 3, we have 9 dimensions. Need unified schema.

### Decision
```
L_total = 
  + w₁·L_routing + w₂·L_context + w₃·L_exec + w₄·L_conf + w₅·L_comply + w₆·L_learn  [Tier 1]
  + w₇·L_memory + w₈·L_plugins + w₉·L_security                                       [Tier 2]
  - λ·L_meta_stability                                                               [Tier 3 penalty]

where:
  w₁..w₉ ∈ [0,1], Σwᵢ = 1.0
  λ = 0.1 (penalizes large weight swings, prevents meta oscillation)
```

### Audit Schema
Every loss computation creates event:
```
{
  tenant_id, timestamp, event_type: "loss_computed",
  L_total, [L₁..L₉], [w₁..w₉], [α₁, α₂, α₃],
  meta_delta: Δw (if meta updated), stability_penalty: λ·L_meta,
  hash, prev_hash  (audit chain)
}
```

### Consequences
- ✅ All 9 dimensions observable (dashboard shows all)
- ✅ Audit-complete (zero decisions silent)
- ✅ Pareto frontier can be computed (3D plot: cost vs quality vs speed)
- ⚠️ More events (larger audit trail, ~100 events/day → ~500 with 9D)
- ⚠️ Computation overhead (9D gradient = 9 backprop passes; profile for SLA)

**Ref:** ADR-0622-9D-Loss-Schema.md

---

## ADR-0623: Tiered Damping Protocol

**Decision:** Use three damping rates (α₁, α₂, α₃) to stabilize learning across tiers and prevent oscillation.

### Context
Without damping, Tier 3 meta loop causes oscillation (weight swings that never converge).

### Decision
Damping rates per tier:
```
Tier 1 (Core):           η₁ = 0.10  (10% per step, ~2K samples to converge)
Tier 2 (Infrastructure): α₂ = 0.05  (5% per step, ~5K samples to converge)
Tier 3 (Meta):           α₃ = 0.01  (1% per step, ~10K samples to converge)

Update rule:
  w_new = (1 - α)·w_old + α·w'_optimized
```

### Stability Proof
- Tier 1 converges fast (proven in ADR-0616)
- Tier 2 converges 2.5x slower (fewer signals, but stable)
- Tier 3 converges 10x slower (highest-order, most unstable → slowest)
- No tier couples backward (meta can't break core)
- Oscillation detection: if Δw variance >threshold for 5 steps → pause learning

### Operator Control
```
if operator sets w_target:
  w_new = (1 - operator_weight)·w_meta + operator_weight·w_target
  (default: operator_weight = 30%, meta_weight = 70%)
```

### Consequences
- ✅ Provably stable (tiered damping prevents oscillation)
- ✅ Predictable convergence (each tier has known SLA)
- ✅ Operator retains control (can override meta anytime)
- ⚠️ Slow adaptation (meta takes ~10K samples = ~3 hours at 1 req/sec)
- ⚠️ Complex (three damping parameters to tune)

### Constraints
- Divergence detection per tier (variance monitoring)
- No tier's learning can violate prior tier's invariants (fail-closed gates always enforced)
- Audit: Every damping decision logged (α, Δw, why updated)

**Ref:** ADR-0623-Tiered-Damping.md, Convergence Proof Appendix

---

## Summary: 9D = 6D + Tier 2 + Tier 3

| **Component** | **Loops** | **Update Rate** | **Convergence** | **Damping** |
|---|---|---|---|---|
| **Tier 1 (Core)** | 6 (proven) | 10% | 2K samples | η₁=0.10 |
| **Tier 2 (Infrastructure)** | 3 (NEW) | 5% | 5K samples | α₂=0.05 |
| **Tier 3 (Meta)** | 1 (NEW) | 1% | 10K samples | α₃=0.01 |
| **Penalty (Stability)** | 1 (NEW) | N/A | Continuous | λ=0.1 |

**Total:** 11 loss components, 9 learnable weights, 4 ADRs, 3 phases, 10 weeks, ~2800 LoC

---

## Open Design Questions (For Review)

1. Should Tier 2 feedback be async (hourly/daily) or per-request?
   - **Proposal:** Per-request for L_memory (user feedback), hourly for L_plugins/L_security (metrics)

2. Should operator be able to lock Tier 3 (disable meta learning)?
   - **Proposal:** Yes, `/skill config os.meta_optimizer --disable` (audit event)

3. What's the rollback strategy if meta diverges?
   - **Proposal:** Automatic: pause learning, hold weights. Manual: operator runs `corvin learning rollback-weights --to=checkpoint`

4. Convergence timeout: 10K samples = 3 hours. Acceptable?
   - **Proposal:** Configurable, default 10K. Alert at 5K if not converged.

---

## Next Steps

1. Design review (this ADR)
2. Write ADR-0620–0623 formally (Corvin-ADR repo)
3. Start Phase 1 implementation (Tier 2 loops)
4. E2E + adversarial tests
5. Production deployment

---

**Ref Documents:**
- CONCEPT-0032: 9D Learning Vector (full design rationale)
- ADR-0614–0616: 6D system (foundation)
- ADR-0620: Infrastructure Loops
- ADR-0621: Meta Loop Hyperparameter Tuning
- ADR-0622: 9D Loss Function Schema
- ADR-0623: Tiered Damping Protocol
