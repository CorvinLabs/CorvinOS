# CorvinOS Headless Core Architecture
## Stable OS Engine with Engine/Worker, Plugin Scoping, Tenant Isolation

**Date:** 2026-07-27
**Status:** ADR-0241 (proposed). Phases 0–5 shipped, Phase 6 **partial**, Phase 7 open —
see [Status per phase](#status-per-phase) for what "shipped" does and does not mean here.
**Audience:** Architecture, DevOps, Platform Team

---

## Read this first: the axis has a mechanism, not a population

Everything below describes the **`boot_layer`** axis. Two of its four values —
`compliance` and `core` — have **zero production instances today**:

* `_GLOBAL_SPECS` is empty and `register_global_plugin()` has **no production caller**,
  so `bootstrap_global()` is a no-op returning `[]` on every install;
* **no plugin anywhere loads with `boot_layer=compliance` or `boot_layer=core`**;
* consequently `registry.replace()` is **structurally unreachable** — it refuses any
  target that is not on the `core` boot layer, and no such target exists.

The mechanism is implemented, tested and enforced. It just has nothing on it yet. That
distinction is deliberate everywhere in this document: **"mechanism present, zero
instances"** is not the same claim as "load-bearing today", and conflating the two is how
this document went stale the first time.

The statement is **pinned by guard tests** in `core/plugins/tests/test_layered_boot.py`
(`TestTheTopOfTheAxisHasNoProductionInstance`): the day someone registers the first global
plugin or claims a privileged boot layer in production code, those tests fail with a
message that names this file. The claim cannot silently rot.

---

## Terminology: the axis is `boot_layer`, not `layer`, not `tier`

This document originally classified components with a numbered "tier" axis, values 0–3.
That name is taken: **"Tier A/B/C" means ADR-0156's capability boundary**, repo-wide, and
ADR-0233 D7 replaced the prototype's `tier` field with `origin` for provenance. The load
axis was therefore renamed to `layer` — and then renamed again, because **`layer` was
already taken four times** in this repository:

* the **L1–L44 layer stack** (CLAUDE.md § Layer Stack Overview);
* **ADR-0124 audit layers** (`core/console/corvin_console/routes/audit_layers.py`);
* the **ADR-0142 layer-extension API**, which answers 403 with
  `reason="core_layer_immutable"` (`routes/extensions.py`);
* **quality layers** (`routes/quality_layers.py`, `operator/bridges/shared/quality_layers.py`).

That is the same collision class the move away from "tier" was supposed to end. The axis is
therefore **`boot_layer`**, enum **`BootLayer`**, with four unchanged values —
`compliance`, `core`, `bundled`, `installed` (ADR-0243).

| Draft value | Current | Failure policy |
|---|---|---|
| 0 (compliance) | `boot_layer: compliance` | Boot fails (existing tripwire) |
| 1 (core infrastructure) | `boot_layer: core` | Degrade + audit event |
| 2 (bundled) | `boot_layer: bundled` | Disabled = quiet no-op |
| 3 (premium) | `boot_layer: installed` | Tenant-local failure only |

`boot_layer` answers *when is it loaded and may it be switched off*; `tier` (ADR-0156)
answers *what may it do, and what does it cost*; `origin` (ADR-0233 D7) answers *where did
it come from*. Three orthogonal axes, three separate fields.

**Renamed API surface** (all of it already carries the new name in code):
`registry.boot_layer_of()` · `registry.plugins_by_boot_layer()` ·
`registry.register(..., boot_layer=)` · `registry.replace(..., boot_layer=)` ·
`bootstrap._declared_boot_layer()` · `bootstrap.register_global_plugin(class_path,
boot_layer=)` · `PluginRecord.boot_layer` · the JSON/YAML key `boot_layer` · the audit
event `plugin.boot_layer_rejected` with detail keys `boot_layer` / `declared_boot_layer` ·
the admin-API response field `boot_layer` and the health aggregate `by_boot_layer`.

---

## The Shift: From Bridge-Centric to Core-Centric

### Current (Today)
```
CorvinOS = Compliance Core + Discord Bridge + Slack Bridge + Web UI + ...
           (tightly coupled, UI-driven)
```

**Problem:**
- Bridges ship with every install
- UI state affects server state (Web UI restart breaks API)
- Hard to deploy as a pure headless server
- Scaling: bridges block orchestration threads

### Target: Headless Core Engine (proposed)
```
CorvinOS Core Engine (Server Only, No UI)
├─ Compliance (boot_layer=compliance — no instances yet)
├─ Agentic Compute (boot_layer=core — no instances yet)
├─ Engine Control (boot_layer=core — no instances yet)
├─ A2A + TDE + Recall (boot_layer=core — no instances yet)
└─ HTTP API Gateway (REST — gRPC deferred, see below)

Bridges + front-ends (boot_layer=bundled / installed)
├─ Discord Bridge (Node daemon, supervised subprocess)
├─ Slack Bridge (Node daemon, supervised subprocess)
├─ Web UI (separate app, talks to the Core API)
└─ Voice (separate plugin)
```

The "no instances yet" annotations are the honest state, not a formatting quirk: the
compliance and core mechanisms listed above are today ordinary in-process modules, not
plugins registered on a boot layer. The boxes describe where they would land.

**Intended benefits** (of the target, not of today's tree):
- Core is pure backend (no UI entanglement)
- Bridges can fail without crashing the core
- Easier to scale (headless farm of worker nodes)
- Simpler to deploy (server + minimal plugins)
- Tenant isolation clearer (plugins are tenant-scoped)

---

## Current state (measured 2026-07-27)

Filter: **`*.py` only, excluding `node_modules/`, `.venv/`, `site-packages/`.** Quote the
filter with the number or it is not reproducible.

| Path | LOC |
|---|---|
| `operator/bridges/shared` | 181,127 |
| `core/console/corvin_console` | 65,305 |
| `core/plugins` | 19,151 |
| `core/plugins/corvin_plugins` (the contract itself) | 7,781 |
| `core/gateway` | 15,285 |
| `core/compliance` | 2,911 |

The compliance mechanisms are **not** where the target layout puts them: house rules
(L44), consent gate (L18) and the erasure orchestrator (L36) live in
`operator/bridges/shared/`; the audit writer, its hash chain and the boot tripwire live in
`core/compliance/corvin_compliance_reports/`. **None of**
`core/compliance/audit_writer.py`, `core/session/middleware.py` or
`core/routing/http_router.py` exists — those are target paths from ADR-0236, which is a
long-term target and explicitly **not** part of the current plan.

---

## Architecture: Headless Core + Plugin Scoping

### `boot_layer: compliance` — Core Compliance

Intended population: immutable regulatory mechanisms — audit writer + hash chain (L16),
consent gate (L18), flow guard (L34), house rules (L44), erasure orchestrator (L36).

**Actual population today: none.** No plugin loads on this boot layer. The regulatory
mechanisms listed above are enforced exactly as they always were — by the boot tripwire
(`core/compliance/corvin_compliance_reports/tripwire.py::assert_all`, no override, no env
var, no flag) and by their own in-process call sites, not by a plugin registration.

**Rules that apply the moment the first instance exists:** loaded first, tripwired at boot,
never disableable, never replaceable, never behind a feature flag in either direction.

---

### `boot_layer: core` — Core Infrastructure (bundled, required, replaceable)

**Actual population today: none.** Same status as `compliance`, with one extra
consequence worth naming: because `registry.replace()` only accepts a target that is on
the `core` boot layer, and no plugin is, **ADR-0237 full replacement is currently a
mechanism with no reachable input.** The rules below are tested; they have never run
against a real target.

#### Global plugin scope (target layout, ships in the wheel)
```
<repo>/core/core_plugins/          (TARGET — does not exist yet, Phase 7)

Global plugins (bundled with the wheel, loaded before any tenant plugin):
├─ compliance/
│  └─ audit_compliance/, consent_gate/, ...   (boot_layer=compliance)
└─ core/                                       (boot_layer=core)
   ├─ a2a_orchestration/
   ├─ tde_routing/
   ├─ conversation_recall/
   ├─ acs_manager/
   ├─ compute_worker/
   ├─ delegation_router/
   ├─ workflow_engine/
   ├─ engine_control/
   └─ admin_control_plane/
```

**Deployment (as designed):** bundled in the Python wheel, registered from code via
`register_global_plugin(class_path, boot_layer=...)`, and loaded by `bootstrap_global()`
before any tenant plugin.

**There is deliberately no discovery step.** A `corvin.global_plugins` entry-point group
was implemented and then **removed before it had a single user**: any third-party wheel on
the machine could have published `compliance:whatever` and been loaded first, undisableable,
with no `PluginRecord` and therefore past the privileged-boot-layer gate, the consent prompt
and the L34/L35 fields. `bootstrap.GLOBAL_ENTRY_POINT_GROUP` is `None` and stays `None`;
re-adding discovery needs signature verification and an allowlist, not an entry-point name.
Earlier revisions of this document described the entry-point group as if it shipped. It
does not.

**Status:** the loader exists (Phase 2) and is exercised only by tests. The directory
layout above does **not** exist — the move is Phase 7 of ADR-0242 and lands with import
shims, deliberately last, because relocating the audit writer touches the live GDPR hash
chain.

---

### `boot_layer: bundled` / `boot_layer: installed` — Tenant Plugins (scoped, optional)

These two are the only boot layers with any reachable path today, and even here the
population is thin: the seven bridge supervisor classes exist and are tested, but **no
shipped config declares them** (see [Bridge Deployment Model](#bridge-deployment-model)).

**Real on-disk layout** (verified — note the **lowercase** `global`, which matters on a
case-sensitive filesystem):

```
~/.corvin/tenants/_default/
├─ global/
│  └─ tenant.corvin.yaml          declarative source: spec.plugins.installed
├─ plugins/
│  ├─ registry.yaml               the runtime registry (PluginRecord.to_dict(), 0600)
│  └─ instances/<plugin_id>/      per-plugin state, removed on uninstall
├─ audit.jsonl                    hash-chained audit trail
├─ sessions/ · voice/ · cowork/ · compute/ · datasource_connections/
```

There is **no `plugin_config.yaml`** anywhere in the repo — earlier revisions of this
document invented it. Tenant plugin state lives in `plugins/registry.yaml`; tenant plugin
*declarations* live in `spec.plugins.installed` of `global/tenant.corvin.yaml`.

**Deployment:** the operator can install, enable and disable these per tenant.

**Trust boundary:** a tenant-scoped declaration may claim `bundled` or `installed`
**only**. A privileged claim (`compliance` / `core`) from `tenant.corvin.yaml` or
`registry.yaml` is downgraded to `installed` and audited (`plugin.boot_layer_rejected`),
never honoured. Otherwise undisableability would be self-service via one YAML line. This
guard *is* exercised — by tests, and it is the reason the two privileged boot layers can
stay empty without being unsafe.

---

## Plugin Scoping: Global vs. Tenant

### Implemented boot API (`core/plugins/corvin_plugins/bootstrap.py`)

```python
def register_global_plugin(class_path: str, *, boot_layer: BootLayer | str) -> None:
    """Declare a bundled global plugin and the boot layer it boots on.
    Refuses anything but compliance/core — tenant-scoped boot layers are declared
    in tenant config, not in the wheel.

    NO PRODUCTION CALLER TODAY.  Pinned by
    test_layered_boot.py::test_register_global_plugin_still_has_no_production_caller.
    """


def bootstrap_global(*, tenant_id: str, corvin_home: Path, **registries) -> list[str]:
    """Load the bundled global plugins, compliance boot layer first.

    NOT behind a feature flag, deliberately: the boot layer it exists to load is
    the compliance boot layer, and CLAUDE.md forbids putting a compliance
    mechanism behind a switch.  With no bundled global plugins registered this is
    a no-op returning [] — which is what it does on EVERY install today, so the
    flagless path currently changes nothing anywhere.

    * compliance failure — raises GlobalComplianceLoadFailed; the boot aborts.
    * core failure       — logged, audited, skipped; the platform boots degraded.

    Both branches are unreachable in production until a global plugin exists.
    """


def bootstrap_all(...):
    """global → declarative → runtime, deduplicated.
    A compliance abort survives all three passes.
    Wired into core/gateway/corvin_gateway/app.py::_lifespan."""
```

Tenant identity comes from the resolver chain `current_tenant()` →
`validate_tenant_id()` → `tenant_home()` (ADR-0007). No plugin path is ever assembled from
a raw env var.

### Boot sequence (as implemented)

`bootstrap_all()` runs three passes in this order, and this is the whole of it:

```
1. global      — bootstrap_global():   compliance first, then core.
                 Returns [] on every install today.
2. declarative — bootstrap_declared(): spec.plugins.installed from tenant.corvin.yaml
3. runtime     — bootstrap_tenant():   plugins/registry.yaml, gated on
                 plugin_runtime_lifecycle
```

The compliance abort reuses the tripwire that already exists and that has no override — no
env var, no flag. No second fail-closed mechanism is introduced.

**What is NOT implemented:** the reordered "start the HTTP server between `core` and
`bundled`" sequence that earlier revisions of this document showed as the headless boot
path. `bootstrap_all()` runs to completion before the gateway serves, exactly as it did
before Phase 6. See [Phase 6](#phase-6--partial) for what headless mode actually changes.

---

## Directory Structure

```
<repo>/
├─ core/
│  ├─ compliance/                 (compliance mechanisms; today:
│  │  └─ corvin_compliance_reports/  audit writer, hash chain, tripwire)
│  │
│  ├─ core_plugins/               (TARGET — does not exist yet, Phase 7)
│  │  ├─ compliance/
│  │  ├─ core/
│  │  └─ bundled/
│  │
│  ├─ plugins/                    (the plugin contract — EXISTS today)
│  │  └─ corvin_plugins/
│  │     ├─ manifest.py           (BootLayer, PluginRecord.boot_layer/.replaces)
│  │     ├─ registry.py           (boot_layer_of, plugins_by_boot_layer,
│  │     │                         can_disable, replace)
│  │     ├─ bootstrap.py          (bootstrap_global / _declared_boot_layer /
│  │     │                         bootstrap_all)
│  │     ├─ extension_points.py   (the hook bus — all 4 points wired)
│  │     ├─ bridges/              (7 supervisor classes — nowhere declared)
│  │     ├─ loader.py
│  │     └─ providers/            (8 provider registries, ADR-0033)
│  │
│  ├─ console/corvin_console/     (Console app + /api/admin/* routes)
│  └─ gateway/                    (HTTP surface; calls bootstrap_all())
│
~/.corvin/
├─ tenants/
│  └─ _default/
│     ├─ global/
│     │  └─ tenant.corvin.yaml    (spec.features.*, spec.plugins.installed)
│     ├─ plugins/
│     │  ├─ registry.yaml         (boot_layer=bundled + boot_layer=installed)
│     │  └─ instances/<plugin_id>/
│     └─ audit.jsonl              (hash-chained audit trail)

operator/bridges/                 (UNCHANGED — the Node daemons)
├─ discord/daemon.js
├─ slack/daemon.js
├─ telegram/daemon.js
├─ whatsapp/daemon.js
├─ signal/daemon.js
├─ teams/daemon.js
├─ email/daemon.js
└─ bridge_manager.py              (existing Python process supervisor)
```

---

## API Server: the only entry point

### REST API (no UI dependency)

The routes that actually exist on the gateway and the Console router:

| Route | What it is |
|---|---|
| `GET /healthz` | gateway liveness probe, unauthenticated by design |
| `GET /v1/console/api/admin/plugins` | list plugins — behind `admin_control_plane` |
| `GET /v1/console/api/admin/plugins/{id}` | plugin detail |
| `POST /v1/console/api/admin/plugins/{id}/enable` | enable |
| `POST /v1/console/api/admin/plugins/{id}/disable` | disable — 403 on `compliance` |
| `PUT /v1/console/api/admin/plugins/{id}/config` | replace stored settings (**PUT**, not POST) |
| `GET /v1/console/api/admin/health` | **aggregated** health — there is no per-plugin health endpoint |

The admin router declares `/api/admin/*`; the gateway mounts the Console router at
`/v1/console`, so the effective path in a default install is `/v1/console/api/admin/*`.

There is **no `/health` route** (it is `/healthz`) and **no `POST /api/chat`** — earlier
revisions of this document showed both as code samples. Neither exists. The chat surface is
the existing Console chat routes, not a route invented for this document.

The list response carries `plugin_id`, `version`, `display_name`, `plugin_type`,
`boot_layer`, `origin`, `enabled`, `runtime_loaded`, `can_disable`, `source` and `health`.
It deliberately carries **no `tier` field**: `tier` is ADR-0156's capability boundary, not
a lifecycle property, and putting it in a lifecycle response is exactly the conflation the
rename exists to prevent.

Admin routes sit behind the `admin_control_plane` flag (default `false`); off means the
routes are absent (404), not an error. Auth uses the existing `SessionRecord`; the tenant
comes from `rec.tenant_id`, never an env var (ADR-0007).

Full reference: [ADMIN_CONTROL_POINTS.md](ADMIN_CONTROL_POINTS.md).

### Headless mode — partial (Phase 6)

`headless_api_mode` (default `false`, Console → Settings → Features) decides whether
this process serves a **browser surface**. Exactly three things change when it is on:

1. `mount_static()` returns without mounting the SPA and registers no fallback route —
   `/console` 404s. The friendly "SPA not built" placeholder is deliberately also
   suppressed: a placeholder is still a browser surface.
2. The gateway does not register the `/local-stats` HTML dashboard.
3. `GET /` returns `{"status": "ok", "version": …, "ui": "headless"}` instead of
   redirecting to `/console/`.

That is the entire behavioural surface of the flag. It does **not** reorder the boot
sequence and it does **not** introduce deployment presets — see below.

Two properties are worth stating because they are easy to assume wrongly:

**It is a process property read from a tenant flag.** The SPA mount belongs to the
process, not to a tenant, so `headless_enabled()` resolves once for the boot tenant and
then applies process-wide. A second tenant in the same process does not get its UI back.
An env var would have modelled this more naturally, but CLAUDE.md requires new features
to be toggleable from Settings, and this repo has ruled out env kill-flags. If
per-process configuration becomes a real requirement, it needs its own mechanism rather
than a second meaning for this flag.

**It does NOT switch off bridges.** That is `bridge_supervisor_plugins`, and the two are
independent on purpose. Coupling them would make "core + CLI + bridges, no browser UI"
unreachable. A guard test (`test_headless_mode.py::TestNoHiddenCoupling`) fails if the
bridge supervisor ever starts reading `headless_api_mode`.

### It is SELF-LOCKING — the off-ramp is the CLI

`headless_api_mode` is the one flag in the registry that removes the surface which could
switch it back off. Every other flag is reversible where it was flipped; this one deletes
`/console/`, and with it Settings → Features. CLAUDE.md's rule — *"toggleable from the
Console, no file editing, no restart"* — silently assumes reversibility at the same
surface, and this flag is the counter-example. It is not a rollout flag, it is a
deployment mode wearing a rollout flag's clothes.

Two surfaces close the trap. Neither is an env var: an env override would be the
kill-flag shape this repo has ruled out, and it would not survive the next process.

**1. The CLI off-ramp (recovery).**

```bash
corvin config set features.headless_api_mode false
# then restart the service
```

`cli.py::_set_feature_flag_config` handles any `features.<flag_id>` key and writes the
same per-tenant overlay the Settings route writes
(`feature_flags.set_enabled` → `<tenant>/global/features.json`). That overlay is the
**highest-precedence** layer, so it also overrides a `spec.features.headless_api_mode:
true` entry in `tenant.corvin.yaml` — the operator never has to edit YAML to get out.
The value must be a boolean spelling (`true|yes|1|on|enabled` /
`false|no|0|off|disabled`); anything else is refused rather than stored as a truthy
string. An unregistered flag id is refused and the registry is printed.

**A restart is required, and the CLI says so.** `mount_static()` runs once when the app
is created, so writing the file does not remount the SPA into a running headless process.
The gateway's `_headless_ui()` *is* read per request — that only keeps its redirect
targets honest, it does not mount anything.

**2. The confirmation gate (prevention).** The registry entry carries
`self_locking=True`, and `describe_all()` exports `self_locking` plus a
`recovery_command` string. The Settings panel renders a warning icon, a "no way back
from the UI" badge, and — instead of writing straight through — a confirmation dialog
that names the consequence and prints the recovery command *before* the door shuts, which
is the last moment the operator can read it there. Nothing is persisted until they
confirm.

The UI decides this from the `self_locking` field, **never** from a hard-coded flag id, so
the next self-locking flag inherits the warning automatically. The gate lives in the UI
and not in the REST route on purpose: the route has to stay a plain `PUT` so the CLI
off-ramp can write the flag with no dialog anywhere.

Tests: `ops/launcher/corvin/tests/test_config_features_cli.py` (CLI parse, precedence over
YAML, refusals, no env-var override) and `core/console/tests/test_settings_features_api.py`
(the `self_locking` / `recovery_command` contract the UI renders from, plus a
console-locks-it / CLI-unlocks-it round trip).

### Deployment models = flag combinations, not a preset mechanism

There is **no "deployment mode" setting and no preset mechanism** — `grep -rn preset`
over `core/plugins`, `core/gateway` and the flag registry returns nothing. The four models
below are simply combinations of two independent flags, which is why none of them needed
new code, and why none of them is selectable by name:

| Model | `headless_api_mode` | `bridge_supervisor_plugins` | Result |
|---|---|---|---|
| **A — Complete** | `false` | `true` | Console UI + supervised bridges |
| **B — Typical (today's default)** | `false` | `false` | Console UI; bridges managed by `bridge.sh` / systemd as before |
| **C — API only** | `true` | `false` | REST API only; no UI, no supervised bridges |
| **D — Custom UI / CLI** | `true` | `true` | No built-in UI, bridges still supervised; an external UI talks to the API |

Model B is what a fresh install gets, because both flags ship dark. Models A and D
additionally require the operator to write bridge declarations into
`spec.plugins.installed` by hand — nothing ships them.

### gRPC: deferred — not in Phase 1

An earlier draft of this document specified a `corvin.proto` service alongside REST.
**Decision (ADR-0239): gRPC is deferred.** It is a pure additional dependency with no
consumer asking for it, and REST over the existing session auth covers every known caller.
This is neither "done" nor "planned" — it is deferred, to be revisited only when a
concrete consumer exists that REST cannot serve.

Consequently, no latency claim ("bridges need <100 ms roundtrip") is made here: it was
never measured, and the transport it argued for is not being built.

---

## Bridge Deployment Model

### What the bridges are

All seven bridges — **discord, slack, telegram, whatsapp, signal, teams, email** — are
**Node.js daemons** at `operator/bridges/<name>/daemon.js`, supervised today by
`operator/bridges/bridge_manager.py`. They are **not** Python modules, there is no
`adapters/` package, and rewriting them into `CorvinPlugin` subclasses is not on the
table — it would discard working, tested code for no functional gain (ADR-0238).

### The model: one Python supervisor plugin per bridge

The generic `BridgeSupervisorPlugin` plus seven thin subclasses. The real shape:

```python
# corvin_plugins.bridges.supervisor
class BridgeSupervisorPlugin:
    plugin_type = "bridge_channel"

    def __init__(self, channel: str, *, bridge_manager=None, ...):
        self.plugin_id = f"{channel}-bridge"      # e.g. "discord-bridge"

    def on_load(self, ctx): ...      # six-condition start gate, never raises
    def on_unload(self): ...         # SIGTERM → 5 s → SIGKILL → 2 s → abandon
    def health_check(self): ...      # reaps, reports, never guesses


class DiscordBridgePlugin(BridgeSupervisorPlugin): ...   # + 6 siblings
```

Process knowledge is **borrowed from** `bridge_manager.py`, through the functions that
actually exist there: `channel_daemon_running()`, `adapter_running_pid()` and
`start_channel_detached()`. There is no `bridge_manager.start()` / `.stop()` / `.health()`
triple — earlier revisions of this document showed a `DiscordBridgeSupervisor` class with
`plugin_id = "discord-bridge-supervisor/1.0.0"` calling those three functions. **None of
those four names exists.**

**Reach today: none.** The seven classes are implemented and covered by 78 tests, but
`corvin_plugins.bridges.registry_entries.declaration_entry()` only *generates* the
`spec.plugins.installed` entry — **nothing in the shipped tree declares one**, and the
shipped `tenant.corvin.yaml` template carries no bridge block. So no supervisor is loaded
on any install, independent of the feature flag.

**Load modes:**

| Component | Mode |
|---|---|
| `boot_layer=compliance`, `boot_layer=core` | in-process (no instances yet) |
| Bridge supervisor plugins | in-process Python objects (declared nowhere yet) |
| Bridge daemons | **subprocess** — that is what they already are, and it is what gives the isolation this document wants |
| Web UI | separate app, talks to the Core API |

There is no in-process load mode for a Node daemon, so the earlier "Option A / B / C"
choice does not exist: the hybrid *is* the architecture.

Bridge supervision ships behind `bridge_supervisor_plugins` (default `false`); off means
bridges are managed exactly as they are today. Full reference:
[BUNDLED_BRIDGES_STRUCTURE.md](BUNDLED_BRIDGES_STRUCTURE.md).

---

## Stability Improvements with a Headless Core

These are the properties the target architecture is meant to buy. None of them is
measured today, because the boot layers they rest on are empty.

### 1. Engine/Worker isolation
```
Engine selection + worker management (boot_layer=core, in-process)
├─ Request arrives via the API
├─ Engine control selects the engine
├─ Delegation router picks ACS / TDE / native
└─ Worker executes (in-process or remote)

Worker fails → core continues (degrade to native)
Bridge fails → core continues (API still answers)
```

Note the operator-facing rule that already applies today, independent of all of this:
every degrade ladder ends at **`native`**, never at another delegation engine.

### 2. No UI restart = no service disruption
```
OLD: Web UI restart → API down → users disconnected
NEW: Bridge restart → API continues → one channel briefly disconnected
```

### 3. Worker scaling
```
CorvinOS Core (fixed, stable)   →   N worker pools (stateless, scale independently)
```

### 4. Easier testing
```
pytest core/ -v                          # core without any bridge
bash operator/bridges/run-all-tests.sh   # bridges on their own (mandatory before
                                         # committing adapter.py / daemon.js / shared/js)
```

---

## Tenant Plugin Isolation

**Guarantee:** a tenant installs a broken plugin → only that tenant is affected.

- Global plugin failure: `compliance` = boot fail, `core` = degrade + audit, all tenants.
  **Neither branch is reachable today** — there are no global plugins.
- Tenant plugin failure: logged, audited, tenant-local; the platform stays up. This one
  *is* live: it is the path every declarative and registry plugin takes.
- A tenant declaration cannot claim a privileged boot layer (`_declared_boot_layer()`
  downgrades it to `installed` and audits `plugin.boot_layer_rejected`), and `state.py`
  applies the same downgrade to a `registry.yaml` record.
- Every install/enable/disable writes an audit event carrying `tenant_id`, `plugin_id`
  and `boot_layer`.

The registry keeps the boot layer per plugin (`boot_layer_of`, `plugins_by_boot_layer`) and
refuses an operator-initiated unload of a compliance plugin
(`unregister(operator_initiated=True)` → `PluginDisableRefused`), so an admin route cannot
reach past `disable()` and unload the audit writer by calling the primitive. That refusal
is tested; it has never fired in production, because nothing is on the compliance boot
layer to refuse.

---

## Config: what actually exists

```yaml
# ~/.corvin/tenants/_default/global/tenant.corvin.yaml     (lowercase `global`)

spec:
  features:
    headless_api_mode: false        # ship dark; default off
    admin_control_plane: false
    bridge_supervisor_plugins: false
    plugin_extension_points: false

  plugins:
    auto_discover_entry_points: false
    installed:
      - id: discord-bridge
        boot_layer: bundled
        class_path: "corvin_plugins.bridges.supervisor:DiscordBridgePlugin"
```

`spec.plugins.installed` is read by `bootstrap_declared()`; each entry's `boot_layer` is
resolved by `_declared_boot_layer()` and clamped to `bundled` / `installed`.

**Keys that do NOT exist and were invented by earlier revisions of this document** — each
verified to have no reader anywhere in the repo:

| Fabricated key | Reality |
|---|---|
| `spec.server.mode` / `.host` / `.port` / `.api` | no reader. Host and port come from the gateway's own startup arguments, not from tenant config |
| `spec.plugins.bundled.enabled` / `.disabled` | no reader. The real key is `spec.plugins.installed`, whose entries carry `boot_layer:` |
| `plugin_config.yaml` | no such file. Tenant plugin state is `plugins/registry.yaml` |
| `~/.corvin/tenants/_default/GLOBAL/` | the directory is lowercase `global` — the uppercase form simply does not resolve on Linux |

Global plugins (`boot_layer=compliance`, `boot_layer=core`) are **not** listed in tenant
config — they ship in the wheel and are registered from code. Disabling a bundled bridge
means its daemon is not started; it is a quiet path, never an error.

---

## Status per phase

Measured 2026-07-27. "Shipped" below means the code exists and its tests pass — read the
qualifier column before treating any row as reach.

| Phase | Objective | Status | The qualifier that matters |
|---|---|---|---|
| 0 | ADR consolidation | ✅ done | — |
| 1 | Boot-layer-aware registry | ✅ done | the two privileged values have zero instances |
| 2 | Boot order + scoping | ✅ done | `bootstrap_global()` returns `[]` on every install |
| 3 | Extension points | ✅ **all 4 wired** | ADR-0251, 2026-07-27; guard-tested per point in both directions |
| 4 | Admin control plane (REST) | ✅ done | REST only; gRPC deferred, not planned |
| 5 | Bridge supervisor plugins | ✅ **wired** | the boot path declares the bundled seven when the flag is on (2026-07-27) |
| 6 | Headless API-only boot | ◑ **partial** | browser surfaces suppressed ✅; **no reordered boot sequence**, **no preset mechanism** |
| 7 | Directory move + shims, docs, v0.11.0 | ⬜ open | `core/core_plugins/` does not exist |

### Phase 6 — partial

What shipped: the three browser-surface suppressions listed under
[Headless mode](#headless-mode--partial-phase-6), plus the guard test that keeps the flag
independent of `bridge_supervisor_plugins`.

What did **not** ship, and is what the phase originally promised:

* the reordered boot sequence `compliance → core → HTTP server → bundled (async)`. Today
  `bootstrap_all()` completes before the gateway serves; a bridge that never comes up
  cannot block readiness only because no bridge supervisor is loaded at all, which is
  luck, not design.
* deployment presets A–D as a **mechanism**. They are documentation of two flags' four
  combinations; there is no `preset` key, no preset resolver, and nothing to select.

### Measured test counts (2026-07-27, not targets)

| Suite | Passing |
|---|---|
| `core/plugins/tests/` (whole suite) | **783** |
| ├─ `test_layered_boot.py` | 25 |
| ├─ `test_layer_registry.py` | 38 |
| ├─ `test_bootstrap.py` | 34 |
| ├─ `test_extension_points.py` | 54 |
| └─ `test_bridge_supervisor.py` | 78 |
| `core/console/tests/test_admin_route.py` | **24** |
| `core/console/tests/test_headless_mode.py` | **9** |

---

## Status per ADR

| ADR | Topic | Status |
|---|---|---|
| ADR-0243 | Core vs. plugins, defines `boot_layer` | Proposed; axis implemented in the registry, **zero instances above `bundled`** |
| ADR-0240 | Plugin scoping (global vs. tenant) | Proposed; boot order + scoping implemented; the global pass is a no-op on every install |
| ADR-0241 | Headless core architecture (this doc) | Proposed; browser-surface suppression implemented, boot-path reorder + presets **not** |
| ADR-0239 | Admin control plane vs. Web UI | Proposed; **REST plane implemented**, gRPC deferred |
| ADR-0238 | Bundled bridges — supervisor over Node daemons | Proposed; supervisor classes implemented, **never declared** |
| ADR-0237 | Extensible core plugins + `replaces:` | Proposed; `replaces` + `registry.replace()` + the hook bus implemented; `replace()` structurally unreachable, hooks unwired |
| ADR-0236 | Minimal core specification | Long-term target, **not** scheduled |
| ADR-0242 | Implementation plan, Phases 0–7 | 0–5 shipped with the qualifiers above, 6 partial, 7 open |

---

## Summary: Headless Core Benefits

Target-state comparison. Nothing in the right-hand column is measured on today's tree.

| Aspect | Before (bridged) | After (headless) |
|--------|-----------------|-----------------|
| **Core restart** | Breaks API + Discord | Bridges restart independently |
| **Scaling** | Whole app scales | Core + workers scale separately |
| **Testing** | Must mock Discord/Slack | Test the core API on its own |
| **Stability** | One plugin can crash all | Isolated failures (tenant + bridge-specific) |
| **Deployment** | Monolith (one container) | Core + bridge subprocesses, optionally separate containers |
| **Development** | All in one process | Easier to debug (separate processes) |

Maintained by **Corvin Labs (solo maintainer)** — support is best-effort with no
contractual SLA, and nothing in this document promises a response time.

---

## Next Steps

1. **Wire the four extension points** into their call sites (engine/provider selection,
   `delegation_policy`, the workflow gate). Until then Phase 3 is bus-only.
2. ~~**Declare the bridge supervisors.**~~ Done 2026-07-27: the boot path injects
   them from `bridges/registry_entries.py`, because `bundled` means "ships with
   CorvinOS" and a dotted class path in `tenant.corvin.yaml` is the `installed`
   contract. An operator entry for a channel still wins over the injected one.
3. **Finish Phase 6** — the reordered boot sequence, or a decision that the reorder is not
   wanted, in which case remove it from ADR-0241 rather than leaving it as an open promise.
4. **First real instance on a privileged boot layer.** The moment one exists, the guard
   tests in `test_layered_boot.py` fail and this document must be updated in the same
   commit — that is the mechanism keeping this file honest.
5. **Phase 7** — move to `core/core_plugins/{compliance,core,bundled}/` behind import
   shims, with `voice-audit verify` exit-0 checked before and after.
