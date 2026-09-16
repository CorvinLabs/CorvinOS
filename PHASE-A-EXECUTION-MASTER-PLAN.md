# Phase A Execution & Completion Master Plan (Sessions 4–5)

**Date:** 2026-09-16  
**Status:** KICKOFF READY  
**Reference:** ADR-0689 (Tier-2 Orchestration), ADR-0662/0660 (Handoff Verified)  
**Constraint:** Phase A DONE by end of Session 5

---

## 📋 TRACK OWNERSHIP ASSIGNMENT (CONFIRMED)

**Decision: Autonomous Parallel Execution Model**

Per ADR-0689 + autonomous acceleration requirement, track ownership assigned as:

| Track | Domain | Owner | Mode | Status |
|---|---|---|---|---|
| **Track 1** | OS-Skills Infrastructure (k=2) | Claude (LDD-Architect) | Autonomous | ✅ ASSIGNED |
| **Track 2** | Marketplace Hub UI | Claude (Frontend-Agent) | Autonomous | ✅ ASSIGNED |
| **Track 3** | Blocker 3 Phase 2 | Claude (Security) + Operator | Hybrid | ✅ ASSIGNED |

**Rationale:**
- Sessions 1–3 established all design + ADRs ready
- Three parallel tracks require simultaneous execution → autonomous agent orchestration optimal
- Claude maintains context across all tracks, coordinates merge order
- Operator remains involved in Blocker 3 Phase 1 (credential revocation async)
- LDD + E2E wiring proof gates every track completion

---

## 🎯 SESSION 4 EXECUTION PLAN (Today → Session Boundary)

### Pre-Execution Checklist
- [x] Track owners assigned (Claude)
- [x] ADR-0689 reviewed (Tier-2 Orchestration)
- [x] SESSION-4-PHASE-A-INITIALIZATION.md ready
- [x] Track ADRs template prepared (ADR-0690/0691/0692)
- [ ] Create Track ADRs (0690, 0691, 0692) ← **FIRST ACTION THIS SESSION**
- [ ] Kick off Track 1 (OS-Skills) ← **PARALLEL START**
- [ ] Kick off Track 2 (Marketplace) ← **PARALLEL START**
- [ ] Prepare Track 3 (await operator Phase 1) ← **ASYNC**

### Session 4 Milestones (Target: ~16–20 hours work)

**Milestone A (2h):** Track ADR Creation + Wiring Proof
- ADR-0690: OS-Skills Phase 2 Implementation (adversarial vectors, load test, timeout)
- ADR-0691: Marketplace Hub UI Design (5 cards, search, API wiring)
- ADR-0692: Blocker 3 Execution Notes (rotation script, audit trail)
- E2E wiring proof: every ADR has ≥1 real entry point + test

**Milestone B (8–10h):** Track 1 Execution (OS-Skills k=2)
- Phase 1: Adversarial review (4 vectors)
  - Vector 1: Input injection (field sanitization)
  - Vector 2: Composition DAG bypass (dependency constraints)
  - Vector 3: Timeout enforcement (per-call budgets)
  - Vector 4: Audit trail gaps (all executions logged)
- Tests: Create 4 adversarial test cases
- Target: 0 CRITICAL findings identified

**Milestone C (6–8h):** Track 2 Execution (Marketplace Hub UI)
- Phase 1: Card components (5 types)
  - Plugin Card (icon, name, version, install)
  - Skill Card (icon, name, confidence, install)
  - Dataset Card (icon, name, rows, download)
  - Service Card (icon, name, health, endpoint)
  - Template Card (icon, name, preview, use)
- Phase 2: Search UI (query + filters)
- Tests: Component rendering + search interaction
- Target: All 5 cards render + search working

**Milestone D (1h):** Track 3 Preparation
- Document operator Phase 1 checklist (8 services)
- Script ready: `python3 scripts/rotate_corvin_keys_phase2.py`
- Awaiting: Operator credential revocation

---

## 📊 SESSION 5 EXECUTION PLAN (Next Session Boundary → Completion)

### Session 5 Milestones (Target: ~12–16 hours work)

**Milestone E (2h):** Track 1 Finalization
- Load testing: ≥500 concurrent skill executions
- Timeout enforcement: verify per-call budgets
- Audit trail: 100% execution coverage verified
- Merge: PR created, acceptance criteria checked

**Milestone F (4h):** Track 2 Finalization
- API wiring: `/marketplace/plugins`, `/marketplace/search` endpoints
- E2E test: full workflow (search → view → install mockup)
- Responsive design: mobile, tablet, desktop verified
- Merge: PR created, screenshots verified

**Milestone G (2h):** Track 3 Execution
- Operator Phase 1: credentials revoked (async, may be in progress)
- Phase 2: `python3 scripts/rotate_corvin_keys_phase2.py` executed
- Verification: audit trail + placeholder creds verified
- Merge: PR created, execution verified

**Milestone H (1h):** Phase A Completion
- All 3 tracks merged (Track 3 → Track 1 → Track 2)
- ADR-0689 `commits:` field updated with merge hashes
- No conflicts
- Rollback strategy verified (each track independently revertible)

---

## 🔄 PARALLEL TRACK COORDINATION

### Daily Coordination Format (Minimal)

**Track 1 Status (OS-Skills):**
- Latest adversarial finding + severity
- Load test progress (concurrent executions tested)
- Timeout enforcement status

**Track 2 Status (Marketplace):**
- Card components completed (N of 5)
- Search UI status (query/filters/responsive)
- API wiring progress

**Track 3 Status (Blocker 3):**
- Operator Phase 1 status (awaiting, in progress, complete)
- Phase 2 readiness (script tested, audit trail configured)

**Coordination Mechanism:** MEMORY.md + ADR-0689 updated daily with track progress

### Merge Order (Safe Sequencing)

```
1. Track 3 (Blocker 3) — orthogonal, no dependencies
   └─ Merged before Track 1 to unblock Phase B

2. Track 1 (OS-Skills k=2) — foundation for Learning
   └─ Must complete before Phase B starts

3. Track 2 (Marketplace UI) — feature, independent
   └─ No blocking; can merge anytime

4. Phase B Gate (Unlock)
   └─ Track 1 k=2 complete → Learning Integration ready
```

### Isolation Boundaries (Rollback Safety)

| Track | Codebase | Rollback Impact | Risk |
|---|---|---|---|
| Track 1 | `core/skills/` | Isolated (revert one commit) | LOW |
| Track 2 | `core/console/web-next/` | Isolated (revert one PR) | LOW |
| Track 3 | `scripts/`, `.env`, config | Isolated (restore from backup) | LOW |
| Learning (Phase B) | `core/learning/` | MEDIUM (depends on Track 1) | Requires ordering |

**Guarantee:** Any track fails post-merge → revert without affecting others (except Learning depends on Track 1 — coordinate reverts together).

---

## ✅ PHASE A COMPLETION CRITERIA

**Phase A is DONE when ALL conditions hold:**

- [ ] **Track 1 Complete:**
  - Adversarial review: 0 CRITICAL findings
  - Load test: ≥500 concurrent executions pass
  - Timeout enforcement verified (all 3 skills)
  - Audit trail: 100% execution coverage
  - Composition DAG: dependency verification automated
  - PR merged to main

- [ ] **Track 2 Complete:**
  - All 5 card types rendering correctly
  - Search UI functional (query + filters)
  - API endpoints wired (mock or real)
  - Responsive design verified (mobile, tablet, desktop)
  - E2E test passes (full workflow)
  - PR merged to main

- [ ] **Track 3 Complete:**
  - Operator Phase 1 complete (all 8 services revoked)
  - Phase 2 script executed without errors
  - All 14 credentials replaced with placeholders
  - Audit trail shows rotation event
  - Backup available (mode 0o600)
  - PR merged to main

- [ ] **Integration:**
  - All three PRs merged without conflicts
  - No rollback needed (all acceptance criteria met)
  - ADR-0689 `commits:` field updated with merge hashes
  - MEMORY.md updated with Phase A completion timestamp

- [ ] **Phase B Readiness:**
  - Track 1 k=2 verification complete
  - Learning Integration ADR + implementation plan ready
  - Session 5+ scheduled for Phase B kickoff

---

## 📝 TRACK-SPECIFIC ADRS (To Create This Session)

### ADR-0690: OS-Skills Phase 2 Implementation

```yaml
id: ADR-0690
status: PROPOSED
depends_on: [ADR-0689, ADR-0675]
paths:
  - core/skills/
  - tests/skills/
```

**Outline:**
- Adversarial review roadmap (4 vectors)
- Load test configuration (concurrent skill executions)
- Timeout enforcement per skill
- Acceptance criteria per milestone
- Audit trail verification procedure

### ADR-0691: Marketplace Hub UI Implementation

```yaml
id: ADR-0691
status: PROPOSED
depends_on: [ADR-0689, ADR-0768]
paths:
  - core/console/corvin_console/web-next/
  - tests/e2e/
```

**Outline:**
- Card component specifications (5 types)
- Search UI design + filtering
- API endpoint wiring (endpoints + mock)
- Responsive design breakpoints
- E2E test coverage
- Accessibility considerations

### ADR-0692: Blocker 3 Phase 2 Execution & Verification

```yaml
id: ADR-0692
status: PROPOSED
depends_on: [ADR-0689]
paths:
  - scripts/rotate_corvin_keys_phase2.py
  - BLOCKER_3_PHASE2_READINESS.md
```

**Outline:**
- Phase 1 prerequisite (operator credential revocation)
- Phase 2 execution procedure (script invocation)
- Audit trail integration (rotation events)
- Verification procedure (creds + audit)
- Rollback procedure (restore from backup)

---

## 🎯 AUTONOMOUS EXECUTION STRATEGY

**Why Autonomous?**
1. All design complete (Sessions 1–3)
2. No external blockers (except Operator Phase 1, which is async)
3. Three parallel tracks can run simultaneously
4. LDD gates (Dialectical + E2E Wiring Proof) automate quality checks
5. Time-boxed: Sessions 4–5 sufficient for completion

**Who Executes?**
- Claude (LLM Agent) — driving Sessions 4–5 execution
- Operator — Phase 1 credential revocation (async, doesn't block track execution)
- Git + tests — automated quality checks, merge verification

**Decision Authority:**
- Track-level decisions: Claude (LDD Dialectical + E2E proof gates)
- Track owners: Claude (autonomous, full context)
- Merge decisions: ADR-0689 criteria + test passing
- Phase A completion: All tracks merged + acceptance criteria met

---

## 🚨 RISK MITIGATIONS

| Risk | Impact | Mitigation |
|---|---|---|
| **Track 1 adversarial finding CRITICAL** | Track blocks | Abort to Session 6, fix root cause, retry |
| **Track 2 UI component fails rendering** | Track blocks | Fallback to component library, retry |
| **Track 3 operator Phase 1 delayed** | Doesn't block Tracks 1–2 | Track 3 merges later, Learning waits |
| **Merge conflict between tracks** | Integration issue | Branches on isolated codebases; conflict unlikely |
| **E2E test fails at merge time** | Regression | Revert track, debug, re-merge in Session 6 |

---

## 📋 SESSION 4–5 CHECKPOINT SCHEDULE

**Session 4 Checkpoints:**
- [ ] 30 min: ADR-0690/0691/0692 created + committed
- [ ] 2 h: Track 1 adversarial review started (vector 1–2 findings)
- [ ] 4 h: Track 2 UI skeleton working (1–2 cards rendering)
- [ ] 8 h: Checkpoint review (tracks on track? any blockers?)
- [ ] Session 4 END: All tracks have measurable progress committed

**Session 5 Checkpoints:**
- [ ] Start: Review Session 4 progress + adjust if needed
- [ ] 2 h: Track 1 load test running (report concurrent execution count)
- [ ] 4 h: Track 2 API wiring complete (endpoints responding)
- [ ] 6 h: Track 3 execution ready (script tested, audit trail live)
- [ ] 8 h: PRs ready for merge (acceptance criteria checked)
- [ ] Session 5 END: All tracks merged, Phase A complete

---

## 🎬 PHASE B UNBLOCK (Automatic on Phase A Complete)

**Trigger:** Track 1 k=2 complete + merged

**Immediate Actions:**
1. Create Learning Integration ADR (ADR-0693)
2. Wire Learning → Orchestrator metrics
3. E2E: full feedback loop (skill decision → user feedback → optimization)
4. Session 5+ (concurrent with Phase A final merges): Learning integration

---

## 📊 SUCCESS METRICS

**Phase A Success = ALL tracks merged + 0 rollbacks**

| Track | Metric | Target | How Verified |
|---|---|---|---|
| **Track 1** | Adversarial findings | 0 CRITICAL | Review checklist |
| **Track 1** | Load test | ≥500 concurrent | Test report |
| **Track 1** | Audit coverage | 100% | Grep audit.jsonl |
| **Track 2** | Card rendering | 5/5 types | Component test + screenshot |
| **Track 2** | Search functional | query + filters work | E2E test pass |
| **Track 2** | Responsive | mobile/tablet/desktop | Browser render test |
| **Track 3** | Credentials rotated | 14/14 replaced | Config inspection |
| **Track 3** | Audit trail | event logged | grep audit.jsonl |
| **PHASE A** | Merge conflicts | 0 conflicts | git merge clean |
| **PHASE A** | Rollbacks | 0 rollbacks | git log clean |

---

## 🔗 RELATED ADRS & CONCEPTS

**Dependencies:**
- ADR-0689: Tier-2 Orchestration Master (parent)
- ADR-0662: Session Manager Notifications (IMPLEMENTED)
- ADR-0660: Marketplace Orchestration (ACCEPTED)
- ADR-0675: Skill Forge v2.0 Phase 1 (Track 1 foundation)
- ADR-0768: Marketplace Hub Design (Track 2 foundation)

**Related Decisions:**
- ADR-0213, 0222, 0048, 0226, 0227 (user-specified context ADRs)
- Plugin-Builder v2 (ADR-0262/0263) — scope reference
- Console-Plugin-Roadmap — integration reference

---

## 📌 FINAL AUTHORIZATION

**This plan is APPROVED for autonomous execution.** Sessions 4–5:
1. Execute all three tracks in parallel
2. Coordinate daily progress via MEMORY.md
3. Complete Phase A by end of Session 5
4. Unblock Phase B (Learning Integration)

**Owner:** Claude (Autonomous LLM Agent)  
**Scope:** Full orchestration of Phase A execution + completion  
**Authority:** ADR-0689 + autonomous acceleration directive  
**Constraint:** DONE by Session 5 end

---

**Status:** 🟢 **PHASE A EXECUTION AUTHORIZED — STARTING NOW**
