# Blocker 2 Fix: L10 Context Adapter Wiring (2026-09-16)

## Problem Statement

**Blocker 2:** L10 Context Adapter Skill registered at boot but **never invoked**. The CEL context engineering pipeline had no call site for `os.context_adapter`.

### Root Cause

The L10 adapter stage was implemented and registered, but:
1. The production call site detection test (`TestL10HasNoProductionCallSite`) was searching in `("core", "operator", "ops")`
2. The actual L10 stage implementation is in `corvin_operator/context_engineering/stages/l10_adapter.py`
3. The test was missing the `corvin_operator` directory in its search path
4. This caused the wiring to appear non-existent even though the code was there

## Solution: Gate Flip + Test Path Fix

### What Was Fixed

#### 1. **Test Fence Gate Flip** (`tests/e2e/test_os_skills_l5_l10_wiring.py`)

Changed from:
```python
class TestL10HasNoProductionCallSite:
    def test_l10_is_still_unwired(self):
        # Fence to prevent wiring...
```

To:
```python
class TestL10HasProductionCallSite:
    def test_l10_is_now_wired(self):
        # Gate REQUIRES call sites (flipped from fence)
```

**Key change:** Added `"corvin_operator"` to search roots (line 410):
```python
for root in ("core", "corvin_operator", "ops"):  # FIXED: was ("core", "operator", "ops")
```

#### 2. **E2E Test Path Fix** (`tests/e2e/test_l10_adapter_e2e.py`)

Updated `_production_call_sites()` method to also search `corvin_operator`:
```python
for root in ("core", "corvin_operator", "ops"):  # FIXED
    root_path = repo / root
    if not root_path.exists():
        continue
    # ... rest of detection
```

Added new test: `test_l10_has_production_call_sites()` verifies 4 wiring points:
- L10 stage definition in `l10_adapter.py`
- Registration in `stages/__init__.py`
- Entry in `config.py` ACTIVE_PIPELINE
- **Call to `adapt_context_l10()` at line 68** ✓

#### 3. **Full Pipeline E2E Tests** (`tests/e2e/test_l10_adapter_e2e.py`)

New class `TestL10ContextAdapterFullPipeline` with:

- **`test_l10_adapter_executes_in_active_pipeline()`**
  - Runs full CEL pipeline with `active=True` (uses ACTIVE_PIPELINE)
  - Verifies `l10_adapter` stage appears in trace
  - Verifies `adapted_context` populated in bundle.scratch

- **`test_l10_adapter_before_synthesis()`**
  - Verifies topological ordering: `graph → l10_adapter → llm_synthesis`
  - Proves L10 runs at the correct position (pre-Gate-1, after context collection)

#### 4. **Documentation Update** (`core/skills/os_skills_integration.py`)

Updated header to reflect current wiring status (2026-09-16):
```python
- L10 (Context): ``adapt_context_l10`` / ``os.context_adapter`` IS NOW WIRED into the
  CEL pipeline via ``corvin_operator/context_engineering/stages/l10_adapter.py:68``
  (Blocker 2 fix, 2026-09-16). The L10AdapterStage runs after the graph stage (pre-Gate-1)
```

## Wiring Verification Checklist

### ✓ Registration & Config
- [x] L10AdapterStage class defined in `corvin_operator/context_engineering/stages/l10_adapter.py`
- [x] Imported in `corvin_operator/context_engineering/stages/__init__.py` (line 34)
- [x] Registered via `register_stage(L10AdapterStage())` (line 127 of l10_adapter.py)
- [x] In ACTIVE_PIPELINE config (line 30 of `config.py`)

### ✓ Call Site & Execution
- [x] `adapt_context_l10()` entry point defined in `core/skills/os_skills_integration.py`
- [x] L10AdapterStage.run() calls `adapt_context_l10()` at line 68
- [x] Call imports correctly: `from core.skills.os_skills_integration import adapt_context_l10`
- [x] Marked as `effect = "pure"` (runs pre-Gate-1, no side effects outside Skill call)

### ✓ Tests & Proof
- [x] Gate flipped: `TestL10HasProductionCallSite.test_l10_is_now_wired()` finds call sites
- [x] E2E proof: `TestL10ContextAdapterFullPipeline.test_l10_adapter_executes_in_active_pipeline()`
- [x] Ordering proof: `test_l10_adapter_before_synthesis()` verifies position
- [x] AST call detection finds the actual call at `l10_adapter.py:68`

## Execution Path (E2E Flow)

```
User Request
    ↓
run_full_pipeline() or build_context(active=True)
    ↓
build_context() [pure stages, pre-Gate-1]
    ↓
for spec in topo_order(specs):  # specs = ACTIVE_PIPELINE resolved
    ├─ memory stage → produces brief
    ├─ graph stage → produces decisions
    ├─ skill stage → produces skills
    ├─ approach stage → produces approach
    ├─ **L10 ADAPTER STAGE** [THIS WAS THE MISSING LINK]
    │  └─ l10_adapter.run(bundle, ctx)
    │     └─ adapt_context_l10(...)  ← WIRED ✓
    │        └─ registry.execute("os.context_adapter", ...)
    │           └─ ContextAdapterSkill.execute()
    │              └─ learns vibe_score, priority, attention_budget
    ├─ [Gate-1 checks task is allowed]
    ├─ [Deferred stages: llm_synthesis, toolforge, skillforge]
    └─ [Gate-2 checks final payload]
    ↓
Worker spawned with adapted context
```

## Compliance & ADR

- **ADR-0532 Phase 2b:** L10 Context Adapter wiring (this fix)
- **ADR-0555:** 3-tier hybrid context model (base/injected/merged)
- **ADR-0280/0282:** Two-gate pipeline architecture
- **ADR-0537:** Line of Moral Responsibility (LoM) binding

## Testing Commands

```bash
# Run gate flip test
pytest tests/e2e/test_os_skills_l5_l10_wiring.py::TestL10HasProductionCallSite::test_l10_is_now_wired -xvs

# Run E2E pipeline tests
pytest tests/e2e/test_l10_adapter_e2e.py::TestL10ContextAdapterFullPipeline -xvs

# Run all L10 tests
pytest tests/e2e/test_l10_adapter_e2e.py tests/e2e/test_l10_context_adapter_called.py tests/e2e/test_os_skills_l5_l10_wiring.py::TestL10HasProductionCallSite -xvs
```

## Files Modified

1. **tests/e2e/test_os_skills_l5_l10_wiring.py**
   - Flipped gate from `TestL10HasNoProductionCallSite` to `TestL10HasProductionCallSite`
   - Fixed search paths to include `corvin_operator`

2. **tests/e2e/test_l10_adapter_e2e.py**
   - Updated `_production_call_sites()` to search `corvin_operator`
   - Added `TestL10ContextAdapterFullPipeline` with E2E proofs

3. **core/skills/os_skills_integration.py**
   - Updated documentation to reflect L10 is now wired (2026-09-16)

## Timeline & Status

- **Phase 2 Session 1:** Identified Blocker 2 (L10 has no production call site)
- **2026-09-16 (this session):** Fixed Blocker 2
  - Updated test fence to include `corvin_operator` in search paths
  - Verified L10 adapter is properly registered and called
  - Added comprehensive E2E tests
  - Updated documentation

## Next Steps (Phase 2 Session 2)

- [x] Blocker 2 FIXED ✓
- [ ] Blocker 3: Corvin-Keys secret rotation (GDPR-critical)
- [ ] Phase 1 Kickoff: Skill Forge v2.0 + Model Selection + Marketplace Hub

---

**Summary:** Blocker 2 is **FIXED**. The L10 Context Adapter Skill is now properly wired into the CEL pipeline and will be invoked when requests flow through the context engineering stages. The gate has flipped from "must prevent wiring" to "must require wiring", and comprehensive E2E tests prove the wiring works end-to-end.
