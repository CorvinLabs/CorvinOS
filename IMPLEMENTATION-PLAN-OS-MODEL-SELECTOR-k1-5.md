# Implementation Plan: OS Model Selector (Tier 1–3) — k=1–5 LDD Cycle

**Status:** k=1 (Planning Phase)  
**Total Effort:** 10–14 Sessions (4–6 weeks)  
**LDD Depth:** Maximum (all 12 layers)

---

## k=1: Foundation (Weeks 1–2, 2 Sessions)

### Code Changes
- Implement `OSModelSelector.classify_with_decomposition_hint()` 
  - File: `core/skills/os_skills/model_selector.py` (expand existing file)
  - Lines: +150 (heuristics, feature extraction)
- Add audit event emission
  - File: `core/models/model_selection_routing.py`
  - Lines: +30

### Tests (Unit)
- `test_os_model_selector_heuristics.py` (20 unit tests)
  - Heuristic logic (orchestration detection, decomposability check, Haiku success lookup)
  - Edge cases (empty task, null task-type, missing historical data)

### Gate (Tier 1+2)
- ✅ Ruff lint
- ✅ mypy type check
- ✅ 20 unit tests pass

### Docs
- `docs/implementation/os-model-selection-architecture.md` (skeleton)
- ADR-0845/0846/0847 finalized

### Deliverable
- OSModelSelector class with static heuristics (hardcoded Haiku success rates)
- No production wiring yet (k=2)

**Loss Signal:** Does classifier produce reasonable outputs? (subjective, k=2 measures real signal)

---

## k=2: E2E Proof (Week 2, 2 Sessions)

### Code Changes
- Wire OSModelSelector via ADR-0251 hook (adapter.py + chat_runtime.py)
  - Lines: +20 (hook registration)

### Tests (E2E)
- `test_os_model_selector_e2e.py` (10 real tasks)
  - Haiku-selected tasks: measure success rate, quality, tokens
  - Sonnet-selected tasks: measure quality (sanity check)
  - Assertion: Quality >= 95% vs. baseline Sonnet-only

### Gate (Tier 1+2+3)
- ✅ Unit tests pass (from k=1)
- ✅ E2E tests pass (10/10 tasks)
- ✅ Quality >= 95% (quality_score >= 0.95 vs. Sonnet baseline)
- ✅ Haiku selected for 30–40% of tasks

### Docs
- Update `docs/implementation/os-model-selection-architecture.md` (wiring section)

### Deliverable
- OSModelSelector wired + callable from production paths
- E2E proof: Haiku-selected tasks work (>= 95% quality)

**Loss Signal:** `loss = 1 - min(quality_score, haiku_success_rate)`  
**Target:** loss < 0.10

---

## k=3: Tier 2 (Prompt-Level Decomposition) (Weeks 3–4, 3 Sessions)

### Code Changes
- Implement `PromptDecomposer` Skill
  - File: `core/skills/os_skills/decomposer.py` (new)
  - Lines: ~200 (decomposition logic, step generation, synthesis)
- Integrate with OSModelSelector
  - File: `core/skills/os_skills/model_selector.py` (+50 lines)

### Tests (E2E + Adversarial)
- `test_prompt_level_decomposition_e2e.py` (20 real tasks)
  - Decomposable tasks: execute OS-structured plan with Haiku
  - Assertion: Quality >= 95%, tokens <= 50% of full-Sonnet
- `test_decomposition_adversarial.py` (10 adversarial tests)
  - Context loss (step-to-step): verify consistency
  - Step dependency failures: fallback to Sonnet
  - Malformed plans: error handling

### Gate (Tier 1+2+3+4)
- ✅ Unit tests pass (k=1)
- ✅ E2E tests pass (k=2)
- ✅ Decomposition tests pass (20/20 tasks)
- ✅ Quality >= 95% on decomposed tasks
- ✅ Token savings >= 40%

### Docs
- Add `docs/implementation/task-decomposition-strategy.md` (detailed examples)

### Deliverable
- Prompt-level decomposition fully functional
- 40–50% token savings validated

**Loss Signal:** `loss = (1 - quality) + 0.3 * (1 - token_savings_pct / 50)`  
**Target:** loss < 0.10

---

## k=4: Learning Loop Integration (Week 4–5, 2 Sessions)

### Code Changes
- Implement `OSModelSelectorOptimizer` (Bayesian update)
  - File: `core/learning/model_selection_optimizer.py` (new)
  - Lines: ~150 (Beta-distribution update, audit emission)
- Wire LearningEvent sink (ADR-0314)
  - File: `core/skills/os_skills/model_selector.py` (+30 lines)

### Tests (Integration)
- `test_os_model_selector_learning_loop.py` (10 tasks, collect feedback)
  - Task outcome recorded → learning event emitted
  - Heuristic updated (haiku_success_rate changes)
  - Next task uses updated heuristic
- Convergence test: run k=4 for 50+ iterations, measure heuristic std-dev
  - Assertion: std-dev < 5% (convergence achieved)

### Gate (Tier 1+2+3+4+5)
- ✅ All prior gates pass
- ✅ Learning loop integrates (events flow → heuristics update)
- ✅ Convergence < 5% std-dev by iteration N=50

### Docs
- Add `docs/implementation/os-model-selection-learning-loop.md` (Bayesian details)
- Update `docs/implementation/os-model-selection-architecture.md` (learning flow)

### Deliverable
- Learning loop fully integrated
- Heuristics converge to production values

**Loss Signal:** `loss = std-dev(heuristic_updates)`  
**Target:** loss < 0.05

---

## k=5: Production Ready (Week 5–6, 2 Sessions)

### Code Changes
- Polish + performance tuning
  - Optimize `classify_with_decomposition_hint()` (cache heuristics)
  - Add monitoring / observability hooks
  - Lines: +50

### Tests (Production)
- `test_os_model_selector_production_load.py` (100+ tasks, realistic distribution)
  - Throughput: >= 1000 tasks/hour (latency SLA)
  - Reliability: error rate < 0.1%
  - Audit trail: complete + hash-chained (verified)
- Drift detection (k=5 specific)
  - Re-run on production data (last 1000 tasks)
  - Assert: Quality, cost, latency unchanged vs. k=4

### Gate (Live / Canary)
- ✅ All prior gates pass
- ✅ Production load test (100+ tasks)
- ✅ Latency SLA met (P99 < 2s overhead)
- ✅ Error rate < 0.1%
- ✅ Drift detection clean (no regressions)

### Docs
- Finalize all documentation
- Add operator runbook (manual heuristic override, debugging)

### Deliverable
- **Production-Ready Declaration**
  - Tier 1–3 fully implemented + tested
  - Ready for canary → rollout (Phase 2)

**Loss Signal:** `loss = (1 - quality) + (latency_ms / 2000) + (error_rate / 0.01)`  
**Target:** loss < 0.05 (production-grade)

---

## Success Metrics (by k=5)

| Metric | k=1 | k=2 | k=3 | k=4 | k=5 (Target) |
|--------|-----|-----|-----|-----|--------------|
| **Haiku selection %** | 30% | 35% | 45% | 60% | 70% |
| **Quality vs. Sonnet** | 98% | 96% | 95% | 95% | 95% |
| **Token cost reduction** | 15% | 35% | 50% | 55% | 60% |
| **Latency increase (ms)** | 0 | 100 | 500 | 600 | 600 |
| **Learning convergence** | — | — | — | ✓ | ✓ |
| **Error rate** | — | 1% | 0.5% | 0.2% | < 0.1% |

---

## Risk & Mitigation

| Risk | Severity | Mitigation | Gate |
|------|----------|-----------|------|
| **Decomposition quality fails** | High | Fallback: k=2 revert to Sonnet | Quality >= 95% |
| **Haiku can't execute structure** | Medium | k=3 test on real tasks | Success >= 85% |
| **Learning loop doesn't converge** | Low | k=4 use static heuristics | Std-dev < 5% |
| **Latency > 2s** | Low | k=5 optimize or revert | P99 latency gate |

---

## Dependencies & Blockers

**Must be ACCEPTED before k=1 implementation:**
- ADR-0642 (Model Selector Skill exists)
- ADR-0314 (Learning infrastructure exists)
- ADR-0535 (Skill composition framework)
- ADR-0251 (Extension points already wired)

**Currently PROPOSED:**
- ADR-0845 (Architecture) ← this effort
- ADR-0846 (Learning) ← this effort
- ADR-0847 (Wiring) ← this effort

**Action:** Review + ACCEPT these 3 ADRs before k=1 starts.

---

## Decision Gates (Go/No-Go)

| Phase | Gate | Owner | Condition |
|-------|------|-------|-----------|
| **k=1 → k=2** | Unit tests pass | Dev | 20/20 tests green |
| **k=2 → k=3** | E2E proof | Dev | Quality >= 95%, Haiku success >= 85% |
| **k=3 → k=4** | Decomposition stable | Dev | 20/20 E2E tests, token savings verified |
| **k=4 → k=5** | Learning converges | Dev | Heuristic std-dev < 5% |
| **k=5 → Canary** | Production ready | Operator | All gates green, docs complete |

---

## Ownership

- **Development:** Claude (LDD-Architect), 10–14 sessions
- **Review:** Operator, end of each k-phase
- **Deployment:** Operator (canary → rollout, Phase 2)

---

## Timeline

- **Week 1–2:** k=1 (foundation)
- **Week 2:** k=2 (E2E proof)
- **Week 3–4:** k=3 (Tier 2 decomposition)
- **Week 4–5:** k=4 (learning loop)
- **Week 5–6:** k=5 (production polish + canary)

**Total:** 4–6 weeks, 10–14 sessions

