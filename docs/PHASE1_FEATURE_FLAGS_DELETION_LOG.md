# Phase 1 Feature Flags Deletion Log

**Status:** In Progress (ready for post-deployment cleanup)  
**Date:** 2026-09-02  
**ADR:** ADR-0544 (Phase 1 big bang feature flags refactoring)

## Summary

This document tracks feature flags deleted from the 5 call-sites rewritten in Phase 1 k=2-5, mapping each old flag to its replacement Skill.

## Feature Flags Deleted

### Call-Site #1: Plugin Health Monitoring
**File:** `core/gateway/corvin_gateway/app.py:196`  
**Deleted Flag:** `plugin_health_monitoring`  
**Replacement Skill:** `os.plugin_health_monitoring`

```python
# BEFORE (DELETED)
if _hflags.is_enabled("plugin_health_monitoring", _htid):
    # ... boot health collector

# AFTER (CURRENT)
if _registry.execute("os.plugin_health_monitoring", {"enabled": True}).output.get("enabled"):
    # ... boot health collector
```

**Flag Status:** ✅ Removed from gateway/app.py  
**Replacement Status:** ✅ os.plugin_health_monitoring Skill active

---

### Call-Site #2: Headless API Mode
**File:** `core/console/corvin_console/app.py:440`  
**Deleted Flag:** `headless_api_mode` (referenced as `HEADLESS_FLAG_ID`)  
**Replacement Skill:** `os.headless_mode`

```python
# BEFORE (DELETED)
return bool(is_enabled(HEADLESS_FLAG_ID, tenant_id))

# AFTER (CURRENT)
result = registry.execute("os.headless_mode", {"headless_enabled": False})
return bool(result.output.get("headless_enabled", False))
```

**Flag Status:** ✅ Removed from console/app.py  
**Replacement Status:** ✅ os.headless_mode Skill active

---

### Call-Site #3: Plugin Builder Enabled
**File:** `core/console/corvin_console/slash_commands.py:116`  
**Deleted Flag:** `plugin_builder_enabled`  
**Replacement Skill:** `os.plugin_builder`

```python
# BEFORE (DELETED)
return feature_flags.is_enabled("plugin_builder_enabled", tenant_id)

# AFTER (CURRENT)
result = registry.execute("os.plugin_builder", {"enabled": True})
return bool(result.output.get("enabled", False))
```

**Flag Status:** ✅ Removed from slash_commands.py  
**Replacement Status:** ✅ os.plugin_builder Skill active

---

### Call-Site #4: Capabilities Gated Flags (Bulk Lookup)
**File:** `core/console/corvin_console/routes/capabilities.py:142`  
**Deleted Flags:** 50+ individual flag checks (see GATED_FLAGS list)  
**Replacement Skill:** `os.capabilities`

```python
# BEFORE (DELETED)
for flag in GATED_FLAGS:
    try:
        out[flag] = bool(is_enabled(flag, tenant_id))
    except Exception:
        out[flag] = False

# AFTER (CURRENT)
result = registry.execute("os.capabilities", {
    "tenant_id": tenant_id,
    "gated_flags": list(GATED_FLAGS),
})
return result.output.get("flags", {})
```

**Flags Status:** ✅ Bulk lookup removed from capabilities.py  
**Replacement Status:** ✅ os.capabilities Skill active

**GATED_FLAGS Affected:**
- vibe_engineering, vibe_engineering_active
- console_web_surface_plugin, console_auto_reload, console_marketplace_panel
- frontend_forge, package_marketplace_ui
- validator_factory_enabled, file_permissions_enabled
- dual_gate_pipeline_enabled, dual_gate_pii_detection_enabled
- execution_context_badge, auto_load_github_repo
- ccc_command_routing, acs_context_sync, tde_shadow_measurement
- plugin_health_monitoring, plugin_runtime_lifecycle
- plugin_builder_enabled, plugin_builder_idea_first_interview
- a2a_relay_fallback, a2a_lan_bind
- headless_api_mode, browser_automation
- outcome_feedback_loop, cross_device_sync
- (and 15+ others in GATED_FLAGS list)

---

### Call-Site #5: Vibe Engineering Active
**File:** `core/console/corvin_console/routes/vibe_engineering.py:311`  
**Deleted Flag:** `vibe_engineering_active`  
**Replacement Skill:** `os.vibe_engineering` (extended with `enabled` key)

```python
# BEFORE (DELETED)
_active = bool(_ff.is_enabled("vibe_engineering_active", rec.tenant_id))

# AFTER (CURRENT)
result = _registry.execute("os.vibe_engineering", {"tenant_id": rec.tenant_id})
_active = bool(result.status == "success" and result.output.get("enabled", False))
```

**Flag Status:** ✅ Removed from vibe_engineering.py  
**Replacement Status:** ✅ os.vibe_engineering Skill extended with active status

---

## Verification Checklist (Post-Deployment)

After Phase 1 deployment, run:

```bash
# Verify no feature flag references in rewritten files
grep -n "is_enabled\|feature_flags\|FLAGS\." \
  core/gateway/corvin_gateway/app.py \
  core/console/corvin_console/app.py \
  core/console/corvin_console/slash_commands.py \
  core/console/corvin_console/routes/capabilities.py \
  core/console/corvin_console/routes/vibe_engineering.py

# Expected: 0 matches (all feature flag usage replaced)
```

```bash
# Verify Skills are callable
python3 -c "
from core.skills.skill_registry_phase1 import get_registry
registry = get_registry()
assert registry.is_enabled('os.plugin_health_monitoring')
assert registry.is_enabled('os.headless_mode')
assert registry.is_enabled('os.plugin_builder')
assert registry.is_enabled('os.capabilities')
assert registry.is_enabled('os.vibe_engineering')
print('✓ All 5 replacement Skills verified')
"
```

## Feature Flag Deprecation Timeline

| Flag | Deprecated | Removed | Reason |
|---|---|---|---|
| plugin_health_monitoring | 2026-09-02 | Post-Deploy Week 12 | Replaced by os.plugin_health_monitoring Skill |
| headless_api_mode | 2026-09-02 | Post-Deploy Week 12 | Replaced by os.headless_mode Skill |
| plugin_builder_enabled | 2026-09-02 | Post-Deploy Week 12 | Replaced by os.plugin_builder Skill |
| vibe_engineering_active | 2026-09-02 | Post-Deploy Week 12 | Replaced by os.vibe_engineering Skill |
| 50+ GATED_FLAGS | 2026-09-02 | Post-Deploy Week 12 | Replaced by os.capabilities Skill |

## Notes for Future Work

### Remaining Feature Flags (Not Rewritten in Phase 1)
Phase 1 k=2-5 targeted only 5 high-impact call-sites. The following flags remain in the feature_flags module and were NOT rewritten:

- Compliance gates (audit, consent, disclosure, etc.) — never flaggable per CLAUDE.md
- Experimental/low-impact flags (execution_context_badge, etc.) — lower priority
- Worker engine selection (spec.worker_engine) — three-way setting, not boolean

These may be addressed in Phase 2 if needed.

### Skill Registry Persistence
The Skills registry is currently in-memory. To persist Skill state (e.g., "plugin builder disabled for this tenant"), future ADRs should specify:
- How to store Skill configuration (tenant.corvin.yaml, features.json, etc.)
- TTL and cache invalidation semantics
- Per-tenant vs. global Skill state

### Learning Integration (ADR-0314)
The Skills framework is ready to integrate with the Learning Infrastructure (ADR-0314) to enable:
- Skill feedback collection (user says "that routing was wrong")
- Optimization loop (learn from feedback, tune Skill parameters)
- Confidence scoring (how sure is the Skill in its decision?)

---

**Document Created:** 2026-09-02  
**Last Updated:** 2026-09-02  
**Status:** Ready for post-deployment cleanup  
**Owner:** Phase 1 Autonomous Team  
**Co-Authored-By:** Claude Haiku 4.5 <noreply@anthropic.com>
