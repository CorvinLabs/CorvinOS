# SPIKE 1: BLOCKER SCENARIOS & PREPARATION
**Purpose:** Pre-prepared code paths for both blocker scenarios (Big Bang vs. Wrapper)  
**Status:** Ready-to-execute templates (will be activated on Sept 3)

---

## SYSTEM OVERVIEW

**Current Feature Flags System (feature_flags.py):**
- **59 Feature Flags** (5 top-level categories)
- **27 Tag categories** (plugins, bridges, chat, learning, compliance, etc.)
- **5 Release Tiers** (alpha, beta, stable, production) — currently only alpha/beta used
- **Public API:** 20 functions (get, set, describe, tier management, canary routing)

**Feature Flag Categories:**
- **Compliance/Security (5):** audit, consent, disclosure, path_gate, flow_guard, license, gdpr, erasure (blocked by protected substrings)
- **Plugin/System (17):** plugin_*, admin_control_plane, bridge_supervisor_plugins
- **Learning (8):** skill_forge_enabled, learning_gap_*, outcome_feedback_loop, cross_device_sync
- **Vibe Engineering (4):** vibe_engineering, vibe_engineering_active, cel_cache_stable, cel_brief_includes_content, cel_load_bearing_anchor
- **Other (25):** delegation, bridges, models, console, tools, etc.

---

## SCENARIO A: BLOCKER #2 = BIG BANG (All 88 call-sites refactored immediately)

### Activation: IF `architecture_choice == "big_bang"`

**Impact:**
- Spike 1: +4h for call-site analysis (find all 88 locations)
- Phase 1b: 10-week big-bang refactoring (high parallelization risk)
- Wrapper: NOT created (direct Skills API from start)
- Call-site refactoring: template-driven migration

### Code Path: NO WRAPPER

**Instead of:**
```python
from corvin_core.feature_flags import is_enabled
if is_enabled("flag_x"):
    # do thing
```

**Becomes:**
```python
from core.skills.feature_flags_skill import FeatureFlagsSkill
skill = FeatureFlagsSkill()
result = skill.execute({"flag_id": "flag_x", "tenant_id": tenant_id})
if result.get("enabled", False):
    # do thing
```

### Implementation Template (Big Bang)

**File:** `core/skills/feature_flags_skill.py`
```python
# Skill: os.feature_flags (replaces feature_flags.py entirely)

class FeatureFlagsSkill:
    """Feature flags as a Skill.
    
    Direct replacement for feature_flags.is_enabled(), set_enabled(), etc.
    No legacy adapter — all 88 call-sites refactored to direct Skills API.
    """
    
    def execute(self, input: dict) -> dict:
        """Execute feature flag operations.
        
        Args:
            input: {
                "operation": "is_enabled" | "set_enabled" | "describe" | etc.,
                "flag_id": str,
                "enabled": bool (for set_enabled),
                "tenant_id": str (default: "_default"),
                ...
            }
        
        Returns:
            {
                "enabled": bool,
                "source": "console" | "tenant_yaml" | "default" | "whitelist",
                "tier": "alpha" | "beta" | "stable" | "production",
                ...
            }
        """
        operation = input.get("operation", "is_enabled")
        flag_id = input["flag_id"]
        tenant_id = input.get("tenant_id", "_default")
        
        # Dispatch to operation handler
        if operation == "is_enabled":
            return self._is_enabled(flag_id, tenant_id)
        elif operation == "set_enabled":
            enabled = input["enabled"]
            return self._set_enabled(flag_id, enabled, tenant_id)
        elif operation == "describe":
            return self._describe_all(tenant_id)
        elif operation == "tier_of":
            return self._tier_of(flag_id)
        # ... more operations
    
    def _is_enabled(self, flag_id: str, tenant_id: str) -> dict:
        """Check if a flag is enabled."""
        # Implementation: migrate from feature_flags.is_enabled()
        # Emit audit event: SKILL_EXECUTED
        # Return: {"enabled": bool, "source": str, ...}
        pass
    
    # ... other operation handlers
```

### Call-Site Refactoring (Big Bang)

**Before (88 sites):**
```python
from corvin_core.feature_flags import is_enabled

if is_enabled("vibe_engineering"):
    # context engineering
```

**After (all 88 sites):**
```python
from core.skills.feature_flags_skill import feature_flags_skill

result = feature_flags_skill.execute({
    "operation": "is_enabled",
    "flag_id": "vibe_engineering",
    "tenant_id": tenant_id
})

if result.get("enabled", False):
    # context engineering
```

**Spike 1 Task:** Find and list all 88 call-sites  
**Phase 1b Task:** Refactor each site (template-driven, parallelizable)

---

## SCENARIO B: BLOCKER #2 = WRAPPER+PHASED (Legacy wrapper until Phase 2)

### Activation: IF `architecture_choice == "wrapper_phased"`

**Impact:**
- Spike 1: –2h (focus on feature_flags.py only, skip call-site analysis)
- Phase 1b: 88 call-sites untouched initially (tech debt, acceptable)
- Wrapper: FeatureFlagLegacyAdapter transparently delegates to Skill
- Phase 2: Gradual migration of call-sites (lower risk)

### Code Path: WITH WRAPPER

**Old API (unchanged 88 sites):**
```python
from corvin_core.feature_flags import is_enabled

if is_enabled("flag_x"):  # Same call, same behavior
    # do thing
```

**Behind-the-scenes (transparent to caller):**
```
is_enabled("flag_x")
  ↓
FeatureFlagLegacyAdapter.is_enabled()  [wrapper]
  ↓
SkillsRegistry.execute("feature_flags_skill", {...})  [Skill]
  ↓
Feature flags data returned
```

### Implementation Template (Wrapper+Phased)

**File 1:** `core/skills/feature_flags_skill.py` (same as Big Bang)
```python
# Skill: os.feature_flags
# Implementation: same as Big Bang
```

**File 2:** `core/console/corvin_core/feature_flags_legacy_adapter.py`
```python
"""Compatibility wrapper: legacy API → Skill delegation.

All 88 call-sites continue using the old API unchanged:
  from corvin_core.feature_flags import is_enabled
  
Behind-the-scenes, every call delegates to the Skills-based Skill
and returns the result.

This is a temporary shim for Phase 1b (Weeks 1–10).
Phase 2 will gradually migrate call-sites to direct Skills API.
"""

from core.skills.feature_flags_skill import FeatureFlagsSkill

_skill = FeatureFlagsSkill()

def is_enabled(flag_id: str, tenant_id: str = "_default") -> bool:
    """Legacy API — transparently delegates to Skill."""
    result = _skill.execute({
        "operation": "is_enabled",
        "flag_id": flag_id,
        "tenant_id": tenant_id
    })
    return result.get("enabled", False)

def set_enabled(flag_id: str, enabled: bool, tenant_id: str = "_default") -> bool:
    """Legacy API — transparently delegates to Skill."""
    result = _skill.execute({
        "operation": "set_enabled",
        "flag_id": flag_id,
        "enabled": enabled,
        "tenant_id": tenant_id
    })
    return result.get("success", False)

# ... all other legacy functions (describe_all, tier_of, worker_engine_mode, etc.)
```

**Modified file:** `core/console/corvin_core/feature_flags.py`
```python
# This file becomes a thin re-export layer pointing to the adapter
# OR gets deprecated entirely, with imports redirected to legacy_adapter

# Deprecated: import from legacy_adapter instead
from core.console.corvin_core.feature_flags_legacy_adapter import *

# All functions now point to adapter, which delegates to Skill
```

**Old 88 call-sites:** UNCHANGED — continue to work

---

## SHARED CODE (Both Scenarios)

### Skills Manifest: `core/skills/feature_flags_registry.yaml`

```yaml
# Shared between Big Bang and Wrapper scenarios
skill_id: "os.feature_flags"
version: "0.1.0-spike1"
description: "Feature flags registry (SKills API replacement for feature_flags.py)"

operations:
  - name: "is_enabled"
    description: "Check if a flag is enabled for a tenant"
    parameters:
      - name: "flag_id"
        type: "string"
        required: true
        description: "Feature flag ID (e.g., 'vibe_engineering')"
      - name: "tenant_id"
        type: "string"
        required: false
        default: "_default"
        description: "Tenant ID"
    returns:
      - name: "enabled"
        type: "boolean"
        description: "Whether the flag is enabled"
      - name: "source"
        type: "string"
        enum: ["console", "tenant_yaml", "whitelist", "default"]
        description: "Where the value came from"
      - name: "tier"
        type: "string"
        enum: ["alpha", "beta", "stable", "production"]
        description: "Release tier of the flag"
  
  - name: "set_enabled"
    description: "Set a flag's enabled state in the console overlay"
    parameters:
      - name: "flag_id"
        type: "string"
        required: true
      - name: "enabled"
        type: "boolean"
        required: true
      - name: "tenant_id"
        type: "string"
        required: false
        default: "_default"
    returns:
      - name: "success"
        type: "boolean"
      - name: "new_state"
        type: "boolean"
      - name: "timestamp"
        type: "string"
  
  # ... other operations: describe_all, tier_of, worker_engine_mode, etc.

audit_events:
  - event_type: "SKILL_EXECUTED"
    description: "Emitted on every Skill.execute() call"
    fields:
      - "skill_id: os.feature_flags"
      - "operation: is_enabled | set_enabled | describe_all | ..."
      - "flag_id: string"
      - "input: dict (operation params)"
      - "output: dict (result)"
      - "tenant_id: string (GDPR isolation)"
      - "lom: string (Line of Moral Responsibility)"
```

### Audit Trail Integration

**Every Skill operation emits:**
```python
# In FeatureFlagsSkill.execute()

# Before operation
audit_backend.write_event({
    "event_type": "skill_executed",
    "skill_id": "os.feature_flags",
    "operation": input["operation"],
    "flag_id": input.get("flag_id"),
    "tenant_id": tenant_id,
    "input": input,  # operation parameters
    "timestamp": datetime.utcnow().isoformat(),
    "lom": f"{__file__}:FeatureFlagsSkill.execute:{inspect.currentframe().f_lineno}",
})

# Execute operation
result = self._dispatch_operation(input, tenant_id)

# After operation
audit_backend.write_event({
    "event_type": "skill_completed",
    "skill_id": "os.feature_flags",
    "operation": input["operation"],
    "success": True,
    "output": result,
    "latency_ms": elapsed_ms,
    "tenant_id": tenant_id,
})

return result
```

### Testing: Equivalence Tests (Both Scenarios)

**Goal:** Prove OLD API == NEW Skill behavior

```python
# tests/integration/test_feature_flags_equivalence.py

import pytest
from corvin_core import feature_flags as old_api
from core.skills.feature_flags_skill import FeatureFlagsSkill

skill = FeatureFlagsSkill()

class TestEquivalence:
    """Verify old API == new Skill behavior for all flag operations."""
    
    def test_is_enabled_equivalence(self):
        """is_enabled(flag_id) returns same value from old and new."""
        for flag_def in feature_flags.REGISTRY:
            # Old API
            result_old = old_api.is_enabled(flag_def.id)
            
            # New Skill
            result_new = skill.execute({
                "operation": "is_enabled",
                "flag_id": flag_def.id,
                "tenant_id": "_default"
            })["enabled"]
            
            # Must match
            assert result_old == result_new, \
                f"Equivalence failed for {flag_def.id}: old={result_old}, new={result_new}"
    
    def test_set_enabled_equivalence(self):
        """set_enabled() produces same state in old and new."""
        test_flag = "test_equivalence_flag"  # Test-only flag
        
        # Old API: set to True
        old_api.set_enabled(test_flag, True)
        result_old = old_api.is_enabled(test_flag)
        
        # New Skill: set to True
        skill.execute({
            "operation": "set_enabled",
            "flag_id": test_flag,
            "enabled": True,
            "tenant_id": "_default"
        })
        result_new = skill.execute({
            "operation": "is_enabled",
            "flag_id": test_flag,
            "tenant_id": "_default"
        })["enabled"]
        
        # Both must be True
        assert result_old == True
        assert result_new == True
        assert result_old == result_new
```

---

## DECISION MATRIX

| Aspect | Big Bang (Option 2a) | Wrapper+Phased (Option 2b) |
|--------|---|---|
| **Wrapper exists?** | ❌ No | ✅ Yes (transparent) |
| **88 call-sites refactored** | ✅ Weeks 1–10 | ⏳ Phase 2+ (gradual) |
| **Spike 1 effort** | +4h (call-site analysis) | –2h (focus feature_flags.py) |
| **Phase 1b effort** | ~880h (all 88 sites) | ~30h (wrapper + gradual migration) |
| **Risk level** | 🔴 HIGH (big parallelization) | 🟡 MEDIUM (tech debt 2–3mo) |
| **Code complexity** | Lower (single audit path) | Higher (dual audit paths) |
| **Rollback difficulty** | Hard (distributed changes) | Easy (wrapper backward-compat) |

---

## NEXT STEPS

**Awaiting Blocker #2 Answer (Sept 3 06:00 UTC):**

```
IF architecture_choice == "big_bang":
  → Activate Code Path A (NO WRAPPER)
  → Add call-site discovery task to Phase 2
  → Refactor template for 88 files
  
IF architecture_choice == "wrapper_phased":
  → Activate Code Path B (WITH WRAPPER)
  → Create feature_flags_legacy_adapter.py
  → Prepare Phase 2 gradual migration plan
```

**Prepared & Ready:**
- ✅ Both code paths documented (this file)
- ✅ Shared code (Skill manifest, audit integration, tests)
- ✅ Execution templates ready for immediate activation

---

**Document:** SPIKE_1_BLOCKER_SCENARIO_PREP.md  
**Status:** Ready for immediate activation on blocker answer  
**Owner:** Spike 1 Dev (autonomous execution)
