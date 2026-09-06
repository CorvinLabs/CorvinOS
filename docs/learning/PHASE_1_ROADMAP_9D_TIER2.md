# 9D Learning Vector: Phase 1 Implementation Roadmap (Tier 2)

**Duration:** 4 weeks  
**Effort:** ~800 LoC + 400 LoC tests  
**Team Size:** 2–3 engineers  
**Deliverable:** Three learnable infrastructure loops (L_memory, L_plugins, L_security) + audit integration + dashboard

---

## Phase 1 Scope: Tier 2 Infrastructure Loops

### **L_memory: Preservation Weight Learning**

**Goal:** Learn which context elements to preserve (task_id, user_prefs, prior_decisions) based on user feedback.

**Current (Static):**
```python
preservation_weights = {
  "task_id": 1.0,           # Always preserve
  "user_prefs": 0.5,        # Sometimes preserve (hardcoded)
  "prior_decisions": 0.2,   # Rarely preserve (hardcoded)
}
```

**Target (Learnable):**
```python
preservation_weights = {
  "task_id": 1.0,                              # Stay fixed (always needed)
  "user_prefs": sigmoid(feedback_score),       # LEARNS from feedback
  "prior_decisions": sigmoid(0.5*feedback),    # LEARNS (lower base rate)
}
```

**Implementation (Week 1):**
- [ ] Define `MemoryFeedbackEvent` schema (user_id, context_id, relevance_score, signal)
- [ ] Add `MemoryLoss` computation (`L_memory = MSE(preserved_context, user_feedback)`)
- [ ] Implement `MemoryOptimizer` (update preservation_weights via gradient descent, η=0.1)
- [ ] Add audit logging (memory_feedback_received event)
- [ ] Write tests: 5 E2E (feedback → weight update), 3 adversarial (edge cases)

**Success Criteria:**
- Preservation weights converge to user feedback (correlation >0.8)
- Zero audit events dropped
- Latency impact <5ms (in-process update)

---

### **L_plugins: Plugin Config Adaptation**

**Goal:** Tune plugin configuration (cache_size, ttl) based on performance metrics.

**Current (Static):**
```yaml
plugins:
  memory:
    cache_size: 1000           # Fixed
    ttl: 3600                  # Fixed (1 hour)
  telemetry:
    batch_size: 100            # Fixed
    flush_interval: 60         # Fixed
```

**Target (Learnable):**
```python
plugin_config = {
  "memory": {
    cache_size: adapt_size(metrics.hitrate, metrics.memory_usage),  # LEARNS
    ttl: adapt_ttl(metrics.staleness),                               # LEARNS
  },
  "telemetry": {
    batch_size: adapt_batch(metrics.latency),                        # LEARNS
    flush_interval: adapt_interval(metrics.data_volume),             # LEARNS
  }
}
```

**Implementation (Week 2):**
- [ ] Define `PluginMetric` schema (plugin_id, latency_ms, error_rate, hitrate, memory_mb)
- [ ] Add `PluginLoss` computation (`L_plugins = f(latency, error_rate, cache_efficiency)`)
- [ ] Implement `PluginOptimizer` (adapt config parameters, update hourly, α=0.05)
- [ ] Collect metrics: CloudWatch → PluginMetricCollector (async, non-blocking)
- [ ] Add audit logging (plugin_config_updated event with delta)
- [ ] Write tests: 5 E2E (metrics → config change), 4 adversarial (metric noise)

**Success Criteria:**
- Cache hitrate improves by >10% after 1000 requests
- Latency impact <10ms (metric collection + optimizer)
- Config changes stable (no oscillation over 24h)

---

### **L_security: Compliance Threshold Tuning**

**Goal:** Learn optimal PII detection thresholds + house-rules strictness based on audit feedback.

**Current (Static):**
```python
pii_detection = {
  "threshold": 0.75,         # Fixed
  "sensitivity": "medium",   # Fixed
}

house_rules = {
  "strictness": 0.8,         # Fixed
  "deny_mode": "fail-closed",  # Fixed
}
```

**Target (Learnable):**
```python
pii_detection = {
  "threshold": adapt_threshold(fp_rate, fn_rate),    # LEARNS
  "sensitivity": adapt_sensitivity(violation_rate),  # LEARNS
}

house_rules = {
  "strictness": adapt_strictness(false_positive_rate, operator_feedback),  # LEARNS
}
```

**Implementation (Week 3):**
- [ ] Define `ComplianceFeedback` schema (flagged_as_pii: bool, actually_pii: bool, reason)
- [ ] Add `SecurityLoss` computation (`L_security = w₁·FP_rate + w₂·FN_rate + w₃·user_satisfaction`)
- [ ] Implement `SecurityOptimizer` (tune thresholds via ROC curve optimization, update daily, α=0.05)
- [ ] Wire operator feedback: `/compliance-feedback --task_id=X --was_correct=yes/no`
- [ ] Add audit logging (compliance_threshold_updated event)
- [ ] Write tests: 6 E2E (FP/FN rates → threshold change), 5 adversarial (bias scenarios)

**Success Criteria:**
- False positive rate <5% (acceptable for compliance)
- False negative rate <2% (high sensitivity to actual violations)
- Operator feedback acceptance >90% (learned thresholds align with operator intent)

---

## Integration: Tier 2 into Unified Loss

**Week 4: Consolidation**

```python
L_total = w₁·L_routing + w₂·L_context + w₃·L_exec + w₄·L_conf + w₅·L_comply + w₆·L_learn
        + w₇·L_memory + w₈·L_plugins + w₉·L_security  # ← NEW Tier 2

where:
  w₇, w₈, w₉ ∈ [0, 1]
  Σwᵢ = 1.0 (for all 9 dimensions)
  Update rate: α₂ = 0.05 (per-request for L_memory, hourly for L_plugins, daily for L_security)
```

**Implementation:**
- [ ] Update `UnifiedLossComputation` to include Tier 2
- [ ] Reweight existing Tier 1 (w₁..w₆ must shrink to make room for w₇..w₉)
- [ ] Add convergence check: all 9 dimensions should stabilize within 5000 samples
- [ ] Dashboard: 9D loss trends (new panels for L₇, L₈, L₉)
- [ ] Audit: Verify all 9 loss calculations logged + hash-chained
- [ ] Tests: 8 E2E (9D integration), 7 adversarial (multi-loop coupling, weight rebalancing)

---

## Deliverables (End of Phase 1)

### **Code**
```
core/learning/
├── tier2_loops/
│   ├── memory_loop.py          (200 LoC, L_memory optimizer)
│   ├── plugin_loop.py          (250 LoC, L_plugins optimizer)
│   ├── security_loop.py        (250 LoC, L_security optimizer)
│   ├── unified_loss_9d.py      (100 LoC, integrate into L_total)
│   └── audit_tier2.py          (50 LoC, event logging)
├── tests/
│   ├── test_memory_loop_e2e.py         (120 LoC)
│   ├── test_plugin_loop_e2e.py         (140 LoC)
│   ├── test_security_loop_e2e.py       (150 LoC)
│   ├── test_9d_integration.py          (80 LoC)
│   ├── adversarial_tier2.py            (200 LoC, 12 attack vectors)
│   └── convergence_tests.py            (80 LoC, convergence proofs)
```

**Total:** ~800 LoC + ~770 LoC tests = ~1570 LoC

### **Documentation**
- [ ] ADR-0620: Infrastructure Loops (design rationale)
- [ ] API docs: MemoryOptimizer, PluginOptimizer, SecurityOptimizer
- [ ] Dashboard panels: L_memory trends, L_plugins metrics, L_security thresholds
- [ ] Troubleshooting guide: "Config not converging? Check feedback signal quality"

### **Dashboard**
- [ ] Panel 1: L_memory trends (preservation weights over time)
- [ ] Panel 2: L_plugins performance (cache hitrate, latency)
- [ ] Panel 3: L_security ROC curve (FP vs FN, current threshold marked)
- [ ] Panel 4: Unified 9D loss (all dimensions on one chart)

### **Tests**
- [ ] 25 E2E tests (all converge + feedback integrated)
- [ ] 12 adversarial tests (edge cases, noisy metrics, operator override)
- [ ] Convergence proof: All 3 Tier 2 loops converge in <5000 samples
- [ ] Audit verification: Zero events dropped, all hash-chained

---

## Success Gate (End of Phase 1)

- [ ] All 3 Tier 2 loops independently convergent (L_memory, L_plugins, L_security)
- [ ] All audit events logged + hash-chained (zero data loss)
- [ ] Dashboard shows all 9 dimensions (Tier 1 + Tier 2)
- [ ] E2E tests: 25/25 PASS
- [ ] Adversarial tests: 12/12 PASS (no security or stability regressions)
- [ ] Performance: <10ms latency impact per request
- [ ] Ready for Phase 2 (Meta Loop)

---

## Team Breakdown

**Week 1 (L_memory):** 1 engineer (full-time)
**Week 2 (L_plugins):** 1 engineer (full-time)
**Week 3 (L_security):** 1 engineer (full-time)
**Week 4 (Integration):** 2 engineers (full-time, parallel: code + tests)

**Or:** 1 engineer across all 4 weeks (part-time code + dedicated testing)

---

## Risks & Mitigations

| **Risk** | **Impact** | **Mitigation** |
|---|---|---|
| Feedback signal too noisy (L_plugins metrics) | Convergence slow/unstable | Smooth metrics (rolling average, outlier removal) |
| Tier 2 overlearns (oscillates) | Regression in core performance | α₂=5% damping + divergence detection (pause if Δw>threshold) |
| Audit overhead (3 new event types, hourly/daily updates) | Storage growth, latency | Batch events, compress audit snapshots |
| Operator doesn't understand adaptation | Confusion, manual override | Dashboard showing WHY weights changed + operator tuning levers |
| Security thresholds adapt too far | PII slips through | Hardcode min/max bounds (FP_rate ≤5%, FN_rate ≤2%) |

---

## Rollout Plan

**Week 1-3:** Develop + test (all on `main`, gated by feature flag `learning_tier2_enabled`)

**Week 4:** Staging (1% traffic, monitor metrics)

**Week 5:** Production (5% traffic, alert on divergence)

**Week 6:** Full deployment (100% traffic)

---

## Success Metrics (Live Monitoring)

- **L_memory convergence:** Preservation weights σ <0.05 over last 100 requests
- **L_plugins optimization:** Cache hitrate +10%, P99 latency -5%
- **L_security adaptation:** False positive rate <5%, false negative rate <2%
- **9D loss:** Downward trend (L_total monotonically improving or stable)
- **Audit health:** Event ingestion rate consistent, zero drops

---

## Next Phase (Phase 2, Weeks 5-7)

After Phase 1 validation:
- Implement Meta Loop (Tier 3)
- Tiered damping (α₃=1%)
- Operator control (hybrid tuning)
- See PHASE_2_ROADMAP_9D_META_LOOP.md

---

## Quick Start (Run This Week 1)

```bash
cd /home/shumway/projects/CorvinOS

# Branch for Phase 1
git checkout -b learning/9d-tier2-infrastructure

# Scaffold Phase 1 directories
mkdir -p core/learning/tier2_loops core/learning/tests/tier2

# Create placeholder files (expand next)
touch core/learning/tier2_loops/{memory_loop,plugin_loop,security_loop,unified_loss_9d,audit_tier2}.py
touch core/learning/tests/tier2/test_{memory,plugin,security,9d_integration,adversarial_tier2,convergence}.py

# Draft ADR-0620 (for design review)
cp /home/shumway/projects/CorvinOS/outputs/ADR_0620-0623_9D_LEARNING_VECTOR.md \
   /home/shumway/projects/Corvin-ADR/decisions/ADR-0620-infrastructure-loops.md

# Commit baseline
git add core/learning/tier2_loops core/learning/tests/tier2
git commit -m "feat(learning): scaffold Phase 1 Tier 2 infrastructure loops

- L_memory: preservation weight learning
- L_plugins: plugin config adaptation
- L_security: compliance threshold tuning

[adr: ADR-0620]

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"

# Run tests (empty, but verify structure)
pytest core/learning/tests/tier2/ -v
```

---

**Ready to start Phase 1?** LMK, I'll expand the code skeleton + write the first loop implementation.
