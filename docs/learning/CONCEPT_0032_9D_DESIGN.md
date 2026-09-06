# CONCEPT-0032: 9D Learning Vector with Meta Loop (Full Design)

**Status:** DESIGN PHASE (Ready for ADR-0620-0623)  
**Date:** 2026-09-06  
**Scope:** Extend 6D core loops → 9D (add TIER 2 infrastructure) → Meta Loop (TIER 3 hyperparameter tuning)  
**Effort:** 10 weeks, 3 phases, ~2800 LoC + tests

---

## Dialektische Synthese (Hidden Assumptions)

### **Thesis: 6D Unified Loss is Complete**
- "We have 6 independent loops (routing, context, exec, conf, comply, learning)"
- "They couple via backpropagation"
- "Converges in <2000 samples"
- **Assumption:** The 6D system learns at the system level. No learning about learning itself.

### **Antithesis: But Three More Loops Are Learnable**
1. **Memory Loop** — "Is preserved context actually helping?" (Tier 2 Infrastructure)
2. **Plugin Loop** — "Are loaded plugins performing as expected?" (Tier 2 Infrastructure)
3. **Security Loop** — "Is compliance checking optimal?" (Tier 2 Infrastructure)

Plus a **Meta Loop (Tier 3):**
4. **Hyperparameter Loop** — "Are the weights w₁..w₆ themselves optimal?" (Self-tuning)

### **Synthesis: 9D = 6D Core + 3 Infrastructure + 1 Meta**

```
TIER 1 (Core Loops):     L_routing, L_context, L_exec, L_conf, L_comply, L_learn
                         (existing 6D, proven)

TIER 2 (Infrastructure): L_memory, L_plugins, L_security
                         (learnable system components)

TIER 3 (Meta Loop):      L_meta_hyperparameters
                         (learns optimal w₁..w₆ weights)

INVARIANT: Tiered damping prevents coupling oscillation
           (Meta learns slowly, ≤1% per step; avoids feedback loops)
```

---

## Part 1: Understanding the Current 6D System

### **6D Loss Vector (ADR-0614)**

```
L_total = w₁·L_routing + w₂·L_context + w₃·L_exec + w₄·L_conf + w₅·L_comply + w₆·L_learn

where:
  w₁..w₆ ∈ [0,1], Σwᵢ = 1.0
  Gradients backprop through decision DAG
  Converges <2000 samples
```

### **Six Independent Feedback Loops (ADR-0615)**

| **Loop** | **Input** | **Feedback Signal** | **Output** | **Update** |
|---|---|---|---|---|
| L_routing | Task | Was route correct? (yes/no) | Routing weights | ±Δw_router |
| L_context | Context | Did context help? (score) | Preservation weights | ±Δw_context |
| L_exec | Task + Agent | SLA met? Error rate? | Skill retry strategy | ±Δw_skill |
| L_conf | Predictions | Accuracy (P_correct) | Confidence thresholds | ±Δw_conf |
| L_comply | All outputs | Violations? (count) | Guard thresholds | ±Δw_guard |
| L_learn | All gradients | Divergence? (variance) | Learning rate η | ±Δη |

### **Backpropagation (ADR-0616)**

Gradients flow BACKWARD:
```
∇L_total = [∂L/∂w₁, ∂L/∂w₂, ..., ∂L/∂w₆]
           ↓
Optimizer: w_new = w_old - η·∇L  (gradient descent)
           ↓
Divergence detection: if |Δw| > threshold for 5 steps → pause + alert
```

**Key Property:** The 6D system is **static-weight** (w₁..w₆ are tunable by operator slider, but not self-optimizing during runtime).

---

## Part 2: Adding Three Infrastructure Loops (Tier 2)

### **Loop 7: L_memory — "Is Preserved Context Helping?"**

**Problem:** Current L10 (Context Adapter Skill) decides preservation weights statically.
- Preserve task_id? Always yes (makes sense)
- Preserve user_preferences? Sometimes (depends on task type)
- Preserve prior_decisions? Rarely (causes context bloat)

**Feedback Signal:**
- User: "Was the context relevant?" (yes/no)
- Auto-metric: Context relevance score (cosine similarity to response)

**Learning:**
```
preservation_weights = {
  task_id: 1.0 (always),
  user_prefs: sigmoid(feedback_score),  ← LEARNS
  prior_decisions: sigmoid(feedback_score * 0.5),  ← LEARNS (lower weight)
}
```

**Update:** Every time context is evaluated, L_memory feedback updates preservation strategy.

### **Loop 8: L_plugins — "Are Loaded Plugins Performing?"**

**Problem:** Plugins are loaded at boot, no feedback on performance.
- Memory plugin: Is it speeding up context retrieval?
- Cache plugin: Is cache hit rate > 70%?
- Telemetry plugin: Is it causing latency?

**Feedback Signal:**
- Metric: Latency (goal: <50ms for plugin overhead)
- Metric: Error rate (goal: <0.1%)
- User: "Is system performance acceptable?" (yes/no)

**Learning:**
```
plugin_config = {
  memory: {
    enabled: true,
    cache_size: adapt(feedback_latency),  ← LEARNS optimal size
    ttl: adapt(feedback_hitrate),         ← LEARNS optimal TTL
  }
}
```

**Update:** Hourly, based on rolling metrics (latency, errors).

### **Loop 9: L_security — "Is Compliance Checking Optimal?"**

**Problem:** L16 (Consent/House-Rules) gates run every request. But:
- Are guards too strict (false positives)?
- Are guards too loose (letting through violations)?

**Feedback Signal:**
- Metric: False positive rate (flagged as PII but wasn't)
- Metric: False negative rate (didn't flag actual PII)
- User: "Was that block justified?" (yes/no)
- Audit: Actual violations caught vs. missed

**Learning:**
```
compliance_thresholds = {
  pii_detection_threshold: adapt(fp_rate, fn_rate),  ← LEARNS optimal threshold
  house_rules_strictness: adapt(false_positive_rate),  ← Adjust enforcement
  consent_ttl: adapt(user_feedback),                  ← Learn user preferences
}
```

**Update:** Daily, based on compliance audit logs.

---

## Part 3: Meta Loop (Tier 3) — Hyperparameter Self-Tuning

### **The Meta Problem**

Current system: Operator manually tunes w₁..w₆ via console slider.
```
Operator decision: "I want 40% routing, 30% context, 30% performance"
                    ↓
            w = [0.4, 0.3, 0.3, ...]
                    ↓
         Next request uses these weights
                    ↓
       Does it work? Operator checks dashboard, adjusts slider
```

**Issue:** Operator's tuning is slow, subjective, not evidence-based.

### **Meta Loop Solution: Self-Tuning Weights**

```
Meta Optimizer:
  Input: (w₁..w₆, L_total trend, user_feedback, operator_goal)
           ↓
  Learn: What (w₁, w₂, ..., w₆) minimizes L_total?
           ↓
  Output: w'₁..w'₆ (updated weights)
           ↓
  Damping: w_new = (1 - α)·w_old + α·w'  (slow update, α ≤ 1%)
           ↓
  Audit: meta_weight_updated event (before, after, reason)
```

### **Why Damping? (Coupling Oscillation Risk)**

Without damping, meta loop can cause oscillation:

```
Iteration 1: w = [0.5, 0.3, 0.2]  (routing-heavy)
             L_total = 0.85 (good)
             ↓
Iteration 2: Meta thinks "routing too high, hurt context"
             w' = [0.3, 0.5, 0.2]  (context-heavy)
             L_total = 0.87 (slightly worse!)
             ↓
Iteration 3: Meta swings back
             w' = [0.5, 0.3, 0.2]  (routing-heavy again!)
             OSCILLATION DETECTED → Loop never converges!
```

**Solution:** Damping (slow learning rate for meta loop)

```
w_new = (1 - α)·w_old + α·w'_optimized
        α = 0.01  ← Only 1% update per step (very slow)
        
This takes ~100 steps to adapt, but STABLE (no oscillation)
```

### **Operator Control (Not Removed, Enhanced)**

Operator can still tune weights via console slider:

```
BEFORE (Manual):  Operator: "Set w₁ = 0.4"
                           ↓
                    w = [0.4, 0.3, 0.3, ...]

AFTER (Hybrid):   Operator: "Prefer routing, but let meta optimize"
                           ↓
                    w_target = [0.4, 0.3, 0.3, ...]  (operator intent)
                    w_current = Meta's current guess
                           ↓
                    w_new = blend(w_target, w_current, operator_preference)
                           ↓
                    Meta learns around operator's intent (constrained learning)
```

---

## Part 4: 9D Loss Function (Full)

```
L_total = 
  + w₁·L_routing      (core loop 1)
  + w₂·L_context      (core loop 2)
  + w₃·L_exec         (core loop 3)
  + w₄·L_conf         (core loop 4)
  + w₅·L_comply       (core loop 5)
  + w₆·L_learn        (core loop 6)
  + w₇·L_memory       (infrastructure loop 1) ← NEW
  + w₈·L_plugins      (infrastructure loop 2) ← NEW
  + w₉·L_security     (infrastructure loop 3) ← NEW
  - λ·L_meta          (meta loop penalty for instability) ← NEW

where:
  w₁..w₉ ∈ [0,1], Σwᵢ = 1.0  (weights sum to 1)
  λ = 0.1  (stabilization penalty: penalizes large weight swings)
  
Meta-optimizer learns: w₁..w₆ by minimizing L_total over ≥100 samples
  (Tier 3: self-tuning, but SLOWLY via damping α=0.01)
  
Convergence: ~5000 samples (slower than 6D, but STABLE)
```

---

## Part 5: Tiered Damping (Prevent Oscillation)

### **Damping Strategy**

| **Tier** | **Loop** | **Update Rate** | **Convergence** | **Why** |
|---|---|---|---|---|
| **Tier 1** | Core (1–6) | η = 0.1 (10% per step) | 2000 samples | Fast, stable (proven) |
| **Tier 2** | Infrastructure (7–9) | α₂ = 0.05 (5% per step) | 5000 samples | Slower than core (fewer feedback signals) |
| **Tier 3** | Meta weights | α₃ = 0.01 (1% per step) | 10000 samples | Slowest (highest-order, most unstable) |

### **Coupling Stability**

Tier 3 doesn't oscillate because:
1. Updates are very small (1% per step)
2. Multiple steps needed to flip weights
3. Each step verified against trend (if L_total worsens, revert)
4. Operator can constrain meta learning (set target weights)

---

## Part 6: New ADRs (0620–0623)

### **ADR-0620: Infrastructure Loops (Tier 2)**

- Scope: L_memory, L_plugins, L_security feedback
- Schema: Per-loop feedback event types
- Integration: Audit-first (every loop event logged)
- Compliance: GDPR Art. 30, 32 (audit trail)

### **ADR-0621: Meta Loop Hyperparameter Tuning (Tier 3)**

- Scope: Meta-optimizer learns w₁..w₉
- Damping: Tiered (α₃ = 1% for meta)
- Stability: Convergence test + divergence detection
- Operator control: Hybrid (manual intent + auto-tuning)

### **ADR-0622: 9D Loss Function Schema**

- Scope: All 9 dimensions unified
- Constraints: Tier 1 static, Tier 2 dynamic, Tier 3 meta-dynamic
- Penalization: λ·L_meta prevents oscillation
- Audit: Every weight change logged (delta_w, reason, timestamp)

### **ADR-0623: Tiered Damping Protocol**

- Scope: Update rates per tier (η₁ > α₂ > α₃)
- Oscillation detection: Variance check per tier
- Recovery: Pause learning, alert operator if Tier 3 diverges
- Validation: E2E test: 100-batch convergence + no oscillation

---

## Part 7: Implementation Roadmap (10 Weeks)

### **Phase 1: Tier 2 Infrastructure Loops (Weeks 1–4)**

**Deliverables:**
- [ ] Implement L_memory feedback loop (preservation weights learning)
- [ ] Implement L_plugins feedback loop (plugin config adaptation)
- [ ] Implement L_security feedback loop (compliance threshold tuning)
- [ ] Audit integration (3 new event types per loop)
- [ ] Dashboard: 3 new panels (memory, plugins, security trends)
- [ ] Tests: 25 E2E, 12 adversarial (isolated loop testing)

**Effort:** ~800 LoC

**Gate:** All 3 loops independently convergent + audit-verified

### **Phase 2: Meta Loop + Damping (Weeks 5–7)**

**Deliverables:**
- [ ] Meta-optimizer implementation (learns w₁..w₆ via gradient descent)
- [ ] Tiered damping (α₃ = 1% update rate for Tier 3)
- [ ] Divergence detection (variance spike monitoring)
- [ ] Operator control (console sliders for w_target)
- [ ] Hybrid tuning (operator intent + meta-learning blend)
- [ ] Tests: 20 E2E, 15 adversarial (oscillation scenarios, convergence edge cases)

**Effort:** ~1000 LoC

**Gate:** 100-batch convergence test PASS + NO oscillation detected

### **Phase 3: Integration + Documentation (Weeks 8–10)**

**Deliverables:**
- [ ] 9D loss computation (all tiers, all penalties)
- [ ] Console dashboard: 9D trends + Pareto frontier (2D/3D)
- [ ] Audit trail: Full history (all 9 dimensions logged)
- [ ] Documentation: ADR-0620–0623 + implementation guides
- [ ] Tests: 15 E2E + 8 adversarial (full 9D system, interaction tests)
- [ ] Production readiness: SLA verification, monitoring config

**Effort:** ~1000 LoC

**Gate:** Full 9D system E2E test PASS + audit chain verified + 30-day run data ready

---

## Part 8: Success Criteria

### **Phase 1 Correctness**
- [ ] L_memory learns preservation weights (coefficient trend >0.8 accuracy)
- [ ] L_plugins learns config (latency reduction >10%)
- [ ] L_security learns thresholds (false positive rate <5%)
- [ ] Audit: All 3 loops logged (zero events dropped)

### **Phase 2 Stability**
- [ ] 100-batch convergence: w₁..w₆ converge to stable values
- [ ] No oscillation: Δw variance <0.05 over last 20 steps
- [ ] Damping effective: α₃=1% prevents Tier 3 swings
- [ ] Operator control: Manual tuning still works (operator overrides meta)

### **Phase 3 Production**
- [ ] 30-day run: L_total trend -20% (loss reduction)
- [ ] All 9 dimensions audit-logged (zero PII, zero data leaks)
- [ ] Operator observable: Dashboard shows all 9 dimensions + Pareto frontier
- [ ] Publication-ready: Data exported for research paper

---

## Part 9: Open Questions (Design Review)

1. **Tier 2 feedback delays:** Should L_memory, L_plugins, L_security update slower (hourly) than Tier 1 (per-request)?
   - **Proposal:** Yes. Infrastructure is stable; core is request-level.

2. **Operator constrained learning:** How do we represent "preference" in meta loop?
   - **Proposal:** Soft constraint (w_target weights, meta learns around them).

3. **Rollback on meta divergence:** What's the recovery strategy?
   - **Proposal:** Pause meta learning, hold weights, alert operator, require manual approval to resume.

4. **Convergence deadline:** What if meta doesn't converge in 10K samples?
   - **Proposal:** Timeout → fall back to operator tuning, log alert, investigate.

---

## Summary

**9D Learning Vector = 6D (proven) + 3 Infrastructure Loops (learnable) + 1 Meta Loop (self-tuning weights)**

**Key Innovation:** Tiered damping prevents coupling oscillation while enabling hyperparameter self-tuning.

**Effort:** 10 weeks, 3 phases, ~2800 LoC + tests

**ADRs:** 0620–0623 (ready to write)

**Next:** Dialektical review + ADR drafting

---

**See Also:**
- ADR-0614: 6D Unified Loss (existing, proven)
- ADR-0615: Loop DAG & Backprop (existing, proven)
- ADR-0616: Weight Discovery & Pareto (existing, proven)
- ADR-0620–0623: 9D Extensions (this design)
