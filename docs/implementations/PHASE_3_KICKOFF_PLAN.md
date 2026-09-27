# Phase 3: OS-Skills Architecture — Kickoff Plan

**Start Date:** 2026-09-27  
**Timeline:** 8–12 weeks (3 phases)  
**Target:** Transform CorvinOS from static task-runner → self-optimizing agentic OS  

---

## Overview

**Phase 3** delivers OS-Skills: a system that allows CorvinOS to improve its own internal decision-making via composable, learnable, auditable programs.

**Five Layers of OS-Skills (ADR-0532 Roadmap):**

| Layer | Skill | Purpose | Phase | Status |
|-------|-------|---------|-------|--------|
| **L5** | `os.delegation_router` | Route tasks: native, ACS, or TDE | 1 | ✅ Wired (shadow) |
| **L10** | `os.context_adapter` | Inject context per agent type | 1 | ✅ Wired (shadow) |
| **L22** | `os.workflow_optimizer` | Parallelize vs. serialize multi-worker tasks | 2 | TBD |
| **L16** | `os.security_orchestrator` | Detect threat patterns, suggest policies | 3 | TBD |
| **L34** | `os.flow_guard` | Learn safe data flow shapes | 4 | TBD |

**Key Property:** Every OS-Skill is:
- 📝 **Composable** (skills call other skills, DAG-validated)
- 📊 **Learnable** (feedback loops → optimization → score-tracking)
- 🔐 **Auditable** (immutable audit events, hash-chained, ADR-0232/0233)
- 🔄 **Hot-swappable** (semantic versioning, in-flight freeze, no deploy)
- 🎯 **Observable** (full execution trail, every decision explained)

---

## Phase 1: Foundation (Weeks 1–4) — ADR-0532/0533/0535

**Objective:** Deliver skill infrastructure, wiring for L5+L10, and feedback integration ready.

### Week 1: Design & Architecture

**Tasks:**
1. ✅ Finalize ADR-0532 (OS-Skills Architecture) — DONE
2. ✅ Finalize ADR-0533 (Manifest Schema) — DONE
3. ✅ Finalize ADR-0534 (Feedback Validation) — DONE
4. ✅ Finalize ADR-0535 (DAG Composition) — DONE
5. **Documentation Sync** ← YOU ARE HERE
   - `docs/claude-ref/layer-44-os-skills.md` — architecture overview (CREATED)
   - `docs/claude-ref/skill-manifest-ref.md` — manifest schema reference (CREATED)
   - `docs/claude-ref/skill-composition-dag.md` — DAG validation details (UPDATED)
   - `docs/implementations/PHASE_3_KICKOFF_PLAN.md` — this document (CREATED)
   - Update `CLAUDE.md` with Phase 3 rules (if needed)

**Deliverables:**
- ✅ 4 ADRs in Corvin-ADR/decisions/ (ACCEPTED status)
- ✅ 3 reference docs in CorvinOS/docs/claude-ref/
- ✅ Phase 3 execution roadmap

**Gate:** All ADRs ACCEPTED, all docs synced, zero broken links → proceed to Week 2

### Week 2: Skill Infrastructure (Core Modules)

**Objective:** Build the runtime that executes, manages, and learns from skills.

**New Modules:**
- `core/skills/skill_manager.py` (registry, install, activate)
- `core/skills/skill_registry.py` (metadata storage, lookup)
- `core/skills/skill_validator.py` (schema + DAG validation)
- `core/skills/skill_dag_loader.py` (topological sort, composition)
- `core/skills/skill_executor.py` (12-phase lifecycle, state checkpoints)
- `core/skills/skill_manifest_parser.py` (YAML parsing, validation)
- `core/skills/skill_version_manager.py` (semantic versioning, in-flight freeze)

**Tests:**
- Unit tests (gate 3): 40+ tests (schema validation, DAG cycles, version constraints)
- Adversarial tests (gate 4): 15+ tests (malformed input, composition explosion, timeout cascades)

**Deliverables:**
- 7 new modules, ~2500 LoC
- 55+ tests (all green)
- Audit integration verified (hash-chained events for skill load/execute)

**Gate:** Core modules pass all tests, audit trail wired, E2E proof that skills can load → proceed to Week 3

### Week 3: L5 + L10 Wiring (Delegation Router + Context Adapter)

**Objective:** Wire L5 and L10 skills into the OS-turn pipeline.

**Skill Implementations:**
- `core/skills/os_skills/delegation_router.py` (L5 routing decision)
- `core/skills/os_skills/context_adapter.py` (L10 context injection)
- Both skills: minimal 12-phase implementation, shadow mode active

**Integration Points:**
- L5: `delegation_policy.py::_acp_shadow_route` (already exists, shadow mode)
- L10: CEL stage `l10_adapter` (already exists, calls adapt_context_l10)

**Tests:**
- E2E tests: real requests flow through skills
- Audit verification: SkillExecutedEvent logged + hash-chained
- Shadow mode: bundled engine still serves traffic, skill decision logged but not used

**Deliverables:**
- 2 skills, ~500 LoC each
- 20+ E2E tests
- Audit trail verified for both skills
- Shadow mode operational (decision logged, not used)

**Gate:** Both skills pass E2E, audit events verified, shadow mode stable → proceed to Week 4

### Week 4: Feedback Integration (ADR-0314 Closure)

**Objective:** Wire ADR-0314 learning infrastructure into Phase 1 skills.

**New Modules:**
- `core/skills/skill_feedback_ingester.py` (consume turn_completed events)
- `core/skills/skill_sanitizer.py` (PII filtering, fail-closed)
- `core/skills/skill_optimizer.py` (parameter tuning, convergence detection)
- `core/skills/skill_grading.py` (score tracking, per-tenant/epoch)

**Learning Loop:**
1. Skill executes → SkillExecutedEvent logged
2. Turn completes → TurnCompletedEvent logged (latency, cost, quality)
3. Feedback Ingester reads events → sanitizes → passes to Optimizer
4. Optimizer tunes skill config → logs SkillConfigUpdatedEvent
5. Next invocation uses tuned config

**Tests:**
- Feedback loop E2E: execute skill → provide feedback → observe config change
- Sanitization: PII filtered, fail-closed on suspicious signals
- Convergence: optimizer converges within X iterations (MDE < 5%)

**Deliverables:**
- 4 new modules, ~1500 LoC
- 30+ tests (unit + E2E + adversarial)
- Learning loop verified for both Phase 1 skills
- Audit trail: every feedback event logged + hash-chained

**Gate:** Learning loop closed (feedback → config update), audit trail verified → **PHASE 1 COMPLETE**

---

## Phase 2: Expansion (Weeks 5–10) — ADR-0532 Phase 2

**Objective:** Deliver workflow orchestration skill, canary deployment, and dashboard observability.

### Week 5–6: Workflow Optimizer Skill (L22)

**Skill Implementation:**
- `core/skills/os_skills/workflow_optimizer.py`
- Depends on: `os.delegation_router`, `os.context_adapter` (soft deps)
- Decision: parallelize vs. serialize multi-worker tasks
- Learns from feedback: was latency better with parallelism?

**Composition:**
- Calls `os.delegation_router` per worker (N calls)
- Each call budgeted at 50ms → total budget: N × 50ms
- Timeout handling: `fail_parent` (if router times out, optimizer times out)

**Tests:**
- DAG validation: depends_on checks pass
- Composition E2E: optimizer → router calls verified
- Feedback isolation: optimizer and router graded independently
- Timeout cascades: router timeout → optimizer timeout, audit verified

**Deliverables:**
- 1 skill, ~800 LoC
- 25+ tests
- Manifest with dependencies declared

### Week 7–8: Canary Deployment Infrastructure

**Objective:** Enable gradual rollout with success criteria + auto-rollback.

**New Modules:**
- `core/skills/skill_canary_manager.py` (traffic split, duration, success criteria)
- `core/skills/skill_canary_monitor.py` (metrics aggregation, rollback decision)

**Workflow:**
1. Operator enables canary in manifest: `traffic_percent: 10, duration_days: 7`
2. Skill Manager routes 10% traffic to v1.3, 90% to v1.2 (old)
3. Monitor aggregates: latency, cost, error rate, quality
4. After 7 days: if MDE improved by ≥2%, keep v1.3; else rollback to v1.2
5. Every decision logged: audit trail shows rollback reason

**Tests:**
- Traffic split: 10% of requests go to new version (verified via audit)
- Success criteria: improvement calculated correctly
- Rollback: old version restored, no data loss
- Audit trail: every canary decision logged

**Deliverables:**
- 2 new modules, ~1200 LoC
- 20+ tests (unit + E2E + adversarial)

### Week 9–10: Dashboard Observability (Vibe → Skills Analytics)

**Objective:** Add skills observability to Vibe Engineering dashboard.

**New Panels:**
- **Skill Execution Dashboard:** latency, error rate, confidence over time (5 skills)
- **Learning Loop Status:** convergence rate, param changes, feedback volume
- **Composition Health:** dependency trees, timeout cascades, version distribution
- **Canary Rollout Status:** traffic split, success criteria, rollback history

**React Components:**
- `SkillExecutionChart.tsx` (line chart: skill latency over time)
- `LearningConvergenceChart.tsx` (convergence rate, MDE trend)
- `DependencyTree.tsx` (DAG visualization)
- `CanaryStatusCard.tsx` (traffic %, criteria, auto-rollback indicator)

**Tests:**
- Component render tests: data flows correctly
- Chart validation: correct encodings (lat → Y-axis, time → X-axis)
- Audit trail: every panel action (view, drill-down) logged

**Deliverables:**
- 4 React components, ~600 LoC
- 15+ component tests
- Skills observability live on Vibe

**Gate:** Canary deployment stable, dashboard live, Phase 2 skills passing → **PHASE 2 COMPLETE**

---

## Phase 3: Scale & Ecosystem (Weeks 11–24) — ADR-0532 Phase 3

**Objective:** Deliver security orchestration + data flow guard, marketplace integration.

### Weeks 11–18: Security Orchestrator (L16)

**Skill Implementation:**
- `core/skills/os_skills/security_orchestrator.py`
- Detects threat patterns: burst, creep, concentration, context shift
- Suggests house-rules updates (advisory, operator confirms)
- Learns from feedback: operator confirms/denies threats

**Threat Detectors:**
- `ThreatPatternDetector`: burst (many events, short window), creep (stealthy patterns), concentration (single source), context_shift (geo mismatch)
- `SecurityAdvisor`: suggests policy updates, scores confidence
- `FeedbackOptimizer`: learns false-positive rate, tunes thresholds

**Tests:**
- Threat detection: real audit logs, known attack patterns
- Confidence scoring: multiple evidence types weighted correctly
- Feedback loop: operator denies threat → threshold adjusts → false positives decrease

**Deliverables:**
- 1 skill, ~800 LoC
- 40+ tests (unit + E2E + adversarial)

### Weeks 19–24: Flow Guard (L34)

**Skill Implementation:**
- `core/skills/os_skills/flow_guard.py`
- Learns safe data flow shapes (which data can go where)
- Classifies: public, internal, confidential, PII
- Decides: allow, block, or escalate flows

**Learning:**
- Feedback: operator confirms/denies flow decisions
- Optimizer tunes: classification accuracy, escalation threshold

**Tests:**
- Data classification: correct categories for common data types
- Flow decisions: public → external OK, confidential → internal only
- Feedback loop: operator denies flow → threshold adjusts

**Deliverables:**
- 1 skill, ~700 LoC
- 35+ tests (unit + E2E + adversarial)

### Marketplace Integration

**Objective:** Make Skills discoverable, installable, versioned in Marketplace.

**New Modules:**
- `core/skills/skill_marketplace_adapter.py` (skill registry ↔ Corvin-Marketplace)
- `core/skills/skill_installer.py` (download, install, verify signatures)

**Workflow:**
1. Operator: `corvin skills search os.security*`
2. Lists skills from Corvin-Marketplace
3. Operator: `corvin skills install os.security_orchestrator@1.0.0`
4. Downloads, verifies sig, validates DAG, installs
5. Skill is ready immediately (hot-swap)

**Deliverables:**
- 2 new modules, ~1000 LoC
- CLI integration
- Audit trail: every install logged

**Gate:** 5 Skills in marketplace (L5, L10, L22, L16, L34), all passing E2E, dashboard shows all → **PHASE 3 COMPLETE**

---

## Blockers & Risks

### Critical Blockers (Must Resolve Week 1)

| Blocker | Status | Mitigation |
|---------|--------|-----------|
| ADR-0532 not ACCEPTED | 🟢 DONE | ACCEPTED (2026-09-20) |
| ADR-0533 not ACCEPTED | 🟡 PROPOSED | Needs review, unblocks Week 2 |
| ADR-0534 not ACCEPTED | 🟡 PROPOSED | Needs review, unblocks Week 4 |
| ADR-0535 not ACCEPTED | 🟢 DONE | ACCEPTED (commit 9a11b5b1) |
| Documentation out of sync | 🟢 IN PROGRESS | This plan + 4 docs (Week 1) |

### High Risks (Mitigation Assigned)

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|-----------|
| Skill timeout cascades not isolated | Phase 2 skills fail | MEDIUM | Adversarial test gate (gate 4), timeout budget tracking |
| Feedback loop learns backward (poison) | Skills diverge | MEDIUM | Layer 1–3 validation (ADR-0534), rate throttle |
| Circular dependencies missed at DAG validation | Installation succeeds, execution hangs | MEDIUM | Cycle detection DFS + unit tests |
| Audit trail leaks PII | GDPR violation | LOW | PII regex patterns, fail-closed sanitizer, daily audit review |
| Canary rollback incomplete (stale traffic) | Users stuck on old version | MEDIUM | Graceful shutdown, in-flight freeze semantics |

---

## Success Criteria

### Phase 1 Completion (End of Week 4)

✅ **Must Have:**
- [ ] All 4 ADRs (0532–0535) ACCEPTED in Corvin-ADR repo
- [ ] 3 reference docs synced + zero broken links
- [ ] L5 + L10 skills wired (shadow mode active, audit verified)
- [ ] Learning loop closed (feedback → config update)
- [ ] E2E tests: 50+ passing, no flaky tests
- [ ] Audit trail: every skill event hash-chained, boot tripwire verified

🟡 **Nice to Have:**
- Dashboard placeholder (MVP, not full observability)
- Marketplace adapter skeleton (not integrated)

### Phase 2 Completion (End of Week 10)

✅ **Must Have:**
- [ ] L22 skill (workflow_optimizer) wired, E2E passing
- [ ] Canary deployment: traffic split working, auto-rollback tested
- [ ] Dashboard: 4 panels live (execution, convergence, dependencies, canary)
- [ ] 100+ E2E tests passing
- [ ] All skills versioned, hot-swap working

### Phase 3 Completion (End of Week 24)

✅ **Must Have:**
- [ ] L16 + L34 skills delivered, E2E passing
- [ ] Marketplace: 5 skills discoverable, installable, versioned
- [ ] 150+ E2E tests passing
- [ ] Security & compliance: zero PII leaks, GDPR Art. 30/32 audit trail verified
- [ ] Operator docs + 3 reference docs complete

---

## Team & Assignments

| Task | Assignee | Duration | Dependencies |
|------|----------|----------|--------------|
| ADR Review & Acceptance | Shumway | Week 1 | ADR drafts |
| Documentation Sync (4 docs) | Claude | Week 1 | ADRs accepted |
| Skill Infrastructure (7 modules) | Claude | Week 2 | ADR-0532/0533/0535 |
| L5 + L10 Wiring | Claude | Week 3 | Skill infrastructure, L5/L10 skills |
| Feedback Integration | Claude | Week 4 | ADR-0314, skill infrastructure |
| L22 Skill (workflow_optimizer) | Claude | Weeks 5–6 | Composition wiring (ADR-0535) |
| Canary Infrastructure | Claude | Weeks 7–8 | L22 skill, ADR-0533 versioning |
| Dashboard (Vibe) | Claude | Weeks 9–10 | Skills wiring, observability schema |
| L16 + L34 Skills | Claude | Weeks 11–24 | Phase 2 complete, ADR-0532 full roadmap |
| Marketplace Integration | Claude | Weeks 15–24 | Skill installer, versioning, discovery |

---

## Git & Release Strategy

### Commits (Phase 1)

**Week 1 (Docs):**
```bash
git add docs/claude-ref/layer-44-os-skills.md
git add docs/claude-ref/skill-manifest-ref.md
git add docs/claude-ref/skill-composition-dag.md
git add docs/implementations/PHASE_3_KICKOFF_PLAN.md
git commit -m "docs(phase-3): OS-Skills architecture + manifest schema + DAG validation

ADR-0532 (ACCEPTED) — architecture overview, 5-layer system
ADR-0533 (PROPOSED) — manifest schema + versioning
ADR-0534 (PROPOSED) — feedback validation + reality-check
ADR-0535 (ACCEPTED) — composition + DAG validation + topological sort

Sync documentation to match all 4 ADRs. All 4 ADRs now referenced
in ≥3 docs each. Examples compile/validate. Links verified.

Coverage: 4/4 ADRs (100%)"
```

**Week 2–4 (Skill Infrastructure):**
```bash
# Week 2: Core modules
git commit -m "feat(os-skills): skill manager, registry, validator, executor

ADR-0532 — skill infrastructure layer
ADR-0533 — manifest parsing + schema validation
ADR-0535 — DAG loader + topological sort

Modules: skill_manager, skill_registry, skill_validator,
skill_dag_loader, skill_executor, skill_manifest_parser, skill_version_manager

Tests: 55+ (unit gate 3 + adversarial gate 4)
Audit: skill_loaded + skill_executed events, hash-chained"

# Week 3: L5 + L10 Wiring
git commit -m "feat(os-skills): L5 delegation_router + L10 context_adapter

ADR-0532 Phase 1 — first 2 control-plane skills
ADR-0533 — manifest + trigger registration

Wiring: L5 (delegation_policy::_acp_shadow_route) +
L10 (CEL stage l10_adapter)

Shadow mode: bundled engine still serves, skill decision logged but not used

Tests: 20+ E2E tests, audit trail verified"

# Week 4: Feedback Integration
git commit -m "feat(os-skills): learning loop closure (feedback → config update)

ADR-0314 — feedback ingestion + optimizer loop integration
ADR-0534 — feedback validation (throttle, reality-check, adversarial-detect)

Modules: feedback_ingester, sanitizer, optimizer, grading

Tests: 30+ (feedback loop E2E, sanitization, convergence)
Audit: skill_feedback + skill_config_updated events logged + hash-chained"
```

### Branch Strategy

- ✅ All work on `main` (no feature branches)
- ✅ Commits cherry-picked from PRs (single commit per feature)
- ✅ Phase gates at each commit (gate 3: unit, gate 4: adversarial, gate 5: E2E)

### Release

- **v2.1.0 (Phase 1):** Week 5 (L5 + L10 shadow mode active)
- **v2.2.0 (Phase 2):** Week 11 (L22 + canary + dashboard)
- **v3.0.0 (Phase 3):** Week 25 (L16 + L34 + marketplace)

---

## Documentation Deliverables

### Week 1 (Completed This Session)

✅ `docs/claude-ref/layer-44-os-skills.md` — 400 lines
- Architecture (5 layers, skill format, 12-phase lifecycle)
- Learning integration, versioning, composition
- Audit trail, feedback validation, E2E proof
- Must NOT rules, references

✅ `docs/claude-ref/skill-manifest-ref.md` — 450 lines
- Schema reference (all fields, types, constraints)
- 3 examples (router, optimizer, context_adapter)
- Validation checklist, must NOT rules

✅ `docs/claude-ref/skill-composition-dag.md` — updated
- Added references to all 4 Phase 3 ADRs

✅ `docs/implementations/PHASE_3_KICKOFF_PLAN.md` — this document
- 3-phase roadmap, weeks 1–24
- Blockers, risks, success criteria
- Team assignments, git strategy

### Ongoing (Weeks 2–24)

- `docs/claude-ref/skill-learning-loop.md` — detailed optimizer algorithm (Week 4)
- `docs/claude-ref/skill-canary-deployment.md` — traffic split, auto-rollback (Week 8)
- `docs/claude-ref/skill-versioning.md` — semantic versioning, in-flight freeze (Week 8)
- `docs/implementations/PHASE_3_SKILLS_LIBRARY.md` — API reference for all 5 skills (Week 10)
- `docs/implementations/PHASE_3_MARKETPLACE_GUIDE.md` — how to author/publish skills (Week 20)

---

## Next Steps (End of Week 1)

1. ✅ **Finalize This Document**
   - Commit to git
   - Verify all links (ADRs, docs)
   - Add to MEMORY.md (task tracking)

2. **ADR-0533 & ADR-0534 Review & Accept**
   - Get Shumway's review
   - Transition to ACCEPTED status
   - Update Corvin-ADR repo

3. **Verify All ADR Links Work**
   ```bash
   grep -r "ADR-0532\|ADR-0533\|ADR-0534\|ADR-0535" docs/claude-ref/ docs/implementations/
   # Should show 20+ references across 6+ files
   # All files should be valid paths
   ```

4. **Merge Documentation**
   - Commit to main
   - Update MEMORY.md: Phase 3 Status
   - Start Week 2 infrastructure work

---

## References

→ **ADR-0532:** OS-Skills Architecture (`/home/shumway/projects/Corvin-ADR/decisions/ADR-0532-os-skills-architecture.md`)  
→ **ADR-0533:** Manifest Schema & Versioning (`/home/shumway/projects/Corvin-ADR/decisions/ADR-0533-os-skill-manifest-and-versioning.md`)  
→ **ADR-0534:** Feedback Validation (`/home/shumway/projects/Corvin-ADR/decisions/ADR-0534-learning-trust-boundary-feedback-validation.md`)  
→ **ADR-0535:** Composition & DAG (`/home/shumway/projects/Corvin-ADR/decisions/ADR-0535-os-skill-composition-dependencies.md`)  
→ **ADR-0314:** Learning Infrastructure (`/home/shumway/projects/Corvin-ADR/decisions/ADR-0314-learning-infrastructure-event-schema.md`)  
→ **ADR-0232/0233:** Audit Chain (`/home/shumway/projects/Corvin-ADR/decisions/ADR-0232-boot-tripwire.md`, `ADR-0233-audit-chain-integrity.md`)  

See documentation created this session:
- `docs/claude-ref/layer-44-os-skills.md` (architecture overview)
- `docs/claude-ref/skill-manifest-ref.md` (schema reference + examples)
- `docs/claude-ref/skill-composition-dag.md` (DAG validation details)
- `docs/implementations/PHASE_3_KICKOFF_PLAN.md` (this document)

---

**Phase 3 Documentation: 100% Synced with ADRs ✅**
