# CorvinOS Deprecation Roadmap — ACP Vision

**Timeline:** 24 weeks (6 months), 3 major phases, graduated obsolescence  
**Start:** 2026-09-01 | **End:** 2027-02-28  
**Status:** APPROVED FOR PLANNING (awaiting ADR-0532–0535 formal acceptance)

---

## PHASE 1: Feature Flags Dead (Weeks 1–4)

### Objective
Remove feature flag system entirely; replace with Skill registry enable/disable.

### Week-by-Week Breakdown

#### Week 1: Planning + Infrastructure
- [ ] Create `FeatureFlagLegacyAdapter` class (shim for backward compat)
- [ ] Add `--migration-mode` flag to all feature-flag calls (log which use legacy path)
- [ ] Create `migrate_flags_to_skills.py` script (console UI-driven)
- [ ] Write 5 E2E tests proving old + new paths both work
- **Deliverable:** Shim implementation + test suite (green)

#### Week 2: Admin Routes Consolidation
- [ ] Add new Skill endpoints to `/api/admin/skills/` (alongside plugin endpoints)
- [ ] Add config auto-migration route: `POST /api/admin/migrate-flags-to-skills`
- [ ] Update CLI: `corvin skills activate/deactivate/config/show` (replaces `corvin flag`)
- [ ] Add deprecation warnings to old CLI + API routes
- **Deliverable:** New admin API + CLI working (A/B testable)

#### Week 3: Scope Expansion + Testing
- [ ] Identify all code sites calling `flag(id)` (~20 files)
- [ ] Wrap each with shim: `legacy_flag(id)` calls → `skills.is_enabled(id)`
- [ ] Add telemetry: count legacy vs. new path usage
- [ ] Run full test suite in both-systems mode
- **Deliverable:** All flag calls wrapped; telemetry baseline captured

#### Week 4: Production Readiness
- [ ] Operator manual: how to migrate from flags to Skills
- [ ] Config validation script: check tenant.corvin.yaml for spec.features
- [ ] Deprecation doc: feature flags end-of-life timeline (12 weeks from now)
- [ ] Documentation update: `docs/claude-ref/` remove feature-flag references
- **Deliverable:** Docs + operator tools ready; migration safe to deploy

### Risks (Phase 1)
- **MEDIUM:** Old configs still reference `spec.features.*` — mitigate with auto-converter script
- **LOW:** Shim performance — mitigate with inline caching (O(1) lookup)

### Success Criteria
- ✅ All feature flag calls proxied through shim
- ✅ New `corvin skills` CLI works end-to-end
- ✅ Telemetry shows <5% traffic on legacy path (week 4)
- ✅ Zero audit trail breakage
- ✅ >90% test coverage maintained

### Blocks
- None (Phase 1 self-contained)

---

## PHASE 2a: Skill Infrastructure (Weeks 5–10)

### Objective
Build Skill 2.0 runtime; extract L5 + L10 hardcoded logic into versioned Skills.

### Week-by-Week Breakdown

#### Week 5: Core Skill Manifests (ADR-0533)
- [ ] Implement `core/skills/skill_manifest.py` (immutable dataclass schema)
  - `id`, `name`, `version`, `description`
  - `entry_point`, `dependencies`, `config_schema`
  - `audit_events`, `tags`, `author`, `status`
- [ ] Validate manifest JSON schema (pytest)
- [ ] Create 5 example manifests (bundled Skills)
- **Deliverable:** Manifest schema + validation (ADR-0533 approved)

#### Week 6: Skill Registry (Audit-First)
- [ ] Implement `core/skills/skill_registry.py`:
  - Load manifests from disk + memory
  - Track enable/disable state (persistent JSON)
  - Emit audit events: `skill_loaded`, `skill_enabled`, `skill_disabled`
  - Hash-chain verification
- [ ] Implement `core/skills/skill_state.py` (versioned state machine)
- [ ] Tests: 15 E2E + 8 adversarial (race conditions, disk corruption)
- **Deliverable:** Registry working with audit trail

#### Week 7: Skill Execution Engine + Composition
- [ ] Implement `core/skills/skill_runtime.py`:
  - `execute(skill_id, input_dict, tenant_id)` → output
  - Emit `SkillExecutedEvent` (input, output, latency, lom)
  - Timeout isolation (30s default)
  - Error handling + retry policy
- [ ] Implement `core/skills/skill_composition.py`:
  - DAG validator (topological sort of dependencies)
  - Circular dependency detection
- [ ] Tests: 20 E2E (execution correctness) + 10 adversarial (timeout, crash)
- **Deliverable:** Runtime execution working; composition validated

#### Week 8: First Skill: os.delegation_router (L5)
- [ ] Extract `core/orchestration/context_bridge.py` logic → Skill
- [ ] Create `core/skills/os_skills/delegation_router.py`:
  - Input: `{project: str, task_type: str, complexity: int, urgency: str}`
  - Output: `{engine: str, confidence: float, reason: str}`
  - Deterministic: hardcoded decision tree (no LLM yet)
- [ ] Wire into L5 call site:
  ```python
  if skills.is_enabled("os.delegation_router"):
      route = skills.execute("os.delegation_router", task_context)
  else:
      route = legacy_hardcoded_route(task_context)
  ```
- [ ] Tests: 15 E2E (routing correctness) + 8 adversarial (injection, timeout)
- [ ] Audit: verify every routing decision is logged + hash-chained
- **Deliverable:** L5 wired to first Skill; audit trail proves decisions

#### Week 9: Second Skill: os.context_adapter (L10)
- [ ] Extract `operator/context_engineering/pipeline.py` stages → Skill
- [ ] Create `core/skills/os_skills/context_adapter.py`:
  - Input: `{user_id: str, task: dict, context_window: int}`
  - Output: `{context: dict, preserved_fields: list, added_fields: list}`
  - Multi-stage: snapshot → preserve → add (as Skill config)
- [ ] Wire into L10:
  ```python
  if skills.is_enabled("os.context_adapter"):
      ctx = skills.execute("os.context_adapter", user_task_context)
  else:
      ctx = legacy_context_engineering_pipeline(user_task_context)
  ```
- [ ] Add audit events:
  - `context_snapshot_taken` (ContextAPI)
  - `context_adapted` (ContextAPI)
  - `memory_lookup_executed` (MemoryCoordinator)
- [ ] Tests: 20 E2E (context preservation) + 10 adversarial (PII leakage, circular refs)
- **Deliverable:** L10 wired to Skill; context + memory audited

#### Week 10: Learning Loop Wiring (ADR-0314 + ADR-0534)
- [ ] Wire Skill execution events → learning event emitter
- [ ] Implement `core/skills/skill_feedback.py`:
  - Accept user feedback on Skill decisions
  - Update Skill confidence scores
  - Log feedback as audit event
- [ ] Add learning dashboard panel (minimal MVP):
  - Show top 3 Skills by execution count
  - Show confidence trend over 7 days
- [ ] Tests: 10 E2E (feedback loop) + 5 adversarial (stale feedback, convergence)
- **Deliverable:** Feedback → learning integration working

### Risks (Phase 2a)
- **HIGH:** Skill execution latency adds overhead (mitigate: async queue, batch writes)
- **MEDIUM:** DAG circular dependencies hard to detect (mitigate: validator + tests)
- **LOW:** First two Skills logic may diverge from hardcoded (mitigate: A/B test)

### Success Criteria
- ✅ Core Skill infrastructure passes 45 E2E + 30 adversarial tests
- ✅ L5 + L10 hardcoded logic extracted to Skills
- ✅ Every Skill decision audited + hash-chained
- ✅ Learning loop integrated (feedback → confidence score update)
- ✅ Performance <50ms per Skill execution (measured)
- ✅ ADR-0533/0534 approved + docs updated

### Blocks
- ADR-0533 (Skill manifest schema) must be approved
- ADR-0534 (Learning feedback integration) must be approved

---

## PHASE 2b: Plugin → Skills Migration (Weeks 11–18)

### Objective
Migrate top 10 plugins to Skills; build backward-compat shim.

### Week-by-Week Breakdown

#### Week 11: Plugin Analysis + Priority
- [ ] Run telemetry query: which plugins used most by operators?
- [ ] Rank top 10 by:
  1. Usage frequency (calls/day)
  2. Code complexity (lines of code)
  3. Test coverage
  4. Security sensitivity
- [ ] Identify low-complexity, high-impact plugins (migrate first)
- **Deliverable:** Priority list + migration plan per plugin

#### Weeks 12–17: Plugin-to-Skill Migration (6 weeks, 1–2 plugins/week)
Each plugin follows this pattern:

**Plugin 1–3 (Weeks 12–14):** Low-complexity plugins
- Extract plugin logic → `core/skills/os_skills/plugin_xyz.py`
- Create Skill manifest (inherit from plugin metadata)
- Add audit events (copy plugin-specific operations)
- Wire backward-compat shim (`PluginToSkillAdapter`)
- Tests: 25 E2E (plugin feature parity) + 15 adversarial (config, error handling)
- Deployment: gradual canary (10% → 50% → 100% of users)

**Plugin 4–6 (Weeks 15–16):** Medium-complexity plugins
- Repeat above; add learning-specific config (confidence tuning, timeout adaptation)

**Plugin 7–10 (Week 17):** Complex plugins or optional ones
- Migrate remaining; some may stay as "plugin shims" if too large

#### Week 18: Backward-Compat Shim
- [ ] Implement `PluginToSkillAdapter`:
  ```python
  # Old: plugin_registry.execute(plugin_id, input)
  # New: skills.execute(f"os.{plugin_id}", input)  # Adapter layer wraps calls
  ```
- [ ] Dual-mode startup: load old + new simultaneously
- [ ] Health check: verify both paths return same results
- [ ] Telemetry: count calls to old path (target: <5% by week 18)
- **Deliverable:** Both systems running in parallel; switchover ready

### Risks (Phase 2b)
- **HIGH:** Large plugins complex to migrate (mitigate: break into sub-Skills)
- **HIGH:** Backward-compat shim adds latency (mitigate: cache adapter results)
- **MEDIUM:** Some plugins tightly coupled to old plugin system (mitigate: identify early, plan deep refactor)

### Success Criteria
- ✅ Top 10 plugins migrated to Skills
- ✅ 25 E2E tests per plugin (all green)
- ✅ Backward-compat shim passes integration tests
- ✅ <5% traffic on old plugin path (week 18)
- ✅ No audit trail breakage
- ✅ Operator manual updated

### Blocks
- Phase 2a must be complete (Skill runtime + feedback loop)
- ADR-0535 (Skill composition/DAG) must be approved

---

## PHASE 3: Final Decommission (Weeks 19–24)

### Objective
Remove old plugin system entirely; complete L-layer migration; cleanup.

### Week-by-Week Breakdown

#### Week 19: Remaining Plugins + L22/L28 Extraction
- [ ] Migrate final 5+ plugins (repeating phase 2b pattern)
- [ ] Extract L22 (workflow) → `os.workflow_optimizer` Skill
- [ ] Extract L28 (memory recall) → `os.memory_recall` Skill
- [ ] Tests: 30 E2E + 15 adversarial per Skill
- **Deliverable:** All plugins migrated; L22/L28 extracted

#### Week 20: L34 (Data Flow Guard) Extraction
- [ ] Extract L34 validators → `os.flow_guard` Skill
- [ ] Skill learns safe data shapes from denial patterns (feedback-based)
- [ ] Tests: 20 E2E + 10 adversarial (injection detection, convergence)
- **Deliverable:** L34 Skill working; all L-layers now versioned

#### Week 21: Manual Wiring Refactor (app.py)
- [ ] Create `core/console/route_loader.py`:
  ```python
  async def load_skills_as_routes(registry: SkillRegistry) -> APIRouter:
      # Discover routes from Skill manifests (not hardcoded imports)
  ```
- [ ] Convert ~50 route modules to Skills incrementally
- [ ] Tests: 30 E2E (route discovery + execution)
- **Deliverable:** Route loading dynamic (Skills-based)

#### Week 22: Shim Removal + Core System Deletion
- [ ] Delete `FeatureFlagLegacyAdapter` (all code now uses Skill registry)
- [ ] Delete `PluginToSkillAdapter` (all plugins now Skills)
- [ ] Delete feature flag files:
  - `core/console/corvin_core/feature_flags.py` (1,528 lines)
  - `core/vibe_engineering/feature_flags.py` (177 lines)
  - `core/console/corvin_console/promotion_daemon.py` (300 lines)
  - Feature flag tests (~500 lines)
- [ ] Delete old plugin system:
  - `core/plugins/corvin_plugins/` (45,000+ lines)
  - Legacy plugin registry (178 lines)
  - Plugin tests (5,000+ lines)
- [ ] Update imports in ~50 files (remove feature flag + plugin references)
- **Deliverable:** ~52,000 lines of dead code removed; full test suite still green

#### Week 23: CLI + Admin Routes Cleanup
- [ ] Delete old CLI command files:
  - `ops/launcher/corvin/flag_commands.py` (163 lines)
  - `ops/launcher/corvin/plugin_cmd.py` (25KB)
  - `ops/launcher/corvin/plugin_runtime_cmd.py` (13KB)
- [ ] Refactor admin routes (keep structure, rewire to Skill API):
  - `core/console/corvin_console/routes/admin.py` (914 lines) → keep, update internals
- [ ] Consolidate into single `corvin skills` CLI entry point
- **Deliverable:** Admin API / CLI unified; legacy commands gone

#### Week 24: Documentation + Signoff
- [ ] Update all layer references in `docs/claude-ref/`:
  - `layer-5-routing.md` → "Implemented by os.delegation_router Skill"
  - `layer-10-context.md` → "Implemented by os.context_adapter Skill"
  - etc.
- [ ] Create migration guide for operators (how to upgrade from Phase 1 → Phase 3)
- [ ] Performance baseline: measure Skill runtime overhead (target: <50ms)
- [ ] Final audit: verify all 3 Audit-Chain-as-Proof assertions hold:
  - ✅ Every Skill decision has audit event
  - ✅ Hash chain integrity verified (no gaps)
  - ✅ Tenant isolation enforced
- [ ] Release notes: "CorvinOS 2.0: Skills-Based Control Plane"
- **Deliverable:** Full documentation; production-ready release

### Risks (Phase 3)
- **HIGH:** Large-scale deletion could break unforeseen dependencies (mitigate: extensive testing + phased rollout)
- **MEDIUM:** Admin routes complexity (mitigate: careful refactor + parallel testing)

### Success Criteria
- ✅ ~52,000 lines of dead code removed
- ✅ All L-layers now Skills (5 Skills: routing, context, workflow, memory, flow-guard)
- ✅ Feature flags completely gone (no leftover references)
- ✅ Old plugin system deleted
- ✅ All routes dynamic (no hardcoded imports in app.py)
- ✅ >95% test coverage maintained
- ✅ Performance <50ms/Skill overhead (measured)
- ✅ Full audit trail coverage (every decision logged + verified)

### Blocks
- Phases 2a + 2b must be complete

---

## DELIVERY NOTES FOR EACH PHASE

### Phase 1 (Weeks 1–4): Feature Flags

**Who:** 1–2 engineers  
**Effort:** ~40 hours  
**Complexity:** LOW (well-isolated system)  
**Risk:** LOW  

**Deliverables:**
1. `FeatureFlagLegacyAdapter` (300 lines)
2. New `corvin skills` CLI (200 lines)
3. Updated admin routes (100 lines)
4. Tests (300+ lines)
5. Operator manual + migration script

**Deployment:** Safe to deploy immediately; can run in parallel with Phase 2.

---

### Phase 2a (Weeks 5–10): Skill Infrastructure + First 2 Skills

**Who:** 2–3 engineers  
**Effort:** ~120 hours  
**Complexity:** MEDIUM (new architecture, needs design review)  
**Risk:** MEDIUM (first-time Skill implementation)  

**Deliverables:**
1. Skill manifest schema + registry (~1,500 lines)
2. Skill runtime + composition (~1,200 lines)
3. `os.delegation_router` Skill (~300 lines)
4. `os.context_adapter` Skill (~500 lines)
5. Learning loop integration (~400 lines)
6. Tests (~2,000+ lines)

**Deployment:** Requires ADR-0533/0534 approval. Can run in parallel with Phase 1 (separate branch).

---

### Phase 2b (Weeks 11–18): Plugin Migration

**Who:** 2–3 engineers + QA  
**Effort:** ~240 hours  
**Complexity:** MEDIUM (repetitive, but each plugin unique)  
**Risk:** MEDIUM (backward-compat risk if shim breaks)  

**Deliverables:**
1. Plugin analysis + prioritization (~100 hours)
2. 10 plugin → Skill conversions (~120 hours)
3. Backward-compat shim (~40 hours)
4. Tests & validation (~40 hours)
5. Canary rollout plan

**Deployment:** Requires Phase 2a complete. Canary rollout (10% → 50% → 100% over 2–3 weeks).

---

### Phase 3 (Weeks 19–24): Final Decommission

**Who:** 2 engineers + release manager  
**Effort:** ~160 hours  
**Complexity:** HIGH (large-scale deletion + refactoring)  
**Risk:** HIGH (breaking changes possible)  

**Deliverables:**
1. Remaining plugin migrations (~80 hours)
2. L-layer extraction (L22/L28/L34) (~40 hours)
3. app.py refactoring (~30 hours)
4. Shim + old system deletion (~20 hours)
5. Documentation (~20 hours)
6. Final testing + sign-off (~40 hours)

**Deployment:** Requires Phase 2 complete. Recommend blue-green deploy + rollback plan.

---

## CHECKPOINT GATES

| Week | Gate | Acceptance Criteria |
|---|---|---|
| **End of Week 4** | Phase 1 Complete | Feature flags working with Skill registry; <5% legacy traffic |
| **End of Week 10** | Phase 2a Complete | Core Skill runtime + first 2 Skills live; audit trail verified |
| **End of Week 18** | Phase 2b Complete | Top 10 plugins migrated; backward-compat shim passing tests |
| **End of Week 24** | Phase 3 Complete | Old systems deleted; all L-layers Skills; production release ready |

---

## ROLLBACK PLAN

**If Phase N fails:** Revert to previous stable phase. Each phase is independently deployable.

- **Phase 1 rollback:** Disable feature-flag shim; revert `corvin skills` CLI to `corvin flag`
- **Phase 2a rollback:** Disable Skill runtime; hardcoded L5/L10 logic still works
- **Phase 2b rollback:** Disable `PluginToSkillAdapter`; old plugin system still loaded
- **Phase 3 rollback:** If large deletion breaks something, restore from branch; investigate + fix + re-deploy

---

## SUCCESS METRICS (End-of-Phase-3)

| Metric | Target | Measure |
|---|---|---|
| Dead code removed | 52,000+ lines | `git diff main..phase-3 --stat` |
| Test coverage | >95% | `pytest --cov` |
| Skill runtime overhead | <50ms per decision | Benchmark suite |
| Audit trail gaps | 0 | `verify_audit_chain.py --tenant=_default` |
| Feature flag references | 0 | `grep -r "spec.features" .` |
| Plugin system files | 0 | `find . -path "*/plugins/corvin_plugins" | wc -l` |
| Operator upgrade pain | None | Manual + automation script provided |
| Skill ecosystem maturity | v1.0 stable | 5+ Skills shipping; ADR-0532–0535 closed |

---

**Prepared by:** Claude Code (Haiku 4.5) | **Date:** 2026-09-01  
**Next Steps:** Present to architecture review; gate on ADR-0532–0535 approval
