# Feature Tier Graduation System — Changelog

**Versions:** 0.10.89–0.11.0 (unreleased)

---

## Phase 1–4: Metrics + Telemetry + CLI (0.10.89)

### Added
- **Telemetry:** Stability metrics collection (`mark_invocation()`, `mark_error()`)
- **Metrics Daemon:** Hourly digests with error rates, adoption, days-in-tier
- **Promotion Daemon:** Automatic feature tier promotion based on metrics
- **Demotion Fail-Safe:** Production features demote immediately on error spike (>1%)
- **Maintainer CLI:** `corvin flag {promote|demote|status|history}`
- **Audit Trail:** All tier transitions logged with metrics snapshots
- **GDPR-Safe:** Anonymized metrics, fail-closed error validation

### Changed
- `feature_flags.py`: Added `release_tier`, `released_date`, `promoted_by` fields
- Feature registry: 3 sample flags marked beta (auto_load_github_repo, vibe_engineering, plugin_builder_enabled)

### Files
- `core/telemetry/stability_metrics.py` — metrics collection
- `core/telemetry/telemetry_daemon.py` — hourly digest dispatch
- `core/console/corvin_console/promotion_daemon.py` — auto-promotion logic
- `core/console/corvin_console/promotion_gates.py` — promotion criteria
- `ops/launcher/corvin/flag_commands.py` — CLI subcommands

---

## Phase 5: Console UI (0.10.90)

### Added
- **PresetSwitcher Component:** Switch between minimal/standard/advanced presets
- **FeatureStatusDashboard:** View all features with tier badges, error rates, adoption
- **Feature Status API:** `/api/feature-status/preset` and `/api/feature-status`
- **Settings Page:** Integrated preset switcher + dashboard in console UI

### Files
- `core/console/corvin_console/web-next/src/components/PresetSwitcher.tsx`
- `core/console/corvin_console/web-next/src/components/FeatureStatusDashboard.tsx`
- `core/console/corvin_console/api/feature_status_endpoints.py`

---

## Phase 6: API Wiring + Tests (0.10.91)

### Added
- **YAML Persistence:** Presets saved to `~/.corvin/tenants/_default/tenant.corvin.yaml`
- **Integration Tests:** 4 tier-2 tests for YAML read/write
- **E2E Test Skeletons:** 8 Playwright test stubs for browser interactions
- **React Component Tests:** 16 vitest tests for PresetSwitcher and Dashboard

### Files
- `core/telemetry/tests/test_stability_metrics.py` — 19 tests
- `core/console/corvin_console/tests/test_feature_status_yaml.py` — 4 integration tests
- `core/console/corvin_console/tests/test_feature_status_e2e.py` — E2E stubs
- `core/console/corvin_console/web-next/src/components/__tests__/*` — React tests

---

## Phase 6.5: Installer Integration (0.10.92)

### Added
- **CLI Preset Flag:** `corvin install --preset [minimal|standard|advanced]`
- **Preset Setup CLI:** `corvin-preset-setup` command for runtime configuration
- **Installer Wiring:** install.sh/ps1 parse and persist preset at install time
- **Documentation:** `docs/INSTALL_PRESETS.md` — complete guide with examples

### Files
- `install.sh` — argument parsing + preset setup invocation
- `ops/launcher/corvin/preset_setup.py` — Python CLI for preset configuration
- `pyproject.toml` — entry point registration

---

## Phase 7a: Live Dashboard Trends (0.11.0)

### Added
- **Tier Distribution Chart:** Recharts bar chart showing tier breakdown over 7 days
- **Trend Visualization:** See features auto-promoting alpha → beta → stable
- **Mock Historical Data:** Sample 7-day trend for demonstration

### Files
- `core/console/corvin_console/web-next/src/components/FeatureStatusDashboard.tsx` — TrendChart component

---

## Phase 7b: Multi-Instance Sync (0.11.0)

### Added
- **Peer Discovery:** `/api/multi-instance/peers` — list A2A-paired devices
- **Metrics Aggregation:** `/api/multi-instance/metrics/aggregate` — cross-device metrics
- **Config Sync:** `/api/multi-instance/sync-config` — sync presets across devices
- **Sync Status:** `/api/multi-instance/sync-status/{peer_id}` — check sync health

### Files
- `core/console/corvin_console/api/multi_instance_sync.py` — endpoints (stubs with TODO for A2A integration)

---

## Phase 7c: Metrics Caching (0.11.0)

### Added
- **1-Hour TTL Cache:** Reduce repeated DB/memory accesses for metrics
- **Thread-Safe:** Lock-protected cache operations
- **Cache Stats:** Monitor cache hit rates and performance

### Files
- `core/telemetry/metrics_cache.py` — CachedMetrics class (60 lines, 3600s TTL)

---

## Phase 8: Deployment-Ready (0.11.0)

### Added
- **Version Bump:** 0.10.89 → 0.11.0 (feature release)
- **PyPI Metadata:** Update setup.py/pyproject.toml with new packages
- **Release Documentation:** This changelog

### Breaking Changes
- None (all features backward-compatible)

### Migration Guide
- **Existing installs:** Features default to alpha tier; set via `corvin-preset-setup`
- **Fresh installs:** Use `install.sh --preset standard` to choose upfront
- **CI/Automation:** Install with `--preset minimal --autostart` for no-UI mode

### Deprecations
- None

---

## Metrics-Driven Tier Progression

**Alpha → Beta:** 7 days + <5% error rate
**Beta → Stable:** 30 days + <1% error rate + >5% adoption + >100 invocations/day
**Stable → Production:** 60 days + <0.1% error rate + >25% adoption + >500 invocations/day
**Auto-Demotion:** Production: >1% error spike → immediate demotion to beta

---

## Known Limitations

- Multi-instance sync APIs are stubs (A2A integration deferred)
- Metrics history not yet persisted to database (in-memory only)
- Trend chart uses mock data (real data integration pending)
- Preset wizard in CLI not yet implemented

---

## Contributors

**Architecture & Design:** ADR-0286/0287/0288, CONCEPT-0007
**Implementation:** Phase 1–6.5 (telemetry, CLI, console, installer)
**Testing:** Tier 1–4 test layers (syntax, unit, integration, E2E)

---

## Installation & Usage

```bash
# Fresh install with preset
curl -fsSL https://corvin-labs.com/install.sh | sh -- --preset advanced

# Runtime preset change
corvin-preset-setup standard

# View feature status
corvin flag status

# CLI help
corvin flag --help
```

---

**Release Date:** 2026-08-10 (unreleased, ready for PyPI 0.11.0)
