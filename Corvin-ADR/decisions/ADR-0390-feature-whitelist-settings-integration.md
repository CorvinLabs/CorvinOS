---
id: ADR-0387
status: accepted
supersedes: []
depends_on: [ADR-0386]
related: [ADR-0286, ADR-0288]
commits: []
paths:
  - core/console/corvin_core/feature_flags.py
  - core/console/corvin_console/routes/settings.py
  - .corvin/tenants/_default/global/tenant.corvin.yaml
  - .corvin/tenants/_default/global/features.json
docs:
  - docs/claude-ref/layer-44-house-rules.md
  - CLAUDE.md § Feature Flags
---

# ADR-0387 — Feature Whitelist & Settings API Integration Fix

**Date:** 2026-08-18  
**Deciders:** shumway (Claude)  
**Status:** Accepted

### Amendment 2026-08-18 — an explicit console toggle outranks the whitelist

The deny-all-else branch of `is_enabled()` returned a hard `False` for every
unlisted flag **without reading the overlay at all**. Consequence, measured on
the live `_default` tenant: **36 of 41** registered flags could not be switched
on from the Console. `POST /settings/features/{id}/toggle` wrote the overlay,
answered `200 {"enabled": true}`, and the flag stayed off — the API reported
success for something that could not happen. This contradicted two things at
once: the load-bearing CLAUDE.md rule that every flag is *"toggleable from the
Console Settings → Features panel, no file editing, no restart"*, and this
repo's own `_source_of()`, which already reported such a flag as coming from
`"console"` while `is_enabled()` ignored it.

**Amended resolution order** (`core/console/corvin_core/feature_flags.py`):

1. An explicit operator decision in the `features.json` overlay wins — in
   **both** directions (on for an unlisted flag, off for a listed one).
2. Otherwise the whitelist decides: listed → ON, unlisted → OFF.
3. Otherwise (no whitelist) the legacy `spec.features` path, then the registry
   default.

**Deny-by-default is intact.** A flag the operator never toggled still resolves
through the whitelist, so nothing turns itself on; the whitelist keeps its
meaning as "what is on when the operator has said nothing". What changed is
that the whitelist is no longer a ceiling the Console cannot raise.

The Verification asserts below still hold as written — they describe flags with
no overlay entry. They are no longer complete, though: with an overlay entry
present, the overlay is the answer. See `test_whitelist_unlisted_flag_can_be_
switched_on_from_console` and `test_whitelist_untouched_flags_stay_dark` in
`core/console/tests/test_feature_flags.py` for both states.

Also fixed in the same pass: `verify_reauth` — a plain helper, not a FastAPI
dependency — was wired into `set_feature` and `set_worker_engine` via
`Depends()`. FastAPI read its signature as request params, making `rec` a
second body field and `presented_token` a required query param, so every
console toggle returned **422** before any of the above could even be reached.
It is now called inline, and its result is actually checked (under `Depends()`
the return value was never read, so the re-auth gate was inert).

And `PUT /settings/worker-engine` was unreachable for a third, independent
reason: it was declared BELOW `PUT /settings/{label}` in the same module, so
Starlette's registration-order matching handed every call to the config-file
writer, which validated it against `SettingsWriteRequest` and answered
`422 {"loc": ["body","body"]}`. This is exactly the trap ADR-0067 documented
for `PUT /settings/engine`; `app.py`'s include-order comment solves it only for
*other* routers and cannot help a route defined in the same file. The
worker-engine routes now sit above the wildcard, with a comment saying why they
must stay there. `PUT /settings/{label}` itself is unaffected (verified: an
unknown label still returns 404).

Note for whoever picks this up next: `core/gateway/corvin_gateway/console_api.py`
carries a SECOND, shadow implementation of these same settings endpoints backed
by an in-process `_SETTINGS` dict rather than `features.json`. It is imported by
nothing (grep confirms) — dead demo code, consistent with what CLAUDE.md already
records about that module's `/auth/login`. It was NOT changed here, but it is a
live trap: editing it looks like fixing the API and changes nothing.

## Context

The Feature Whitelist System (ADR-0386) introduced a deny-all-else strategy where only whitelisted features are enabled. However, the Console Settings UI route (`GET /settings/features`) was not respecting this strategy—it only read the `features.json` overlay file, bypassing the whitelist resolution logic entirely.

Additionally, the whitelist in the tenant configuration included non-existent features (`tree_of_thoughts`, `learning_objectives`, `token_metrics`), causing all whitelisted features to be incorrectly marked as disabled.

## Problem

**Conceptual:** The Settings API and the Feature Flags module had two different understandings of "is a feature enabled?"
- Feature Flags module: respects whitelist → overlay → default
- Settings API: only looks at overlay, ignores whitelist

**Structural:** The Settings route called `_read_features_config()` and checked `overlay.get("flags")` directly, bypassing `is_enabled()` which implements the full resolution strategy.

**Implementation:** Two issues:
1. Settings route: `enabled=enabled_flags.get(flag.id, False)` instead of `enabled=is_enabled(flag.id, tenant_id)`
2. Whitelist: contained features not in REGISTRY, causing whitelist resolution to fail
3. Repo config: missing `features_whitelist` section; Console loads from repo-internal `.corvin/`, not `~/.corvin/`

## Decision

### Layer 1: Conceptual
All feature-flag queries must use the same resolution strategy. A single source of truth: `feature_flags.is_enabled()`.

### Layer 2: Structural
- Settings API (`/settings/features`) must delegate to `is_enabled()` for enabled/disabled state
- Whitelist must contain only features that exist in `REGISTRY`
- Tenant config files (YAML + JSON overlay) must remain consistent across repo and home directories

### Layer 3: Implementation
- **settings.py:** Replace direct overlay read with `is_enabled(flag_id, tenant_id)` call
- **tenant.corvin.yaml (both repo and home):** Remove non-existent features from `features_whitelist`
- **features.json (both repo and home):** Sync overlay to match whitelist; only enable whitelisted features

## Changes

### 1. Settings API Route Fix
**File:** `core/console/corvin_console/routes/settings.py`

**Before:**
```python
# Read feature flags state from config
config = _read_features_config(rec.tenant_id)
enabled_flags = config.get("flags", {})

# Build feature list from registry
features: list[FeatureState] = []
for flag in _feature_flags_module.REGISTRY:
    features.append(FeatureState(
        id=flag.id,
        label=flag.label,
        description=flag.description,
        enabled=enabled_flags.get(flag.id, False),  # ❌ WRONG: ignores whitelist
        ...
    ))
```

**After:**
```python
# Build feature list from registry with proper resolution logic
features: list[FeatureState] = []
for flag in _feature_flags_module.REGISTRY:
    # Use is_enabled() which respects whitelist, overlay, and defaults
    enabled = _feature_flags_module.is_enabled(flag.id, rec.tenant_id)  # ✅ CORRECT
    features.append(FeatureState(
        id=flag.id,
        label=flag.label,
        description=flag.description,
        enabled=enabled,
        ...
    ))
```

### 2. Whitelist Correction
**File:** `.corvin/tenants/_default/global/tenant.corvin.yaml`

**Before:**
```yaml
features_whitelist:
  - vibe_engineering
  - vibe_engineering_active
  - tree_of_thoughts           # ❌ NOT in REGISTRY
  - learning_objectives        # ❌ NOT in REGISTRY
  - token_metrics              # ❌ NOT in REGISTRY
  - outcome_feedback_loop
  - cross_device_sync
  - package_marketplace_ui
```

**After:**
```yaml
features_whitelist:
  - vibe_engineering           # ✅ IN REGISTRY
  - vibe_engineering_active    # ✅ IN REGISTRY
  - outcome_feedback_loop      # ✅ IN REGISTRY
  - cross_device_sync          # ✅ IN REGISTRY
  - package_marketplace_ui     # ✅ IN REGISTRY
```

### 3. Overlay Sync
**Files:** Both `.corvin/tenants/_default/global/features.json` and `~/.corvin/tenants/_default/global/features.json`

Synced to only enable the whitelisted features, removing phantom entries for non-existent features.

## Rationale

### Why is_enabled()?
`is_enabled()` is the canonical resolution function in `feature_flags.py`. It encodes the full strategy:
1. If `spec.features_whitelist` exists, ONLY those features are ON (deny-all-else)
2. Check `features.json` overlay for per-tenant override
3. Fall back to registry `default` (always False per CLAUDE.md)

The Settings API must use this same function to avoid drift between admin interface and runtime behavior.

### Why audit the whitelist?
Non-existent features in the whitelist are a silent failure: `is_enabled(nonexistent_feature)` returns False because the feature is not in `_BY_ID` (line 974 in feature_flags.py). The whitelist becomes dead weight.

### Why sync both config files?
Console loads from `.corvin/` inside the repo (for local development), not from `~/.corvin/`. Both files must agree to ensure consistent behavior across environments.

## Verification

### Test: Whitelisted Features Are Enabled
```python
assert is_enabled("vibe_engineering", "_default") == True
assert is_enabled("outcome_feedback_loop", "_default") == True
assert is_enabled("cross_device_sync", "_default") == True
assert is_enabled("package_marketplace_ui", "_default") == True
assert is_enabled("vibe_engineering_active", "_default") == True
```

### Test: Non-Whitelisted Features Are Disabled
```python
assert is_enabled("browser_automation", "_default") == False
assert is_enabled("acs_context_sync", "_default") == False
assert is_enabled("admin_control_plane", "_default") == False
```

### Test: Settings API Returns Correct State
```python
features = describe_all("_default")
enabled_count = sum(1 for f in features if f["enabled"])
assert enabled_count == 5  # exactly the 5 whitelisted features
```

## Impact

- **Blast radius:** Settings UI now correctly reflects whitelist strategy
- **Backward compat:** No breaking changes; Settings API still returns same structure
- **User visible:** Settings → Features page now shows only whitelisted features as enabled
- **Admin visible:** Operator can toggle individual whitelisted features on/off via Console

## Related Decisions

- **ADR-0386:** Feature Whitelist System (the parent decision)
- **ADR-0286:** Feature flag automatic graduation
- **CLAUDE.md § Feature Flags:** Ship dark by default; default OFF on fresh install

---

**Acceptance Criteria:** ✅
- Settings API calls `is_enabled()` for all feature state lookups
- Whitelist contains only features that exist in REGISTRY
- Both repo and home tenant configs are in sync
- E2E verified: whitelisted features enabled, non-whitelisted disabled
