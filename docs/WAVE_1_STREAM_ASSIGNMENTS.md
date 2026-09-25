# Wave 1 Stream Assignments (2026-09-25 → 2026-09-27)

**Master Orchestrator:** Claude (coordination + Wave 2 planning + daily sync)  
**Execution Model:** Parallel streams, async standup at 2026-09-26 EOD

---

## 📋 Stream B: T09 Audit Phase 2b Wiring (3 days)

**Assigned to:** `[Team Member: Audit/Security]`

### Assignment Package

| Field | Value |
|-------|-------|
| **Task ID** | T09 |
| **Title** | Audit Phase 2b: Register 4 missing worker/A2A/plugin events |
| **Complexity** | MEDIUM (mechanical event registration + emit wiring) |
| **Duration** | 3 days (2026-09-26 → 2026-09-27 EOD) |
| **Owner Contact** | [INSERT NAME] |
| **Backup Contact** | Claude (cc: daily standup) |

### Deliverables

- [x] **Events Registered** in `EVENT_SEVERITY` + `_EVENT_ALLOWLIST` (already done)
- [ ] **Emit Calls Wired:** 4 events emitting on triggers (pseudocode in WAVE_1_STREAM_B_T09_AUDIT_WIRING.md)
  - `compute.worker_terminated` → `core/compute/corvin_compute/worker.py`
  - `a2a.genesis_block_created` → `ops/launcher/a2a_entry.py`
  - `a2a.offline_pair_initiated` → `core/bridges/shared/a2a_token.py`
  - `plugin.execution_timeout` → `core/plugins/corvin_plugins/lifecycle.py`
- [ ] **E2E Tests Pass:** `pytest tests/security/test_audit_phase2_events.py -v`
- [ ] **Hash-Chain Integrity:** `python3 scripts/verify_audit_chain.py --tenant=_default` returns 0 gaps
- [ ] **Commit:** `refactor(audit): T09 Phase 2b Wiring — emit 4 missing events [ADR-2041]`

### Success Criteria

```bash
# All 4 events must land in audit chain
grep -E "compute.worker_terminated|a2a.genesis|a2a.offline|plugin.timeout" ~/.corvin/audit.jsonl | wc -l
# Expected: ≥4 (one per trigger)

# Chain integrity must remain intact
python3 scripts/verify_audit_chain.py
# Expected: "✓ Chain valid, 0 gaps"
```

### Key Files

- Reference: `corvin_operator/forge/forge/security_events.py` (lines 861, 877–878, 883, 3248+)
- Implementation Guide: **`WAVE_1_STREAM_B_T09_AUDIT_WIRING.md`** (full pseudocode + test templates)
- Tests: `tests/security/test_audit_phase2_events.py`, `tests/a2a/`, `tests/plugins/`, `tests/compute/`

### Blockers / Dependencies

- ✅ T05 (ADR dedup) — COMPLETE, no blockers
- External: None
- Internal: Must emit within prod code, not mocks

### Escalation Path

1. **Blocker found:** Ping daily standup (async post-mortem)
2. **Cannot reach event emit site:** Check `WAVE_1_STREAM_B_T09_AUDIT_WIRING.md` line numbers — files may have shifted
3. **Hash-chain breaks:** Likely bad event payload; re-read allowlist constraints
4. **Critical:** Escalate via standup with proposed workaround

### Daily Standup Talking Points (2026-09-26 EOD)

- [ ] Events wired? (Y/N) — which ones done, which pending
- [ ] Any blockers? (file paths shifted, unclear pseudocode, test failures)
- [ ] ETA for completion? (Day 3 or earlier)
- [ ] Hash-chain status? (valid or gaps)

---

## 📋 Stream C: T04 Agent-Hub Mocks → Real Data (2 days)

**Assigned to:** `[Team Member: Console/Frontend]`

### Assignment Package

| Field | Value |
|-------|-------|
| **Task ID** | T04 |
| **Title** | Remove mock messages from Agent-Hub UI; wire ADR-2063 ContentStore |
| **Complexity** | LOW-MEDIUM (API integration, no audit changes) |
| **Duration** | 2 days (2026-09-26 → 2026-09-27) |
| **Owner Contact** | [INSERT NAME] |
| **Backup Contact** | Claude (cc: daily standup) |

### Deliverables

- [ ] **Mock Data Removed:** No hardcoded sample messages in `agent_hub_store.py`
- [ ] **API Wired:** `GET /v1/console/hub/messages` → ADR-2063 ContentStore
- [ ] **React Component Updated:** SkillAdminData hook calls real endpoint
- [ ] **E2E Test Passes:** `curl http://localhost:8765/v1/console/hub/messages` → real data
- [ ] **Commit:** `refactor(console): T04 Agent-Hub — replace mocks with ADR-2063 store [ADR-0763]`

### Success Criteria

```bash
# No mock references in prod code
grep -r "mock_messages\|sample_data\|hardcoded" core/console/ --include="*.py" | grep -v test | wc -l
# Expected: 0

# API returns real data
curl -s http://localhost:8765/v1/console/hub/messages | jq '.messages[0]'
# Expected: Real record (timestamp, content, sender — not invented data)

# ADR-0763 compliance: no fabricated data in UI
pytest tests/console/test_agent_hub_real_data.py -v
# Expected: All green
```

### Key Files

- Mock data location: `core/console/corvin_console/services/agent_hub_store.py` (find `mock_messages()`)
- API route: `core/console/corvin_console/routes/agent_hub_routes.py`
- Frontend hook: `core/console/corvin_console/web-next/src/hooks/useSkillAdminData.ts`
- ADR reference: `ADR-2063-agent-hub-content-store.md` (defines real schema)

### Blockers / Dependencies

- ✅ T05 (ADR dedup) — COMPLETE, no blockers
- ADR-2063 must define schema clearly (verify it exists + read it first)
- ContentStore API must be callable (check if module is importable)

### Escalation Path

1. **ContentStore API unclear:** Read ADR-2063 + grep repo for usage examples
2. **Module import fails:** Check `core/console/paths.py` for correct import path
3. **Endpoint 404:** Verify route is registered in Flask/FastAPI app
4. **Critical:** Escalate via standup with specific line number + error

### Daily Standup Talking Points (2026-09-26 EOD)

- [ ] Mock removal done? (Y/N) — estimated completion
- [ ] API integration started? (Y/N) — any schema mismatches?
- [ ] Frontend tested? (Y/N) — real messages rendering?
- [ ] Any blockers? (import errors, schema mismatch, route 404?)

---

## 📋 Stream D: T12 L10-Proof + T16 Marketplace (2.5 days)

**Assigned to:** `[Team Member: Architecture/Infrastructure]`

### Assignment Package

| Field | Value |
|-------|-------|
| **Task ID** | T12 + T16 (sequential, same stream) |
| **Title** | (T12) Prove L10-Reachability; (T16) Consolidate Marketplace per ADR-0892 |
| **Complexity** | MEDIUM (code tracing + route consolidation) |
| **Duration** | 2.5 days (2026-09-26 → 2026-09-27 EOD) |
| **Owner Contact** | [INSERT NAME] |
| **Backup Contact** | Claude (cc: daily standup) |

### Deliverables

#### T12: L10-Reachability Proof (Day 1, 0.5 days)

- [ ] **Verification Done:** `DEFAULT_PIPELINE` in `config.py` includes `l10_adapter` ✓
- [ ] **Code Audit:** `os_skills_integration.py` confirms L10 is "active/wired" ✓
- [ ] **E2E Test:** Real turn flows through L10, emits `context.adapted` audit event
- [ ] **Documentation:** Update CLAUDE.md L10 status from "NOT WIRED" to "WIRED + AUDITED"

#### T16: Marketplace Consolidation (Days 2–3, 2 days)

- [ ] **Duplicate Routes Identified:** Only ONE marketplace install path remains
- [ ] **Phase 6 Routes Deleted/Merged:** ADR-0892 enforced (no `/marketplace` + `/api/v1/marketplace` both active)
- [ ] **All Imports Updated:** No stale references to deleted route
- [ ] **E2E Tests Pass:** Install flow uses single canonical route
- [ ] **Commit:** `refactor(marketplace): T16 Consolidation — enforce ADR-0892 (one marketplace) [ADR-0892]`

### Success Criteria

**T12:**
```bash
# L10 adapter in DEFAULT_PIPELINE
grep -A 5 "DEFAULT_PIPELINE" core/skills/os_skills/context_engineering/stages/config.py | grep l10_adapter
# Expected: "l10_adapter" present

# Audit event emitted on context adaptation
pytest tests/skills/test_l10_reachability_e2e.py -v
# Expected: All green, audit event captured
```

**T16:**
```bash
# Only ONE marketplace route definition (canonical)
find core/console/corvin_console/routes -name "*marketplace*" -type f | wc -l
# Expected: 1 (not 2)

# No duplicate install paths
grep -r "marketplace_routes\|@marketplace_bp\|/marketplace" core/console --include="*.py" | grep -v archive | wc -l
# Expected: ≤5 (core references, no duplicates)

# E2E test passes
pytest tests/console/test_marketplace_single_path.py -v
# Expected: All green
```

### Key Files

**T12 (L10-Proof):**
- `core/skills/os_skills/context_engineering/stages/config.py` (DEFAULT_PIPELINE definition)
- `core/skills/os_skills/os_skills_integration.py` (active/wired flag check)
- Test: `tests/skills/test_l10_reachability_e2e.py` (new, E2E proof)

**T16 (Marketplace):**
- `core/console/corvin_console/routes/marketplace_routes.py` (Phase 6 — to be deleted or merged)
- `core/console/corvin_console/routes/` (find canonical marketplace route)
- ADR reference: `ADR-0892-one-marketplace.md`
- Tests: `tests/console/test_marketplace_*.py` (update to expect single route)

### Blockers / Dependencies

- ✅ T05 (ADR dedup) — COMPLETE, no blockers
- T12 → T16 (sequential within this stream; T16 starts after T12 completes)
- ADR-0892 must define "canonical marketplace path" clearly (verify it exists)

### Escalation Path

**T12:**
1. Cannot find `l10_adapter` in config? → Check if naming changed (search for "context_adapter")
2. No audit event? → Verify `write_event()` call exists in L10 code path
3. Critical: L10 genuinely unreachable → Re-run e2e-wiring-proof gate (may need redesign)

**T16:**
1. Don't know which route is canonical? → Read ADR-0892 (should specify)
2. Imports break after deletion? → Use `git grep` to find all callers before deleting
3. Tests fail post-consolidation? → Check if test setup is hardcoded to /marketplace path
4. Critical: Two routes still active → Escalate, may need architectural review

### Daily Standup Talking Points (2026-09-26 EOD)

- [ ] T12 L10-Proof complete? (Y/N) — audit event confirmed?
- [ ] T16 started? (Y/N) — canonical marketplace identified?
- [ ] Any blockers? (config missing, route naming confusion, import breaks?)
- [ ] ETA for T16 completion? (Day 3 EOD or earlier?)

---

## 🎯 Daily Standup Format (2026-09-26 EOD)

**Async Standup Post (one per Stream in shared channel/doc):**

```markdown
### Stream [B/C/D] Daily Standup — 2026-09-26

**Owner:** [Name]  
**Status:** 🔄 IN_PROGRESS | ✅ COMPLETE | 🚫 BLOCKED

#### Deliverables Progress
- [ ] Deliverable 1: [DONE / IN_PROGRESS / BLOCKED]
- [ ] Deliverable 2: [DONE / IN_PROGRESS / BLOCKED]
- [ ] (etc.)

#### Key Metrics
- Lines of code changed: X
- Tests passing: Y/Z
- Blockers: [none / 1. X, 2. Y]

#### Blockers (if any)
1. **Issue:** [description]
   - **Impact:** [what's blocked]
   - **ETA to resolve:** [date/time]

#### Tomorrow's Plan
- [ ] [action 1]
- [ ] [action 2]

#### Confidence Level
- Delivery on 2026-09-27 EOD: [HIGH / MEDIUM / LOW] — [reason]
```

---

## 🚨 Critical Success Factors

1. **Dependency Management:** T12 must complete before T16 can be verified
2. **No Silent Failures:** Each stream must post standup (async is OK, but MUST post)
3. **Reachability Proof:** Every stream has E2E test with success criteria
4. **Hash-Chain Integrity:** T09 must verify zero hash breaks
5. **ADR Compliance:** All commits reference relevant ADRs

---

**Orchestration Start:** 2026-09-25 23:45 UTC (Wave 1 launch)  
**First Standup:** 2026-09-26 EOD (async posts expected)  
**Next Sync:** After Wave 1 completion (2026-09-27), Wave 2 kickoff

