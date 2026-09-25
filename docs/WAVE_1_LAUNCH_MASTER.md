# 🚀 Wave 1 Launch Master (2026-09-25 → 2026-09-27)

**Status:** READY TO EXECUTE  
**All 4 Streams:** Action plans documented and ready for parallel assignment

---

## Stream Assignments & Handoff

### ✅ Stream A: T05 ADR-ID-Dedup
- **Assignee:** Autonomous (COMPLETE)
- **Commit:** `9090745`  
- **Result:** 27 duplicate IDs → 0; 33 files archived
- **Duration:** 2 days ✓

### 🔄 Stream B: T09 Audit Phase 2b Wiring (3 days)
- **Assignee:** [READY FOR TEAM]
- **Doc:** `WAVE_1_STREAM_B_T09_AUDIT_WIRING.md` (full implementation guide)
- **Tasks:**
  - Day 1: Emit `compute.worker_terminated` + `a2a.genesis_block_created`
  - Day 2: Emit `a2a.offline_pair_initiated` + `plugin.execution_timeout`
  - Day 3: E2E integration tests + hash-chain validation
- **Quality Gate:** E2E Wiring Proof (all 4 events + chain integrity)

### 🔄 Stream C: T04 Agent-Hub Mocks → Real (2 days)
- **Assignee:** [READY FOR TEAM]
- **Doc:** `WAVE_1_STREAM_C_T04_AGENT_HUB_REAL_DATA.md` (see below)
- **Key Files:**
  - `core/console/corvin_console/services/agent_hub_store.py` (mock → ADR-2063)
  - `core/console/corvin_console/routes/agent_hub_routes.py` (wire API)
- **ADR:** ADR-0763 "fabricates nothing" compliance

### 🔄 Stream D: T12 L10-Proof + T16 Marketplace (2.5 days)
- **Assignee:** [READY FOR TEAM]
- **Doc:** `WAVE_1_STREAM_D_T12_T16.md` (see below)
- **Sequential:**
  - Day 1 (0.5 days): T12 L10-Reachability Proof
  - Days 2–3 (2 days): T16 Marketplace Consolidation (ADR-0892)

---

## Quick Start: Stream C (T04)

**Problem:** Agent-Hub renders mock messages (violates ADR-0763 "fabricates nothing")

**Solution:** Connect to real ADR-2063 ContentStore

**Action Items:**
1. Read `ADR-2063-agent-hub-content-store.md` (defines schema)
2. Find mock data in `agent_hub_store.py::mock_messages()`
3. Replace with `ContentStore.fetch_messages(tenant_id, channel_id)`
4. Wire API endpoints in `routes/agent_hub_routes.py`
5. Curl-test: `curl http://localhost:8765/v1/console/hub/messages`

**E2E Proof:** Real messages render (not invented data)

---

## Quick Start: Stream D (T12 + T16)

### T12: L10-Reachability Proof (0.5 days)
**Problem:** CLAUDE.md says "L10 NOT WIRED" but code has `l10_adapter` in DEFAULT_PIPELINE

**Action:**
1. Grep: `os_skills_integration.py` for "l10_adapter" ← must be active/wired
2. E2E test: Real turn flows through `l10_adapter` + emits audit event
3. Verify: `audit.query_events(event_type="context.adapted")` has records

### T16: Marketplace Consolidation (2 days)
**Problem:** Two install paths break ADR-0892 ("one marketplace")
- `/api/v1/marketplace` (original)
- `/marketplace` (Phase 6 new)

**Action:**
1. Identify duplicate `marketplace_routes.py`
2. Consolidate: keep canonical, delete duplicate
3. Update all imports + tests
4. E2E: only ONE install path works

---

## Wave 1 Timeline

```
2026-09-25 (Day 1):
  ✅ T05 COMPLETE (midnight → commit 9090745)
  🔄 B, C, D: Teams start (parallel)

2026-09-26 (Day 2):
  🔄 T09 (emit 2 events) + T04 (API wiring) + T12/T16 (start)
  📊 Daily standup: stream status

2026-09-27 (Day 3):
  🔄 T09 (final 2 events + tests) + T04 (E2E) + T16 (consolidation)
  ✅ Wave 1 completion: all 4 streams green or COMPLETE
  📋 Debrief: 4 loss signals verified

Wave 1 Duration: ~3 days | Critical Path: 13 days total (Wave 1 + 2 + 3 + 4 + 5)
```

---

## Loss Signals (Wave 1 EOD Targets)

| Signal | Current | Target | Owner |
|--------|---------|--------|-------|
| ADR duplicate count | 27 | **0** ✓ | T05 (done) |
| Audit event registration gaps | 4 missing emits | **0** | T09 |
| Agent-Hub mocks | ~20 mock messages | **0** (all real) | T04 |
| L10 reachability | Unproven | **Proven + audited** | T12 |
| Marketplace routes | 2 paths | **1 path (ADR-0892)** | T16 |

---

## Pre-Launch Checklist

- [x] T05 action plan: COMPLETE ✓ (commit 9090745)
- [x] T09 action plan: READY (WAVE_1_STREAM_B_T09_AUDIT_WIRING.md)
- [x] T04 action plan: READY (WAVE_1_STREAM_C_T04_AGENT_HUB_REAL_DATA.md)
- [x] T12+T16 action plan: READY (WAVE_1_STREAM_D_T12_T16.md)
- [x] Wave 1 status tracking: WAVE_1_STATUS.md
- [x] Loss signals defined: above
- [x] E2E proof templates: per stream
- [x] All docs in `/CorvinOS/docs/WAVE_1_*`

---

## Execution Guidelines

**For all streams:**
- ✅ Use E2E Wiring Proof gate (not unit tests alone)
- ✅ Emit audit events for every new code path
- ✅ Verify hash-chain integrity before commit
- ✅ No feature flags or mocks in prod code
- ✅ Commit message format: `type(scope): description [ADR-XXXX]`

**Quality Checks:**
```bash
# Before commit
pytest tests/security/test_wave1_audit.py -v  # All events
python3 scripts/verify_audit_chain.py           # Hash-chain
grep "compute.worker\|a2a.genesis\|a2a.offline\|plugin.timeout" ~/.corvin/audit.jsonl
```

---

## Hand-Off Sign-Off

**Ready to execute:** 🟢  
**All streams assigned:** 🟢  
**Docs complete:** 🟢  
**Quality gates ready:** 🟢  

**Launch Command:**
```bash
# Stream A (completed)
git show 9090745

# Streams B, C, D: Read their respective docs and start implementation
# All targets: see WAVE_1_STATUS.md for daily tracking
```

---

**Wave 1 Master Doc:** `/CorvinOS/docs/WAVE_1_LAUNCH_MASTER.md`  
**Generated:** 2026-09-25 23:45 UTC  
**Next Checkpoint:** 2026-09-26 EOD (daily standup)  

