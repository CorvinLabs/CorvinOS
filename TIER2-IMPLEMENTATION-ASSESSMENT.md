# TIER 2 IMPLEMENTATION ASSESSMENT (Phase C)

**Date:** 2026-09-17  
**Status:** CLARIFICATION REQUIRED  
**Scope:** Determine which Tier 2 initiative to focus on + verify implementation state

---

## ADR-0693 AMBIGUITY — CRITICAL CLARIFICATION

There are TWO **different** ADR-0693 versions in Corvin-ADR:

### Version A: Asset Analyzer Worker (PROPOSED)
- **File:** `ADR-0693-asset-analyzer-worker.md`
- **Scope:** Video production pipeline Phase 2 (deep-read asset analysis)
- **Purpose:** Extract and validate factual claims from mixed-media assets
- **Depends On:** ADR-0692 (Video Producer), ADR-0314 (Learning)
- **Stage:** Stage 1 (Ingestion) + Stage 2 (Deep Read) + Stage 2b (Contradiction Detection) + Stage 3 (Asset Roles)
- **Implementation Status:** PARTIAL (analyzer.py exists, no tests)
- **Path:** `core/skills/workers/asset_analyzer/analyzer.py`

### Version B: Learning Integration — Skill Learning Bridge Wiring (PROPOSED)
- **File:** `0693-learning-integration.md` (no ADR- prefix, indicates draft/alternate)
- **Scope:** Wire OS-Skills to EventStore feedback loop (Tier 2 Foundation)
- **Purpose:** Connect Skill Execution → Audit → Feedback → Optimizer → Config Updates
- **Depends On:** ADR-0314 (Learning Infrastructure), ADR-0675 (OS-Skills Phase 1), ADR-0232 (Audit)
- **Implementation Status:** PARTIAL (SkillLearningBridge exists, tests exist but incomplete)
- **Path:** `core/learning/skill_learning_bridge.py`

**→ RECOMMENDATION:** These are DIFFERENT initiatives. The user's task probably refers to **Version A** (Asset Analyzer), but the Phase C spec references **Version B** (Learning Integration) under C-T2-1.

---

## TIER 2 INITIATIVES — CURRENT STATE

### C-T2-1: Skill Learning Bridge Wiring (Learning Integration ADR-0693B)
**Status:** 🟡 PARTIAL (60% complete)

**Exists:**
- ✅ SkillLearningBridge class (core/learning/skill_learning_bridge.py, ~300 LOC)
- ✅ ConfigUpdateEvent + SkillFeedbackEvent dataclasses
- ✅ E2E test sketch (tests/test_phase4_skill_learning_e2e.py)
- ✅ Console routes (routes/skill_learning_routes.py)

**Missing:**
- ❌ LearningOptimizer implementation (referenced but not fully wired)
- ❌ Real call sites (reachability proof via E2E wiring)
- ❌ Audit integration (hash-chaining events not verified)
- ❌ PII scrubbing validation
- ❌ Bounds enforcement testing
- ❌ Convergence detection tests
- ⚠️ Tests: 5+ E2E tests exist but need completion + adversarial coverage

**Blockers:** None (ADR-0314 done, ADR-0675 done, ADR-0232 done)

**Acceptance Criteria (Phase C spec):**
- [ ] SkillLearningBridge.execute_with_learning() called from real orchestrator (reachability)
- [ ] LearningOptimizer.process_feedback() deterministic + replay-able
- [ ] Bounds enforcement: no delta > ±1σ (tested + verified)
- [ ] Convergence: stops at 95% confidence or slope plateau
- [ ] PII scrubbing: all patterns caught (email, phone, IPv4)
- [ ] Audit trail: all events logged + hash-chained
- [ ] Console panel: Learning Health metrics rendering
- [ ] 30+ E2E tests passing

---

### C-T2-2: Outcome Sink Implementation
**Status:** 🔵 DESIGN (ready for implementation)

**Exists:**
- ✅ ADR-0314 Phase 3 (EventStore, LearningEvent types)
- ✅ OutcomeSink skeleton (definition_of_done_verifier/feedback_event.py)
- ✅ TaskManager integration points (core/task_engine/)

**Missing:**
- ❌ Full OutcomeSink implementation
- ❌ Feedback attribution (task_id → skill_id tracing)
- ❌ GDPR compliance validation
- ❌ Consent check integration
- ❌ E2E tests (15+ required)

**Blockers:** None

---

### C-T2-3: Multi-Skill Orchestration
**Status:** 🔵 DESIGN (ADR TBD)

**Exists:**
- ✅ ADR-0535 (Skill Composition dependency model)
- ✅ Skill dependency graph concept

**Missing:**
- ❌ SkillComposer orchestrator
- ❌ DAG validation + topological sort
- ❌ Cost modeling + skill selection
- ❌ Multi-skill outcome attribution
- ❌ E2E tests (10+ required)

**Blockers:** ADR-XXXX (Multi-skill learning coordination) — needs to be written

---

### C-T2-4: Confidence Scoring + Consensus
**Status:** 🔵 DESIGN (ADR-0315 exists)

**Exists:**
- ✅ ADR-0315 (Confidence Intervals)
- ✅ Conceptual algorithm (Bayesian posterior)

**Missing:**
- ❌ ConfidenceScorer implementation
- ❌ Consensus algorithm (weighted voting)
- ❌ Skill selector optimization (epsilon-greedy)
- ❌ Dashboard: Skill Comparison panel
- ❌ E2E tests (15+ required)

**Blockers:** None

---

## OTHER IMPLEMENTATIONS (NOT TIER 2)

### Asset Analyzer Worker (ADR-0693A, Video Producer)
**Status:** 🟡 PARTIAL (30% complete)

**Exists:**
- ✅ `core/skills/workers/asset_analyzer/analyzer.py` (~250 LOC)
- ✅ Stages 1-3 implemented (Ingestion, Deep Read, Contradiction Detection, Asset Roles)
- ✅ Gate check (ready_for_narration flag)
- ❌ **ZERO tests** — critical gap

**Missing:**
- ❌ Tests: 20+ test cases required (asset ingestion, deep read, contradiction detection, gate blocking)
- ❌ Audit integration (skill_executed, skill_feedback events)
- ❌ E2E wiring proof (reachability from video_producer orchestrator)
- ❌ LLM prompt audit (contradiction detection, asset role inference)

**Blockers:** None (narrated-video-producer ingest_assets.py assumed available)

---

### DoD Verifier Skill 2.0
**Status:** ✅ MOSTLY COMPLETE (85% — design + tests exist)

**Exists:**
- ✅ skill.py (main executor, ~300 LOC)
- ✅ api_handlers.py (HTTP endpoints)
- ✅ checks/ (5 verification checks)
- ✅ scoring.py (numeric 0-100 scoring)
- ✅ loss_signal.py (feedback integration)
- ✅ weight_optimizer.py (learning)
- ✅ Tests: test_dod_verifier_phase[1-3].py (50+ test cases)

**Missing:**
- ⚠️ Minor: Audit integration edge cases
- ⚠️ Minor: Console panel styling

**Blockers:** None

---

### DataHub + Creator 2.0
**Status:** 🟡 PARTIAL (40% complete)

**Exists:**
- ✅ `core/skills/os_skills/datahub_unified/` (creator.py, datahub.py, models.py)
- ✅ `core/skills/os_skills/creator_2_0/` (alternative implementation)

**Missing:**
- ❌ Tests: None found — critical gap
- ❌ HTTP routes for ingestion (POST /v1/datahub/ingest)
- ❌ Schema validation tests
- ❌ Query API tests (GET /v1/datahub/query)
- ❌ E2E tests: ingest → query → create skill
- ❌ Audit integration

**Note:** Not a Tier 2 initiative per Phase C spec; may be Tier 3 or backlog.

---

## EXECUTION DECISION TREE

### IF User Wants: Asset Analyzer Worker (ADR-0693A)
→ **DO NOT proceed.** This is a **Video Producer component**, not Tier 2 Foundation.
→ Classify as Track 3 (Integration phase).
→ Execution steps:
   1. Write 20+ tests (asset ingestion, deep read, contradictions, gate)
   2. Add audit integration (skill_executed, skill_feedback events)
   3. E2E proof: call from video_producer.orchestrate_video_production()
   4. Merge into Track 3 Video Producer ADRs

### IF User Wants: Learning Integration (ADR-0693B, Tier 2 C-T2-1)
→ **RECOMMENDED.** This is **Tier 2 Foundation**, blocks downstream learning improvements.
→ Execution steps:
   1. Complete SkillLearningBridge implementation (30% missing)
   2. Wire LearningOptimizer (deterministic algorithm)
   3. Add PII scrubbing + bounds enforcement
   4. E2E wiring proof (real orchestrator call site)
   5. Write 30+ E2E tests + adversarial coverage
   6. Verify audit events hash-chained
   7. Complete Console learning health panel
   8. Rename ADR file from `0693-learning-integration.md` → `ADR-0693-learning-integration.md` (add prefix)

### IF User Wants: DataHub + Creator
→ **LIKELY BACKLOG or Tier 3.** Not in Tier 2 per Phase C spec.
→ If priority: Add 25+ tests + E2E wiring + audit integration

### IF User Wants: DoD Verifier
→ **ALREADY 85% COMPLETE.** Minor: audit edge cases + console panel.
→ 1-2 hour finalization work.

---

## RECOMMENDATION FOR THIS SESSION

**Focus Area:** C-T2-1 (Learning Integration, ADR-0693B)

**Rationale:**
- Tier 2 Foundation initiative (blocks all subsequent learning)
- 60% implemented (close to completion)
- Clear acceptance criteria (Phase C spec)
- Enables ADR-0314 feedback loop to actually work
- 6-8 hour implementation + testing

**Execution Plan:**
1. ✅ Clarify ADR-0693 ambiguity (document two versions, use prefix)
2. ✅ Complete SkillLearningBridge + LearningOptimizer wiring
3. ✅ Add comprehensive tests (30+ E2E cases)
4. ✅ E2E wiring proof (reachability from orchestrator)
5. ✅ Audit integration + hash-chaining verification
6. ✅ Commit with proper ADR reference
7. ✅ Merge to main

---

## ALTERNATIVE: Multi-Initiative Approach

If the user wants to tackle **multiple Tier 2 initiatives in parallel:**

| Initiative | Effort | Status | Owner |
|---|---|---|---|
| C-T2-1 (Learning Bridge) | 6-8h | 60% → 100% | **Focus this session** |
| C-T2-2 (Outcome Sink) | 4-6h | 0% → 100% | Session 9 |
| C-T2-3 (Multi-Skill Orch) | 6-8h | 0% → 100% | Session 9+ |
| C-T2-4 (Confidence) | 6-8h | 0% → 100% | Session 10+ |

**Total Tier 2 effort:** 22-30 hours (~1 week)

---

**NEXT STEP:** Clarify which initiative the user wants to tackle. This document provides the complete state for decision-making.
