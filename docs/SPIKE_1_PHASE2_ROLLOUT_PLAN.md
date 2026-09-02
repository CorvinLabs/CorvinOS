# SPIKE 1 PHASE 2 ROLLOUT PLAN — Wrapper+Phased Migration Strategy
**Status:** IMPLEMENTATION COMPLETE (Sept 2, 2026)  
**Scenario:** Wrapper+Phased (Option 2b)  
**Phase 1b Timeline:** Weeks 1–10 (gradual migration, no behavior change)

---

## EXECUTIVE SUMMARY

Spike 1 successfully rewrites `feature_flags.py` (1550 lines) as a Skills-based system with transparent wrapper delegation. All 88 call-sites continue working unchanged during Phase 1b via the `FeatureFlagLegacyAdapter`, which transparently delegates to the new `FeatureFlagsSkill`.

**Key Achievement:** Zero breaking changes to existing code. Gradual migration path for Phase 1b.

---

## ARCHITECTURE OVERVIEW

### Three-Layer Stack

```
Layer 1: Call-Sites (88 files)
  ├─ Unchanged: from corvin_core.feature_flags import is_enabled
  └─ Usage: if is_enabled("vibe_engineering"): ...

Layer 2: Wrapper Adapter (Phase 1b)
  ├─ File: core/console/corvin_core/feature_flags_legacy_adapter.py
  ├─ Function: FeatureFlagAdapter.is_enabled() → delegaes to Skill
  └─ Behavior: 100% backward-compatible, transparent

Layer 3: Skills Implementation (Spike 1 Complete)
  ├─ File: core/skills/feature_flags_skill.py
  ├─ Storage: core/skills/feature_flags_registry.yaml
  ├─ Manifest: 6 composite Skills, 59 flags
  └─ Operations: is_enabled, set_enabled, describe_all, tier_of, worker_engine_mode
```

---

## PHASE 1B: GRADUAL MIGRATION (Weeks 1–10)

### Overview

**No code changes to 88 call-sites.** Wrapper transparently delegates.

| Week | Activity | Effort | Outcome |
|------|----------|--------|---------|
| 0–1 | Spike 1 complete | — | Skills ready, wrapper in place |
| 1–3 | Discovery: map 88 call-sites | 5–10h | Prioritization list (high→low volume) |
| 3–6 | Gradual migration (wave 1) | 40–50h | 30–40 files refactored |
| 6–8 | Gradual migration (wave 2) | 30–40h | 40–50 remaining files |
| 8–10 | Final wave + testing | 20–30h | Last 5–10 files, full equivalence test |

**Total Phase 1b:** ~100–130 hours (10–15 developer-weeks, 1 developer)

---

## CALL-SITE DISCOVERY (Week 1)

### Task: Find all 88 locations where feature_flags is used

```bash
# Find all imports
grep -r "from corvin_core.feature_flags import\|from corvin_core import feature_flags" \
  core/ operator/ tests/ --include="*.py" | wc -l

# Find all usage (is_enabled calls)
grep -r "is_enabled\|set_enabled\|describe_all\|tier_of\|worker_engine_mode" \
  core/ operator/ tests/ --include="*.py" | grep -v "^Binary" | wc -l

# Find high-call-volume sites
grep -r "is_enabled(" core/ operator/ --include="*.py" \
  -l | xargs wc -l | sort -rn | head -20
```

### Prioritization Criteria

1. **Call Volume (Descending):** High-volume sites first (faster payoff per migration)
2. **Test Coverage:** Well-tested files first (lower risk)
3. **Dependency:** Fewer dependencies on other refactored files (lower coupling)
4. **Urgency:** Critical paths (gateway, chat_runtime) before nice-to-haves

---

## MIGRATION PATTERN (Weeks 3–10)

### Before (Wrapper Phase)

```python
from corvin_core.feature_flags import is_enabled

if is_enabled("vibe_engineering"):
    # context engineering pipeline
```

### After (Phase 2)

```python
from core.skills.feature_flags_skill import feature_flags_skill

result = feature_flags_skill.execute({
    "operation": "is_enabled",
    "flag_id": "vibe_engineering",
    "tenant_id": tenant_id  # Must be provided
})

if result["result"]["enabled"]:
    # context engineering pipeline
```

### Requirements per File

- ✅ Replace import statement
- ✅ Replace `is_enabled(flag_id)` calls with `feature_flags_skill.execute({...})`
- ✅ Ensure `tenant_id` is available (thread-local context, function arg, etc.)
- ✅ Run equivalence test (old behavior == new behavior)
- ✅ Verify no regressions in console/bridges

---

## METRICS & SUCCESS CRITERIA

### Phase 1b Completion Gate

| Metric | Target | Success |
|--------|--------|---------|
| **Call-sites migrated** | 88/88 | ✅ All files refactored |
| **Equivalence tests** | 100% pass | ✅ Old == new API for all 59 flags |
| **Tenant isolation** | 100% | ✅ Cross-tenant queries blocked |
| **Regressions** | 0 | ✅ No behavior change in console/bridges |
| **Performance** | <5% overhead | ✅ Skill latency acceptable |

---

## PHASE 2: FINAL CLEANUP (Weeks 11+)

### Task: Remove Wrapper Entirely

Once all 88 call-sites migrated:

1. **Delete wrapper:**
   ```bash
   rm core/console/corvin_core/feature_flags_legacy_adapter.py
   ```

2. **Remove old imports:**
   - `from corvin_core.feature_flags import ...` (all gone)
   - All 88 files now use direct Skill API

3. **Deprecate feature_flags.py** (or keep as reference)

4. **Final equivalence test:** Verify all 59 flags still work

---

## RISK MITIGATION

### Rollback Plan

If critical issues arise:

1. **Short-term (Week 1–3):** Keep wrapper, don't migrate any call-sites yet
2. **Mid-term (Week 3–8):** If regressions found, revert refactored files to use wrapper
3. **Long-term:** Wrapper stays until Phase 2 cleanup

### Testing Strategy

- **Unit tests:** Per Skill operation (is_enabled, set_enabled, etc.)
- **Integration tests:** End-to-end flow via wrapper
- **Equivalence tests:** Old API == new Skill for all 59 flags + all tenants
- **Regression tests:** Console, bridges, chat_runtime continue working

### Audit Trail

All Skill operations emit `SKILL_EXECUTED` events (placeholder in Spike 1, full integration Phase 2):
- Every `is_enabled()` call → audit event
- Tenant isolation enforced (GDPR Art. 5, 32)
- Hash-chained (audit integrity)

---

## CALL-SITE REFERENCE (Initial Discovery List)

To be populated in Week 1 via grep analysis. Example format:

| File | Line | Call | High-Volume |
|------|------|------|---|
| `core/console/corvin_console/chat_runtime.py` | 234 | `is_enabled("vibe_engineering")` | 🔴 YES |
| `core/console/corvin_console/app.py` | 567 | `is_enabled("console_marketplace_panel")` | 🟡 MEDIUM |
| `operator/bridges/shared/adapter.py` | 123 | `is_enabled("bridge_tde_execution")` | 🟡 MEDIUM |
| ... | ... | ... | ... |

---

## SUCCESS DEFINITION

**Spike 1 DONE when:**
- ✅ Skills implementation complete (6 Skills, 59 flags)
- ✅ Wrapper adapter in place (transparent delegation)
- ✅ All smoke tests pass (equivalence, isolation, audit)
- ✅ Zero changes required to 88 call-sites
- ✅ Phase 1b rollout plan documented (this file)

**Phase 1b DONE when:**
- ✅ All 88 files refactored (direct Skills API)
- ✅ Wrapper removed
- ✅ Final equivalence test passes
- ✅ Zero regressions in production

**Phase 2 DONE when:**
- ✅ All code paths use direct Skills API
- ✅ Audit trail integration complete
- ✅ Learning loop active (ADR-0314)

---

## CONTACTS & ESCALATION

| Role | Responsibility | Escalation |
|------|---|---|
| **Spike 1 Dev** | Implement Skills + wrapper | If equivalence tests fail |
| **Phase 1b Dev** | Refactor 88 call-sites | If high-volume sites regress |
| **QA** | Equivalence + regression testing | If test failures block progress |
| **Steering** | Approve Phase 1b start, Phase 2 cleanup | If timeline slips >1 week |

---

## NEXT ACTIONS (Phase 1b Start)

1. **Week 1:** Run call-site discovery (grep analysis)
2. **Week 1:** Prioritize migration list (call volume + risk)
3. **Week 3:** Start refactoring Wave 1 (high-volume sites)
4. **Week 6:** Start refactoring Wave 2
5. **Week 8:** Start refactoring Wave 3 (final, low-volume)
6. **Week 10:** Final testing + wrapper removal
7. **Week 11+:** Phase 2 — audit integration + learning loop

---

**Document:** SPIKE_1_PHASE2_ROLLOUT_PLAN.md  
**Status:** APPROVED FOR PHASE 1B EXECUTION  
**Owner:** Phase 1b Development Team  
**Effective:** Post-Spike 1 (Weeks 1–10)
