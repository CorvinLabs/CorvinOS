# Standard plugins — what a fresh installation gets

**The list:** [`core/console/corvin_console/default_plugins.yaml`](../../core/console/corvin_console/default_plugins.yaml).
It is the only place that decides which plugins a new CorvinOS installation installs. To bundle a
plugin, add an entry there; to stop bundling one, remove it (already-installed copies stay).

| # | Plugin | Index id | Min. version | Enabled by boot? |
|---|---|---|---|---|
| 1 | Video Producer | `plugin:contributor-media-video_producer` | 1.2.0 | No — its manifest has `requires_consent: true`; the operator enables it (Marketplace → Enable) |
| 2 | Knowledge Graph | `plugin:contributor-knowledge_management-corvin_knowledge` | 1.1.0 | No — `enable: true`, but a contributor plugin is `community` origin, so consent is required. The task board shows a banner until the operator enables it; the enable creates the tenant's knowledge base and binds it to the board (see below) |

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

## Knowledge Graph: the tenant knowledge base (ADR-2265)

Enabling the Knowledge Graph plugin is all a new operator does. On its next tick (≤ 2 s, no
restart) the board projector (`corvin_console/kb_projection.py`) runs the plugin's
`src/kb_init.py`. It creates `<CORVIN_HOME>/tenants/<tenant>/knowledge` — a git repository
with the plugin's copy of the kb engine (`scripts/kb.py`, the same engine Corvin-Knowledge
uses) — and projects it onto the task board. The concept, ADR and implementation-plan gates
(G1–G3) and the review gate (G4) then work exactly as in Corvin-Knowledge: `python3
scripts/kb.py new …` / `check` in that directory, and the **ready** badge on an epic once G1–G4
hold. An explicitly configured KB (`CORVIN_KB_REPO`, or the sibling `Corvin-Knowledge` on the
canonical home) always wins. Details: [task-tracking-ssot.md](task-tracking-ssot.md) and the
plugin's `docs/TENANT_KNOWLEDGE_BASE.md`.

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
* `core/console/corvin_console/web-next/tests/e2e-fresh/knowledge-graph-default.spec.ts` — fresh instance:
  boot alone installs the Knowledge Graph (disabled), the board banner names the missing click, consent
  gate holds, enable creates and binds `<tenant>/knowledge` without a restart, work created with its
  `scripts/kb.py` reaches the board, gate findings go from G2+G3 to G4 only, the graph panel reads the same KB.
  Before a marketplace change is pushed: `CORVIN_FRESH_MARKETPLACE_REF=<commit>` takes the plugin source from a
  `git archive` of the sibling Corvin-Marketplace instead of GitHub (pre-push only; the release gate runs without it).
* `core/console/tests/test_tenant_kb_binding_e2e.py` — the same binding through the HTTP routes in-process.
* Restart persistence on the `corvinos-serve` host was verified by hand (boot → install, uninstall,
  restart → `already_offered`, stays uninstalled).

## Release hygiene for a bundled plugin

The installer reads **`plugin.yaml`**, not `plugin.json`. Bump `version` in `plugin.yaml` (and `provider.py`)
together with `plugin.json`, `setup.py` and the four index blocks — Video Producer 1.2.0 shipped with
`plugin.yaml` still at 1.0.0 and a fresh install recorded 1.0.0. `min_version` here is checked against `plugin.yaml`.
