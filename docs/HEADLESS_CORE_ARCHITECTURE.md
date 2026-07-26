# CorvinOS Headless Core Architecture
## Stable OS Engine with Engine/Worker, Plugin Scoping, Tenant Isolation

**Date:** 2026-07-26
**Status:** ADR-0241 (proposed) · boot-order groundwork implemented (Phases 1–2, commit c455516) · headless boot path itself still open (Phase 6)
**Audience:** Architecture, DevOps, Platform Team

---

## Terminology: the axis is `layer`, not `tier`

This document originally classified components with a numbered "tier" axis, values 0–3.
That name is taken: **"Tier A/B/C" means ADR-0156's capability boundary**, repo-wide, and
ADR-0233 D7 replaced the prototype's `tier` field with `origin` for provenance. The load
axis is therefore called **`layer`**, with four values — `compliance`, `core`, `bundled`,
`installed` (ADR-0243).

| Draft value | Current | Failure policy |
|---|---|---|
| 0 (compliance) | `layer: compliance` | Boot fails (existing tripwire) |
| 1 (core infrastructure) | `layer: core` | Degrade + audit event |
| 2 (bundled) | `layer: bundled` | Disabled = quiet no-op |
| 3 (premium) | `layer: installed` | Tenant-local failure only |

`layer` answers *when is it loaded and may it be switched off*; `tier` (ADR-0156) answers
*what may it do, and what does it cost*; `origin` (ADR-0233 D7) answers *where did it come
from*. Three orthogonal axes, three separate fields.

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

### New: Headless Core Engine (proposed)
```
CorvinOS Core Engine (Server Only, No UI)
├─ Compliance (layer=compliance, hardcoded)
├─ Agentic Compute (layer=core, in-process or remote)
├─ Engine Control (layer=core, local)
├─ A2A + TDE + Recall (layer=core, in-process)
└─ HTTP API Gateway (REST — gRPC deferred, see below)

Bridges + front-ends (layer=bundled / installed)
├─ Discord Bridge (Node daemon, supervised subprocess)
├─ Slack Bridge (Node daemon, supervised subprocess)
├─ Web UI (separate app, talks to the Core API)
└─ Voice (separate plugin)
```

**Benefits:**
- ✅ Core is pure backend (no UI entanglement)
- ✅ Bridges can fail without crashing the core
- ✅ Easier to scale (headless farm of worker nodes)
- ✅ Simpler to deploy (server + minimal plugins)
- ✅ Tenant isolation clearer (plugins are tenant-scoped)

---

## Current state (measured 2026-07-26)

Filter: **`*.py` only, excluding `node_modules/`, `.venv/`, `site-packages/`.** Quote the
filter with the number or it is not reproducible.

| Path | LOC |
|---|---|
| `operator/bridges/shared` | 181,127 |
| `core/console/corvin_console` | 63,734 |
| `core/plugins` | 12,660 |
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

### `layer: compliance` — Core Compliance

Immutable regulatory mechanisms: audit writer + hash chain (L16), consent gate (L18),
flow guard (L34), house rules (L44), erasure orchestrator (L36).

**Deployment:** always present, loaded first, tripwired at boot. Never disableable, never
replaceable, and never behind a feature flag in either direction.

**Today:** see "Current state" — these live across `operator/bridges/shared/` and
`core/compliance/corvin_compliance_reports/`, not yet under a single compliance package.

---

### `layer: core` — Core Infrastructure (bundled, required, replaceable)

#### Global plugin scope (target layout, ships in the wheel)
```
<repo>/core/core_plugins/

Global plugins (bundled with the wheel, loaded before any tenant plugin):
├─ compliance/
│  └─ audit_compliance/, consent_gate/, ...   (layer=compliance)
└─ core/                                       (layer=core)
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

**Deployment:** bundled in the Python wheel, registered from code via
`register_global_plugin(class_path, layer=...)` or the `corvin.global_plugins` entry-point
group, and loaded by `bootstrap_global()` before any tenant plugin.

**Status:** the loader exists (Phase 2). The directory layout above does **not** exist
yet — the move is Phase 7 of ADR-0242 and lands with import shims, deliberately last,
because relocating the audit writer touches the live GDPR hash chain.

---

### `layer: bundled` / `layer: installed` — Tenant Plugins (scoped, optional)

```
~/.corvin/tenants/_default/

Tenant plugins (per tenant):
├─ plugins/
│  ├─ discord_bridge/            (layer=bundled, supervises a Node daemon)
│  ├─ slack_bridge/              (layer=bundled, supervises a Node daemon)
│  ├─ telegram_bridge/           (layer=bundled, supervises a Node daemon)
│  ├─ structured_logging/        (layer=bundled, in-process)
│  ├─ postgres_audit_backend/    (layer=installed, licensed, in-process)
│  ├─ okta_auth/                 (layer=installed, licensed, in-process)
│  ├─ custom_routing/            (layer=installed, replaces a layer=core component)
│  └─ (user-installed plugins)
│
└─ plugin_config.yaml            (tenant plugin settings)
```

**Deployment:** the operator can install, enable and disable these per tenant.

**Trust boundary:** a tenant-scoped declaration may claim `bundled` or `installed`
**only**. A privileged claim (`compliance` / `core`) from `tenant.corvin.yaml` or
`registry.yaml` is downgraded to `installed` and audited (`plugin.layer_rejected`), never
honoured. Otherwise undisableability would be self-service via one YAML line.

---

## Plugin Scoping: Global vs. Tenant

### Implemented boot API (`core/plugins/corvin_plugins/bootstrap.py`)

```python
def register_global_plugin(class_path: str, *, layer: PluginLayer | str) -> None:
    """Declare a bundled global plugin and the layer it boots on.
    Refuses anything but compliance/core — tenant-scoped layers are declared
    in tenant config, not in the wheel."""


def bootstrap_global(*, tenant_id: str, corvin_home: Path, **registries) -> list[str]:
    """Load the bundled global plugins, compliance layer first.

    NOT behind a feature flag, deliberately: the layer it exists to load is the
    compliance layer, and CLAUDE.md forbids putting a compliance mechanism behind
    a switch. With no bundled global plugins registered this is a no-op returning
    [], so the flagless path changes nothing on an install that has none.

    * compliance failure — raises GlobalComplianceLoadFailed; the boot aborts.
    * core failure       — logged, audited, skipped; the platform boots degraded.
    """


def bootstrap_all(...):
    """global → declarative → runtime, deduplicated.
    A compliance abort survives all three passes."""
```

Tenant identity comes from the resolver chain `current_tenant()` →
`validate_tenant_id()` → `tenant_home()` (ADR-0007). No plugin path is ever assembled from
a raw env var.

### Boot sequence

```
1. layer=compliance   — boot FAILS on error (existing tripwire at
                        core/compliance/corvin_compliance_reports/tripwire.py::assert_all)
2. layer=core         — degrade + audit event on error; the platform stays up
3. per tenant:
   layer=bundled, then layer=installed, with tenant enable/disable applied
4. start the HTTP API server
```

No second fail-closed mechanism is introduced; the compliance abort reuses the tripwire
that already exists and that has no override — no env var, no flag.

---

## Directory Structure (target)

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
│  │     ├─ manifest.py           (PluginLayer, PluginRecord.layer/.replaces)
│  │     ├─ registry.py           (layer_of, plugins_by_layer, can_disable, replace)
│  │     ├─ bootstrap.py          (bootstrap_global / _declared_layer / bootstrap_all)
│  │     ├─ loader.py
│  │     └─ providers/            (8 provider registries, ADR-0033)
│  │
│  └─ gateway/                    (HTTP surface; calls bootstrap_all())
│
~/.corvin/
├─ tenants/
│  └─ _default/
│     ├─ plugins/                 (layer=bundled + layer=installed)
│     │  ├─ discord_bridge/       (supervisor plugin; payload is the Node daemon)
│     │  ├─ slack_bridge/
│     │  ├─ postgres_backend/
│     │  └─ custom_routing/
│     │
│     ├─ plugin_config.yaml       (tenant plugin settings)
│     ├─ audit.jsonl              (hash-chained audit trail)
│     └─ hooks/                   (tenant extension hooks)

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

```python
@app.get("/health")
async def health_check():
    """Is the core alive? Minimal response."""
    return {"status": "healthy"}

@app.post("/api/chat")
async def execute_request(request: Request):
    """Execute a request. Request → engine control → delegate/execute → response.
    No UI knowledge, pure API."""
    return {"response": "...", "cost": 100, "model": "sonnet"}

@app.get("/api/admin/plugins")
async def list_plugins(current_user: User = Depends(require_admin)):
    """List plugins (global + tenant) with layer, origin, tier and health."""
    return control_plane.list_plugins()
```

Admin routes sit behind the `admin_control_plane` flag (default `false`); off means the
routes are absent (404), not an error. Auth uses the existing `SessionRecord`; the tenant
comes from `rec.tenant_id`, never an env var (ADR-0007).

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

```python
class DiscordBridgeSupervisor(CorvinPlugin):
    plugin_id = "discord-bridge-supervisor/1.0.0"
    layer = "bundled"

    def on_load(self, ctx):
        self.handle = bridge_manager.start("discord")

    def on_unload(self):
        bridge_manager.stop("discord")

    def health_check(self) -> HealthStatus:
        return bridge_manager.health("discord")
```

The supervisor is an in-process Python object; the daemon stays a subprocess. Process
management is delegated to the existing `bridge_manager.py` rather than reimplemented,
and the daemons' wire protocol and inbox/outbox contract are untouched.

**Load modes:**

| Component | Mode |
|---|---|
| `layer=compliance`, `layer=core` | in-process |
| Bridge supervisor plugins | in-process Python objects |
| Bridge daemons | **subprocess** — that is what they already are, and it is what gives the isolation this document wants |
| Web UI | separate app, talks to the Core API |

There is no in-process load mode for a Node daemon, so the earlier "Option A / B / C"
choice does not exist: the hybrid *is* the architecture.

Bridge supervision ships behind `bridge_supervisor_plugins` (default `false`); off means
bridges are managed exactly as they are today.

---

## Stability Improvements with a Headless Core

### 1. Engine/Worker isolation
```
Engine selection + worker management (layer=core, in-process)
├─ Request arrives via the API
├─ Engine control selects the engine
├─ Delegation router picks ACS / TDE / native
└─ Worker executes (in-process or remote)

Worker fails → core continues (degrade to native)
Bridge fails → core continues (API still answers)
```

Note the operator-facing rule that already applies today: every degrade ladder ends at
**`native`**, never at another delegation engine.

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
pytest core/ -v                  # core without any bridge
bash operator/bridges/run-all-tests.sh   # bridges on their own (mandatory before
                                         # committing adapter.py / daemon.js / shared/js)
```

---

## Tenant Plugin Isolation

**Guarantee:** a tenant installs a broken plugin → only that tenant is affected.

- Global plugin failure: `compliance` = boot fail, `core` = degrade + audit, all tenants.
- Tenant plugin failure: logged, audited, tenant-local; the platform stays up.
- A tenant declaration cannot claim a privileged layer (`_declared_layer()` downgrades it
  to `installed` and audits `plugin.layer_rejected`).
- Every install/enable/disable writes an audit event carrying `tenant_id`, `plugin_id`
  and `layer`.

The registry keeps the layer per plugin (`layer_of`, `plugins_by_layer`) and refuses an
operator-initiated unload of a compliance plugin (`unregister(operator_initiated=True)` →
`PluginDisableRefused`), so an admin route cannot reach past `disable()` and unload the
audit writer by calling the primitive.

---

## Config: Headless Mode

```yaml
# ~/.corvin/tenants/_default/GLOBAL/tenant.corvin.yaml

spec:
  features:
    headless_api_mode: false        # ship dark; default off
    admin_control_plane: false
    bridge_supervisor_plugins: false
    plugin_extension_points: false

  server:
    mode: "headless"                # headless or bridged
    host: "0.0.0.0"
    port: 8000
    api: "rest"                     # gRPC deferred — not in Phase 1

  plugins:
    bundled:
      enabled:
        - discord_bridge
        - slack_bridge
        - web_ui
      disabled:
        - telegram_bridge
        - whatsapp_bridge
        - signal_bridge
        - teams_bridge
        - email_bridge
```

Global plugins (`layer=compliance`, `layer=core`) are **not** listed in tenant config —
they ship in the wheel and are registered from code. Disabling a bundled bridge means its
daemon is not started; it is a quiet path, never an error.

---

## Deployment Presets (ADR-0241)

### Preset A: Complete
Core + all seven bridges + Web UI — single instance.

### Preset B: Typical
Core + Discord + Slack + Web UI + Forge + SkillForge.

### Preset C: API-only (enterprise)
Core only, no bridges — pure REST backend.

### Preset D: Custom UI
Core + a custom dashboard, or Core + CLI, with `web_ui` disabled.

Bridges run as subprocesses in every preset; whether they share a container or get their
own is a packaging decision, not an architectural one.

---

## API-only boot (Phase 6, open)

```
1. Load layer=compliance   (boot fails on error — tripwire)
2. Load layer=core         (degrade + audit on error)
3. Start the HTTP API server
4. Load layer=bundled      (bridges, async, optional)
```

Result: the API is ready before any bridge is, and a bridge that never comes up cannot
block readiness. Behind `headless_api_mode` (default `false`); off means today's boot
path and the Console is always started.

---

## Status per ADR

| ADR | Topic | Status |
|---|---|---|
| ADR-0243 | Core vs. plugins, defines `layer` | Proposed; `layer` implemented in the registry |
| ADR-0240 | Plugin scoping (global vs. tenant) | Proposed; boot order + scoping implemented (Phase 2) |
| ADR-0241 | Headless core architecture (this doc) | Proposed; headless boot path **not** implemented |
| ADR-0239 | Admin control plane vs. Web UI | Proposed; **gRPC deferred**; REST plane not implemented |
| ADR-0238 | Bundled bridges — supervisor over Node daemons | Proposed; supervisors not implemented |
| ADR-0237 | Extensible core plugins + `replaces:` | Proposed; `replaces` field + `registry.replace()` implemented, extension points open |
| ADR-0236 | Minimal core specification | Long-term target, **not** scheduled |
| ADR-0242 | Implementation plan, Phases 0–7 | Phases 0–2 done, 3–7 open |

---

## Summary: Headless Core Benefits

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

1. Phase 3 — document the eight existing provider registries, add 3–5 extension points
2. Phase 4 — admin control plane (REST) behind `admin_control_plane`
3. Phase 5 — bridge supervisor plugins over the untouched Node daemons
4. Phase 6 — headless API-only boot path + presets A–D
5. Phase 7 — move to `core/core_plugins/{compliance,core,bundled}/` behind import shims,
   with `voice-audit verify` exit-0 checked before and after
