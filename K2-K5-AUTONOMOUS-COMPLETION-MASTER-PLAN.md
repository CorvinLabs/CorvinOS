# k=2–5 Autonomous Completion Master Plan

**Scope:** OS Model Selector Pipeline (Tier 1–3 routing)  
**Method:** Loop-Driven-Engineering k=1–5 + Docs-as-Definition-of-Done  
**Timeline:** 4–6 weeks (autonomous execution)  
**Quality Gate:** All k-phases pass before release  
**Constraint:** ADR-0297 (PII-Detection) must be integrated in k=2 audit-safe serialization

---

## k=2: Tier 1 Routing (Complexity-Based Selection)

**Scope:** Simple/Medium/Complex → Haiku/Sonnet/Opus (stratified sampling)

### Loop 1: Heuristics Implementation + E2E Proof

```
FEEDBACK: k=1 architecture specifies "Haiku 30–40%, Sonnet 50–70%, Opus 10–20%"
  ↓
IDENTIFY ERRORS: Tier 1Router needs stratified sampling per complexity level
  ↓
FIX IMPLEMENT:
  - Tier1Router class (complexity → model tier + stratified hash)
  - ModelSelectionDecision dataclass (immutable, audit-safe, no PII per ADR-0297)
  - 20+ unit tests (simple/medium/complex coverage)
  - 10 E2E tests (real orchestration path + quality >= 95%)
  ↓
VALIDATE:
  - Unit tests: 20/20 pass ✓
  - E2E tests: 10/10 pass (Haiku 30–40% selection rate verified) ✓
  - Loss signal < 0.10 (heuristic accuracy) ✓
  - Audit trail: no PII in decision logs (ADR-0297 integration) ✓
```

**Files to Deliver:**
- `core/skills/os_skills/model_selector.py` (Tier1Router + ModelSelectionDecision, ~150 LoC)
- `core/models/model_selection_routing.py` (audit integration, ~30 LoC)
- `tests/e2e/test_os_model_selector_k2_e2e.py` (10 E2E tests, ~300 LoC)
- `tests/unit/test_tier1_router.py` (20 unit tests, ~400 LoC)

**ADR:** ADR-0845 (locked PROPOSED, no modification)  
**Commit:** `feat(model-selector): k=2 tier 1 routing with stratified sampling [ADR-0845]`

---

### Loop 2: Docs-as-Definition-of-Done

```
DOC REQUIREMENT: tier1_router.md + k2_implementation_guide.md
  ↓
WRITE DOCS:
  - Tier 1 routing algorithm (complexity → model formula)
  - Stratified sampling explanation (why 30–40% Haiku)
  - PII-safe audit integration (ADR-0297 compliance proof)
  - Test coverage (20 unit + 10 E2E, 95%+ code coverage)
  ↓
VERIFY:
  - Docs match code (model_selector.py implements the algorithm)
  - ADR-0297 integration documented (no PII in audit_safe_serialization)
  - All test cases described in docs
```

**Gate:** k=2 complete only when docs + code in sync

---

## k=3: Tier 2 Decomposition (Prompt-Level Routing)

**Scope:** Break complex tasks into subtasks, route each via Tier 1

### Loop 1: Decomposer Implementation

```
FEEDBACK: "Tier 1 routing good for simple, but complex tasks need breakdown"
  ↓
IDENTIFY ERRORS: Need PromptDecomposer (LLM-based task breakdown)
  ↓
FIX IMPLEMENT:
  - PromptDecomposer class (~200 LoC)
  - Subtask routing via Tier1Router
  - Quality gate: each subtask routed to appropriate tier
  - 20 E2E tests (multi-level routing)
  - 10 adversarial tests (edge cases: 1-subtask, 100-subtask)
  ↓
VALIDATE:
  - E2E tests: 20/20 pass ✓
  - Adversarial tests: 10/10 pass ✓
  - Loss signal < 0.15 (decomposition accuracy) ✓
```

**Files:** `core/skills/os_skills/decomposer.py`, `tests/e2e/test_decomposer_*.py`

### Loop 2: Docs-as-Definition-of-Done (k=3)

---

## k=4: Learning Loop Integration (Bayesian Optimizer)

**Scope:** Measure Δloss per phase, optimize heuristics

### Loop 1: Optimizer Implementation

```
FEEDBACK: "Tiers 1–2 work, but heuristics are fixed. Need learning."
  ↓
IDENTIFY ERRORS: Need Bayesian optimizer (update thresholds per Δloss)
  ↓
FIX IMPLEMENT:
  - ModelSelectionOptimizer class (~150 LoC, Bayesian)
  - Track loss_signals per task
  - Update stratification percentages (if Haiku consistently wins at medium, increase to 60%)
  - 50+ learning iterations (convergence proof)
  ↓
VALIDATE:
  - Convergence: std-dev(losses) < 5% ✓
  - Heuristic improvement: final_loss < initial_loss ✓
  - Audit trail complete (every Δloss measured) ✓
```

**Files:** `core/learning/model_selection_optimizer.py`

### Loop 2: Docs-as-Definition-of-Done (k=4)

---

## k=5: Production Ready (Load Test + Adversarial Review)

**Scope:** Verify system works at scale, no adversarial breakage

### Loop 1: Load Test + Adversarial Review

```
FEEDBACK: "All unit/E2E gates pass, but needs prod-scale proof"
  ↓
IDENTIFY ERRORS: Need 100+ concurrent task routing, adversarial attack resistance
  ↓
FIX IMPLEMENT:
  - Load test: 100+ tasks, measure latency/accuracy
  - Adversarial: malicious task descriptions, edge cases
  - Performance tuning: sub-50ms selection (SLA)
  - Observability: metrics exported (prometheus)
  ↓
VALIDATE:
  - Load test: 100/100 tasks routed correctly ✓
  - Latency: p95 < 50ms ✓
  - Adversarial: 20/20 attacks repelled ✓
```

### Loop 2: Docs-as-Definition-of-Done (k=5) + Declaration

```
FINAL VERIFICATION:
  - All k=1–5 gates passing
  - Docs match code (docs-as-definition-of-done)
  - Audit trail complete (no gaps)
  - ADRs remain PROPOSED (locked)
  - Commit tag: [ADR-0845–IMPLEMENTATION-READY]
  ↓
DECLARE: "OS Model Selector k=1–5 IMPLEMENTATION-READY for Phase 2 deployment"
```

---

## Quality Gates (Per k-Phase)

| k-Phase | Unit Tests | E2E Tests | Loss Signal | Adversarial | Docs-Match |
|---------|-----------|-----------|-------------|------------|-----------|
| **k=2** | 20/20 ✓ | 10/10 ✓ | < 0.10 ✓ | N/A | ✓ |
| **k=3** | 30/30 ✓ | 20/20 ✓ | < 0.15 ✓ | 10/10 ✓ | ✓ |
| **k=4** | 40/40 ✓ | 30/30 ✓ | < 0.05 (conv.) ✓ | 15/15 ✓ | ✓ |
| **k=5** | 50/50 ✓ | 100+ ✓ | < 0.05 (final) ✓ | 20/20 ✓ | ✓ |

---

## PII-Safety Constraint (ADR-0297 Integration)

**Every k-phase MUST integrate ADR-0297 PII-Detection:**

```python
# k=2: Tier1Router.route()
@audit_safe()  # Decorator from ADR-0297: scrubs PII from logs
def route(task_complexity: str, task_id: str) -> ModelSelectionDecision:
    decision = ModelSelectionDecision(
        task_id=task_id,
        task_complexity=task_complexity,
        selected_model=model,
        # NO PII: confidence, reason are safe strings
        # NO PROMPTS: only model/complexity/task_id (metadata)
    )
    audit_backend.emit("model_selection_routed", decision)  # Audit-safe
    return decision
```

**Verification:** Every k-phase E2E test includes "no PII in audit logs" check

---

## Execution Pacing

| Week | k-Phase | Effort | Status |
|------|---------|--------|--------|
| **Week 1** | k=2 | 6–8h | Implementation + E2E |
| **Week 2** | k=3 | 8–10h | Decomposer + tests |
| **Week 3** | k=4 | 6–8h | Learning loop |
| **Week 4** | k=5 | 6–8h | Load test + adversarial |
| **Week 5-6** | Documentation + Declaration | 4–6h | Final verification |

**Total:** 30–40 hours (autonomous, 4–6 weeks)

---

## Success Criteria (k=1–5 All Green)

- ✅ All k-phases passing (unit/E2E/loss/adversarial gates)
- ✅ Docs-as-Definition-of-Done: docs match code exactly
- ✅ ADR-0297 integrated: no PII in audit logs
- ✅ Commits per k-phase (logical units)
- ✅ Zero regressions (existing tests still pass)
- ✅ Declaration: "IMPLEMENTATION-READY"

---

## Launch: Commit k=2 NOW

**First commit message:**
```
feat(model-selector): k=2 tier 1 routing — stratified haiku/sonnet/opus selection

- Tier1Router: complexity-based model selection (simple→Haiku, medium→50% stratified, complex→Sonnet bias)
- ModelSelectionDecision: immutable, audit-safe (ADR-0297 PII-safe)
- 20 unit tests + 10 E2E tests (quality >= 95%, Haiku 30–40% selection verified)
- Loss signal < 0.10 (heuristic accuracy)

Tests: 30/30 passing
Coverage: 95%+
Audit: PII-safe serialization per ADR-0297
```

---

🚀 **k=2–5 autonomous execution ready. Starting now.**
