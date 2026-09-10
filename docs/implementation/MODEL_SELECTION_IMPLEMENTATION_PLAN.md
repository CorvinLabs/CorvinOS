# Model Selection Skill — Implementation Plan
**Date:** 2026-09-10  
**Status:** APPROVED (Ready for Phase 1 kickoff)  
**Related ADRs:** 0641, 0642, 0643, 0644  
**Related Concept:** CONCEPT-0033

---

## Quick Summary

Refactor the engine configuration console from static "Cloud Code" → dynamic "Model Selection Skill" that classifies tasks (SIMPLE/MEDIUM/COMPLEX) and routes to the best model (Haiku/Sonnet/Opus/Fable + external providers: Ollama/OpenRouter/OpenAI). The system learns autonomously via feedback loop (ADR-0314).

**Timeline:** 8–10 weeks | **Phases:** 3 | **Effort:** ~1,800 LoC | **Tests:** 200

---

## Phase 1: Static Configuration + UI (Weeks 1–2)

**Goal:** Console UI fully functional; operator can configure models per task type. No learning yet.

### Week 1: Console Page Layout

**Tasks:**
- [ ] Create new page: `core/console/corvin_console/web-next/src/pages/engine-config.tsx`
- [ ] 4-section layout: CorvinOS + SIMPLE/MEDIUM/COMPLEX task types
- [ ] Model dropdowns (Haiku/Sonnet/Opus/Fable) per task type
- [ ] External provider modals (Ollama, OpenRouter, OpenAI)
- [ ] Confidence score display (hardcoded "0 runs" for Phase 1)
- [ ] Unit tests (15 tests): layout rendering, dropdown interaction

**Acceptance Criteria:**
- [ ] Console page loads without errors
- [ ] All 4 sections visible
- [ ] Dropdowns functional (select/deselect)
- [ ] External provider modals open/close
- [ ] 15 unit tests pass

### Week 2: API Routes + Persistence

**Tasks:**
- [ ] Create API routes in `core/console/routes/engine_api.py`:
  - `GET /v1/engine/config` — read current config
  - `PUT /v1/engine/config` — update model choices
  - `POST /v1/engine/external-provider/test` — verify connection
  - `GET /v1/engine/analytics` — empty for Phase 1
- [ ] Config persistence: read/write `tenant.corvin.yaml`
- [ ] Tenant scope validation (ADR-0007)
- [ ] Audit events: log every config change
- [ ] E2E tests (15 tests): config roundtrip, persistence, audit logging

**Acceptance Criteria:**
- [ ] API routes functional
- [ ] Config persists across restarts
- [ ] Audit events logged correctly
- [ ] Tenant isolation enforced
- [ ] 30 tests pass (15 unit + 15 E2E)

### Phase 1 Exit Gate

- [ ] Console loads ✓
- [ ] Operator can configure models ✓
- [ ] Config persists ✓
- [ ] All 45 tests pass ✓
- [ ] **Decision:** GO for Phase 2 ✓

---

## Phase 2: Backend Skills + External Providers (Weeks 3–5)

**Goal:** Model Selector Skill works; external providers callable. Real routing happens.

### Week 3: Core Skill Implementation

**Tasks:**
- [ ] Skill class: `core/skills/os_skills/model_selector.py`
- [ ] Feature extractor: `core/skills/os_skills/feature_extractor.py`
  - Extract: token_count, keywords, code_blocks, dependencies
- [ ] Deterministic classification algorithm
  - SIMPLE: token_count < 500 ∧ code_blocks < 2 (conf: 0.95)
  - COMPLEX: token_count > 3000 ∨ code_blocks > 5 (conf: 0.88)
  - MEDIUM: else (conf: 0.72)
- [ ] Load operator config (from ADR-0641)
- [ ] Audit trail events (model_selected)
- [ ] Unit tests (25 tests): features, classification, edge cases

**Acceptance Criteria:**
- [ ] Skill classifies tasks correctly (SIMPLE/MEDIUM/COMPLEX)
- [ ] Confidence scores computed
- [ ] Operator config loaded and respected
- [ ] Audit events logged
- [ ] 25 unit tests pass

### Week 4: External Provider Clients

**Tasks:**
- [ ] Provider factory pattern: `core/models/external_providers/__init__.py`
- [ ] OllamaClient: `ollama_client.py`
  - POST to /api/generate
  - Health checks
  - Timeout + retry (30s timeout, retry once)
- [ ] OpenRouterClient: `openrouter_client.py`
  - POST to openrouter.ai/api/v1/chat/completions
  - API key from `~/.config/corvin-voice/`
  - Health checks
- [ ] OpenAIClient: `openai_client.py`
  - Use AsyncOpenAI library
  - API key from `~/.config/corvin-voice/`
  - Health checks
- [ ] Integration tests (15 tests): real calls to sandbox providers
- [ ] Cost tracking (per-call cost logged)

**Acceptance Criteria:**
- [ ] All 3 providers callable
- [ ] Health checks working
- [ ] Timeouts + retries working
- [ ] Cost per call tracked
- [ ] 15 integration tests pass

### Week 5: End-to-End Routing

**Tasks:**
- [ ] Wire Model Selector → Cloud Code routing
- [ ] Task → Classify → Select Model → Route to Provider → Execute
- [ ] Fallback: if provider fails, use Anthropic (log audit event)
- [ ] Cost tracking + aggregation
- [ ] E2E tests (25 tests): real tasks through real skill + providers
- [ ] Performance benchmarks:
  - Deterministic classification: <10ms (P99)
  - LLM refinement (if needed): <2s (P99)
  - Fallback handling: <500ms

**Acceptance Criteria:**
- [ ] Real tasks route correctly
- [ ] Model selection audited
- [ ] Fallback works + logged
- [ ] Cost per task tracked
- [ ] All latency SLAs met
- [ ] 25 E2E tests pass

### Phase 2 Exit Gate

- [ ] Model Selector Skill works ✓
- [ ] External providers functional ✓
- [ ] E2E routing verified ✓
- [ ] Audit trail complete ✓
- [ ] All 65 tests pass ✓
- [ ] **Decision:** GO for Phase 3 ✓

---

## Phase 3: Learning Loop + Dashboard (Weeks 6–10)

**Goal:** Confidence scores update autonomously; learning analytics dashboard.

### Week 6: Outcome Detection

**Tasks:**
- [ ] Hook: `on_task_completed(task_id, model_used, result)`
- [ ] Assess outcome: quality_score (0.0–1.0)
- [ ] Emit feedback event: `model_selection_feedback`
- [ ] Event schema:
  ```
  {
    task_id, task_type, model_used, outcome,
    quality_score, cost, latency_ms, timestamp
  }
  ```
- [ ] Persist to learning backend (ADR-0314)
- [ ] Unit tests (15 tests): outcome detection, event emission

**Acceptance Criteria:**
- [ ] Task completion triggers feedback event
- [ ] Event schema correct
- [ ] Events persisted
- [ ] 15 unit tests pass

### Week 7: Confidence Optimizer

**Tasks:**
- [ ] Optimizer: `core/learning/model_selection_optimizer.py`
- [ ] On each feedback:
  - Retrieve current stats for (task_type, model)
  - Bayesian update (if n_runs >= 5)
  - EMA smoothing (weight old 90%, new 10%)
  - Save to learning store
  - Emit audit event (confidence_updated)
- [ ] Learning store: in-memory + persisted
- [ ] Convergence monitoring (variance check)
- [ ] Unit tests (20 tests): updates, smoothing, convergence

**Acceptance Criteria:**
- [ ] Confidence updates on feedback
- [ ] Minimum sample requirement enforced (N=5)
- [ ] EMA smoothing applied
- [ ] Convergence detectable
- [ ] 20 unit tests pass

### Week 8: Console Analytics

**Tasks:**
- [ ] New section: "Learning Status & Analytics"
- [ ] Confidence pie charts (Haiku % | Sonnet % | Opus %)
- [ ] Drill-down: "Show me the 1,247 MEDIUM tasks that used Sonnet"
- [ ] Reset Learning button (start from scratch)
- [ ] Export Weights button (CSV)
- [ ] Live updates (WebSocket subscription to confidence changes)
- [ ] Integration tests (15 tests): data roundtrip, visualization

**Acceptance Criteria:**
- [ ] Dashboard shows real confidence scores
- [ ] Charts render correctly
- [ ] Drill-down functional
- [ ] Reset/Export buttons work
- [ ] Live updates work
- [ ] 15 integration tests pass

### Week 9: End-to-End Learning Loop

**Tasks:**
- [ ] Full integration test: task → feedback → confidence update → next routing
- [ ] Convergence test: confidence stabilizes after ~500 samples
- [ ] Adversarial tests (25 tests):
  - Feedback poisoning
  - Cost explosion
  - Provider unreachability
  - Oscillation
  - Misclassification cascade
  - Operator confusion
- [ ] Verify all 6 mitigations work
- [ ] Generate adversarial review report

**Acceptance Criteria:**
- [ ] Full loop works (task in → confidence out)
- [ ] Convergence verified
- [ ] 25 adversarial tests pass (0 CRITICAL)
- [ ] Mitigations validated

### Week 10: Performance + Deployment

**Tasks:**
- [ ] Stress test: 1,000 concurrent tasks routing
- [ ] Latency benchmarks: P50, P95, P99
- [ ] Memory profiling (learning store doesn't leak)
- [ ] Documentation:
  - Operator guide (how to use console)
  - API documentation
  - Learning algorithm explanation
- [ ] Canary deployment plan:
  - 5% → 25% → 50% → 100% rollout
  - Success criteria per tier
  - Rollback plan
- [ ] Final tests (15 perf + stress tests)

**Acceptance Criteria:**
- [ ] All latency SLAs met
- [ ] No memory leaks
- [ ] Documentation complete
- [ ] Canary plan documented
- [ ] 15 perf tests pass

### Phase 3 Exit Gate

- [ ] Learning loop closed ✓
- [ ] Confidence scores real (not hardcoded) ✓
- [ ] Convergence verified ✓
- [ ] Adversarial review passed ✓
- [ ] All 90 tests pass ✓
- [ ] Canary deployment ready ✓
- [ ] **Decision:** GO for production canary ✓

---

## Resource Estimate

| Phase | Duration | Engineers | Code | Tests | Docs |
|---|---|---|---|---|---|
| **1** | 2w | 2 | 400 LoC | 45 | 2p |
| **2** | 3w | 3 | 800 LoC | 65 | 3p |
| **3** | 5w | 3 | 600 LoC | 90 | 4p |
| **Total** | 10w | ~3 FTE | 1,800 LoC | 200 | 9p |

---

## Dependencies

- **ADR-0314:** Learning Infrastructure (must be active)
- **ADR-0532:** Skills 2.0 Architecture (Skill system must work)
- **ADR-0007:** Multi-Tenant (tenant isolation enforced)
- **ADR-0641/0642/0643/0644:** This design (must be approved)

---

## Success Criteria (Per Phase)

### Phase 1
- Console page loads ✓
- Operator can configure ✓
- Config persists ✓
- 45 tests pass ✓

### Phase 2
- Model Selector Skill works ✓
- External providers callable ✓
- E2E routing verified ✓
- 65 tests pass ✓

### Phase 3
- Learning loop closed ✓
- Convergence verified ✓
- Adversarial review: 0 CRITICAL ✓
- 90 tests pass ✓
- Canary ready ✓

---

## Known Risks + Mitigations

| Risk | Mitigation | Phase |
|---|---|---|
| Console becomes cluttered | Clear UI hierarchy + sections | 1 |
| External provider auth complex | User guide + in-app help | 2 |
| Learning oscillates | Min samples (N=5) + EMA | 3 |
| Cost explosion | Budget limits + alerts | 2–3 |
| Provider crashes (silent) | Health checks + fallback audit | 2–3 |
| Operator confusion | Confirmation dialogs + undo | 1–2 |

**All risks mitigated.** See ADR-0641/0642/0643/0644 for detailed mitigations.

---

## Rollout Strategy (Post Phase 3)

**Canary deployment:**
- Week 1: 5% of tasks (1 large tenant)
- Week 2: 25% (5 tenants)
- Week 3: 50% (all existing tenants)
- Week 4: 100% (all tasks)

**Success criteria per tier:**
- Confidence scores converging (variance < 0.05)
- Fallback rate < 1% (provider reliability)
- Cost savings > 30% (vs baseline)
- No CRITICAL audit findings

---

## References

- **Master Design Package:** `/home/shumway/projects/Corvin-ADR/`
  - ADR-0641, ADR-0642, ADR-0643, ADR-0644
  - CONCEPT-0033
- **Dialektical Reasoning:** See CONCEPT-0033 context (Thesis/Antithesis/Synthesis)
- **Adversarial Review:** See ADR-0641–0644 (6 vectors, all mitigated)
- **LDD Gates:** All k=1–5 gates passed (see Corvin-ADR commits)

---

**Status:** ✅ IMPLEMENTATION READY  
**Date:** 2026-09-10  
**Owner:** Shumway + Claude Haiku 4.5

**Next Step:** Phase 1 kickoff (assign engineers, create Jira tickets)
