# Context Engineering Optimization Initiative — Implementation Plan

**Version:** 1.0  
**Status:** Active Planning (Phase 0)  
**Date:** 2026-08-19  
**Team:** 2 persons (1 Engineer, 1 Reviewer)  
**Scope:** Phases 0–3 (G1–G5 Glass Box delivery)  
**Duration:** 12 weeks (3 weeks prep + 3 weeks per phase)  

---

## 1. Overview & Motivation

**Goal:** Unify context engineering observability and enable cross-device learning state sync, allowing operators to see exactly what context reaches the LLM and to learn from graded outcomes across devices.

**Phases (3–4 weeks each):**
- **Phase 0 (Prep):** ADRs, test scaffolding, flag plumbing → **Ready Gate**
- **Phase 1 (Foundation):** G1 Glass Box + G2 Overview unification → **Token measurement baseline**
- **Phase 2 (Learning):** G3 Stage-Grades + G4 Outcome-Wiring → **Flywheel active**
- **Phase 3 (Sync):** G5 Cross-Device Sync (Git+GPG) → **Multi-device learning**

Each phase ships behind feature flags (default-off); each has ≥1 E2E test proving real transport boundary. No semantic dedup (Phase 4, future).

---

## 2. Phases, Deliverables & Critical Path

### Phase 0: Prep & Infrastructure (Weeks 1–3)

**Owner:** Engineer + Reviewer (paired)  
**Gate:** All ADRs accepted + test scaffolding runs (0 failures, baseline established)

| Task | Days | Owner | Acceptance Criteria |
|------|------|-------|-----|
| ADR-0368 (Glass Box Reveal) | 1–2 | Engineer | ADR accepted; paths={`vibe_engineering.py`, `vibe.ts`} |
| ADR-0369 (Tenant Sync) | 1–2 | Engineer | ADR accepted; security model locked (GPG mandatory, U1 resolved) |
| ADR-0370 (Vibe Overview) | 0.5 | Engineer | ADR accepted (refactor-only, no new contract) |
| ADR-0371 (Learning Ledger + Outcome) | 1 | Engineer | ADR accepted; endpoints defined |
| Token Measurement Baseline | 2 | Engineer | Reference run on current `chat_runtime` + `prompt_assembly`; capture `final_prompt` length distribution (mean/p50/p99) |
| Feature Flag Infrastructure | 1 | Engineer | flags `{vibe_engineering, learning_ledger, outcome_feedback_loop, cross_device_sync}` exist in `spec.features`; Settings→Features renders toggles (off by default) |
| Test Scaffolding (5 failing E2E tests) | 2 | Engineer | `tests/e2e/{glassbox,vibe_overview,learning_ledger,outcome_wiring,tenant_sync}.py` exist, all fail; each names expected behavior in docstring |
| Review Checklist & Commit Template | 1 | Reviewer | CLAUDE.md extended: Layer 2 checklist (HTTP method, endpoint prefix, auth gate); commit message template includes ADR + E2E proof |
| **Phase 0 Total** | **9 days** | | **Ready Gate passes**: all ADRs ✅, flags ship-dark ✅, 5 E2E scaffolds fail→ ready for Phase 1 |

**Critical Path:** ADRs must precede any code (blocks approval); Token baseline must capture current state before optimization (comparison point).

---

### Phase 1: Foundation — Context Reveal & Trace Unification (Weeks 4–6)

**Owner:** Engineer (lead); Reviewer (sign-off)  
**Entry:** Phase 0 Ready Gate  
**Gate:** Token measurement Phase 1 (no regression); 2/2 E2E tests green

**G1: Glass Box Prompt Reveal** (Weeks 4–5)

| Task | Days | Acceptance Criteria |
|---|---|---|
| Fix F3 blocker: `persist_assembly` in Console chat path | 1 | `chat_runtime.py:4686` calls `_persist_assembly(rec)` (fire-and-forget); Console-Turn + Glass Box → `found: true` ✅ |
| Frontend: extract `TurnGlassBox` from modal | 1.5 | `pages/vibe-engineering.tsx` has `<GlassBoxPrompt turn={turn} />` component; renders `final_prompt` + CEL-block split + sections legend |
| Backend: `GET /vibe-engineering/prompt/{turn}` with session param | 1 | Route added; requires session_id (fixes rglob alias collision, C2); returns `{final_prompt, sections[], metadata}` |
| E2E Test (Transport boundary) | 2 | **Real Console chat-turn** → POST `/v1/console/chat/stream` → await CEL → refresh → `GET /vibe-engineering/prompt` → assert `final_prompt` ≠ empty + CEL block visible (C11) |
| **G1 Total** | **5.5 days** | |

**G2: Vibe Inspector Removal & Overview** (Weeks 5–6)

| Task | Days | Acceptance Criteria |
|---|---|---|
| Delete: `public/external-panels/vibe-inspector/`, registry entry, nav item | 0.5 | `git diff --stat` shows deletions only (no new code); `/app/vibe-inspector` 404s or redirects |
| New: `pages/vibe-overview.tsx` (flow diagram + aggregates) | 2 | Renders 8-stage pipeline (Turn → CEL-8 → Assembly → Engine → Outcome → Learning); aggregate stats from `/traces`; "How to read a trace" explanation box |
| E2E Test (reachability + regression) | 1.5 | `GET /app/vibe-overview` 200 ✅; aggregates render; no console.error(); old inspector-E2E removed/replaced |
| **G2 Total** | **4 days** | |

**Phase 1 Summary:**

| Metric | Target | Proof |
|--------|--------|-------|
| E2E tests passing | 2/2 | CI green; Playwright run on real Console |
| Token regression | < 3% vs Phase 0 baseline | `final_prompt` length mean, p50, p99 ≤ baseline × 1.03 |
| Trace unification | 1 surface (not 2) | Inspector gone; vibe-overview is sole observability entry |
| New API reachability | 100% | real call-site in UI + E2E via transport boundary (C11) |

---

### Phase 2: Learning Flywheel — Operator Grading & Outcome Wiring (Weeks 7–9)

**Owner:** Engineer; Reviewer  
**Entry:** Phase 1 gate  
**Gate:** Token measurement Phase 2 (learning event emission); 2/2 E2E tests green

**G3: Learning Ledger + Stage-Grade-UI** (Weeks 7–8)

| Task | Days | Acceptance Criteria |
|---|---|---|
| Backend: `grade_stage(tenant_id, stage_id, score, notes, grader)` function exists | 1 | `operator/context_engineering/grades.py:66` callable; writes to `grades.py`-Store with `grader="operator"` |
| Routes: `GET/POST /vibe-engineering/grades[/{stage}]` | 1 | GET returns all grades; POST/{stage} calls `grade_stage`; both auth-gated (require_session) + CSRF |
| Frontend: `pages/learning-ledger.tsx` (3 sections) | 2 | **Section 1:** CEL-Grade buttons (👎/😐/👍) for each stage; POST updates list; **Section 2:** mount existing TreeOfThoughts (make reachable — proof of reachability C11); **Section 3:** ULO placeholder |
| E2E Test (reachability + signal) | 2 | Real Console session → visit ledger → POST grade → `GET /grades` → n_grades incremented; TreeOfThoughts nav-reachable; 0 console.error |
| **G3 Total** | **6 days** | |

**G4: Lern-Kreislauf (Outcome-Wiring)** (Weeks 8–9)

| Task | Days | Acceptance Criteria |
|---|---|---|
| Backend: `record_turn_outcome(tenant_id, stage_ids, success)` wiring in `chat_runtime` | 1.5 | After CEL run (near `emit_decision_record`), hook fires with correct stage_ids; writes advisory (non-promoting) grade `__loop__` (C5); never raises |
| E2E Test (real signal) | 1.5 | Real Console chat-turn → successful response → check Grade Store → outcome record exists with correct stage_ids (not-promoting advisory grade); C11 transport boundary |
| Honest ADR amendment (F1 2nd order) | 0.5 | G4 ADR explicitly names: "advisory grades ≠ promoting"; "C5 literal, Flywheel only on Operator-Grades (G3)"; prevents overmarketing |
| **G4 Total** | **3.5 days** | |

**Phase 2 Summary:**

| Metric | Target | Proof |
|--------|--------|-------|
| E2E tests passing | 2/2 | Playwright green; real chat-turn signal |
| Grade velocity | ≥1 grade per turn | E2E: Chat-Turn + Advisory → n_grades+1 |
| Learning surfaces unified | 3/3 reachable | CEL-Grades, TreeOfThoughts, ULO all nav-accessible from single Ledger page |
| Token regression | < 3% vs Phase 0 | `final_prompt` + learning event payloads ≤ baseline × 1.03 |
| Flywheel honest scoping | explicit | ADR names what drives promotion (Operator-Grades only); advisory loop documented |

---

### Phase 3: Cross-Device Sync — Git-Based Learning State (Weeks 10–12)

**Owner:** Engineer; Reviewer  
**Entry:** Phase 2 gate  
**Gate:** Token measurement Phase 3; 1/1 E2E test green; PII backstop validation

**G5: Tenant Sync (Git+GPG, Opt-In)** (Weeks 10–12)

| Task | Days | Acceptance Criteria |
|---|---|---|
| Migrate `multi_instance.py` stub → `core/cross_device/tenant_sync.py` (typ-spec merge) | 2 | `TenantSync.run()` implements: **JSONL union** (Events), **Array union** (Grades — `n_grades`/`mean_score` recalc), **LWW** (Skills/Memory); no Git text-merge |
| `POST /sync` endpoint wiring | 1 | Route auth-gated; calls `TenantSync.run()`; returns Merge-Report; pulls from `spec.cross_device.sync_remote` (Config); PAT from Vault |
| PII Backstop (`_assert_no_raw_pii`) | 1 | Scans Grade `notes`, Memory, Skill bodies; fail-closed (drops PII-shaped payloads); tests with poisoned fixtures (C7) |
| Frontend: `pages/multi-instance.tsx` | 1.5 | Remove localhost port-probing; show Merge-Report; simple UI for conflict resolution (LWW display); clear "Git State ↔ A2A Live Metrics" split |
| E2E Test (real Merge + Transport) | 2 | **Two local Tenant-Checkouts** (divergent `ce_stage_grades.json` + `learning-events.jsonl`) → `POST /sync` on one → **Union: Events sorted, Grades n_grades/mean recalc'd, no Git conflicts** (C6) → **PII: poisoned fixture dropped, benign payload sent GPG-encrypted** (C7) → Git-Remote receives ciphertext (C11 real transport) |
| Feature Flag + Ready State | 1 | Flag `cross_device_sync` default-**false**; Settings→Features toggle; unset = old behavior (no sync offered) |
| **G5 Total** | **8.5 days** | |

**Phase 3 Summary:**

| Metric | Target | Proof |
|--------|--------|-------|
| E2E test passing | 1/1 | Real-checkout merge; GPG encryption; transport boundary |
| Sync success rate | 100% (test path) | Merge report ≠ stub; both sides converge |
| PII Backstop | 0 raw PII in push | Poisoned fixture dropped; benign payload GPG'd |
| Grade recovery (Sync benefit) | Grade-arrays unionized | recalc `n_grades`, `mean_score` from union, not additive |
| Token regression | < 3% vs Phase 0 | Payload size (pre-GPG) ≤ baseline × 1.03 |
| Opt-in + Transparency | Documented defaults | CLAUDE.md: Sync is default-off, GPG mandatory, Dritt-PII is GDPR egress event |

---

## 3. Critical Path & Dependencies

```
Phase 0 (Weeks 1–3)
├─ ADRs (0368/0369/0370/0371)
├─ Token Baseline
├─ Flags + Test Scaffolding
└─ Ready Gate ────────────────┐
                              │
                    Phase 1 (Weeks 4–6)
                    ├─ G1: Console `persist_assembly` ✅
                    ├─ G1: Glass Box Prompt UI ✅
                    ├─ G1: E2E (transport boundary) ✅
                    ├─ G2: Inspector deletion
                    ├─ G2: Vibe Overview
                    ├─ G2: E2E
                    ├─ Token Measurement Ph1
                    └─ Phase 1 Gate ─────────────────┐
                                                    │
                                        Phase 2 (Weeks 7–9)
                                        ├─ G3: Grade routes + UI
                                        ├─ G3: E2E (real grade)
                                        ├─ G4: `record_turn_outcome` hook
                                        ├─ G4: E2E (outcome signal)
                                        ├─ Token Measurement Ph2
                                        └─ Phase 2 Gate ─────────────────┐
                                                                        │
                                                            Phase 3 (Weeks 10–12)
                                                            ├─ G5: Tenant Sync Core
                                                            ├─ G5: GPG + PII Backstop
                                                            ├─ G5: Frontend Conflict UI
                                                            ├─ G5: E2E (real checkout merge)
                                                            ├─ Token Measurement Ph3
                                                            └─ Phase 3 Gate (Prod Readiness)
```

**Blockers:**
- Phase 0 Ready Gate blocks Phase 1 start
- Phase 1 Gate blocks Phase 2 start  
- Phase 2 Gate blocks Phase 3 start
- ADRs must be accepted before code merges (code/ADR sync per CLAUDE.md)

---

## 4. Risks & Mitigations

| # | Risk | Severity | Mitigation |
|---|------|----------|-----------|
| **R1** | `persist_assembly` missing in Console path (F3) causes G1 Glass Box to show `found: false` for Console-turns | **HIGH** | Phase 1 Task 1 is dedicated blocker-fix; E2E test MUST use real Console-turn (not mock); Reviewer audits call-site |
| **R2** | PII leakage in Sync payload (Dritt-PII from End-User chats) violates GDPR Art. 6/32 | **CRITICAL** | (a) GPG encryption mandatory (U1, non-optional), (b) `_assert_no_raw_pii` fail-closed, (c) G5 ADR explicitly frames as GDPR egress event, (d) E2E poisons fixtures; Reviewer sign-off required before flag-on |
| **R3** | Grade/Memory merge conflicts (two devices LWW on same `mtime`) cause silent data loss | **MEDIUM** | Merge-Report UI shows conflicts; LWW is transparent in Report; docs advise operators to check Report before re-editing. No hidden conflicts. |
| **R4** | Outcome-wiring (G4) drives promotion flywheel if `__loop__` grades accidentally marked promoting | **MEDIUM** | ADR explicitly names `__loop__` non-promoting; test asserts `grader != "operator"` for advisory; Grade-Store audits `grader` field |
| **R5** | Token regression from payload bloat (learning events, grades, memory sync payload) | **MEDIUM** | Token Measurement runs per phase; if regress > 3%, block phase advance; Engineer optimizes before proceeding |
| **R6** | E2E test flakiness (real Console-turn, real HTTP, Playwright timing) causes CI/CD false negatives | **MEDIUM** | E2E tests use explicit waits + retry logic (Playwright defaults); CI runs E2E twice; only pass if both pass; timeout cap 30s per test |
| **R7** | 2-person team overload if Reviewer bottlenecks code review | **MEDIUM** | Reviewer role is sign-off + Layer-2 checklist validation only (not deep code read); pair-programming on complex ADR decisions; Reviewer allocates ≥1 day/week dedicated |

---

## 5. Metrics & Measurement

### Token Economy (Reference: Phase 1 Framework)

**Baseline (Phase 0):** Capture current `final_prompt` length distribution on realistic Console-turns (n=50).

| Phase | Metric | Target | Method |
|-------|--------|--------|--------|
| **0** | `final_prompt` mean, p50, p99 | Baseline | Log 50 real turns; histogram |
| **1** | `final_prompt` (Glass Box + Overview) | ≤ baseline × 1.03 | Same 50 turns; compare |
| **2** | Learning event payload overhead | ≤ 2% of `final_prompt` | Grade + Outcome-record sizes; amortize per turn |
| **3** | Sync payload overhead (pre-GPG) | ≤ baseline × 1.03 | Merge-Report size; extrapolate to 30-day state |

**Acceptance:** Phase does NOT advance if token regression > 3% (Engineer must optimize or defer feature).

### Feature Coverage (Per-Phase)

| Feature | Phase | Metric | Target |
|---------|-------|--------|--------|
| **C1** Glass Box reachability | 1 | Clicks Turn→Prompt | ≤ 3 |
| **C3** Trace unification | 1 | Number of surfaces | = 1 (Inspector gone) |
| **C4** Learning UI access | 2 | Surfaces (CEL, ToT, ULO) | 3/3 reachable |
| **C5** Outcome feedback | 2 | `record_turn_outcome` callers | ≥ 1 (real) |
| **C6** Real Sync (not stub) | 3 | Merge-Report structure | ≠ stub; events/grades unionized |
| **C7** PII backstop | 3 | Poisoned payloads dropped | 100%; none in push |
| **C11** E2E via transport | All | New endpoints with call-sites | 100%; real HTTP |

### Quality Gates (Checkpoints)

```
Phase 0 Ready Gate (End of Week 3):
  ✅ All 4 ADRs accepted
  ✅ Token baseline stable
  ✅ 5 E2E scaffolds failing (ready for red→green)
  ✅ Feature flags wired (Settings→Features renders)
  → PROCEED to Phase 1 or PIVOT (re-plan)

Phase 1 Gate (End of Week 6):
  ✅ 2/2 E2E tests green (Glass Box + Overview)
  ✅ Token regression < 3%
  ✅ Trace-unification verified (1 surface)
  ✅ Code/ADR sync: ADRs 0368/0370 reflected in code
  → PROCEED to Phase 2 or EXTEND (fix token bloat)

Phase 2 Gate (End of Week 9):
  ✅ 2/2 E2E tests green (Grade + Outcome)
  ✅ Learning Flywheel active (advisory grades in Store)
  ✅ Grade velocity ≥ 1/turn (measurement)
  ✅ Token regression < 3%
  → PROCEED to Phase 3 or PAUSE (flywheel tuning)

Phase 3 Gate (End of Week 12):
  ✅ 1/1 E2E green (real Merge + Sync)
  ✅ PII Backstop: poisoned fixtures 100% dropped
  ✅ Token regression < 3%
  ✅ Operator sign-off on Sync default-off + GPG mandatory
  ✅ Code/ADR sync: ADR 0369 security model locked
  → READY FOR CANARY (10% rollout) or HOLD
```

---

## 6. Rollout & Feature Flags

**Default State (Fresh Install):**
```yaml
spec:
  features:
    vibe_engineering: false        # G1/G2 Glass Box
    learning_ledger: false         # G3 Grades UI
    outcome_feedback_loop: false   # G4 Wiring
    cross_device_sync: false       # G5 Sync
```

**Phase 1 (Week 6, Canary 10% users):**
- Flag `vibe_engineering` → **true** (Glass Box + Overview visible)
- Others remain off
- Metric: `final_prompt` latency p99 ≤ 100ms (GET `/prompt` call)

**Phase 2 (Week 9, Canary 25% users):**
- Flag `learning_ledger` + `outcome_feedback_loop` → **true** (Grades + advisory loop active)
- `vibe_engineering` → **true** (stable)
- Metric: Grade velocity, Outcome-record count

**Phase 3 (Week 12, Canary 10% users only, explicit opt-in):**
- Flag `cross_device_sync` → **true** for volunteers only
- Requires: operator consent, Vault PAT configured, `spec.cross_device.sync_remote` set
- Metric: Sync success rate, PII drop rate

**Production-Ready (Week 13+):**
- `vibe_engineering` + `learning_ledger` + `outcome_feedback_loop` → default-**true** (no longer flags)
- `cross_device_sync` remains **false** (opt-in; data-residency sensitive)

---

## 7. Team Assignments & Responsibilities

**Engineer (Days 1–3):**
1. Implement feature (code + E2E)
2. Capture token metrics
3. Debug failures (E2E flakiness, token regression)

**Reviewer (Days 2–3):**
1. ADR validation (Layer 2 checklist + security model)
2. Code review (E2E proves transport boundary, no mocks)
3. Sign-off (checkpoint gate decision)

**Weekly Sync (15 min):**
- Checkpoint status (on-track/at-risk/blocked)
- Token regression review
- Risk escalation (R1–R7)

---

## 8. Success Criteria & Post-Phase Actions

**Phase 0 Exit:** Infrastructure stable, ADRs accepted, tests scaffolded → **Ready Gate PASS**

**Phase 1 Exit:** Glass Box + Overview shipped dark → **Token measurement validated** → **10% canary**

**Phase 2 Exit:** Learning Ledger + Outcome loop active → **Grade velocity measured** → **25% canary**

**Phase 3 Exit:** Sync protocol proven, PII backstop validated → **Operator consent required** → **10% volunteer opt-in**

**Post-Phase (Week 13):**
- Deprecate raw multi_instance.py stub
- Backfill ADR-0380+ for lessons learned (Concept Gate)
- Plan Phase 4 (Semantic dedup) with updated risk model

---

## References

- **Concept:** `docs/concepts/vibe-engineering-glassbox-concept.md`
- **Existing Plan (Source):** `docs/implementation/vibe-engineering-glassbox-plan.md` (G1–G5 scope)
- **ADRs:** 0368 (Glass Box), 0369 (Sync), 0370 (Overview), 0371 (Grades + Outcome)
- **CLAUDE.md:** Feature Flags, E2E Wiring Proof, ADR Gate, Code/Docs Sync
- **Compliance:** Layer 16 (Audit), Layer 44 (House-Rules), GDPR Art. 6/32 (Consent, Data Flow)

