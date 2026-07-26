# CorvinOS Plugin System — Detailed Implementation Plan
## Layer-aware registry, extension points, admin control plane

**Date:** 2026-07-26
**Status:** Phases 0–2 implemented (commit c455516) · Phases 3–7 open
**Audience:** Engineering + Product

---

## Critical Refinement: What the first draft got wrong

### 1. The axis is `layer`, not `tier`

The first draft introduced a four-value classification, called it "tier", and numbered the
values 0–3. That word was **already taken twice** in this repository:

- **"Tier A/B/C" means ADR-0156's capability boundary**, repo-wide — what a plugin may
  do, plus the licensing gate on it. Nothing redefines that.
- **ADR-0233 D7 deliberately replaced the prototype's `tier` field with `origin`**
  (`builtin` | `vetted` | `community`) for provenance.

A third meaning of the same word inside the same registry is exactly the second taxonomy
`CLAUDE.md` forbids. The concept therefore ships under a free name: **`layer`**.

| Draft value | Current | Meaning |
|---|---|---|
| 0 (compliance) | `layer: compliance` | Immutable regulatory mechanisms; boot fails on error |
| 1 (core infrastructure) | `layer: core` | Bundled reference implementations; degrade + audit on error |
| 2 (bundled) | `layer: bundled` | Shipped in the wheel, enable/disable per tenant |
| 3 (premium) | `layer: installed` | User-installed; licensing is the separate ADR-0156 `tier` gate |
| 4 ("community marketplace") | `layer: installed`, `origin: community`, `support_class: community` | Three separate facts, three separate fields |

Directory paths and config keys follow the same rename: the numbered plugin directories
become `core/core_plugins/{compliance,core,bundled}/`, and the numbered config key becomes
`plugins.bundled`.

**Reference:** ADR-0243 § "Naming: why `layer`, not `tier`".

### 2. "License core = not replaceable" did not survive

The first draft argued that A2A, TDE and Recall must be non-replaceable because they are
strategic IP. That is not what shipped, and not what the ADRs decided:

- `layer=core` components are **reference implementations and explicitly replaceable**
  via the manifest field `replaces: <plugin_id>` (ADR-0237). In the implemented registry,
  `replace()` accepts a `layer=core` target and **only** a `layer=core` target.
- The only non-replaceable, non-disableable layer is `compliance`.
- Licensing is enforced on the **separate ADR-0156 `tier` axis**, not by making the load
  layer immutable. Keeping a component undisableable is a compliance argument, not a
  commercial one, and the two must not be conflated.

### 3. Additive before structural

The first draft opened with a directory refactor and called it "no functional changes,
pure refactoring". A move that relocates the audit writer touches the **live GDPR hash
chain**, its path resolution, and the boot tripwire. That is the single highest-risk
change in the plan. It is therefore the **last** phase, and it lands with import shims
rather than as a big-bang cutover.

### 4. Numbers are targets, not commitments

All test counts below are planning targets. Duration is **~8 weeks for the additive
work**. The ADR-0236 core extraction — moving house rules, consent and erasure out of the
181k-LOC `operator/bridges/shared/` tree — is **explicitly not included** in those eight
weeks. It is a separate project with its own plan and its own migration gate.

---

## The four layers

```
layer: compliance   (immutable, boot-fail on error)
  ├─ Audit writer + hash chain (L16)
  ├─ Consent gate (L18)
  ├─ Flow guard (L34)
  ├─ House rules (L44)
  └─ Erasure orchestrator (L36)

layer: core         (bundled reference implementations, degrade + audit on error)
  ├─ A2A instance coordination (L38)
  ├─ TDE routing (L22)
  ├─ Conversation recall (L28)
  ├─ ACS manager · Compute worker · Delegation router · Workflow engine
  ├─ Engine control (L22 / ADR-0181)
  ├─ Voice summary
  └─ Admin control plane
  → extensible via extension points, replaceable via `replaces:`

layer: bundled      (shipped in the wheel, enable/disable per tenant)
  ├─ Bridges: discord, slack, telegram, whatsapp, signal, teams, email
  ├─ Web UI
  └─ CLI
  → disabled is a quiet no-op, never an error

layer: installed    (user-installed into ~/.corvin/tenants/<id>/plugins/)
  ├─ Replacements for layer=core components
  ├─ Licensed add-ons (STT, ML classification, OKTA, Postgres) — gated by ADR-0156 tier
  └─ Community plugins (origin=community, support_class=community)
  → failure stays tenant-local
```

Orthogonal to `layer`: `tier` (A/B/C, ADR-0156, capability + licensing),
`origin` (builtin/vetted/community, ADR-0233 D7, provenance), and `support_class`
(product/infrastructure/community, ADR-0235, maintenance contract).

### Current state (measured 2026-07-26)

Filter: **`*.py` only, excluding `node_modules/`, `.venv/`, `site-packages/`.** The
filter is part of the number — without it the figure is not reproducible six months from
now.

| Path | LOC |
|---|---|
| `operator/bridges/shared` | 181,127 |
| `core/console/corvin_console` | 63,734 |
| `core/plugins` | 12,660 |
| `core/compliance` | 2,911 |

Where the compliance building blocks actually live today: house rules, consent gate and
erasure orchestrator in `operator/bridges/shared/`; audit writer, hash chain and the boot
tripwire in `core/compliance/corvin_compliance_reports/`. **None of**
`core/compliance/audit_writer.py`, `core/session/middleware.py` or
`core/routing/http_router.py` exists — earlier drafts of ADR-0236 cited them as if they
did. They are target paths.

---

## Phase Plan

| Phase | Objective | Status | Tests (target) |
|---|---|---|---|
| 0 | ADR consolidation | ✅ done | — |
| 1 | Layer-aware registry (additive, no file moves) | ✅ done (c455516) | ~25 |
| 2 | Boot order + scoping | ✅ done (c455516) | ~30 |
| 3 | Extension points | ⬜ open | ~35 |
| 4 | Admin control plane (REST) | ⬜ open | ~30 |
| 5 | Bridge supervisor plugins | ⬜ open | ~25 |
| 6 | Headless API-only boot | ⬜ open | ~15 |
| 7 | Directory move + shims, docs, v0.11.0 | ⬜ open | both-state coverage |

---

## Phase 1 — Layer-aware registry ✅ implemented

**Scope:** `core/plugins/corvin_plugins/` only. No file moves.

### 1.1 Manifest (`manifest.py`)

```python
class PluginLayer(str, Enum):
    COMPLIANCE = "compliance"
    CORE = "core"
    BUNDLED = "bundled"
    INSTALLED = "installed"


@dataclass
class PluginRecord:
    ...
    # Defaults to the LEAST privileged value: a record that does not say what it
    # is must not land somewhere privileged.
    layer: PluginLayer = PluginLayer.INSTALLED
    # plugin_id of the layer=core reference implementation this plugin takes over
    replaces: Optional[str] = None

    def can_disable(self) -> bool:
        """False for the compliance layer, True for every other layer."""
        return self.layer is not PluginLayer.COMPLIANCE
```

Two gates ship with it:

- **`origin=community` may not claim a privileged layer** (`compliance` or `core`).
  Without that gate a community manifest could buy itself boot priority and permanent
  undisableability with one YAML line.
- **`layer=compliance` may not declare `replaces`** at all — compliance mechanisms are
  not replaceable.

A manifest written before ADR-0243 keeps working: an absent `layer` reads as `installed`.

### 1.2 Registry (`registry.py`)

Implemented API — `layer_of()`, `plugins_by_layer()`, `can_disable()`, `disable()`,
`replace()`, and `unregister(operator_initiated=)`:

```python
def unregister(self, plugin_id: str, *, operator_initiated: bool = False) -> None:
    """Shutdown, hot-reload and replacement are machinery and may unload anything.
    An operator action routed through the Console or the admin API may not switch
    off a compliance-layer plugin — without the distinction the admin surface could
    reach past disable() and unload the audit writer by calling the primitive."""
    if operator_initiated and not self.can_disable(plugin_id):
        raise PluginDisableRefused(...)
```

`replace()` accepts a `layer=core` target only, and it does **not** roll back: the old
plugin's `on_unload` has already run, so "restoring" it would hand callers a torn-down
object. An empty slot plus an audit record is the honest outcome.

Every registration and every unregistration writes an audit event carrying the layer.

**Tests:** manifest round-trip with and without `layer`; `replaces` parsing; `can_disable()`
per layer; the community/privileged-layer refusal; audit events emitted.

---

## Phase 2 — Boot order + scoping ✅ implemented

### 2.1 Global bootstrap (`bootstrap.py`)

```python
def bootstrap_global(*, tenant_id: str, corvin_home: Path, **registries) -> list[str]:
    """Load the bundled global plugins, compliance layer first.

    NOT behind a feature flag, and that is deliberate rather than an oversight:
    the layer it exists to load is the compliance layer, and CLAUDE.md forbids
    putting a compliance mechanism behind a switch. With no bundled global
    plugins registered this is a no-op returning [], so the flagless path
    changes nothing on an install that has none.

    * compliance failure — raises GlobalComplianceLoadFailed; the boot aborts.
    * core failure       — logged, audited, skipped; the platform boots degraded.
    """
```

`register_global_plugin(class_path, *, layer)` declares a bundled global plugin; it
refuses anything other than `compliance` or `core`, because global plugins ship in the
wheel and tenant-scoped layers are declared in tenant config. Global entry points
(`corvin.global_plugins`) encode the layer in the name — `"compliance:my-gate"`.

### 2.2 The tenant/global trust boundary

`_declared_layer()` resolves the layer of a tenant-declared plugin. A tenant scope may
declare **`bundled` or `installed` only**. A privileged claim coming from
`tenant.corvin.yaml` or `registry.yaml` is **downgraded to `installed` and audited**
(`plugin.layer_rejected`) rather than honoured — on both the declarative and the runtime
path. The asymmetry is the point: if tenant config could declare `layer: compliance`,
undisableability would be self-service.

### 2.3 Boot order

`bootstrap_all()` runs **global → declarative → runtime**, deduplicated, and is called
from `core/gateway/corvin_gateway/app.py`. A compliance abort survives all three passes;
it is not swallowed by the wrapper.

**Tests:** `core/plugins/tests/test_layered_boot.py` covers ordering, the compliance
boot-fail, the core degrade path, the privileged-claim downgrade, **and the call site** —
`bootstrap_global()` being correct is worth nothing if nothing invokes it.

---

## Phase 3 — Extension points ⬜ open

**Flag:** `plugin_extension_points` (default `false`)

**Step 1 — document what already exists.** Eight provider registries are already
implemented under `core/plugins/corvin_plugins/providers/`: `router_backend`,
`recall_backend`, `summary_provider`, `audit_backend`, `user_backend`, `stt_provider`,
`data_connector`, `notification_backend` (ADR-0033). Documenting them is the deliverable
— no code change.

**Step 2 — add 3–5 new ones**, not the fifty the first draft promised:

- `engine_selection` and `model_selection` (ADR-0181)
- `route_selection_policy`
- `workflow_gate`

**Step 3 — replacement** via the manifest field `replaces:` from Phase 1.

**Immutable — no extension point, ever:** hash-chain audit write, Ed25519 verification
(A2A), token accounting (TDE), and every compliance gate. `audit_backend` stays
additive-only: it receives a COPY after the core write commits and can never suppress or
rewrite it (ADR-0233). An extension point on an immutable mechanism is not a feature
request, it is a compliance regression.

**Tests (target ~35):** each new point fires; replacement swaps the active
implementation; hooking an immutable mechanism fails; flag-off = no hooks.

---

## Phase 4 — Admin control plane ⬜ open

**Flag:** `admin_control_plane` (default `false`)

**REST only. gRPC is deferred — not in Phase 1, not in this plan** (ADR-0239). It is a
pure additional dependency with no consumer asking for it, and REST over the existing
session auth covers every known caller. Revisit only when a concrete consumer exists that
REST cannot serve.

| Route | Purpose |
|---|---|
| `GET /api/admin/plugins` | list, with `layer`, `origin`, `tier`, health |
| `POST /api/admin/plugins/{id}/enable` | enable |
| `POST /api/admin/plugins/{id}/disable` | disable — **refused for `layer=compliance`** |
| `POST /api/admin/plugins/{id}/config` | set config |
| `GET /api/admin/plugins/{id}/health` | health check |

- Auth via the **existing `SessionRecord`** — no new auth surface.
- Tenant from `rec.tenant_id`, **never** an env var (ADR-0007).
- Every mutating call writes an audit event.
- Disable routes through `registry.disable()` / `unregister(operator_initiated=True)`, so
  the compliance refusal cannot be bypassed by calling the primitive.

### Admin dashboard (planned)

```
Plugins Tab:

┌──────────────────────────────────────────────────────────────────┐
│ Plugin                    Layer        Status    Actions         │
├──────────────────────────────────────────────────────────────────┤
│ audit-compliance          compliance   ✅        [info]          │
│ consent-gate              compliance   ✅        [info]          │
│ a2a-orchestration         core         ✅        [config]        │
│ tde-routing               core         ✅        [config]        │
│ forge                     bundled      ✅        [disable]       │
│ discord-bridge            bundled      ✅        [disable]       │
│ advanced-stt              installed    ⚠️        [install]       │
│ okta-auth                 installed    ❌        [install]       │
└──────────────────────────────────────────────────────────────────┘

Legend:
- compliance: mandatory, never disableable, never flag-gated
- core:       bundled default; configurable, extensible, replaceable via `replaces:`
- bundled:    shipped in the wheel; enable/disable per tenant
- installed:  user-installed; licensed add-ons gated by the ADR-0156 tier
```

**Tests (target ~30):** each route; disable refused for `compliance`; cross-tenant access
denied; audit event per mutation; flag-off = routes absent (404), not error.

---

## Phase 5 — Bridge supervisor plugins ⬜ open

**Flag:** `bridge_supervisor_plugins` (default `false`)

### The bridges are Node daemons, not Python modules

An earlier draft assumed bridges were Python modules under `adapters/discord_adapter`
that could be refactored into `CorvinPlugin` subclasses. That is wrong on both counts:
`adapters/` does not exist, and all seven bridges — **discord, slack, telegram, whatsapp,
signal, teams, email** — are **Node.js daemons** at `operator/bridges/<name>/daemon.js`.
A Python supervisor already exists at `operator/bridges/bridge_manager.py`.

**No rewrite.** Each bridge gets a thin Python **supervisor plugin** (`layer=bundled`)
that starts, stops and health-checks the **existing** daemon as a subprocess, delegating
process management to `bridge_manager.py` rather than reimplementing it:

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

The supervisor is the plugin; the daemon is the payload. The daemons' wire protocol and
their inbox/outbox contract are untouched.

**Tests (target ~25):** start/stop/health per supervisor; a daemon crash surfaces as
unhealthy without taking down the core; flag-off = bridges start exactly as today.

---

## Phase 6 — Headless API-only boot ⬜ open

**Flag:** `headless_api_mode` (default `false`)

Boot order: `compliance` → `core` → HTTP server → `bundled` (async, optional). The API is
ready before any bridge is; a bridge that never comes up cannot block readiness.
Deployment presets A–D per ADR-0241.

**Tests (target ~15):** boots with zero bridges; API answers before bridges are up;
flag-off = today's boot.

---

## Phase 7 — Directory move, docs, release ⬜ open

**No new flag** — this phase realizes the target layout once everything above is green.

- Move to `core/core_plugins/{compliance,core,bundled}/` **with import shims** at the old
  paths, kept for at least one release.
- Audit-writer path resolution and the boot tripwire verified **before and after**:
  `voice-audit verify` must exit 0 on the same chain.
- Tests for **both** states of every flag. A flag tested in only one state rots.
- Docs + diagrams updated in the same commits as the code.
- CHANGELOG, then release **v0.11.0**, with every new flag still default-`false`.

---

## Feature flags

| Phase | Flag ID | Default | Off-path behavior |
|---|---|---|---|
| 3 | `plugin_extension_points` | `false` | Bundled defaults run; registered hooks ignored |
| 4 | `admin_control_plane` | `false` | `/api/admin/*` routes absent (404) |
| 5 | `bridge_supervisor_plugins` | `false` | Bridges managed exactly as today by `bridge_manager.py` |
| 6 | `headless_api_mode` | `false` | Today's boot path; the Console is always started |

All four are registered in `core/console/corvin_console/feature_flags.py`, each with an
owner and a target release in which it either flips to default-on or the feature is
removed. Flags are not permanent architecture.

### Explicitly NOT flagged

**`bootstrap_global()` carries no flag, in either direction — deliberately.** The layer
it exists to load is the compliance layer, and `CLAUDE.md` forbids a switch on a
compliance mechanism; a default-off flag there is the same violation as an env kill-flag.
The path is safe unflagged because it is a **no-op until a global plugin is registered**:
`_GLOBAL_SPECS` is empty today and no production call site invokes
`register_global_plugin()`.

The same holds for the Phase 1 registry work: the `layer` field is additive and defaults
to the least privileged value, so flag-off and flag-on behavior would be identical.

Untouched by every phase above, and never flag-gated: the audit hash chain, the boot
tripwire ("no override — no env var, no flag", ADR-0232/0233), the consent gate, the L10
path gate, the L44 house-rules gate, the L34 flow guard, and the licensing gates.

---

## Key Principles for Admin & Extensibility

### 1. Hierarchy of control

```
Admin cannot disable:
  layer=compliance — hardcoded, tripwired, no flag in either direction

Admin can configure and extend:
  layer=core       — extension points; full replacement via `replaces:`
  layer=bundled    — enable/disable per tenant
  layer=installed  — install/remove; licensed add-ons gated by the ADR-0156 tier
```

### 2. Extensibility without forking

```
A2A keeps its reference implementation and its immutable parts
(Ed25519 verification, audit of every event, denial on failed attestation).
Everything around them is a hook — and a full replacement is a manifest field,
not a fork.
```

### 3. Licensing lives on a different axis

```
layer  = when it loads and whether it can be switched off   (ADR-0243)
tier   = what it may do, and the licensing gate on that     (ADR-0156)
```

Never use undisableability as a commercial lever. `compliance` is undisableable because
regulation says so, not because it is valuable.

---

## Implementation Checklist

- [x] **Phase 1:** Layer-aware registry
  - [x] `PluginLayer`, `PluginRecord.layer`, `.replaces`, `.can_disable()`
  - [x] `origin=community` refused a privileged layer
  - [x] `layer_of`, `plugins_by_layer`, `can_disable`, `disable`, `replace`
  - [x] `unregister(operator_initiated=)`
  - [x] Audit event per registration / unregistration

- [x] **Phase 2:** Boot order + scoping
  - [x] `bootstrap_global()` — compliance first, abort on failure; core degrades + audits
  - [x] `register_global_plugin()` + `corvin.global_plugins` entry points
  - [x] `_declared_layer()` — tenant scope limited to `bundled` / `installed`
  - [x] `bootstrap_all()` global → declarative → runtime, wired into the gateway
  - [x] Call-site test, not only unit tests

- [ ] **Phase 3:** Extension points
  - [ ] Document the 8 existing provider registries
  - [ ] Add `engine_selection`, `model_selection`, `route_selection_policy`, `workflow_gate`
  - [ ] Replacement path exercised end-to-end
  - [ ] Immutable-mechanism hook attempts fail

- [ ] **Phase 4:** Admin control plane
  - [ ] REST routes under `/api/admin/plugins`
  - [ ] Auth via existing `SessionRecord`, tenant from `rec.tenant_id`
  - [ ] Console panel + per-plugin config forms
  - [ ] Audit event per mutation

- [ ] **Phase 5:** Bridge supervisor plugins (7 bridges, daemons untouched)
- [ ] **Phase 6:** Headless API-only boot + presets A–D
- [ ] **Phase 7:** Directory move behind import shims, both-state flag tests, docs, v0.11.0

---

## Success Criteria

**Architecture**
- [x] The registry expresses `layer`, and `compliance` is undisableable through it
- [x] A tenant declaration cannot claim a privileged layer
- [ ] Admin can see `layer`, `origin`, `tier` and disableability for every plugin
- [ ] `core/core_plugins/{compliance,core,bundled}/` layout reached (Phase 7)

**Control**
- [ ] Admin can disable `bundled` / `installed` plugins from the dashboard
- [x] Admin cannot disable `compliance` — refused at the registry, not only in the UI
- [ ] Admin can configure any plugin
- [x] Registry mutations are audited

**Extensibility**
- [ ] The 8 existing provider registries are documented
- [ ] 3–5 new extension points exist and fire
- [x] Replacement targets `layer=core` only

**Honest non-goals**
- ADR-0236's core extraction is not in this plan, and no task above references it
- gRPC is deferred, not planned
- Test counts are targets; no number is a commitment

---

## Example: replacing a `layer=core` component

```python
# A user-installed plugin that takes over routing entirely.
# manifest: layer: installed, replaces: "delegation-router/1.0.0"

from corvin_plugins import CorvinPlugin

class KubernetesDelegationRouter(CorvinPlugin):
    plugin_id = "k8s-delegation-router/1.0.0"

    def on_load(self, ctx):
        self.ctx = ctx
        ctx.logger.info("k8s delegation router active")
```

The registry's `replace()` refuses this if the named target is not on `layer=core`, and
refuses it outright for anything on `layer=compliance`. What stays immutable regardless of
who replaces what: the hash-chained audit write, Ed25519 verification, token accounting,
and every compliance gate.

---

## Conclusion

**What changed from the first draft:**
1. The numbered "tier" values 0–3 → `layer: compliance | core | bundled | installed`
2. `layer=core` is replaceable; only `compliance` is immutable, and for regulatory reasons
3. Licensing moved off the load axis onto ADR-0156's `tier`
4. Bridges are supervised Node daemons, not rewritten Python plugins
5. gRPC deferred; REST only
6. The directory move is last, not first
7. ~8 weeks for the additive work, excluding the ADR-0236 extraction; test counts are targets

**Why this works:**
- Compliance is immutable and unflagged (`layer=compliance`)
- Defaults are ours, but nothing above `compliance` is a lock-in
- Admin control is explicit and audited
- Every new surface ships dark behind a default-`false` flag
