# BIG BANG FEATURE FLAGS REWRITE — STATUS REPORT
**Date:** Sept 2, 2026 | **Time:** Post-Blocker Submission | **Status:** 🟡 **CRITICAL BLOCKERS PENDING**

---

## EXECUTIVE SUMMARY

**Project:** Feature Flags → Skills API Migration (Phase 1 Big Bang)  
**Current Phase:** SPIKE 1 Scoping (Blocker Resolution)  
**Blocker Status:** 4 critical architectural decisions **UNANSWERED** (Deadline: Sept 3 10:00 UTC)  
**Action:** All coding blocked until Architecture Lead responds to ADR-0544 Amendment Request  
**Risk Level:** 🔴 **HIGH** — escalation required if blockers remain unanswered

---

## SPIKE 1 OVERVIEW

### Objective
Rewrite `feature_flags.py` (1550 lines) from the monolithic feature flag system to the Skills API in ≤10 hours. Results will extrapolate to a full big-bang refactoring of 88 call-sites across the codebase (Phase 1b, Weeks 1–10).

### Deliverables (Target: Sept 4 EOD)
1. ✅ Scoped 4 blocking questions → **SUBMITTED in ADR-0544 Amendment Request**
2. ⬜ Blocker resolution from Architecture Lead (Sept 3 06:00–10:00 UTC)
3. ⬜ Spike 1 rewrite (feature_flags.py → Skills-based) (Sept 3 10:00 → Sept 4 ~20:00 UTC)
4. ⬜ Final report + velocity data (Sept 4 EOD)

---

## 4 CRITICAL BLOCKERS (AWAITING ANSWERS)

### **BLOCKER #1: Flag-to-Skill Mapping**
**Question:** How do 60 registered feature flags map to Skills?

| Option | Approach | Pros | Cons | Effort Impact |
|--------|----------|------|------|---------------|
| **1a** | 60 Skills (1:1) | Simple mapping | 60 manifests, versioning overhead | +2h design |
| **1b** | 10–15 composite Skills | Simpler structure | Loss of granularity | –1h design |
| **1c** | Dual system (flags + Skills) | Minimal refactor | Tech debt | ±0h |

**Submission:** `docs/SPIKE_1_ADR_0544_AMENDMENT_REQUEST.md` (Lines 13–46)

---

### **BLOCKER #2: Architecture Choice (Big Bang vs. Wrapper)**
**Question:** Phase 1b scope — refactor all 88 call-sites now (Big Bang) or use compatibility wrapper?

| Option | Approach | Timeline | Pros | Cons | Phase 1b Hours |
|--------|----------|----------|------|------|---|
| **2a: Big Bang** | All 88 files refactored in parallel | 10 weeks | Clean, single audit | High risk, 176h+ | ~176h |
| **2b: Wrapper+Phased** | Wrapper delegates to Skill; migrate gradually | Phase 1b gradual, Phase 2 cleanup | Spike 1 fast (4–6h), Phase 1b on-track | 2–3 mo. tech debt | ~30h Phase 1b + wrapper |

**Submission:** `docs/SPIKE_1_ADR_0544_AMENDMENT_REQUEST.md` (Lines 50–82)

**Impact on Spike 1 Velocity:**
- If Big Bang: +4h call-site analysis phase
- If Wrapper: –2h (focus on feature_flags.py only)

---

### **BLOCKER #3: Worker Engine Mode (Skill or Legacy?)**
**Question:** Is `worker_engine_mode()` a Skill parameter or remain separate config?

| Option | Approach | Impact on Spike 1 | Impact on Phase 1b | Audit Trail |
|--------|----------|---|---|---|
| **3a: Skill Parameter** | New Skill `os.worker_engine_selection` | +1h | Unified audit | ✅ Single trail |
| **3b: Legacy Config** | Keep `worker_engine_mode()` separate | 0h | Dual audit paths | ⚠️ Split trails |

**Submission:** `docs/SPIKE_1_ADR_0544_AMENDMENT_REQUEST.md` (Lines 86–123)

---

### **BLOCKER #4: Tier Management (Keep or Drop?)**
**Question:** Do ADR-0286/0288 tier management features (alpha/beta/stable/production) continue in Skills?

| Option | Approach | Scope Impact | Operator UX | Complexity |
|--------|----------|---|---|---|
| **4a: Keep Tiers** | Each Skill has `release_tier` + auto-promotion daemon | +1h Spike 1, Phase 1b integration | ✅ Full observability | ⚠️ Promotion daemon rewrite |
| **4b: Drop Tiers** | Tiers are legacy-only; Skills launch stable | 0h Spike 1 | ❌ Loses maturity visibility | ✅ Simpler |

**Submission:** `docs/SPIKE_1_ADR_0544_AMENDMENT_REQUEST.md` (Lines 125–160)

---

## TIMELINE & ESCALATION

### Sept 2 (TODAY)
- ✅ **16:30 UTC:** 4 blockers scoped and submitted in ADR-0544 Amendment Request
- ✅ **16:30 UTC:** Velocity Tracking document created
- ⬜ **EOD:** Awaiting acknowledgment from Architecture Lead

**Checkpoint:** All blockers formatted and clearly stated. Ready for async response.

### Sept 3
- **Targeted: 06:00–10:00 UTC** — Blocker Resolution Window
  - All 4 answers expected from Architecture Lead
  - If ANY unanswered by 10:00 UTC → **ESCALATE** (HARD deadline)
  
- **Targeted: 10:00 → Sept 4 ~20:00 UTC** — Code Execution Window
  - 7 rewrite tasks (manifest, wrapper, storage, audit, validation, testing, docs)
  - Real-time tracking every 30–60 minutes
  - Escalation if cumulative >10h by Sept 4 18:00 UTC

### Sept 4
- **Morning:** Final verification (equivalence, audit, tenant isolation)
- **EOD:** Spike 1 Final Report + velocity extrapolation to Phase 1b

---

## WORK BREAKDOWN (PENDING BLOCKER RESOLUTION)

**Total Estimated Effort:** 7.5–13 hours (blocker-dependent)

| Task | Blocker Dependency | Est. Hours | Actual Hours | Status |
|------|---|---|---|---|
| **1. Skill manifest creation** | Blocker #1 | 1–2h | — | 🟡 PENDING |
| **2. Skills Registry wrapper** | Blocker #2 | 1–2h | — | 🟡 PENDING |
| **3. JSON storage layer** | Blockers #1, #2 | 1–1.5h | — | 🟡 PENDING |
| **4. Audit event injection** | Blocker #2 | 1–2h | — | 🟡 PENDING |
| **5. Tenant isolation validation** | Blocker #1 | 0.5–1h | — | 🟡 PENDING |
| **6. Testing (unit + equivalence + audit)** | All | 2–3h | — | 🟡 PENDING |
| **7. Documentation + rollout plan** | Blocker #2 | 0.5–1h | — | 🟡 PENDING |

**Total:** ~10h (on target if blockers answered by Sept 3 10:00 UTC)

---

## UNTRACKED FILES REQUIRING COMMITMENT

### Documentation (Ready to Commit)
| File | Size | Status | Purpose |
|------|------|--------|---------|
| `docs/SPIKE_1_VELOCITY_TRACKING.md` | 7.6K | ✅ Complete | Real-time progress tracking |
| `docs/SPIKE_1_ADR_0544_AMENDMENT_REQUEST.md` | 7.5K | ✅ Complete | 4 blocker questions + submission format |
| `docs/A2A_HARDWARE_SETUP_OPTION_A.md` | 12K | ⬜ Pending integration | A2A network setup guidance |
| `docs/A2A_VERIFICATION_LOG_SEPT_2_5.md` | 15K | ⬜ Pending integration | Verification data for A2A |

### Code (Pending Decisions)
| File | Size | Status | Depends On |
|------|------|--------|---|
| `operator/bridges/shared/mid_turn_heartbeat.py` | 11K | ⬜ Pending merge | Feature flag architecture |
| `tests/.../test_mid_turn_heartbeat.py` | — | ⬜ Pending merge | Mid-turn heartbeat feature |

---

## MODIFIED FILES (IN CURRENT DIFF)

### Changed
1. **core/console/corvin_core/feature_flags.py** (1550 lines)
   - Status: ✅ Staged for Spike 1 rewrite
   - Contains: 60 feature flags + utilities

2. **operator/bridges/shared/adapter.py** (100+ lines shown)
   - Status: ✅ CEL integration wired
   - Changes: Context Engineering pipeline hooks, mid-turn heartbeat support

3. **operator/bridges/profiletest/settings.json**
   - Status: 🗑️ Deleted
   - Reason: Profile test cleanup

---

## CRITICAL DEPENDENCIES

### On Architecture Decisions
- ❌ **Cannot start Spike 1 coding** until Blockers #1–#4 are answered
- ⚠️ **Phase 1b scope & timeline** depends on Blocker #2 (Big Bang vs. Wrapper)
- ⚠️ **Audit trail design** depends on Blockers #2, #3
- ⚠️ **Tier management tooling** depends on Blocker #4

### On ADRs
- **ADR-0544** (Big Bang Feature Flags) — Amendment required (Sept 3 10:00 UTC deadline)
- **ADR-0549** (Skills Registry + A2A Connector) — Already merged (commit 2394d5ad)
- **ADR-0286/0288** (Tier management) — May be superseded by Blocker #4 decision

---

## ESCALATION RULES (DO NOT WAIT FOR SEPT 6)

| Condition | Escalate To | Action |
|-----------|---|---|
| **Any blocker unanswered by Sept 3 10:00 UTC** | Architecture Lead + Steering | **STOP work**, request immediate decision |
| **Task exceeds estimate by >50%** (e.g., Task 1 takes >3h) | Dev Lead + Steering | Analyze root cause, adjust plan |
| **Cumulative actual >10h by Sept 4 18:00 UTC** | Dev Lead + Steering | Document why, assess impact |
| **Audit integration fails** (events don't emit) | Audit Lead + Bridge Eng | **CRITICAL**: compliance gate, cannot ship |
| **Tests fail equivalence** (old vs. new API differ) | Dev Lead + QA | **CRITICAL**: must fix before marking done |

---

## QUALITY GATES (DEFINITION OF "DONE")

Spike 1 is **COMPLETE** only when ALL gates pass:

- ✅ **API Equivalence:** Old `is_enabled(flag_id)` == New `Skill.execute()`
- ✅ **Audit Trail:** Every `is_enabled()` call emits `SKILL_EXECUTED` event
- ✅ **Tenant Isolation:** Cross-tenant queries blocked (GDPR Art. 32)
- ✅ **Unit Tests:** 100% of feature_flags.py API covered
- ✅ **E2E Tests:** Real Skills system integration verified
- ✅ **No Regressions:** Console, bridges, all surfaces unchanged

---

## NEXT ACTIONS

### TODAY (Sept 2)
1. ✅ Submit ADR-0544 Amendment Request → Done
2. ✅ Create Velocity Tracking document → Done
3. **MONITOR** for Architecture Lead acknowledgment (async)
4. **PREPARE** blocker-conditional code structure (so rewrite starts immediately on Sept 3)

### SEPT 3 (CRITICAL)
1. **06:00 UTC:** Check for blocker answers
2. **10:00 UTC:** ESCALATE if any blocker unanswered
3. **10:00 onwards:** Begin Spike 1 rewrite (code execution)
4. **Every 30–60 min:** Update Velocity Tracking with actual hours

### SEPT 4
1. **Morning:** Final verification
2. **EOD:** Spike 1 Final Report + extrapolation to Phase 1b

---

## CONTACTS

| Role | Status | Purpose |
|------|--------|---------|
| **Architecture Lead** | ⬜ Awaiting response | Blocker resolution |
| **Spike 1 Dev** | ✅ Ready to code | Execute on blocker answers |
| **Steering** | ⬜ Standby | Escalation decisions |

---

## KEY METRICS (FOR PHASE 1B EXTRAPOLATION)

**Hypothesis:** If Spike 1 (1 file, ~1550 lines) takes ≤10 hours → Phase 1b (88 files, ~137K lines) extrapolates to:
- **Linear:** ~10h × (137K / 1.55K) ≈ **880 hours** = ~22 weeks (1 person)
- **With Team:** 5 parallel tracks → ~4.4 weeks
- **Risk Multiplier:** ×1.5 for coordination overhead → ~6.6 weeks

**If Blocker #2 = Big Bang:** Phase 1b is a 10-week big-bang refactoring (high parallelization risk).  
**If Blocker #2 = Wrapper+Phased:** Phase 1b stays on gradual migration schedule (lower risk).

---

## STATUS LEGEND

- ✅ **DONE** — Complete, ready for next step
- 🟢 **IN PROGRESS** — Active work
- 🟡 **PENDING** — Awaiting decision or input
- 🔴 **BLOCKED** — Cannot proceed (escalation required)
- ⬜ **NOT STARTED** — Queued for later
- 🗑️ **DELETED** — Removed (intentional cleanup)

---

**Last Updated:** Sept 2, 2026 | 16:30 UTC  
**Next Update:** Sept 3, 06:00 UTC (blocker resolution report)  
**Document Owner:** Spike 1 Dev  
**Status:** 🔴 **CRITICAL BLOCKERS PENDING** — All coding blocked until Architecture Lead responds
