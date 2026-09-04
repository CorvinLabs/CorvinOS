# CorvinOS v0.3.0: Vibe Engineering + Cross-Device Learning Sync

**Release Date:** 2026-08-18  
**Status:** Production Ready  
**Git Tag:** `v0.3.0`

---

## Executive Summary

CorvinOS v0.3.0 brings **Glass Box Vibe Engineering** (complete operator observability of prompt assembly and context evolution) and **Cross-Device Learning Sync** (secure Git-based tenant learning state synchronization). All five Glass-Box phases (G1–G5) are complete and E2E verified. The system is now fully traceable — operators can see exactly what context entered the worker engine and what the system learned from each turn.

**Key Achievements:**
- ✅ Glass-Box Prompt Reveal (G1) — audit exactly what the worker engine received
- ✅ Vibe Overview (G2) — consolidated operator dashboard replacing redundant panels
- ✅ Operator Stage-Grading UI (G3) — close the learning feedback loop
- ✅ Turn Outcome Recording (G4) — first production caller for `record_turn_outcome`
- ✅ Cross-Device Git Sync (G5) — GPG-encrypted learning state sync across instances

**Test Coverage:** All 636 E2E tests from v0.2-rc1 passing, plus 42 new tests for G1–G5.  
**ADRs:** ADR-0368 (G1), ADR-0370 (G2), ADR-0371 (G3/G4), ADR-0369 (G5), ADR-0233 (ContextVar isolation).

---

## Major Features

### 1. Glass-Box Vibe Engineering (ADR-0368/0370/0371)

Complete, operator-friendly observability of context engineering. Answers the three critical questions:
- *What context went into the worker engine?*
- *Where did each part come from?*
- *What did the system learn?*

#### G1: Glass-Box Prompt Reveal
- **Backend:** `persist_assembly()` integrated into Console chat path (not just Bridge)
- **Frontend:** `TurnGlassBox` component — displays final prompt with:
  - **CEL-Block vs. System Prompt** split (visual separation)
  - **Sections legend** with cross-references to `/traces` records
  - **Rücklinks** from Memory, Skills, Graphs back to their origins
  - **Audit anchors** (hash-chain record, GDPR erasure status)
- **UI Location:** `pages/vibe-engineering.tsx` (erstklassige Ansicht, ≤3 clicks from turn list)
- **E2E Proof:** Real Console chat → Glass Box → assert `final_prompt` rendered (not `found:false`)

#### G2: Vibe Overview (Consolidated Dashboard)
- **Deleted:** Vibe Inspector (was redundant read-only subset of Context Pipeline)
- **New:** `vibe-overview.tsx` with:
  - **Mental-model flow diagram:** Turn → 8 CEL-Stages → Assembly → Worker Engine → Outcome → Learning
  - **Aggregate kacheln:** Turns, Sessions, Ø-Score, Degraded-Count
  - **"How to read a trace" onboarding** (embedded help)
- **Nav Integration:** Inspector gone; Overview now primary entry point
- **E2E Proof:** `/app/vibe-inspector` → 404/redirect; `/app/vibe-overview` renders diagram + aggregates

#### G3: Operator Stage-Grade UI
- **Endpoints:** `GET/POST /vibe-engineering/grades` + `POST /vibe-engineering/grades/{stage_id}`
- **Frontend:** `StageGradePanel` in stage modal (👎 -0.5 / 😐 0 / 👍 +1.0)
- **Backend:** Direct `grade_stage()` call (Production's first explicit grader)
- **Learning Ledger:** Consolidated three-part view:
  1. **Stage Confidence** (CEL-Grades)
  2. **Patterns** (TreeOfThoughts nodes, now routed)
  3. **Objectives** (ULO goals)
- **E2E Proof:** HTTP POST grade → `/vibe-engineering/grades/memory` → score 0→1 reflects in UI

#### G4: Turn Outcome Recording (Closes Learning Loop)
- **Blocker Fixed:** `record_turn_outcome` had no production caller; now wired from `stream_turn` completion
- **Signal:** Success = no error (ADR-0269 Phase-4b semantic)
- **Grading:** Automatically generates 8 advisory CEL-Grades per turn (backend grades, operator can refine with G3 UI)
- **Flag:** `outcome_feedback_loop` (default-off, but wired)
- **E2E Proof:** Stream turn with flag-on → 8 advisory grades appear; flag-off → 0 grades

#### G5: Cross-Device Learning Sync (ADR-0369)
- **Transport:** Git-based state sync (Candidate A from architectural decision)
- **What syncs:** Skills, Grades, Learning-Events, Memory, Panels
- **Merge Strategy:** Type-specific (not git-merge):
  - **Learning-Events** (JSONL) = union + sort
  - **Grades** = array union (maintains full grade history per stage)
  - **Skills/Memory** = last-write-wins (mtime) with collision report
- **Security:**
  - **Opt-in:** default-off via `cross_device_sync` feature flag
  - **Encryption:** GPG-mandatory (Tenant-local key, no auto-push to GitHub without consent)
  - **Auth:** Session token + CSRF gate
  - **Tenant-isolation:** All queries filtered by `tenant_id`
  - **PII Backstop:** `_assert_no_raw_pii()` before transmit (best-effort)
- **Endpoint:** `POST /sync` — trigger manual sync or scheduled (scheduler in v0.3.1)
- **Live Git Transport:** 6/6 unit tests pass; HTTP E2E verified (flag-on POST /sync → "synced", Remote receives encrypted blob)
- **Offen (v0.3.1):** Conflict resolution UI (for LWW collisions), live scheduler, richer success signals

---

### 2. ContextVar Isolation Spike (ADR-0233)

Prevent plugin boot-layer escalation via thread escape.

**Problem:** A thread spawned in `on_load()` could inherit the `_loading` ContextVar and attempt re-registration with elevated `boot_layer`.

**Fix:** Serialize compliance grant recording *before* `_register_instance()`, locking it in the current epoch before threads spawn.

**Changes:**
- `core/plugins/corvin_plugins/bootstrap.py` — grant serialization order
- `core/plugins/corvin_plugins/registry.py` — thread-safe epoch tracking

**Tests:** Plugin loading with concurrent `on_load()` threads — no escalation.

---

### 3. YAML Persistence for Feature Status (Tier 1)

Feature flags now persist via YAML, enabling:
- Tenant-scoped preset definitions
- Installer integration
- Feature graduation tracking

**Scope:** Feature-tier metadata stored in `tenant.corvin.yaml` under `spec.features`

**Note:** Full "Memplace" task-template/error-pattern YAML storage deferred to v0.3.1.

---

## Breaking Changes

**None.** v0.3.0 is fully backward-compatible with v0.2-rc1.

---

## Deprecations

- **Vibe Inspector** (external panel) removed. Functionality absorbed into Vibe Overview.
- **`group:"observability"` metadata** in panel registry cleaned up.

---

## Performance Metrics

| Metric | v0.2-rc1 | v0.3.0 | Change |
|--------|----------|--------|--------|
| Context query latency | <1 μs | <1 μs | — |
| Prompt persist latency (Glass Box) | N/A | <50ms | +50ms added to turn flow |
| Grade submission latency | N/A | <100ms | +100ms operator interaction |
| Git sync latency (G5) | N/A | <2000ms | new feature |
| Total E2E tests passing | 636 | 678 | +42 (G1–G5 coverage) |

**Impact:** Negligible (<50ms per turn for Glass Box audit trail; batched with existing audit writes).

---

## Security & Compliance

### GDPR Compliance
- ✅ Glass-Box persists only audit-chain-anchored data (no new PII exposure)
- ✅ Cross-Device Sync uses GPG encryption (GDPR Art. 32)
- ✅ Tenant isolation enforced on all Sync queries
- ✅ `_assert_no_raw_pii()` backstop (best-effort) before Sync transmit

### Audit Trail Integration
- ✅ All Glass-Box operations hash-chained
- ✅ Grade submissions logged as audit events
- ✅ Sync operations include operation ID for forensics

---

## Known Limitations (Deferred to v0.3.1)

1. **File Organization Refactoring** — directory consolidation, utils cleanup (no functional impact)
2. **Memplace Full Template Storage** — task templates and error patterns will use YAML in v0.3.1
3. **G5 Conflict Resolution UI** — manual LWW collision reports only; UI for operator to pick winner deferred
4. **G5 Automatic Scheduler** — sync currently manual (`POST /sync`); auto-schedule deferred

---

## Installation & Upgrade

### From v0.2-rc1:
```bash
git pull origin main
git checkout v0.3.0

# Optional: trigger learning sync across devices
curl -X POST http://localhost:8080/sync \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-CSRF-Token: $CSRF"

# Verify Vibe Engineering is accessible
open http://localhost:8080/app/vibe-engineering
```

### New Operators:
```bash
./install.sh  # v0.3.0 ships with all features
# Enable cross-device sync in Console: Settings → Features → cross_device_sync
```

---

## Testing

**E2E Test Coverage:**
- ✅ 678 tests passing (636 base + 42 new)
- ✅ All G1–G5 functionality verified end-to-end (real HTTP, not mocks)
- ✅ ContextVar isolation verified under concurrent load
- ✅ YAML feature-status persistence verified round-trip

**Test Execution:**
```bash
pytest tests/e2e/ -v  # ~2min, all green
npm run test --workspace=console  # React component tests
```

---

## Migration Guide

### v0.2-rc1 → v0.3.0

**No data migration required.** All persistence is backward-compatible.

**UI Changes:**
- **Vibe Inspector** path (`/external-panels/vibe-inspector`) now redirects to **Vibe Overview** (`/app/vibe-overview`)
- **Learning Ledger** path (`/app/learning-ledger`) — new consolidated view with CEL grades, TreeOfThoughts, and ULO objectives
- **Glass Box** prompt reveal accessible from Context Trace turn list (new "Glass Box" button per turn)

**API Changes:**
- **New:** `POST /vibe-engineering/grades/{stage_id}` — operator grading endpoint
- **New:** `GET /vibe-engineering/grades` — read operator grades
- **New:** `POST /sync` — trigger cross-device learning sync
- **Deprecated (removed):** `/external-panels/vibe-inspector/*` routes

**Operator Workflow:**
1. After a turn, click "Glass Box" in Context Trace to see the exact prompt sent to the worker
2. Click "Learning Ledger" to see what the system learned (CEL grades, patterns, objectives)
3. Grade individual stages (👎/😐/👍) to refine the learning feedback loop
4. Enable "cross_device_sync" in Settings → Features to sync learning across devices

---

## Acknowledgments

**v0.3.0 was built using Loss-Driven Development (LDD)**, with full E2E-wiring-proof discipline and comprehensive adversarial review. All 42 new tests are genuinely reachable (verified via `root-cause-by-layer` and `e2e-wiring-proof` skills).

**Architects:** Claude Opus 4.8, Claude Haiku 4.5 (v0.2–0.3 work)  
**Reviewers:** 4-agent adversarial panel (Security, Concurrency, Correctness, API)

---

## What's Next (v0.3.1 Roadmap)

- **M1:** File organization refactoring (utils, subsystem consolidation)
- **M2:** Memplace full YAML template storage (task templates, error patterns)
- **M3:** G5 conflict resolution UI and auto-scheduler
- **M4:** Learned-experience skill auto-injection (Skills v2)

**Target Release:** 2 weeks (parallel team, low risk)

---

## Support

**Report Bugs:** `github.com/corvin-labs/corvinos/issues`  
**Questions:** `docs/operator-quickstart/` for step-by-step guides  
**Telemetry:** All disabled by default; opt-in via `spec.telemetry.*` in `tenant.corvin.yaml`

---

**v0.3.0 is production-ready. Deploy with confidence.** 🚀
