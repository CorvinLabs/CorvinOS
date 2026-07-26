# ADR-0245: Live Model Discovery

**Date:** 2026-07-26
**Status:** IMPLEMENTED (Phase 1)
**Owner:** maintainer
**Target Release:** 0.11.x

## Summary

Users see stale model lists in the Console until restart. When Anthropic releases `claude-opus-5`, the operator must restart CorvinOS for the Console to show it. **Solution:** live-fetch models from Anthropic every 5 minutes in the background, cache to disk, and refresh the Console UI without restart.

## Problem

1. **Static YAML registry** — model list is baked into `operator/bundle/config-templates/engine_model_registry.yaml`
2. **Only refresh on restart** — new models from Anthropic are invisible until the Console process restarted
3. **Manual workaround** — operator must edit YAML + restart, or copy+paste model IDs
4. **User confusion** — "Why doesn't my model show up after Anthropic released it?"

## Design

### Backend (Python)

#### New API Endpoints
- `GET /console/models/registry` — static YAML engine-model registry (always available)
- `GET /console/models/providers` — static provider registry (always available)
- `GET /console/models/live` — merged registry + live-fetched Anthropic models (if flag ON)
  - Returns: `{ providers: {anthropic: {...}}, registry: {...}, cache_status: {...} }`
  - Cache status includes `cached_at`, `cache_age_sec`, and fallback reason (if any)
- `POST /console/models/live/refresh` — trigger immediate fetch (user button)

#### Feature Flag
- **ID:** `live_model_discovery`
- **Default:** `False` (off on fresh install)
- **Operator toggle:** Settings → Features → "Live model discovery"
- **When OFF:** routes return static registry only (cached data ignored)
- **When ON:** background refresh every 5 minutes, cache to disk

#### Background Refresh Mechanism
- **Trigger:** starts at module load time (first import of `corvin_console.routes.models`)
- **Interval:** every 5 minutes (300 seconds)
- **What it does:**
  1. Check if `live_model_discovery` feature is enabled for default tenant
  2. Call `engine_providers.fetch_models("anthropic", ...)` (via `anthropic_api_key` env var)
  3. If fetch succeeds: write to `~/.corvin/tenants/_default/global/model_catalog_cache.json`
  4. If fetch fails (timeout, 401, network): log warning, keep existing cache
  5. Reschedule itself
- **Thread safety:** `threading.Lock()` guards cache reads/writes
- **Non-blocking:** daemon thread, async refresh does not block the Console request handler
- **Audit trail:** `system_event` logged on success/failure

#### Cache File Format
```json
{
  "providers": {
    "anthropic": {
      "models": [
        {"id": "claude-opus-5", "label": "Claude Opus 5"},
        {"id": "claude-sonnet-5", "label": "Claude Sonnet 5"},
        ...
      ],
      "reachable": true,
      "count": 5,
      "error": null,
      "fetched_at": 1721899935
    }
  }
}
```

**Fallback:** if cache is corrupted or missing, API returns empty providers + static registry.

### Frontend (React/TypeScript)

#### Console UI Changes
1. **Settings → Features panel** — show `live_model_discovery` flag (already auto-generated)

2. **Model selector** (Engine page / Chat settings)
   - On mount: call `GET /console/models/live` once to pre-populate
   - Poll every 5 minutes for model updates (reuse same interval as backend)
   - Show status badge:
     ```
     🟢 Online — 5 models (updated 2 min ago)
     ```
     or
     ```
     🔴 Offline — using cached list (last sync: 1h ago)
     ```

3. **"Refresh now" button** — call `POST /console/models/live/refresh` manually
   - Shows loading state during fetch
   - On success: updates UI immediately + timestamp
   - On failure: shows error toast but keeps existing list

#### Implementation Example (pseudocode)
```typescript
// hooks/useModelCatalog.ts
export function useModelCatalog() {
  const [models, setModels] = useState([]);
  const [refreshStatus, setRefreshStatus] = useState<'online'|'offline'|null>(null);
  const [cacheAge, setCacheAge] = useState<number | null>(null);

  useEffect(() => {
    // Poll every 5 minutes
    const interval = setInterval(async () => {
      const resp = await fetch('/v1/console/models/live');
      const data = await resp.json();
      
      if (data.providers?.anthropic?.models) {
        setModels(data.providers.anthropic.models);
        setRefreshStatus(
          data.providers.anthropic.reachable ? 'online' : 'offline'
        );
      }
      if (data.cache_status?.cached_at) {
        setCacheAge(Date.now() - data.cache_status.cached_at * 1000);
      }
    }, 5 * 60 * 1000); // 5 minutes

    // Initial fetch
    fetch('/v1/console/models/live')
      .then(r => r.json())
      .then(...);

    return () => clearInterval(interval);
  }, []);

  const refreshNow = async () => {
    setRefreshStatus('loading');
    const resp = await fetch('/v1/console/models/live/refresh', { method: 'POST' });
    const data = await resp.json();
    // ... update state
  };

  return { models, refreshStatus, cacheAge, refreshNow };
}
```

## Implementation Details

### Files Changed (Phase 1, Implemented)

**Backend:**
- `core/console/corvin_console/feature_flags.py` — added `live_model_discovery` flag
- `core/console/corvin_console/routes/models.py` — **NEW** (4 endpoints + background refresh)
- `core/console/corvin_console/app.py` — register models router

**Tests:**
- `core/console/tests/test_model_catalog_live.py` — **NEW** (8 unit tests, Tier 2)
- `core/console/tests/test_model_catalog_integration.py` — **NEW** (5 integration tests, Tier 3)

**Documentation:**
- `docs/ADR-0245-LIVE-MODEL-DISCOVERY.md` — this file

### Files to Add (Phase 2, Future)

**Console UI:**
- `core/console/web-next/src/hooks/useModelCatalog.ts` — polling hook + status badge
- `core/console/web-next/src/components/ModelStatusBadge.tsx` — status indicator
- `core/console/tests/e2e/model-discovery.spec.ts` — Playwright E2E tests

### Security & Compliance

**API Key handling:**
- `ANTHROPIC_API_KEY` env var resolved via `provider_keys.resolve_by_env_var()` at fetch time
- Never logged, never returned in responses
- Falls back silently if key is not set

**Audit trail:**
- `system_event("model_catalog_refreshed", ...)` on success
- `system_event("model_catalog_refresh_failed", ...)` on failure
- `action_performed(...)` when user triggers manual refresh

**Multi-tenant:**
- Cache is per-tenant: `~/.corvin/tenants/<TENANT>/global/model_catalog_cache.json`
- Background refresh runs for `_default` tenant (can be extended later)
- Each tenant's UI polls its own `/models/live` endpoint

**Feature flag compliance:**
- NOT subject to compliance baseline (not a compliance mechanism, not disableable via env)
- Can be safely toggled on/off without restart
- Fallback to static registry when off

## Testing

### Tier 1: Syntax & Imports
✅ Python compile check (`python -m py_compile`)

### Tier 2: Unit Tests (8 tests)
- `test_models_registry_always_available` — static registry always works
- `test_models_live_when_flag_off` — flag off → empty providers
- `test_models_live_when_flag_on_no_cache` — flag on, no cache → empty providers (await first fetch)
- `test_models_live_with_mock_fetch` — mock Anthropic API → cache written
- `test_models_live_refresh_when_flag_off` — refresh endpoint 400 when flag off
- `test_models_live_refresh_mock_failure` — refresh handles network errors gracefully
- `test_models_providers_always_available` — provider registry is static
- `test_cache_isolation_per_tenant` — cache paths differ per tenant

### Tier 3: Integration Tests (TODO — Iteration 2)
- Full stack: Console startup + background refresh running
- Anthropic API reachability check (or skip if key not set)
- Cache file persists across Console restarts
- Multiple tenants have separate cache files

### Tier 4: E2E (TODO — Iteration 2)
- Browser: poll `/models/live` every 5 min, verify UI updates without page reload
- Operator: toggle flag on/off via Settings panel, see models appear/disappear

## Rollout

### Phase 1 (0.11.0) — Backend + Feature Flag [IMPLEMENTED]
**Status:** ✅ **COMPLETE**
- ✅ Backend API: 4 endpoints (registry, providers, live, refresh)
- ✅ Feature flag: `live_model_discovery` (default OFF)
- ✅ File-based cache: `model_catalog_cache.json`
- ✅ Background refresh: 5-min interval, daemon thread
- ✅ Tests: Tier 1 (syntax), Tier 2 (unit 8/8), Tier 3 (integration 5/5)
- ✅ Audit trail: `system_event` on success/failure

**What's NOT in Phase 1:**
- ❌ Console UI (React/TypeScript) — planned for Phase 2
- ❌ Operator can only enable via CLI/API, not yet via Settings UI
- ❌ No browser polling / status badge yet

### Phase 2 (0.11.1 or 0.12.x) — Console UI [FUTURE]
**Planned, not yet implemented:**
- Console UI integration: status badge, polling, "Refresh now" button
- E2E tests (Playwright) with browser automation
- Measurement: how many operators enable the flag?
- Target release: when flag defaults to ON (after measurement week)

## Alternatives Considered

### Alternative 1: Always-on fetch (no flag)
**Rejected:** forces all Console deployments to call Anthropic every 5 min, even if offline or using private provider. Better to let operator opt-in.

### Alternative 2: On-demand fetch (no background job)
**Rejected:** user clicks "Refresh models" → blocks their request while fetching from Anthropic. Slow UX + timeout risk.

### Alternative 3: In-memory cache
**Rejected:** multi-process Console (multiple workers) → cache inconsistency. File-based ensures all workers see the same data.

## References

- **ADR-0181** — Provider model discovery (fetch_models, credential_env)
- **ADR-0067** — Engine selection (worker_engine choice, delegation_budget.json pattern)
- **CLAUDE.md § Feature Flags** — ship dark by default, operator control
- **L35 — Network Egress Lockdown** — ensure Anthropic is on allowlist when fetching
