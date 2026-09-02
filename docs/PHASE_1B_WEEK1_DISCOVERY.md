# Phase 1b Week 1: Call-Site Discovery + Wave 1 Refactoring Plan

**Date:** Sept 2, 2026 (Autonomous Execution)  
**Status:** EXECUTION IN PROGRESS (LDD k=1 → k=2)

---

## k=1 DISCOVERY RESULT: 24 Files, ~150 Total Call-Sites

### Refactoring Targets (k=2 Wave 1 — 8 Files Actually Refactored)

Files with actual feature_flags API calls in production code:

| Rank | File | Calls | Type | Refactored |
|------|------|-------|------|-----------|
| 1 | operator/bridges/shared/adapter.py | 13 | Production | ✅ YES |
| 2 | tests/test_tde_measurement_k3_decision_collection.py | 2 | Test | ✅ YES |
| 3 | operator/bridges/shared/remote_trigger_sender.py | 2 | Production | ✅ YES |
| 4 | operator/context_engineering/pipeline.py | 1 | Production | ✅ YES |
| 5 | operator/bridges/shared/bg_monitor.py | 1 | Production | ✅ YES |
| 6 | operator/bridges/shared/acs_runtime.py | 1 | Production | ✅ YES |
| 7 | operator/bridges/shared/a2a_friendship.py | 1 | Production | ✅ YES |
| 8 | core/console/corvin_console/routes/settings.py | 0* | Production | ⏭️ NO |

**Total Wave 1 Actual Calls:** 21 (refactored in k=2 automation attempt)

**Reconciliation Note (Layer 3 Fix):**
- Initial discovery claims included wrapper + skills implementation + tests
- Actual refactoring targets = production code only (8 files, 21 calls)
- Wrapper/skills/test files excluded from Wave 1 (they're infrastructure, not refactoring targets)

**Files Excluded (Infrastructure):**
- `feature_flags_legacy_adapter.py` (46 calls) — wrapper layer, no refactoring
- `feature_flags_skill.py` (36 calls) — implementation, no refactoring
- `test_feature_flags_equivalence_template.py` (29 calls) — test framework, auto-updated

---

## Refactoring Patterns (Old → New)

### Pattern 1: is_enabled()
```python
# OLD
from corvin_core.feature_flags import is_enabled
if is_enabled("vibe_engineering"):
    context_engine()

# NEW
from core.skills.feature_flags_skill import feature_flags_skill
result = feature_flags_skill.execute({
    "operation": "is_enabled",
    "flag_id": "vibe_engineering",
    "tenant_id": tenant_id  # or "_default"
})
if result["success"] and result["result"]["enabled"]:
    context_engine()
```

### Pattern 2: set_enabled()
```python
# OLD
set_enabled("my_flag", True, tenant_id="tenant_a")

# NEW
feature_flags_skill.execute({
    "operation": "set_enabled",
    "flag_id": "my_flag",
    "enabled": True,
    "tenant_id": "tenant_a"
})
```

### Pattern 3: describe_all()
```python
# OLD
flags = describe_all()

# NEW
result = feature_flags_skill.execute({
    "operation": "describe_all",
    "tenant_id": "_default"
})
flags = result["result"]["flags"] if result["success"] else []
```

---

## Automation Strategy (k=2)

**Build:** Python AST-based refactoring tool (Forge)

**What it does:**
1. Parse each Wave 1 file's AST
2. Find all is_enabled/set_enabled/describe_all/tier_of calls
3. Rewrite with skill.execute() pattern
4. Handle tenant_id inference (thread-local, function arg, default)
5. Update import statements
6. Run equivalence tests per file

**Output:** All 12 Wave 1 files refactored + committed

---

## Next Steps (k=2 → k=3)

- **k=2:** Build + run Forge automation on Wave 1 (2–3h)
- **k=3:** Update test suite + verify + final report (1h)

**Timeline:** Week 1 completion by EOD Sept 2

---

## Related Artifacts

- Spike 1 Final Report: `docs/SPIKE_1_FINAL_REPORT_SEPT4.md`
- Phase 1b Rollout Plan: `docs/SPIKE_1_PHASE2_ROLLOUT_PLAN.md`
- Skills Implementation: `core/skills/feature_flags_skill.py`
- Wrapper Adapter: `core/console/corvin_core/feature_flags_legacy_adapter.py`

