# Wave 2 Concurrent Planning (2026-09-26 → 2026-09-27 EOD)

**Coordinator:** Claude  
**Execution:** While Streams B/C/D run Wave 1, plan Wave 2 (Tage 4–6)  
**Status:** 🔄 PLANNING (design phase, no execution until Wave 1 complete)

---

## Wave 2 Overview (Tag 4–6)

**Dependency:** ✅ T05 complete → T06 can start  
**Parallel Streams:** A (T06), B (T10 continuation), C (T17), D (T03)

| Stream | Task | Duration | Blocker | EOD |
|--------|------|----------|---------|-----|
| A | T06: Retroaktive ADRs | 2 days | T05 ✓ | 2026-09-27 |
| B | T10: Learning k=6 | 5 days | T09 (phase 2b) | 2026-09-30 (extends) |
| C | T17: Flag→Plugin | 5 days | – (parallel start) | 2026-09-30 (extends) |
| D | T03: Phase-3a–c | 2 days | Dialektik gate | 2026-09-27 |

**Critical Path (Wave 2 only):** T05 (done) → T06 (2d) → [Wave 3]  
**Puffer:** T10, T17 extend into Wave 3 (acceptable; not blocking Wave 3)

---

## Stream A: T06 Retroactive ADRs (2 days)

### Tasks (Wave 2, Day 1–2)

**Problem:** 3 commits reference wrong ADRs (Voice-Persistence, STT, ML-Retraining).  
**Solution:** Create retroactive ADRs, update commit references.

**Commits to address:**
1. `98b89bee5` — feat(phase-3a): "Persistent Database Abstraction Layer" → cites ADR-0275 (wrong: Vibe Context Surface)
2. `4687d09c4` — feat(phase-3b-3d): "Google Cloud STT + Performance" → cites ADR-0400 (wrong: Skill-Creator Frontend)
3. `fc9ee19f5` — feat(phase-3c): "ML Feedback Loop + Model Retraining" → cites ADR-0314 (overloaded reference)

**Action:**
1. Create new ADRs:
   - `ADR-0920`: Voice Session Persistence (Phase 3a)
   - `ADR-0921`: Google Cloud STT Integration (Phase 3b-3d) + L35/L34/L23 Compliance
   - `ADR-0922`: ML Feedback Loop & Model Retraining (Phase 3c)

2. Update commits:
   ```bash
   git rebase -i <base> # Interactive rebase to amend commit messages
   # Change ADR-0275 → ADR-0920, ADR-0400 → ADR-0921, ADR-0314 (append ADR-0922)
   ```

3. Validate:
   ```bash
   git log --oneline | grep "ADR-0920\|ADR-0921\|ADR-0922"
   # Must show 3 commits with new ADR IDs
   ```

**Deliverables:**
- [ ] ADR-0920 (Voice-Persistence) written + PROPOSED
- [ ] ADR-0921 (Google STT) written + PROPOSED + compliance review (L35/L34/L23)
- [ ] ADR-0922 (ML-Retraining) written + PROPOSED
- [ ] Commit messages updated (3 commits now reference correct ADRs)
- [ ] Cross-references validated (grep "ADR-0920\|0921\|0922" in code)

**Estimated Effort:** 2 days  
**E2E Proof:** Commits reference correct ADRs; ADRs exist in Corvin-ADR with status PROPOSED

---

## Stream B: T10 Learning k=6 (5 days, extends to Wave 3)

### Tasks (Wave 2 Day 1–3, Wave 3 Day 1–2)

**Dependency:** T09 Phase 2b (all 4 events emitted) must complete first

**Problem:** Learning loop stuck at k=5 (manifest discovery, audit integration). k=6 requires plugin events.

**What:** Make plugins emit real Learning Events (ADR-0314).

**Scope:**
1. Plugin lifecycle events (init, execute, error, disable)
2. Learning event payload (plugin_id, version, input, output, latency, feedback)
3. Audit chain integration (emit + hash-chain verify)
4. Confidence scoring (plugin is reliable/unreliable based on events)

**Blockers:**
- ✅ T09 must emit phase 2b events (compute, a2a, plugin.timeout) — needed for plugin health scoring
- ADR-0314 must define plugin event schema (verify it exists)
- Plugin registry must be reachable (ADR-0233 enforcement)

**Deliverables:**
- [ ] Plugin event schema defined (ADR-0314 appendix or new ADR-0923)
- [ ] Emit calls wired in plugin lifecycle (init, execute, error, terminate)
- [ ] Confidence scoring logic implemented (simple: error_count/total_runs)
- [ ] Dashboard updated (Vibe Learning panel shows plugin confidence)
- [ ] E2E test: plugin executes → event emitted → confidence calculated

**Estimated Effort:** 5 days (split: 3 days Wave 2, 2 days Wave 3)  
**EOD Wave 2:** Plugin events emitting + confidence scoring logic complete  
**EOD Wave 3:** Dashboard shows real plugin confidence scores

---

## Stream C: T17 Flag→Plugin Migration (5 days, extends to Wave 3)

### Tasks (Wave 2 Day 1–3, Wave 3 Day 1–2)

**Problem:** Feature flags still control visibility (old model). Plugins should be the control unit.

**What:** Migrate all feature flags → plugin enable/disable in registry.

**Scope:**
1. Inventory all feature flags (grep `spec.features.*` in tenant config)
2. For each flag:
   - Create plugin or enhance existing plugin to gate the feature
   - Wire flag value into plugin config
   - Remove flag from code
3. Test: feature works via plugin disable/enable (not via flag)

**Flags likely affected (estimate):**
- ~15–20 feature flags across Settings, Console, Voice, etc.

**Deliverables:**
- [ ] Feature flag inventory complete (doc list of all flags)
- [ ] 50% of flags migrated (Day 1–2 Wave 2)
- [ ] Remaining 50% migrated (Day 1–2 Wave 3)
- [ ] All tests pass (feature still works via plugin, not flag)
- [ ] CLAUDE.md updated (no more feature-flag guidance)

**Estimated Effort:** 5 days (split: 3 days Wave 2, 2 days Wave 3)  
**EOD Wave 2:** Core flags (web-chat, voice, learning) migrated  
**EOD Wave 3:** All flags migrated, old flag code deleted

---

## Stream D: T03 Phase-3a–c Verdrahten oder Löschen (2 days)

### Dialektische Entscheidung (Wave 2, Day 1, 0.5 days)

**Problem:** Phase 3a–c code exists (voice_session_store, ml_feedback_pipeline, performance_monitor) but has zero production callers.

**Decision Gate:** Verdrahten oder löschen?

**Option A: Verdrahten** (keep code, wire into production)
- Effort: ~3–4 additional days
- Payoff: Voice persistence + ML feedback working end-to-end
- Risk: May break existing voice sessions if not careful
- Timeline: Extends Wave 3 + Wave 4

**Option B: Löschen** (remove dead code)
- Effort: 1 day (grep callers, delete, test)
- Payoff: Cleaner codebase, lower maintenance
- Risk: Architectural plans for k=6 may depend on this code
- Timeline: Completes Wave 2

### Decision Process

1. **Stakeholder Alignment (Day 1, morning):**
   - Check if Phase 3a–c features are in the Roadmap
   - Verify no dependencies from Wave 3+ (grep ADR references)
   - Get sign-off on decision

2. **If Verdrahten:**
   - Create ADR-0923: Phase 3a–c Reactivation (voice persistence + ML feedback)
   - Wire voice_session_store into console session lifecycle
   - Wire ml_feedback_pipeline into Learning loop (k=6+)
   - E2E test: real voice session persists + ML model trains on feedback

3. **If Löschen:**
   - Grep for all references to the 3 modules
   - Delete with git rm (preserve in git history)
   - Update CLAUDE.md: note phase 3a–c deferred
   - Commit: `refactor(phase3): T03 Defer voice persistence + ML feedback [decision-doc-link]`

**Wave 2 Deliverable:**
- [ ] Decision documented (verdrahten or löschen)
- [ ] If verdrahten: ADR-0923 + implementation started (1–2 days)
- [ ] If löschen: Code removed + tests updated (1 day)

**Estimated Effort:** 2 days (0.5 days decision + 1.5 days execution)

---

## Risk Matrix (Wave 2)

| Risk | Probability | Impact | Mitigation | Owner |
|------|-------------|--------|-----------|-------|
| **R1:** T06 retroactive ADRs become too detailed (scope creep) | MEDIUM | HIGH | Time-box each ADR to 3 hours; use ADR template | T06 owner |
| **R2:** T10 plugin events conflict with existing plugin lifecycle | MEDIUM | MEDIUM | Review ADR-0233 plugin contract; test with real plugin | T10 owner |
| **R3:** T17 feature flags have hidden dependencies (break on removal) | HIGH | HIGH | Grep exhaustively before deletion; integration test each flag | T17 owner |
| **R4:** T03 phase-3a–c code has security implications (sessions, model data) | LOW | CRITICAL | If verdrahten: security review required; if löschen: archive code for audit | T03 owner |
| **R5:** Wave 1 Streams B/C/D run over schedule (delay T06 start) | MEDIUM | MEDIUM | Start T06 as soon as T05 done; don't wait for full Wave 1 EOD | Orchestrator |
| **R6:** Learning k=6 design conflicts with existing k=1-5 schema | LOW | HIGH | Validate schema alignment with ADR-0314 BEFORE coding | T10 owner |

---

## Wave 2 Timeline (Theoretical, assumes Wave 1 EOD on schedule)

```
2026-09-27 (Wave 1 EOD):
  ✅ Streams B/C/D complete (T09, T04, T12+T16)
  ✅ T05 done (ADR dedup)
  → T06 ready to start (no blockers)

2026-09-27 (Wave 1 EOD → Wave 2 Start):
  🔄 T06 starts (2 days) → target 2026-09-28 EOD
  🔄 T03 decision gate (0.5 days, morning 2026-09-27) → action path chosen
  🔄 T10, T17 start (if T09 complete; else wait 1 day)

2026-09-28 (Wave 2 Day 2):
  🔄 T06 continuation or complete
  🔄 T10 (plugin events wiring started)
  🔄 T17 (flag inventory + first batch migration)
  🔄 T03 (execution: verdrahten OR delete)

2026-09-29 (Wave 2 Day 3):
  ✅ T06 COMPLETE (retroactive ADRs + commit refs updated)
  🔄 T10 (plugin events emitting, confidence scoring logic)
  🔄 T17 (50% of flags migrated)
  ✅ T03 COMPLETE (if löschen; if verdrahten → extends to Wave 3)

Wave 2 EOD (2026-09-29):
  ✅ T06 done
  ✅ T03 done (or defer verdrahten to Wave 3)
  🔄 T10, T17 continue into Wave 3 (5-day tasks)
```

---

## Wave 2 Success Criteria

- ✅ All retroactive ADRs written + commit refs updated (T06)
- ✅ Phase-3a–c decision made + executed start (T03)
- ✅ Learning k=6 plugin events emitting + confidence calculating (T10)
- ✅ 50%+ of feature flags migrated to plugins (T17)
- ✅ Zero regression in Wave 1 deliverables (T09/T04/T12/T16 hold)
- ✅ Daily standup: async posts from T06/T03 owners (T10/T17 optional if no blockers)

---

**Wave 2 Orchestrator:** Claude  
**Start Date:** 2026-09-27 (when Wave 1 complete)  
**Next Review:** 2026-09-28 EOD (Wave 2 Day 2 sync)

