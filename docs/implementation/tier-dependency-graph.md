# Tier Dependency Graph — CorvinOS Autonomous Execution Plan

**Master Reference:** ADR-0688  
**Status:** Active (2026-09-16)  
**Updated:** 2026-09-16 by Claude (Haiku 4.5)

---

## Overview: 4 Tiers, 18 Initiatives, 4–6 Weeks

This graph defines:
1. **Dependency order** (what blocks what)
2. **Estimated effort** per initiative
3. **Session estimate** (how many 4–6h sessions needed)
4. **Handoff conditions** (when tier is done, next tier starts)
5. **Parallel tracks** (what can run simultaneously)

---

## TIER 1: Blocker Fixes (Session 2, Est. 1–2 Sessions)

### 1.1 Blocker 2: L10 Context Adapter Wiring

| Attribute | Value |
|---|---|
| **Status** | CONDITIONAL (verify if actually blocking Tier 2) |
| **Scope** | Wire `os.context_adapter` Skill into L10 context-engineering pipeline |
| **Effort** | 2–3h (if required); defer if not blocking Skill Forge |
| **Dependencies** | None (orthogonal to Tier 2, but Skill Forge *might* need it) |
| **Audit Event** | `skill_context_adapter_wired` (audit-logged) |
| **Success Criteria** | Skill.execute() called from context pipeline; audit event logged; E2E test passes |
| **Tests** | `tests/e2e/test_l10_context_adapter_wiring.py` (real skill invocation) |
| **ADR** | None (wiring, not architectural decision) — or inline in ADR-0532 (os-skills) if new |
| **Handoff Condition** | Pull request merged, E2E tests green, audit trail verified |
| **Fallback** | If blocking unclear, defer to Tier 4 (polish); Tier 2 can proceed without it |

**Decision Logic (Session 2, Day 1):**
```
if SkillForgeV2Design requires ContextAdapter:
  Priority = HIGH  (include in Tier 1)
  Urgency = NOW
else:
  Priority = MEDIUM  (defer to Tier 4)
  Urgency = can wait
```

**If HIGH: Execution Steps**
1. Review ADR-0532 Phase 1 (os-skills) context-adapter spec
2. Implement Skill wiring: `core/skills/os_skills/context_adapter.py`
3. Wire into CEL pipeline: `core/console/corvin_console/context_engineering/...`
4. Write E2E test: `tests/e2e/test_l10_context_adapter_wiring.py`
5. Run: `pytest tests/e2e/test_l10_context_adapter_wiring.py -v`
6. Verify audit event in `~/.corvin/audit.jsonl`
7. Commit: `git commit -m "feat(l10): wire context_adapter Skill [skill-wiring] [HANDOFF tier-1-blocker-2]"`

---

### 1.2 Blocker 3: Corvin-Keys Secret Rotation (PARALLEL)

| Attribute | Value |
|---|---|
| **Status** | REQUIRED (GDPR Art. 32) |
| **Scope** | Rotate secret-encryption keys; update all audit chain + persisted secrets |
| **Effort** | 2–3h |
| **Dependencies** | None (separate codebase: `corvin_keys/`) |
| **Parallel** | YES — can run simultaneously with Blocker 2 (no shared files) |
| **Audit Event** | `secret_rotation_completed` (audit-logged with hash of new keyring) |
| **Success Criteria** | Rotation script ran; all secrets re-encrypted; audit chain verified; no stale secrets on disk |
| **Tests** | `tests/security/test_secret_rotation_no_stale_keys.py` (verify all re-encrypted) |
| **ADR** | Check ADR-0537 (audit-event-schema) if rotation events need ADR update; likely not (config change) |
| **Handoff Condition** | Rotation script merged, security tests green, audit trail shows rotation event |
| **Fallback** | If rotation incomplete, can defer to Session 3 (not on critical path for other tiers) |

**Execution Steps (parallel to Blocker 2):**
1. Review BLOCKER_3_ROTATION_PLAN.md (existing documentation)
2. Implement rotation script: `scripts/rotate_corvin_keys.py`
3. Write security test: `tests/security/test_secret_rotation_*.py`
4. Run: `pytest tests/security/test_secret_rotation_*.py -v`
5. Verify audit event logged
6. Commit: `git commit -m "security(keys): rotate encryption keys [skip-adr-check] [HANDOFF tier-1-blocker-3]"`

**Parallel Execution (Same Session, Different Agent?):**
- Blocker 2 can run in Session 2 main thread
- Blocker 3 spawns as async task OR runs in Session 2 in parallel (if time)
- Both should finish before Tier 2 starts (but not strictly required if Blocker 3 is deferrable)

---

### Tier 1 Handoff Condition

**Done when:**
- [ ] Blocker 2 is either (a) merged + tested, or (b) deferred with documented reason
- [ ] Blocker 3 is merged + tested
- [ ] Git commits tagged with `[HANDOFF tier-1-complete]`
- [ ] Memory.md updated: "Tier 1 DONE, Tier 2 starting"
- [ ] ADR-0688 `commits:` field updated with Tier 1 merge commit hashes

**Next Tier:** Tier 2 (Foundation) can start immediately after Tier 1 handoff

---

## TIER 2: Foundation Features (Sessions 3–5, Est. 4–5 Sessions)

### 2.1 Skill Forge v2.0

| Attribute | Value |
|---|---|
| **Design Status** | ✅ Complete (ADR-0262/0263, 159 tests, E2E verified) |
| **Effort** | 2–3 sessions (Impl + Code-Review + merge) |
| **Dependencies** | None (can start immediately) |
| **Parallel** | Can run alongside DataHub (separate codebases) |
| **Audit Event** | `skill_forge_deployed` (initial deploy + version tag) |
| **Success Criteria** | Code merged, tests 170+ green, `corvin plugin new --ideas` works E2E |
| **Tests** | Existing: 159 tests; add: E2E integration with plugin system |
| **ADR** | ADR-0262/0263 (already written, just set status:ACCEPTED) |
| **Handoff Condition** | PR merged, ADR.status:ACCEPTED, tests green, audit event logged |

**Execution Steps:**
1. Commit existing code (was in working tree, not committed): `git add core/plugins/plugin_builder/`
2. Run full test suite: `pytest tests/unit/test_plugin_builder_*.py tests/integration/ -v` (expect 159 green)
3. Code review (ultra, if needed)
4. Merge to main
5. Set ADR-0262/0263 `status: ACCEPTED`
6. Verify task_registry.json picks it up (next daily sync OR manual run)

---

### 2.2 DataHub + Creator

| Attribute | Value |
|---|---|
| **Design Status** | ✅ Complete (datahub_creator_learning_concept_2026_09_10.md, 12 phases) |
| **Effort** | 2–3 sessions |
| **Dependencies** | None (can start in parallel with Skill Forge) |
| **Parallel** | YES — separate codebase |
| **Audit Event** | `datahub_initialized` (first data source created) |
| **Success Criteria** | Backend routes live, creator UI works, learning events emitted |
| **Tests** | E2E: create data source → ingest → query; verify learning events logged |
| **ADR** | Write new ADR-0689+ (DataHub architecture) OR add to existing Skill-Forge ADR |
| **Handoff Condition** | PR merged, tests green, DataHub discoverable in API |

---

### 2.3 Learning Loop Closure (ADR-0613 Integration)

| Attribute | Value |
|---|---|
| **Status** | READY (ADR-0613 already written; wiring just needs verification) |
| **Effort** | 1 session (verify + test existing code) |
| **Dependencies** | None; independent verification step |
| **Parallel** | Can run anytime (just verification) |
| **Audit Event** | `learning_loop_closed_verified` (confirm outcome_sink receives events) |
| **Success Criteria** | outcome_sink receives feedback events from real Skill execution; audit trail shows closed loop |
| **Tests** | `tests/e2e/test_learning_loop_closure.py` (real Skill → feedback → audit trail) |
| **ADR** | ADR-0613 (already exists; just verify implementation) |
| **Handoff Condition** | E2E test merged + green, outcome_sink confirmed receiving events |

**Why this is important:** ADR-0613 describes shadow-mode routing, but the outcome sink wiring needs live verification. If feedback loop doesn't work, Tier 3+ learning-dependent initiatives (Model Selection, optimization) are broken.

---

### 2.4 DoD Verifier Skill 2.0 (Fast Track)

| Attribute | Value |
|---|---|
| **Design Status** | ✅ Complete (definition_of_done_as_loss_2026_09_14.md) |
| **Effort** | 1 session (fastest of Tier 2) |
| **Dependencies** | None |
| **Parallel** | YES |
| **Audit Event** | `dod_verifier_initialized` (skill loads) |
| **Success Criteria** | Skill loads, 5 checks run, numeric score computed, audit event logged |
| **Tests** | Existing (from design); just merge + verify |
| **ADR** | Write ADR-0690+ (DoD Verifier as Skill) |
| **Handoff Condition** | PR merged, tests green, skill callable via /dod-verify |

---

### Tier 2 Handoff Condition

**Done when:**
- [ ] All 4 initiatives merged (Skill Forge, DataHub, Learning Loop, DoD Verifier)
- [ ] All tests green (170+ for Skill Forge, + new tests for others)
- [ ] ADR status updated: all Tier 2 ADRs set to ACCEPTED
- [ ] Learning loop verified working (outcome_sink receives feedback)
- [ ] Git commit tagged `[HANDOFF tier-2-complete]`
- [ ] Memory.md: "Tier 2 DONE, Marketplace phase starting"

**Next Tier:** Tier 3 can start immediately after

---

## TIER 3: Marketplace + Licensing (Sessions 6–8, Est. 3–4 Sessions)

### 3.1 Marketplace Hub

| Attribute | Value |
|---|---|
| **Design Status** | ✅ Complete (marketplace_hub_concept_adr_2026_09_11.md) |
| **Effort** | 1–2 sessions |
| **Dependencies** | None (independent) |
| **Parallel** | YES — can run with Licensing in parallel |
| **Audit Event** | `marketplace_hub_initialized` (first plugin discoverable) |
| **Success Criteria** | `/v1/marketplace/plugins/` returns full index; console shows marketplace panel |
| **Tests** | E2E: discover plugin → install → load |
| **ADR** | Write ADR-0691+ (Marketplace Hub) |
| **Plugin Status** | Use plugin.json, not ADR status (ADR-0516 exception) |
| **Handoff Condition** | PR merged, marketplace API live, console UI working |

---

### 3.2 Licensing 1.0.0 (Complex, ~1–1.5 Sessions)

| Attribute | Value |
|---|---|
| **Design Status** | ✅ Complete (licensing_1_0_0_audit_2026_09_13.md) |
| **Effort** | 1–1.5 sessions (9 rounds of red-teaming already done; just cleanup + merge) |
| **Dependencies** | None (existing red-team fixes can merge independently) |
| **Parallel** | YES — can run with Marketplace Hub |
| **Audit Event** | `licensing_gates_enforced` (computed when license checked) |
| **Success Criteria** | All 3 metering axes (compute_units, chat_turns, engines_allowed) gated at all entry points |
| **Tests** | Existing: license-redteam tests (rounds 1–9); add: final integration test |
| **ADRs** | ADR-0700–0704 (Licensing 1.0.0 suite, already written) |
| **Handoff Condition** | ADR-0700+ status:ACCEPTED, license-metering.md up-to-date, all tests green |

---

### 3.3 Marketplace Plugins (4 Plugins, ~1 Session Shared)

| Attribute | Value |
|---|---|
| **Scope** | Deliver 4 key plugins (if Skill Forge needs them) |
| **Effort** | Shared 1 session (plugin development parallelizable via CI) |
| **Dependencies** | Marketplace Hub (3.1 must be done first) |
| **Parallel** | Each plugin can develop in parallel |
| **Audit Event** | `plugin_deployed` (per plugin) |
| **Success Criteria** | Each plugin installable, loads in marketplace, audit events logged |
| **Tests** | Per-plugin E2E tests |
| **Status** | Use plugin.json (no ADRs for marketplace plugins) |
| **Handoff Condition** | All 4 plugins merged, installable, discoverable |

---

### 3.4 OTEL Telemetry Multi-Tenant (1 Session)

| Attribute | Value |
|---|---|
| **Design Status** | ✅ Complete (otel_telemetry_multi_tenant_learning_2026_09_12.md) |
| **Effort** | 1 session |
| **Dependencies** | None (independent integration) |
| **Parallel** | YES |
| **Audit Event** | `otel_instrumentation_active` (when traces start flowing) |
| **Success Criteria** | Traces collected, multi-tenant isolation verified, dashboard populated |
| **Tests** | E2E: generate trace → verify in OTEL backend |
| **ADR** | Write ADR-0692+ (OTEL integration) |
| **Handoff Condition** | PR merged, traces flowing, tenant isolation verified |

---

### Tier 3 Handoff Condition

**Done when:**
- [ ] Marketplace Hub live (3.1)
- [ ] Licensing gates all working (3.2)
- [ ] 4 Marketplace plugins deployed (3.3)
- [ ] OTEL telemetry active (3.4)
- [ ] All Licensing ADRs (0700–0704) set to status:ACCEPTED
- [ ] Git commit tagged `[HANDOFF tier-3-complete]`
- [ ] Memory.md: "Tier 3 DONE, Console integration phase starting"

**Next Tier:** Tier 4 starts immediately

---

## TIER 4: Integration + Polish (Sessions 9–11, Est. 2–3 Sessions)

### 4.1 Model Selection Skill

| Attribute | Value |
|---|---|
| **Design Status** | ✅ Complete (model_selection_skill_2026_09_10.md) |
| **Effort** | 1–1.5 sessions |
| **Dependencies** | Tier 2 (Learning Loop must be working) + Tier 3 (Licensing for model tiers) |
| **Parallel** | Starts after Tier 3 |
| **Audit Event** | `model_selection_completed` (when model chosen) |
| **Success Criteria** | Skill selects correct model based on task + learning feedback |
| **Tests** | E2E: different task types → verify correct model selected + audit event |
| **ADR** | Write ADR-0693+ (Model Selection Skill) |
| **Handoff Condition** | PR merged, tests green, model selection working in real requests |

---

### 4.2 Console Wiring (Web Surface + Capabilities)

| Attribute | Value |
|---|---|
| **Design Status** | ✅ Complete (from ADR-0352–0365, console-plugin-roadmap) |
| **Effort** | 0.5–1 session (mostly verification + final polish) |
| **Dependencies** | None (console is already done, just wiring) |
| **Parallel** | Can run with Model Selection |
| **Audit Event** | `console_web_surface_loaded` (when console initializes) |
| **Success Criteria** | Console loads, all panels render, no 404s, capabilities gated correctly |
| **Tests** | E2E: start console → verify all panels load |
| **ADR** | ADR-0352–0365 (already complete; just set to ACCEPTED if not) |
| **Handoff Condition** | Console verified working, no missing panels, audit trail clean |

---

### 4.3 Video Producer 2.0

| Attribute | Value |
|---|---|
| **Design Status** | ✅ Complete (video_producer_skill_2_0_2026_09_12.md) |
| **Effort** | 1 session |
| **Dependencies** | Tier 2 (Learning Loop) + Tier 4.1 (Model Selection, for orchestration) |
| **Parallel** | Starts after Tier 3, can run with Model Selection + Console |
| **Audit Event** | `video_produced` (when video render completes) |
| **Success Criteria** | Video orchestration works, learning feedback integrated, audit trail shows all steps |
| **Tests** | E2E: produce video → verify output + audit trail |
| **ADR** | Write ADR-0694+ (Video Producer 2.0) |
| **Handoff Condition** | PR merged, video production working E2E, audit trail complete |

---

### 4.4 End-to-End Integration Tests

| Attribute | Value |
|---|---|
| **Scope** | Cross-initiative tests proving all tiers work together |
| **Effort** | 1 session (last step) |
| **Dependencies** | All of Tier 4 (4.1–4.3) |
| **Parallel** | Runs after all others |
| **Audit Event** | `integration_tests_passed` (when full E2E suite completes) |
| **Success Criteria** | Create request → Skill Forge → Model Selection → Console → Video Producer → Marketplace plugin → License check → OTEL trace → Learning feedback → all audit events logged + immutable |
| **Tests** | `tests/e2e/test_full_integration.py` (comprehensive cross-system test) |
| **ADR** | None (testing, not architectural) |
| **Handoff Condition** | Full suite green, no audit trail gaps, ready for production deploy |

---

### Tier 4 Handoff Condition

**Done when:**
- [ ] Model Selection Skill deployed (4.1)
- [ ] Console fully wired (4.2)
- [ ] Video Producer 2.0 deployed (4.3)
- [ ] Full integration tests green (4.4)
- [ ] All new Tier 4 ADRs (0693+) set to status:ACCEPTED
- [ ] Git commit tagged `[HANDOFF tier-4-complete]`
- [ ] Memory.md: "ALL TIERS COMPLETE — Ready for production deployment"

**Next Phase:** Production deployment (separate workflow, not in this Master-Plan)

---

## Cross-Tier Dependency Summary

```
Tier 1 (Blockers)
├─ Blocker 2: L10 Context [CONDITIONAL]
└─ Blocker 3: Corvin-Keys [PARALLEL]
    ↓
Tier 2 (Foundation)
├─ 2.1 Skill Forge v2.0
├─ 2.2 DataHub + Creator [PARALLEL]
├─ 2.3 Learning Loop Closure [PARALLEL]
└─ 2.4 DoD Verifier [PARALLEL]
    ↓
Tier 3 (Marketplace)
├─ 3.1 Marketplace Hub
├─ 3.2 Licensing 1.0.0 [PARALLEL]
├─ 3.3 Marketplace Plugins (4x) [PARALLEL]
└─ 3.4 OTEL Telemetry [PARALLEL]
    ↓
Tier 4 (Integration)
├─ 4.1 Model Selection [depends on Tier 2.3 + Tier 3.2]
├─ 4.2 Console Wiring [PARALLEL]
├─ 4.3 Video Producer [PARALLEL]
└─ 4.4 E2E Tests [depends on 4.1–4.3]
```

---

## Session Capacity & Cadence

| Tier | Initiatives | Est. Sessions | Real Time | Cadence |
|---|---|---|---|---|
| **Tier 1** | 2 (Blockers) | 1–2 | 2–4 hours | Daily |
| **Tier 2** | 4 (Foundation) | 4–5 | 1–1.5 weeks | 2x/week |
| **Tier 3** | 4 (Marketplace) | 3–4 | 1 week | 2x/week |
| **Tier 4** | 4 (Integration) | 2–3 | 1 week | 2x/week |
| **TOTAL** | 18 | 10–14 | 4–6 weeks | — |

---

## Success Metrics (How to Know You're Done)

| Metric | Target | Verification |
|---|---|---|
| **All initiatives merged** | 18/18 | `git log --oneline main` shows 18 [HANDOFF] commits |
| **Tests green** | 500+ passing | `pytest tests/ -v --tb=short` (all green) |
| **All Tier ADRs ACCEPTED** | 18+ ADRs | `grep "status: accepted" Corvin-ADR/decisions/*.md` (18+) |
| **Task Completion Registry updated** | All tasks marked ACCEPTED | `jq '.tasks | to_entries | map(select(.value.status == "ACCEPTED")) | length' task_registry.json` (≥18) |
| **Audit trail complete** | No gaps in hash chain | `corvin audit verify-chain --since=tier-1-start` (0 gaps) |
| **No broken tests from prior work** | All green, no flakes | `pytest tests/ --tb=short` (no xfails, no timeouts) |
| **Integration E2E green** | 4.4 passes | `pytest tests/e2e/test_full_integration.py -v` (all pass) |

---

## Notes for Maintainers

- **Conditional Blocker 2:** Decide scope Day 1 of Session 2. If unclear, defer to Tier 4.
- **Agent Spawning:** Blocker 3 can run as a parallel Agent. If it hangs, Session 3 handles it (not critical path).
- **Code Review Ultra:** Trigger early in each tier (tests green → async review). Don't wait for all coding to finish.
- **Rollback Plan:** If a Tier fails (tests red, merge conflict), revert and re-plan next session. Don't block on single initiative.
- **Audit Trail:** Every [HANDOFF] commit should have a matching audit event. If missing, manually log it: `corvin audit write-event --event initiative_handoff --tier X --initiative Y`.

---

**Document Last Updated:** 2026-09-16  
**Master ADR:** ADR-0688  
**Status:** Active (ready to execute)
