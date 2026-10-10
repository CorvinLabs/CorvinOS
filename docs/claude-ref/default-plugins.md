# Standard plugins — what a fresh installation gets

**The list:** [`core/console/corvin_console/default_plugins.yaml`](../../core/console/corvin_console/default_plugins.yaml).
It is the only place that decides which plugins a new CorvinOS installation installs. To bundle a
plugin, add an entry there; to stop bundling one, remove it (already-installed copies stay).

| # | Plugin | Index id | Min. version | Enabled by boot? |
|---|---|---|---|---|
| 1 | Video Producer | `plugin:contributor-media-video_producer` | 1.4.1 | No — its manifest has `requires_consent: true`; the operator enables it (Marketplace → Enable) |

## Entry format

```yaml
schema: 1
plugins:
  - index_id: plugin:contributor-media-video_producer   # marketplace index id (required)
    registry_id: video_producer                          # id in the tenant registry (display + state lookup)
    name: Video Producer
    min_version: "1.2.0"     # the marketplace listing must be at least this, else the entry is skipped
    enable: false            # true = enable after install, but ONLY if the plugin needs no consent
    reason: why it is bundled
```

Entries install top to bottom (a dependency must be listed before its dependent). A malformed
file is refused as a whole (`DefaultPluginsError`) — it never installs a subset.

## How it runs

1. Both hosts call `default_plugins.start()` at boot (`corvin_console.standalone` — what `corvinos-serve`
   and `install.sh` run — and `corvin_gateway.app`; also `corvin_console.app`'s lifespan). It runs in a
   daemon thread, once per process and tenant, and never blocks or aborts boot.
2. `provision()` syncs the Corvin-Marketplace source (local checkout, else GitHub) and installs each
   entry through the same gates as the marketplace UI: index allowlist, ADR-0247 manifest gate,
   location-derived origin, `PluginLifecycle.install`, audit (`marketplace.install`, actor `boot:default_plugins`).
3. **Once per tenant per entry.** `<tenant>/plugins/default_plugins.json` (mode 0600) records every
   entry that was offered. A plugin the operator uninstalls is not brought back by the next boot.
4. **Failures are retried.** A failed or skipped entry (offline first boot, marketplace too old) is not
   written to the ledger, so the next boot tries again.
5. **Consent is never granted on the operator's behalf** (no auto-admit). A consent-gated plugin is
   installed and left disabled.

## Observe it

`GET /v1/console/api/v1/marketplace/default-plugins` (session required) returns the list with the tenant's
live state per entry: `offered`, `installed`, `enabled`, `installed_version`.

## Tests

* `core/console/tests/test_default_plugins.py` — list shape and refusal rules.
* `core/console/corvin_console/web-next/tests/e2e-fresh/default-plugins.spec.ts` — fresh instance, no
  local marketplace: boot alone installs the Video Producer (disabled), consent gate holds, enable works,
  sidebar entry appears, uninstall sticks. Run together with `video-producer-lifecycle.spec.ts` (27 tests):
  `cd core/console/corvin_console/web-next && npx playwright test -c playwright.fresh-install.config.ts`
  (needs network and a built SPA — `scripts/console-deploy.sh`).
* Restart persistence on the `corvinos-serve` host was verified by hand (boot → install, uninstall,
  restart → `already_offered`, stays uninstalled).

## Release hygiene for a bundled plugin

The installer reads **`plugin.yaml`**, not `plugin.json`. Bump `version` in `plugin.yaml` (and `provider.py`)
together with `plugin.json`, `setup.py` and the four index blocks — Video Producer 1.2.0 shipped with
`plugin.yaml` still at 1.0.0 and a fresh install recorded 1.0.0. `min_version` here is checked against `plugin.yaml`.
