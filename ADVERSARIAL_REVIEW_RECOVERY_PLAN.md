---
title: "Recovery Plan: Phase 2B Adversarial Findings"
date: 2026-09-19
status: IN_PROGRESS
critical_fixes: 3 (G1, G2, G3)
timeline: "3-5 hours critical path"
---

# Recovery Plan — Phase 2B Adversarial Findings

**Status:** Recovery Implementation  
**Gate:** Release remains blocked until P0 fixed  
**Next Step:** Execute P0 fixes in parallel

---

## P0 Recovery (Critical Path)

### Phase 0: Preparation (30 min)

- [x] Identify all 3 P0 findings (completed via adversarial review)
- [ ] Create feature branches (one per finding)
- [ ] Verify test environment ready (pytest, Playwright)

### Phase 1: Fix FINDING 1 (G3 Gates Not Implemented) — 1–2h

**Goal:** Add `Depends(require_forge_capability)` to all console routes

**Steps:**

1. **Add gate to skill_creator_api.py**
   - File: `/core/console/corvin_console/routes/skill_creator_api.py`
   - Route: `POST /skill-creator/generate` (line ~203)
   - Add: `_: Annotated[CapabilityDecision, Depends(require_forge_capability)]`

2. **Add gate to skills_manual.py**
   - File: `/core/console/corvin_console/routes/skills_manual.py`
   - Routes: `POST/PUT /skills/manual`
   - Add gate dependency

3. **Add gate to promote.py**
   - File: `/core/console/corvin_console/routes/promote.py`
   - Routes: `POST /tools/{name}/promote`, `POST /skills/{name}/promote`
   - Add gate dependency

4. **Add gate to panels.py**
   - File: `/core/console/corvin_console/routes/panels.py`
   - Route: `POST /panels`
   - Add gate dependency

5. **Create E2E test**
   - File: `/tests/license/test_g3_gates_e2e.py` (new)
   - Test: Verify free-tier gets 402 on all 5 routes
   - Test: Verify member-tier gets 200 on all 5 routes

6. **Verify no regressions**
   - Run: `pytest tests/license/test_g3_gates_e2e.py -v`
   - Run: `pytest tests/console/ -v` (existing console tests)
   - Expected: All green

**Commit:** `fix(license-g3): implement forge.create gate in console routes [ADR-0701]`

---

### Phase 2: Fix FINDING 2 (Delete core/license) — 30 min

**Goal:** Remove deprecated core/license directory

**Steps:**

1. **Verify no live imports**
   ```bash
   grep -r "from core\.license\|from core/license" /core /operator --include="*.py"
   grep -r "from core\.license\|from core/license" /tests --include="*.py"
   # Expected: 0 matches
   ```

2. **Delete directory**
   ```bash
   rm -rf /core/license/
   ```

3. **Verify deletion**
   ```bash
   ls -d /core/license/ 2>&1
   # Expected: No such file or directory
   ```

4. **Run full test suite**
   ```bash
   pytest tests/ -k license -v
   # Expected: All green (no import failures)
   ```

5. **Verify git status**
   ```bash
   git status | grep "deleted:"
   # Should show: deleted: core/license/...
   ```

**Commit:** `chore(license): delete deprecated core/license directory [ADR-0703]`

---

### Phase 3: Fix FINDING 3 (Complete G1-G5 Gates) — 2–3h

**Goal:** Verify and complete all 5 Forge chokepoints

**Steps:**

1. **Verify G1: MCP Server (forge_tool, forge_promote)**
   - Location: `/corvin_operator/forge/mcp_forge_server.py` or similar
   - Verify: Both forge_tool and forge_promote check forge.create capability
   - Action: Add gate if missing

2. **Verify G2: SkillRegistry.create**
   - Location: `/corvin_operator/skill-forge/skill_forge/registry.py`
   - Verify: Calls require_forge_capability before creating
   - Action: Confirm gate is in place (likely already done)

3. **Verify G3: Console Routes (FINDING 1)**
   - Status: Being fixed in Phase 1
   - Action: None needed here

4. **Verify G4: /plugin-builder Chat Path**
   - Location: `/operator/chat/plugin_builder_handler.py`
   - Verify: Checks forge.create before responding to builder commands
   - Action: Add gate if missing or remove from G list if not applicable

5. **Verify G5: Brain v0.2 quota_gate**
   - Location: (search for brain and quota_gate)
   - Action: If used, ensure forge.create check; if dead code, delete

6. **Create verification test**
   - File: `/tests/license/test_forge_chokepoints_comprehensive.py` (new)
   - Test all 5 gates (G1–G5) with free/member credentials
   - Verify: All 5 gates actually deny free-tier access

**Commit:** `test(license-g5): verify all forge chokepoints [ADR-0701]`

**Note:** May need to refactor Finding 4 (exception type) during this phase

---

## P1 Recovery (High Priority)

### Phase 4: Fix FINDING 4 (Exception Type Mismatch) — 45 min

**Steps:**
1. Clarify `require_capability()` to document raise behavior
2. Update skill-forge to catch LicenseDenied consistently
3. Add test for exception propagation

**Commit:** `fix(license-require-capability): clarify exception contract`

---

### Phase 5: Fix FINDING 5 (Audit Events) — 2–3h

**Steps:**
1. Grep for each unconfirmed event in production code
2. Add emission at decision points (fail-closed: log first, execute second)
3. Update tests to verify emission

**Commit:** `fix(audit): emit all license decision events [ADR-0701-0704]`

---

### Phase 6: Fix FINDING 6 (Device FP Mode 0600) — 1–2h

**Steps:**
1. Update device_fp.py to use os.open(..., 0o600)
2. Add mode verification (fail-closed)
3. Add test: test_device_id_file_mode

**Commit:** `fix(license-device-fp): enforce mode 0600 for device_id [ADR-0700]`

---

### Phase 7: Fix FINDING 7 (Commit Traceability) — 1h

**Steps:**
1. Run git log to find all commits for each ADR
2. Update ADR frontmatter commits: field
3. Verify ADR-0704 has commits field (currently missing)

**Commit:** `docs(adr): complete commit traceability for ADR-0700-0704`

---

## Timeline

| Phase | Task | Effort | Duration | Status |
|-------|------|--------|----------|--------|
| 0 | Preparation | 30 min | Now | TODO |
| 1 | Fix G3 gates (Finding 1) | 1–2h | 30 min–2h | TODO |
| 2 | Delete core/license (Finding 2) | 30 min | 30 min | TODO |
| 3 | Complete G1-G5 (Finding 3) | 2–3h | 1–3h | TODO |
| 4 | Exception type (Finding 4) | 45 min | 45 min | TODO (after P0) |
| 5 | Audit events (Finding 5) | 2–3h | 1–3h | TODO (after P0) |
| 6 | Device FP mode (Finding 6) | 1–2h | 1–2h | TODO (after P0) |
| 7 | Commit trace (Finding 7) | 1h | 1h | TODO (after P0) |

**Critical Path (P0):** 3–5 hours (Phases 1–3)  
**Full Path (P0+P1):** 8–14 hours (Phases 1–7)

---

## Success Criteria

### P0 Completion (Gate Passes)
- [ ] All 5 G-gates are implemented and verified
- [ ] E2E tests pass for all gates (free-tier = 402, member = 200)
- [ ] core/license directory deleted
- [ ] No regression in existing tests
- [ ] ADR-0700-0704 frontmatter accurate

### P1 Completion (Release-Ready)
- [ ] All P1 findings addressed
- [ ] Audit events confirmed emitted
- [ ] Device FP mode verified
- [ ] Commit traceability complete
- [ ] Full test suite green

---

## Verification Checklist

```bash
# After each phase, run:

# 1. Unit tests
pytest tests/license/ -v

# 2. E2E tests
pytest tests/e2e/ -k "license or forge" -v

# 3. Console tests
pytest tests/console/ -v

# 4. Full test suite
pytest tests/ -v

# 5. Linting
pylint core/console/corvin_console/routes/*.py

# 6. Git verification
git status  # All changes staged
git diff --cached | head -50  # Verify changes look correct
```

---

## Next Steps

1. **Now:** Start Phase 0 (Preparation)
2. **In parallel:** Implement Phases 1–3 (P0 fixes)
3. **After P0:** Implement Phases 4–7 (P1 fixes)
4. **Final:** Merge all commits to main, tag v2.0.0-licensing-ready

---

**Recovery Plan Status:** Ready to Execute  
**Start Time:** 2026-09-19 16:00 UTC  
**Target Completion:** 2026-09-19 19:00–21:00 UTC (3–5 hours)
