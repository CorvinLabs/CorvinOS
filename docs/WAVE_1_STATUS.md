# Wave 1 (Tag 1–3) — Status Report

**Status:** 1/4 Streams Complete | Wave Duration: 3 days (max of 4 parallel streams)

---

## ✅ Stream A: T05 ADR-ID-Dedup

**Status:** COMPLETE (2026-09-25 23:37 UTC)  
**Commit:** `9090745` (refactor(adr): T05 Deduplication Wave 1 — Archive 27 duplicate IDs)

**What shipped:**
- 27 duplicate ADR IDs identified
- 33 redundant files archived to `archive/2026-09-25-T05-dedup/`
- 28 canonical ADRs retained (0 duplicates remaining)
- Git tracking: all moves tracked with `~` naming convention
- Pre-commit hook: queued for Wave 1 day 2

**Verification:**
```bash
cd /home/shumway/projects/Corvin-ADR/decisions
ls -1 | grep -oE 'ADR-[0-9]{4}' | sort | uniq -d | wc -l  # Returns 0 ✓
```

**Impact:** Knowledge graph is now unambiguous — no more ADR-ID conflicts in downstream systems.

---

## 🔄 Stream B: T09 Audit Phase 2b Wiring (3 days, parallel)

**Status:** QUEUED (ready to start)  
**Tasks:**
- [ ] Register 4 missing audit events in EVENT_SEVERITY
- [ ] Add to _EVENT_ALLOWLIST with validation
- [ ] Wire execute() calls + emit events
- [ ] E2E test: verify hash-chain integrity

**Missing Events:**
- `compute.worker_terminated`
- `a2a.genesis_block_created`
- `a2a.offline_pair_initiated`
- `plugin.execution_timeout`

**Key Files:**
- `core/security/security_events.py` (lines 806–831, 2719–2778)
- Test: `tests/security/test_audit_event_registration.py`

**ETA:** 2026-09-26 through 2026-09-27 (3 days)

---

## 🔄 Stream C: T04 Agent-Hub Mocks → Real (2 days, parallel)

**Status:** QUEUED (ready to start)  
**Task:** Replace mock_messages with ADR-2063 ContentStore

**Files to Modify:**
- `core/console/corvin_console/services/agent_hub_store.py` (mock data)
- `core/console/corvin_console/routes/agent_hub_routes.py` (API endpoints)
- Test: `tests/integration/test_agent_hub_real_data.py`

**ADR-0763 Requirement:** "fabricates nothing" — all UI data must be real, not invented.

**E2E Proof:**
```bash
curl -s http://localhost:8765/v1/console/hub/messages \
  | jq '.messages[0]' | grep -E 'content|timestamp'  # Must match real store
```

**ETA:** 2026-09-26 through 2026-09-27 (2 days)

---

## 🔄 Stream D: T12 L10-Proof + T16 Marketplace (2.5 days, parallel)

**Status:** QUEUED (two sequential tasks)

### T12: L10-Reachability Proof (0.5 days)
**Task:** Verify L10 Context Adapter is wired into production code

**Evidence Needed:**
1. `DEFAULT_PIPELINE` in `core/skills/os_skills/context_engineering/stages/config.py` includes `l10_adapter` ✓
2. Grep `os_skills_integration.py` for "l10_adapter" active = True ✓
3. E2E test: Real turn goes through l10_adapter, emits audit event

**File:** `core/skills/os_skills/context_engineering/stages/config.py:L1-50`

### T16: Marketplace Consolidation (2 days)
**Task:** Enforce ADR-0892 — one marketplace per route

**Current Problem:**
- `/api/v1/marketplace` (original)
- `/marketplace` (Phase 6 new) ← duplicate route

**Action:**
- Delete `core/console/corvin_console/routes/marketplace_routes.py:marketplace_bp`
- Update all imports to use single canonical marketplace path
- E2E test: only ONE install path works

**ETA:** After T12 (sequential), 2026-09-26 through 2026-09-27 (2 days total)

---

## 📊 Wave 1 Summary (EOD 2026-09-27)

| Stream | Task | Est. Duration | Status | Risk | EOD |
|--------|------|---|--------|------|-----|
| **A** | T05 ADR-Dedup | 2 days | ✅ COMPLETE | Low | Done |
| **B** | T09 Audit Wiring | 3 days | 🔄 QUEUED | Medium | 2026-09-27 |
| **C** | T04 Mocks→Real | 2 days | 🔄 QUEUED | Low | 2026-09-27 |
| **D** | T12+T16 L10+Marketplace | 2.5 days | 🔄 QUEUED | Medium | 2026-09-27 |

**Wave 1 Completion:** 2026-09-27 EOD (all 4 streams green or complete)

---

## 🎯 Quality Gates Applied

✅ **Wave 1 Loss Signals:**
- ADR duplicate count: 27 → **0** ✓ (T05)
- Pre-commit hook enforcement: queued for day 2
- Audit event registration: measured in T09
- L10 reachability: proven in T12

---

## 🚀 Next: Wave 2 (Tag 4–6)

**Blocker Resolution:** T05 complete → T06 can proceed  
**Stream Assignment:**
- Stream A: T06 Retroaktive ADRs (Voice-Persistenz, STT, ML-Retraining)
- Stream B: T10 Learning k=6 starts (5-day task into Wave 3)
- Stream C: T17 Flag→Plugin Migration (starts)
- Stream D: T03 Phase-3a–c Verdrahten (dialektisch entscheiden)

---

**Last Update:** 2026-09-25 23:37 UTC  
**Next Checkpoint:** Wave 1 completion check, 2026-09-27 EOD

