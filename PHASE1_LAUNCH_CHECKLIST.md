# Phase 1 Launch Checklist (ACP Vision)

**Feature Flags Deprecation + Skills Registry Migration**
**Timeline:** 4 weeks (Weeks 1–4)  
**ADR:** ADR-0543  
**Status:** READY FOR IMPLEMENTATION ✅  
**Target Go-Live:** Week 1 (after Planning + Infrastructure)

---

## Pre-Launch (Before Week 1)

### Architecture Review

- [ ] ADR-0543 approved by architecture team
- [ ] ADR-0532 Phase 0 (Bootstrap) approved
- [ ] ADR-0533 (Manifest Schema) scoped for Phase 2a
- [ ] No blockers from compliance/security

### Team Allocation

- [ ] 1–2 engineers assigned (feature flag deprecation)
- [ ] 1 QA engineer assigned (testing both systems)
- [ ] 1 tech writer assigned (docs, migration guide)
- [ ] 1 tech lead assigned (integration oversight)

### Environment Setup

- [ ] CorvinOS repo cloned locally
- [ ] Python 3.9+ installed with required packages
- [ ] Test suite runs locally: `pytest core/console/tests/ -v`
- [ ] Linting passes: `ruff check core/console/` + `mypy --strict`

---

## WEEK 1: Planning + Infrastructure

### Task 1.1: Create FeatureFlagLegacyAdapter Shim

- [x] Stub created: `core/console/corvin_core/feature_flag_adapter.py`
- [ ] Implementation complete:
  - [ ] `query(flag_id, min_version=None) → FeatureFlagQueryResult`
  - [ ] Skill registry routing (when available)
  - [ ] Legacy config fallback
  - [ ] Cache with O(1) lookup (with stats)
  - [ ] Audit event emission (_emit_audit_event)
- [ ] Unit tests: ≥10 tests, all green
  - [ ] test_adapter_initialization
  - [ ] test_query_unknown_flag
  - [ ] test_query_with_skill_registry
  - [ ] test_fallback_when_unavailable
  - [ ] test_cache_behavior
  - [ ] test_cache_clear
  - [ ] test_migration_mode_logging
  - [ ] test_version_constraint
  - [ ] test_tenant_isolation
  - [ ] (2 more: edge cases)
- [ ] Code review: 0 blockers
- [ ] Linting passes

### Task 1.2: Add Migration Telemetry

- [ ] Add `--migration-mode` flag to feature flag calls
- [ ] Telemetry baseline captured (week 1 end):
  - [ ] Count calls via new path (Skill registry) vs old path (legacy config)
  - [ ] Cache hit rate measured
  - [ ] Audit events logged
- [ ] Dashboard/logging shows telemetry (human-readable)

### Task 1.3: Config Migration Script

- [ ] Script created: `scripts/migrate_flags_to_skills.py`
- [ ] Functionality:
  - [ ] Read `spec.features.*` from `tenant.corvin.yaml`
  - [ ] Convert to Skill manifest entries
  - [ ] Validate output config
  - [ ] Option to apply changes (with backup)
- [ ] Script tested on sample config: `operator/bundle/config-templates/tenant.corvin.yaml`
- [ ] User-friendly output (clear warnings, examples)

### Task 1.4: E2E Tests (Phase 1 Go Gate)

- [x] E2E test stub: `core/console/tests/test_feature_flag_adapter_e2e.py`
- [ ] Tests implemented (≥5):
  - [ ] test_e2e_old_api_to_skill_registry (CORE: old → new routing)
  - [ ] test_a_b_testing_both_systems (CORE: equivalence test)
  - [ ] test_telemetry_migration_tracking
  - [ ] test_phase1_go_criteria_cache_performance
  - [ ] (1 more: operator-level test)
- [ ] All tests green

### Week 1 Go-Criteria (EOW Gate)

- [ ] Shim is transparent (no behavioral change to existing code)
- [ ] Telemetry baseline captured (baseline = 100% legacy path, 0% new path)
- [ ] 5+ E2E tests passing
- [ ] Full test suite passes (`pytest core/console/tests/`)
- [ ] Code review: 0 blockers
- [ ] **Commit:** "feat(phase1): Feature flag adapter + telemetry baseline [ADR-0543]"

---

## WEEK 2: Admin Routes Consolidation

### Task 2.1: New Skill Admin API

- [x] CLI stub created: `ops/launcher/corvin/skills_cmd.py`
- [ ] API endpoints created (all in `core/console/corvin_console/routes/admin.py`):
  - [ ] `GET /api/admin/skills/` — list Skills
  - [ ] `GET /api/admin/skills/{skill_id}` — Skill details
  - [ ] `POST /api/admin/skills/{skill_id}/enable` — enable Skill
  - [ ] `POST /api/admin/skills/{skill_id}/disable` — disable Skill
  - [ ] `PUT /api/admin/skills/{skill_id}/config` — set Skill config
- [ ] Both old and new endpoints working (side-by-side)
- [ ] API documentation updated (OpenAPI spec)

### Task 2.2: New Skills CLI

- [ ] CLI implementation complete (all commands):
  - [ ] `corvin skills list [--enabled-only] [--format json]`
  - [ ] `corvin skills enable {skill_id} [--confirm]`
  - [ ] `corvin skills disable {skill_id} [--confirm]`
  - [ ] `corvin skills config {skill_id} [--set key=value] [--show]`
  - [ ] `corvin skills show {skill_id} [--audit-trail] [--metrics]`
- [ ] CLI help system complete: `corvin skills --help` shows all commands
- [ ] Deprecation warning on old CLI: `corvin flag` prints "deprecated, use `corvin skills` instead"

### Task 2.3: Integration Tests

- [ ] 8+ integration tests created:
  - [ ] test_cli_list_skills
  - [ ] test_cli_enable_skill
  - [ ] test_cli_disable_skill
  - [ ] test_cli_configure_skill
  - [ ] test_api_endpoint_list_skills
  - [ ] test_api_endpoint_enable_skill
  - [ ] test_both_systems_equivalent (old API vs new CLI)
  - [ ] (1 more: error handling)
- [ ] All tests green
- [ ] A/B mode: both old and new paths working simultaneously

### Week 2 Go-Criteria (EOW Gate)

- [ ] New Skill endpoints live in `/api/admin/skills/`
- [ ] New `corvin skills` CLI works end-to-end
- [ ] Old `corvin flag` CLI still works (backward-compat)
- [ ] 8+ integration tests passing
- [ ] Full test suite passes
- [ ] Code review: 0 blockers
- [ ] **Commit:** "feat(phase1): Skills admin API + CLI [ADR-0543 Week 2]"

---

## WEEK 3: Scope Expansion + Call-Site Wrapping

### Task 3.1: Audit Call-Sites

- [ ] Grep scan: find all references to `spec.features`, `flag(`, `feature_flag(`
- [ ] List generated: all ~20 files that call feature flag logic
- [ ] Each call-site categorized (low-risk, medium-risk, high-risk)

### Task 3.2: Wrap Call-Sites

For each call-site (~20 files):
- [ ] Replace direct calls with shim: `legacy_flag("flag_id")` instead of direct access
- [ ] Add deprecation comment: `# TODO: migrate to Skill registry by week 4`
- [ ] Test after each edit: local linting + unit test for that module
- [ ] Update related tests to validate both paths

Example:
```python
# OLD (deprecated)
if config.features.vibe_engineering:
    enable_vibe_mode()

# NEW (via shim)
if legacy_flag("vibe_engineering_v0_2"):
    enable_vibe_mode()
```

### Task 3.3: Telemetry Snapshot (Week 3 End)

- [ ] Run full test suite in "both systems" mode
- [ ] Telemetry captured:
  - [ ] % of calls hitting new path (target: ≥5%)
  - [ ] % of calls hitting legacy path (target: ≤95%)
  - [ ] Cache hit rate
  - [ ] No audit trail gaps
- [ ] Dashboard shows metrics (can be simple CSV/JSON)
- [ ] Trend: telemetry should show **migration progress** (new path % increasing)

### Task 3.4: Regression Testing

- [ ] Full test suite runs: `pytest core/console/ operator/launcher/ -v`
- [ ] Code coverage ≥90% (new + modified code)
- [ ] No test failures
- [ ] Integration tests all green (A/B mode)

### Week 3 Go-Criteria (EOW Gate)

- [ ] All ~20 call-sites wrapped
- [ ] Telemetry shows >5% new path traffic (migration working)
- [ ] Full test suite passes (>90% coverage)
- [ ] No audit trail breakage
- [ ] **Commit:** "refactor(phase1): Migrate all feature-flag call-sites [ADR-0543 Week 3]"

---

## WEEK 4: Production Readiness

### Task 4.1: Operator Manual

- [ ] Documentation file created: `docs/phase1-migration-guide.md`
- [ ] Contents:
  - [ ] What changed (old CLI → new CLI)
  - [ ] Migration script walkthrough
  - [ ] Example: migrating `spec.features.*` in config
  - [ ] Troubleshooting (what if script fails? rollback?)
  - [ ] Timeline: when old code will be deleted (week 22)
  - [ ] FAQ

### Task 4.2: Config Validation Script

- [ ] Script created: `scripts/validate_config_features.py`
- [ ] Validates:
  - [ ] No old `spec.features` keys in config
  - [ ] If found, suggests migration command
  - [ ] Runs on CI/CD (gates deployment if old config detected)
- [ ] Tested on sample configs

### Task 4.3: Deprecation Roadmap Docs

- [ ] File created: `docs/feature-flags-deprecation-timeline.md`
- [ ] Contents:
  - [ ] Phase 1 (now): Feature flags available, Skill registry available
  - [ ] Phase 2 (weeks 5–10): Skill infrastructure built
  - [ ] Phase 3 (weeks 19–24): Old code deleted
  - [ ] Migration path at each phase
  - [ ] Warnings: when to expect old code to stop working

### Task 4.4: Update Reference Docs

- [ ] Remove feature flag references from:
  - [ ] `docs/claude-ref/layer-5-routing.md` (update: routing now via Skill)
  - [ ] `docs/claude-ref/layer-10-context.md`
  - [ ] `docs/ADMIN_CONTROL_POINTS.md` (if public)
  - [ ] Any other layer docs that mention flags
- [ ] Replace with: "This layer is now implemented by the `os.<skill>` Skill (ADR-0543)"
- [ ] Add link to ADR-0543 + Phase 1 guide

### Task 4.5: Release Notes

- [ ] File created: `RELEASE_NOTES_PHASE1.md`
- [ ] Sections:
  - [ ] What's new (Skill CLI)
  - [ ] What's deprecated (Feature flag CLI)
  - [ ] How to migrate (link to operator manual)
  - [ ] Timeline (when old code deleted)
  - [ ] Support (where to report issues)

### Week 4 Go-Criteria (EOW Gate)

- [ ] Operator manual complete + tested by human
- [ ] Config validation script working
- [ ] Deprecation timeline documented
- [ ] All reference docs updated
- [ ] Release notes complete
- [ ] No audit trail breakage
- [ ] Full test suite still passes (regression test)
- [ ] **Commit:** "docs(phase1): Operator manual + deprecation timeline [ADR-0543 Week 4]"

---

## Final Phase 1 Go-Criteria (End of Week 4)

### Correctness

- [ ] **E2E-Wiring-Proof:** Feature flag API call → Skill registry query → Audit event
- [ ] **Backward-Compat:** Old code still works (no breaking changes)
- [ ] **A/B Equivalence:** Both paths produce identical results
- [ ] **Audit Trail:** All queries logged, no gaps, hash-chain intact

### Performance

- [ ] **Cache Performance:** O(1) lookup, >95% cache hit rate
- [ ] **Latency:** Query time <10ms (95th percentile)
- [ ] **No Regression:** Full app startup time unchanged

### Coverage

- [ ] **Unit Tests:** ≥75 tests passing, coverage ≥85%
- [ ] **E2E Tests:** ≥5 end-to-end scenarios, all green
- [ ] **Integration Tests:** Both systems in parallel, all green

### Security & Compliance

- [ ] **Audit Events:** 100% of queries emitted as immutable events
- [ ] **Tenant Isolation:** No cross-tenant leakage
- [ ] **No PII:** Audit events contain no plaintext flag values
- [ ] **Hash-Chain:** All audit events hash-chained, verifiable

### Documentation

- [ ] **ADR:** ADR-0543 complete + approved
- [ ] **Operator Manual:** Step-by-step migration guide
- [ ] **Release Notes:** User-facing changes documented
- [ ] **Code Comments:** All code has compliance notes (GDPR, ADR)

### Team Sign-Off

- [ ] Engineering sign-off (lead engineer)
- [ ] QA sign-off (all tests green, no known issues)
- [ ] Compliance sign-off (audit trail verified, GDPR/EU AI Act)
- [ ] Product sign-off (operator manual complete)

---

## If Phase 1 Fails (Escalation Path)

| Scenario | Action |
|---|---|
| Shim causes performance regression | Profile + optimize cache; if not fixable, revert to direct feature-flag queries (no shim) |
| Call-site audit misses references | Do a second grep scan; update remaining sites; re-run tests |
| Audit trail breaks | Investigate + fix hash-chain; confirm with `verify_audit_chain.py` before proceeding |
| Telemetry shows <5% new path traffic | Likely indicates call-sites not wrapped; grep for missed references |
| Test suite fails on A/B mode | Investigate equivalence; may indicate old and new paths diverged; fix + re-test |

---

## Handoff to Phase 2a

Once Phase 1 is complete (all go-criteria met):

1. **Phase 1 artifacts archived:**
   - ADR-0543 closed (ACCEPTED)
   - Deprecation timeline published
   - Operator manual in docs/

2. **Phase 2a begins (Skill Infrastructure):**
   - Build Skill manifest schema (ADR-0533)
   - Build Skill registry (audit-first)
   - Extract L5 routing → `os.delegation_router` Skill
   - Extract L10 context → `os.context_adapter` Skill
   - Wire learning loop (ADR-0314 integration)

3. **Shim status during Phase 2a:**
   - FeatureFlagLegacyAdapter stays active
   - All old code continues working
   - Skill infrastructure built in parallel
   - No operator disruption

4. **Phase 3 (later) deletes shim:**
   - Once all Skills live + confidence high
   - Shim removed
   - Old feature-flag code deleted
   - Migration complete

---

## Metrics to Track

| Metric | Baseline (Week 1) | Target (Week 4) | Success |
|---|---|---|---|
| % calls via new path | 0% | >5% | ✓ if metric increases |
| % calls via legacy path | 100% | <95% | ✓ if metric decreases |
| Cache hit rate | N/A | >95% | ✓ if sustained |
| Query latency (p95) | TBD | <10ms | ✓ if no regression |
| Test coverage | N/A | ≥85% | ✓ if met |
| Audit events emitted | 0 | 100% of queries | ✓ if verified |

---

## Communication Plan

### Week 1 Kick-Off

- [ ] Announce Phase 1 to operator community (blog post / email)
- [ ] Link to ADR-0543 + migration guide
- [ ] Timeline: 4 weeks to feature-flag removal (week 22)

### Week 2 (New CLI Available)

- [ ] Announce `corvin skills` CLI is live
- [ ] Deprecation warnings on `corvin flag` commands
- [ ] Suggest operators try new CLI

### Week 4 (Migration Guide Published)

- [ ] Publish operator manual + migration script
- [ ] FAQ + troubleshooting
- [ ] Support contact for issues

### Week 22 (Old Code Deleted)

- [ ] Announce feature-flag code removed
- [ ] Confirm migration complete
- [ ] Link to Phase 3 success metrics

---

## Sign-Off

**Phase 1 Launch Checklist v1.0**  
**Created:** 2026-09-01  
**Status:** READY FOR IMPLEMENTATION  
**Next Action:** Week 1 Kick-Off (architecture review + team allocation)

**Engineering Lead:** (to be assigned)  
**QA Lead:** (to be assigned)  
**Tech Writer:** (to be assigned)

---

**End of Checklist**
