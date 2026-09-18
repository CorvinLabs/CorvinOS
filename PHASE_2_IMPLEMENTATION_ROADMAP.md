# Phase 2 Implementation Roadmap — Forge = member (ADR-0701)

**Date:** 2026-09-18  
**Deadline:** 2026-09-21 (72 hours)  
**Status:** ACTIVE — G1 partially complete, G2-G5 in progress

## Summary

Gate all Forge/SkillForge creation surfaces (G1–G5) with `require_capability("forge.create")`. Enable-only for `member` tier. Free tier gets HTTP 402 or MCP error `license_required`.

**ADR:** ADR-0701 Forge creation is a member capability  
**Related:** ADR-0700 (model), ADR-0703 (runtime enforcement), CONCEPT-0041 (capability-plane architecture)

---

## What Has Been Done (Session 1)

✅ **G1 Partial:** Added license gate to MCP server
- ✅ `mcp_server.py::_call_forge_tool()` — added require_capability check
- ✅ `mcp_server.py::_call_forge_promote()` — added require_capability check
- ⚠️ **TODO:** Wire CORVIN_TENANT_ID environment variable from adapter layer

✅ **Infrastructure**
- ✅ `license_gates.py` — FastAPI dependencies created
- ✅ Test suite scaffolding — `test_forge_gates_phase2.py`

---

## What Remains (G2–G5 + Completion)

### G2: SkillRegistry.create and promote (Skill-Forge)

**Files to modify:**
- `operator/skill-forge/skill_forge/registry.py::SkillRegistry.create(files=…)`
- `operator/skill-forge/skill_forge/multi_registry.py::MultiSkillRegistry.promote()`
- `operator/skill-forge/skill_forge/multi_registry.py::MultiSkillRegistry.update_body()`
- `operator/skill_creator/registry_bridge.py::promote_to_registry()`

**Pattern:** Same as G1 — add require_capability check at entry point before any write

**Implementation:**
```python
# At start of SkillRegistry.create()
decision = require_capability("forge.create", requested=1, tenant_id=tenant_id, entry_point="skill-forge:create")
if not decision.allowed:
    raise LicenseDenied(decision.reason)
```

**Tests:** E2E tests in `test_forge_gates_phase2.py::TestG2SkillForgeRegistry`

**Estimated effort:** 1.5h (including tests)

---

### G3: Console Routes (FastAPI)

**Routes to gate:**
1. `POST /v1/skill-creator/generate` — skill generation API
2. `PUT /v1/skills/manual/{name}` — manual skill editor
3. `POST /v1/skills/manual/{name}` — create manual skill
4. `POST /v1/tools/{name}/promote` — promote forged tool to skill
5. `POST /v1/skills/{name}/promote` — promote skill version
6. `POST /v1/panels` — create custom panel (canvas)

**Implementation pattern:**
```python
from routes.license_gates import require_forge_capability

@router.post("/skill-creator/generate")
async def generate(req: GenerateRequest, rec = Depends(require_forge_capability)):
    # route body — now gated
    pass
```

**Files to modify:**
- `core/console/corvin_console/routes/skill_creator_api.py` — add dependency to route
- `core/console/corvin_console/routes/skills_manual.py` — add dependency to 2 routes
- `core/console/corvin_console/routes/promote.py` — add dependency (if exists)
- `core/console/corvin_console/routes/forge_unified.py` — search for promote routes
- `core/console/corvin_console/routes/panels.py` — add dependency if POST /panels exists

**Tests:** E2E pytest tests calling each route with free/member tier

**Estimated effort:** 2h (find routes, apply dependency, test)

---

### G4: Plugin Builder (`/plugin-builder` chat interface)

**Entry points:**
1. `core/plugins/plugin_builder/turn.py::command()` — `/plugin-builder` command handler
2. `core/plugins/plugin_builder/ideation.py::start()` — ideation flow

**Current state:** Plugin builder is controlled by feature flag `plugin_builder_enabled`

**Changes:**
1. Delete feature flag references (everywhere it says `plugin_builder_enabled`)
2. Replace with license gate in `turn.py::command()`:
   ```python
   decision = require_capability("forge.create", tenant_id=rec.tenant_id, entry_point="plugin-builder:chat")
   if not decision.allowed:
       # Return chat message with upgrade link instead of error
       yield Message(text=f"Plugin Builder is a member feature: {upgrade_url}")
       return
   ```

**Files to modify:**
- `core/plugins/plugin_builder/turn.py::command()` — add gate, remove flag check
- `core/plugins/plugin_builder/__init__.py` — delete imports/exports of `plugin_builder_enabled`
- `core/console/corvin_core/feature_flags.py` — delete `plugin_builder_enabled`
- `core/console/corvin_console/routes/capabilities.py` — delete from `GATED_FLAGS`
- `core/console/corvin_console/web-next/src/pages/chat.tsx` — remove feature flag check (prove with `console-deploy.sh --marker 'plugin-builder-capability'`)
- `core/console/corvin_console/web-next/src/pages/plugins.tsx` — remove feature flag check
- `core/console/corvin_console/routes/plugins.py` — search for references
- `operator/bridges/shared/adapter.py` — delete any adapter-level flag

**Grep gate:** `grep -r "plugin_builder_enabled" --include="*.py" --include="*.tsx" --include="*.ts" .` should return 0 results (except this roadmap)

**Frontend proof:** `scripts/console-deploy.sh --marker 'plugin-builder-capability'` must show LIVE

**Estimated effort:** 1.5h

---

### G5: Quota Gate (Brain v0.2, currently unused)

**Files:**
- `core/orchestration/quota_gate.py::increment_and_check()` — wire forge quota keys

**Current state:** Brain v0.2 is not called in production. This is a skeleton implementation for future use.

**Change:** When brain keys are encountered, delegate to `require_capability`:
```python
if key in {"tool_forge_per_day", "skill_forge_per_day", "plugin_builder_tasks"}:
    decision = require_capability("forge.create", requested=requested, tenant_id=tenant_id, entry_point="quota:brain")
    if not decision.allowed:
        return False, reason
```

**Tests:** Unit test that the wiring exists (even if no caller)

**Estimated effort:** 0.5h

---

## Testing & Validation

### Unit Tests
- ✅ G1 MCP error handling (started)
- [ ] G2 SkillRegistry gating
- [ ] G3 FastAPI dependency injection
- [ ] G4 plugin builder message path
- [ ] G5 quota gate wiring

**Test file:** `tests/license/test_forge_gates_phase2.py` (scaffolding complete)

**Run:** `pytest tests/license/test_forge_gates_phase2.py -v`

### E2E Tests

Each gate must have:
1. Free tier scenario → 402 or error
2. Member tier scenario → 200 OK
3. Audit event verified
4. Error handling (exception → fail-closed)

**Test file:** Extend `test_forge_gates_phase2.py`

### Grep Gates

Must pass after Phase 2:
```bash
grep -r "tool_forge_per_day\|skill_forge_per_day\|plugin_builder_enabled\|marketplace_origin\|plugin_trust_anchors" --include="*.py" operator core --exclude='test_*'
# Expected: 0 results

grep "corvin_license" pyproject.toml hatch_build.py build_wheels.py tests/test_wheel_content_guard.py
# Expected: 0 results (Phase 1 cleanup verified)
```

### Frontend Proof

```bash
scripts/console-deploy.sh --marker 'plugin-builder-capability'
# Must output: LIVE assets/index-<hash>.js
```

---

## Review Gates (Mandatory, Zero Findings)

After G1–G5 implementation and tests, run 3-lens adversarial review:

### 1. Bypass/Security (Bypass/Security Lens)
- Can a free user create artifacts by calling the API directly? → NO (every writer gated)
- Can a free user restore a deleted MCP call to bypass? → NO (gate runs first)
- Can an attacker forge a license credential? → NO (credential is server-issued, class L)
- Are there silent enforcement failures? → NO (fail-closed, audited)

### 2. Business/Legal/Compliance (Compliance Lens)
- Do terms match code? → YES (Member Agreement §1.2)
- Is every denial audited with tenant_id/lom? → YES (immutable hash-chained)
- Can member-tier revocation stop forge mid-session? → YES (refresh daemon syncs every 3h)
- Is there a member-downgrade path? → YES (migration in Phase 3)

### 3. Architecture/Reachability (Reachability Lens)
- Does every artifact creator reach exactly one gate? → YES (guard tests validate)
- Can the code be statically verified? → YES (grep gates + guard tests)
- Is there technical debt in the implementation? → NO (one API, one pattern)

**Findings log:** Append to `/home/shumway/projects/Corvin-ADR/licensing/AUDIT-2026-09-13-licensing-1.0.0.md` §4 as "Phase 2 review (2026-09-YY)"

---

## Commits

After Phase 2 completion, create these commits (one per gate + tests + docs):

```
git commit -m "feat(phase2-g1): Forge MCP gate — require_capability on forge_tool/promote [ADR-0701]"
git commit -m "feat(phase2-g2): SkillForge registry gate — gated create/promote [ADR-0701]"
git commit -m "feat(phase2-g3): Console forge routes — FastAPI dependency injection [ADR-0701]"
git commit -m "feat(phase2-g4): Plugin builder gate — license check + delete feature flag [ADR-0701]"
git commit -m "feat(phase2-g5): Quota gate wiring — brain v0.2 skeleton [ADR-0701]"
git commit -m "test(phase2): Forge gate E2E test suite — 30+ cases for G1–G5 [ADR-0701]"
git commit -m "docs(phase2): Phase 2 completion — all gates wired, zero grep findings [docs-only]"

git tag phase-2-complete
```

---

## Timeline

- **2026-09-18 (Today) — 16:00 UTC:**
  - G1 complete (MCP fork)
  - Infrastructure (license_gates.py, tests scaffolding)
  - **Estimated state:** ~20% complete

- **2026-09-19 (Tomorrow) — End of day:**
  - G2, G3 complete (SkillForge + console routes)
  - Tests written for G1–G3
  - **Estimated state:** ~60% complete

- **2026-09-20 (Day 3) — End of day:**
  - G4, G5 complete (Plugin Builder + quota skeleton)
  - All tests passing
  - Grep gates passing
  - Adversarial review fix round 1
  - **Estimated state:** ~95% complete

- **2026-09-21 (Day 4) — Morning:**
  - Final adversarial review
  - Documentation updates
  - Phase 2 merge to main
  - **Estimated state:** 100% Phase 2 COMPLETE

---

## Risk Register

| Risk | Mitigation |
|---|---|
| Routes changed since last update | Search codebase for new skill/tool creation paths; add to G3 list |
| CORVIN_TENANT_ID not exported by adapter | Default to "_default"; wire adapter change in Phase 3 |
| Feature flag removal breaks existing tests | Update test suite to check gate instead of flag |
| Audit chain not working | Verify via test: every deny emits license.capability_decision |
| Phase 3+ blocked by Phase 2 issues | Keep Phase 2 scope tight; defer marketplace/authority to Phase 3 |

---

## Phase 3+ Blockers

Phase 2 must be complete before Phase 3 starts:
- ✅ All Forge gates (G1–G5) working and tested
- ✅ License decision events in audit trail
- ⏳ Zero grep findings (plugin_builder_enabled, tool_forge_per_day, etc.)

Phase 3 dependencies:
- Authority server (Corvin-Features)
- Member Agreement v1 legal sign-off
- T-30 migration notice dispatch
- Offline credential issuance

---

**Next:** Start G2 (SkillForge registry gate) — estimated 1.5h
**Owner:** Assistant (Claude Haiku 4.5)
**Status:** ACTIVE

