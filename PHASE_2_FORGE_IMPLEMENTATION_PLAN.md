# Phase 2 Implementation Plan — Forge = member (ADR-0701)

**Date:** 2026-09-18  
**Deadline:** 2026-09-21 (72 hours)  
**Status:** Starting → 100%

## Summary

Gate all Forge/SkillForge creation surfaces (G1–G5) with `require_capability("forge.create", tenant_id=…, entry_point=…)`. All denied calls return HTTP 402 or MCP error `license_required`. Emit audit events `license.capability_decision` and `forge.artifact_provenance_signed/invalid`.

## Chokepoints

| Gate | Location | Coverage |
|---|---|---|
| **G1** | `operator/forge/forge/registry.py::Registry.create`, `Registry.promote`, `MultiRegistry.create` | L6 MCP forge_tool, forge_promote; CLI forge create/promote |
| **G2** | `operator/skill-forge/skill_forge/registry.py::SkillRegistry.create(files=…)`, `MultiSkillRegistry.promote`, `MultiSkillRegistry.update_body` | L7 MCP skill_create, skill_promote; console manual editor `/skills/manual` |
| **G3** | FastAPI dependency `require_forge_capability(rec)` on console routes | `POST /skill-creator/generate`, `POST/PUT /skills/manual`, `POST /tools/{name}/promote`, `POST /skills/{name}/promote`, `POST /panels` |
| **G4** | `core/plugins/plugin_builder/turn.py::command`, `ideation.start` | `/plugin-builder` chat | Delete `plugin_builder_enabled` flag |
| **G5** | `core/orchestration/quota_gate.py::increment_and_check` for forge keys | Brain v0.2 (currently unused) |

## Implementation Checklist

- [ ] **G1:** Wire `require_capability("forge.create", ...)` to `Registry.create`, `Registry.promote`
- [ ] **G2:** Wire `require_capability("forge.create", ...)` to `SkillRegistry.create(files=…)`
- [ ] **G3:** Create FastAPI dependency `require_forge_capability(rec)` and apply to 5 routes
- [ ] **G4:** Wire plugin builder turn.py + delete plugin_builder_enabled flag
- [ ] **G5:** Wire quota_gate (skeleton ready, no production caller)
- [ ] **Audit events:** Emit `license.capability_decision` from every gate
- [ ] **Tests:** E2E test for every gate (deny on free, allow on member, HTTP 402 response)
- [ ] **Grep gates:** No `plugin_builder_enabled`, `tool_forge_per_day`, `skill_forge_per_day` remain
- [ ] **Frontend marker:** `console-deploy.sh --marker 'plugin-builder-capability'` LIVE

## Test Coverage Required

1. G1 MCP deny on free — forge_tool, forge_promote
2. G1 CLI deny on free — `forge create`, `forge promote`
3. G2 MCP deny on free — skill_create, skill_promote
4. G2 console deny on free — `POST /skills/manual` → 402
5. G3 routes all 5 — `POST /skill-creator/generate` → 402
6. G4 plugin-builder chat — `/plugin-builder` → chat message with upgrade link
7. G5 quota_gate — skeleton wired
8. Audit: every deny emits `license.capability_decision` with reason
9. Free-tier error handling: degradation, not block

## Execution Timeline

**Session 1 (today):**
- [ ] Implement G1 (2h)
- [ ] Implement G2 (2h)
- [ ] Implement G3 (3h)
- [ ] Write initial tests (3h)

**Session 2 (tomorrow):**
- [ ] Implement G4 (1h)
- [ ] Implement G5 (1h)
- [ ] Complete test suite (6h)
- [ ] Grep gates + cleanup (2h)

**Session 3:**
- [ ] Adversarial review + fixes (4h)
- [ ] Final commit (1h)

---

**Review Gates (3-lens, zero findings):**
1. Bypass/Security: No privilege escalation, all gates reached
2. Business/Legal/Compliance: Terms match code, audit events immutable
3. Architecture/Reachability: Every writer reaches exactly one gate, no bypass

