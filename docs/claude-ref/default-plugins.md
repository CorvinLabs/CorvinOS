# Standard plugins — what a fresh installation gets

**The list:** [`core/console/corvin_console/default_plugins.yaml`](../../core/console/corvin_console/default_plugins.yaml).
It is the only place that decides which plugins a new CorvinOS installation installs. To bundle a
plugin, add an entry there; to stop bundling one, remove it (already-installed copies stay).

| # | Plugin | Index id | Min. version | Enabled by boot? |
|---|---|---|---|---|
| 1 | CorvinTV (formerly Video Producer) | `plugin:buildin-media-corvintv` | 1.7.0 | **Yes** — first-party (`buildin` tier: origin `vetted`, `requires_consent: false`, `pii_risk: medium`), so boot installs AND enables it; the registry id stays `video_producer` |
| 2 | Knowledge Graph | `plugin:buildin-knowledge_management-corvin_knowledge` | 1.2.0 | **Yes** — first-party (`buildin` tier: origin `vetted`, `requires_consent: false`, `pii_risk: low`), so boot installs AND enables it; the tenant knowledge base is created on the next projector tick (ADR-2265) |

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

## CorvinTV: a fresh install can make a video at once

CorvinTV (until 1.6.0 "Video Producer") is the first entry. Boot installs **and enables** it, so the
**CorvinTV** panel is in the sidebar and `/v1/console/video/settings` answers on the first start, with no
operator step. It reached that state the way the Knowledge Graph did: it moved from `plugins/contributor/`
(community origin, consent on enable) to `plugins/buildin/media/corvintv/` (first-party, reviewed, `vetted`;
maintainer decision 2026-10-19). `PluginRecord.consent_required()` is not narrowed — the plugin is simply no longer in
the class that needs consent.

**What it does, stated so nobody has to guess.** Nothing is contacted at boot or in the background. A video is made
only when an operator asks for one; the narration text (derived from the task, never the task's files) then goes to
the narration host: **OpenAI TTS first** (needs the operator's key) and **edge-tts** (Microsoft voices, no key) when
OpenAI declines — an exhausted, invalid or missing key falls back instead of stopping the job; if both decline the job
fails with a named error, never a silent video. Piper is not part of the chain. Every host is gated per job by the
console's L34/L35 checks (`egress_gate.DEFAULT_ENGINE_HOSTS`: `api.openai.com`, `speech.platform.bing.com`; the
optional storyboard backend adds `api.anthropic.com`), and declared in the manifest (`egress_hosts`, `pii_risk: medium`).
The default engine is `auto` (that chain); `openai` means OpenAI only and refuses instead of falling back. The
operator can disable or uninstall the plugin at any time; an uninstall sticks (the ledger remembers it was offered).

**The rename (`replaces:` in `default_plugins.yaml`).** The index id changed (`plugin:contributor-media-video_producer`
→ `plugin:buildin-media-corvintv`); the **registry id `video_producer`, the console API (`/v1/console/video/...`), the
tenant data folder and every audit event name did not**, so existing installs, videos and automations keep working.
A tenant that still carries the pre-1.7 community record is **upgraded** to the vetted listing at the next boot and
enabled; a tenant whose operator uninstalled it keeps it uninstalled. The console loader still finds the old
marketplace path (`plugins/contributor/media/video_producer`) as a fallback for a checkout that has not been updated.
The "already installed" case that opens the upgrade branch is the installer's typed `PluginAlreadyInstalled`
(`corvin_plugins.manifest`, `code == "already_installed"`, raised by `PluginLifecycle.install`), checked by type —
until 2026-10-21 `provision` matched the words "already installed" in the message (review PLG-13), so a rewording
would have failed every boot upgrade.

## Knowledge Graph: the tenant knowledge base (ADR-2265)

A new operator does nothing. Boot installs **and enables** the Knowledge Graph plugin; on the next
tick (≤ 2 s, no restart) the board projector (`corvin_console/kb_projection.py`) runs the plugin's
`src/kb_init.py`. It creates `<CORVIN_HOME>/tenants/<tenant>/knowledge` — a git repository
with the plugin's copy of the kb engine (`scripts/kb.py`, the same engine Corvin-Knowledge
uses) — and projects it onto the task board. The concept, ADR and implementation-plan gates
(G1–G3) and the review gate (G4) then work exactly as in Corvin-Knowledge: `python3
scripts/kb.py new …` / `check` in that directory, and the **ready** badge on an epic once G1–G4
hold. An explicitly configured KB (`CORVIN_KB_REPO`, or the sibling `Corvin-Knowledge` on the
canonical home) always wins.

**Why no consent step (maintainer decision 2026-10-17, ADR-2265 amendment).** Consent is never
granted on an operator's behalf, and `PluginRecord.consent_required()` is not narrowed. The plugin
simply is not in the class that needs it: it moved from `plugins/contributor/` (community origin)
to `plugins/buildin/` (first-party, reviewed, `vetted`). What it does, stated so nobody has to
guess: it writes one directory inside the tenant home, runs `git` and its vendored engine as local
subprocesses with a minimal environment, sends nothing anywhere and has no telemetry. Its only
network use is the panel's sync button, which the operator presses and which needs a `remote_url`
the operator sets (empty by default). The enable is audited (`marketplace.enable`, actor
`boot:default_plugins`; `kb.tenant_created` for the KB). The operator can disable or uninstall it
at any time; an uninstall sticks (the ledger remembers it was offered).

**Reclassification (`replaces:` in `default_plugins.yaml`).** The index id changed
(`plugin:contributor-…` → `plugin:buildin-…`). A tenant that still carries the pre-1.2 community
record is **upgraded** to the vetted listing at the next boot (`PluginLifecycle.upgrade` with
`approve_escalations=True`: a first-party listing may declare MORE than its community predecessor —
CorvinTV 1.7.0 widened `pii_risk` and egress over Video Producer 1.6.0, and without the approval the
upgrade was refused on every boot and the plugin stayed community/disabled; the approved escalations
are named in the boot result and the log) and enabled; a tenant whose operator
uninstalled the old listing keeps it uninstalled (`status: skipped`).

## Observe it

`GET /v1/console/api/v1/marketplace/default-plugins` (session required) returns the list with the tenant's
live state per entry: `offered`, `installed`, `enabled`, `installed_version`.

## Tests

* `core/console/tests/test_default_plugins.py` — list shape and refusal rules.
* `core/console/tests/test_default_plugins_corvintv_e2e.py` — fresh tenant: both standard plugins installed and enabled by one `provision`, the real route answers with the default chain; the pre-1.7 record is upgraded; an operator's uninstall is kept.
* `core/console/corvin_console/web-next/tests/e2e-fresh/default-plugins.spec.ts` — fresh instance, no
  local marketplace: boot alone installs AND enables CorvinTV and the Knowledge Graph (no consent step), the
  sidebar entry is CorvinTV, the panel API answers with the default chain, uninstall sticks. Run together with
  `video-producer-lifecycle.spec.ts`:
  `cd core/console/corvin_console/web-next && npx playwright test -c playwright.fresh-install.config.ts`
  (needs network and a built SPA — `scripts/console-deploy.sh`).
* `core/console/corvin_console/web-next/tests/e2e-fresh/knowledge-graph-default.spec.ts` — fresh instance:
  boot alone installs AND enables the Knowledge Graph (vetted, no consent), creates and binds
  `<tenant>/knowledge` without a restart or a click, no "enable the plugin" banner, work created with its
  `scripts/kb.py` reaches the board, gate findings go from G2+G3 to G4 only, the graph panel reads the same KB.
  Before a marketplace change is pushed: `CORVIN_FRESH_MARKETPLACE_REF=<commit>` takes the plugin source from a
  `git archive` of the sibling Corvin-Marketplace instead of GitHub (pre-push only; the release gate runs without it).
* `core/console/tests/test_default_plugins_knowledge_graph_e2e.py` — the boot call path in-process
  (`default_plugins.provision` → registry → HTTP list → `/kb/sync` → KB → board): fresh tenant, migration of a
  pre-1.2 community record, a predecessor uninstall kept, a second boot a no-op.
* `core/console/tests/test_tenant_kb_binding_e2e.py` — the binding after an operator disable/enable, through the HTTP routes.
* Restart persistence on the `corvinos-serve` host was verified by hand (boot → install, uninstall,
  restart → `already_offered`, stays uninstalled).

## Release hygiene for a bundled plugin

The installer reads **`plugin.yaml`**, not `plugin.json`. Bump `version` in `plugin.yaml` (and `provider.py`)
together with `plugin.json`, `setup.py` and the four index blocks — CorvinTV (then Video Producer) 1.2.0 shipped with
`plugin.yaml` still at 1.0.0 and a fresh install recorded 1.0.0. `min_version` here is checked against `plugin.yaml`.
