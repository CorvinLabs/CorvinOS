# CorvinOS Plugin System — Detailed Implementation Plan
## Boot-layer-aware registry, extension points, admin control plane

**Date:** 2026-07-27
**Status:** Phases 0–5 shipped, Phase 6 partial, Phase 7 open — with the reach qualifiers
in [Phase Plan](#phase-plan). Commit c455516 is Phases 1–2 only.
**Audience:** Engineering + Product

---

## Read this first: shipped ≠ reached

Phases 1–5 all have working, tested code. Three of them have **no reach**:

| What shipped | What uses it |
|---|---|
| the `boot_layer` axis, four values | nothing on `compliance` or `core` — `_GLOBAL_SPECS` is empty, `register_global_plugin()` has no production caller, `bootstrap_global()` returns `[]` |
| `registry.replace()` (ADR-0237 full replacement) | **nothing can** — it accepts only a `core` target and no plugin is on that boot layer |
| the four extension-point bus points | **all 4 wired** (ADR-0251, 2026-07-27) |
| the seven bridge supervisor classes | **nothing declares them** in `spec.plugins.installed` |

Guard tests pin all four statements — `core/plugins/tests/test_layered_boot.py::TestTheTopOfTheAxisHasNoProductionInstance`
and `test_extension_point_call_sites.py` (per point, both directions). The day any of them stops
being true the suite goes red with a message naming this file, so these lines cannot
silently rot into a lie.

Everything below reads with that qualifier. Where a checkbox is ticked, it means the
mechanism exists and its tests pass — not that anything reaches it.

---

## Critical Refinement: What the first draft got wrong

### 1. The axis is `boot_layer` — not `tier`, and not `layer` either

The first draft introduced a four-value classification, called it "tier", and numbered the
values 0–3. That word was **already taken twice** in this repository:

- **"Tier A/B/C" means ADR-0156's capability boundary**, repo-wide — what a plugin may
  do, plus the licensing gate on it. Nothing redefines that.
- **ADR-0233 D7 deliberately replaced the prototype's `tier` field with `origin`**
  (`builtin` | `vetted` | `community`) for provenance.

A third meaning of the same word inside the same registry is exactly the second taxonomy
`CLAUDE.md` forbids. The second draft therefore renamed it to `layer` — and hit the same
collision class from the other side: "layer" already meant the **L1–L44 stack**, **ADR-0124
audit layers** (`routes/audit_layers.py`), the **ADR-0142 layer-extension API** (403 with
`reason="core_layer_immutable"`, `routes/extensions.py`) and **quality layers**
(`routes/quality_layers.py`). Four existing meanings, versus "tier"'s two.

The concept therefore ships under a genuinely free name: **`boot_layer`**, enum
`BootLayer`. Renamed surface: `boot_layer_of()` · `plugins_by_boot_layer()` ·
`register(..., boot_layer=)` · `replace(..., boot_layer=)` · `_declared_boot_layer()` ·
`register_global_plugin(class_path, boot_layer=)` · `PluginRecord.boot_layer` · the
JSON/YAML key `boot_layer` · the audit event `plugin.boot_layer_rejected` (detail keys
`boot_layer` / `declared_boot_layer`) · the admin-API field `boot_layer` and the health
aggregate `by_boot_layer`.

| Draft value | Current | Meaning |
|---|---|---|
| 0 (compliance) | `boot_layer: compliance` | Immutable regulatory mechanisms; boot fails on error |
| 1 (core infrastructure) | `boot_layer: core` | Bundled reference implementations; degrade + audit on error |
| 2 (bundled) | `boot_layer: bundled` | Shipped in the wheel, enable/disable per tenant |
| 3 (premium) | `boot_layer: installed` | User-installed; licensing is the separate ADR-0156 `tier` gate |
| 4 ("community marketplace") | `boot_layer: installed`, `origin: community`, `support_class: community` | Three separate facts, three separate fields |

Target directory paths follow the same rename: the numbered plugin directories become
`core/core_plugins/{compliance,core,bundled}/` (Phase 7, not yet created).

There is **no `plugins.bundled` config key** — earlier revisions of this document promised
one, and nothing in the repo reads it. The single declarative key is
**`spec.plugins.installed`**, whose entries each carry `boot_layer:`.

**Reference:** ADR-0243 § "Naming: why not `tier`".

### 2. "License core = not replaceable" did not survive

The first draft argued that A2A, TDE and Recall must be non-replaceable because they are
strategic IP. That is not what shipped, and not what the ADRs decided:

- `boot_layer=core` components are **reference implementations and explicitly replaceable**
  via the manifest field `replaces: <plugin_id>` (ADR-0237). In the implemented registry,
  `replace()` accepts a `boot_layer=core` target and **only** a `boot_layer=core` target.
- The only non-replaceable, non-disableable boot layer is `compliance`.
- Licensing is enforced on the **separate ADR-0156 `tier` axis**, not by making the load
  boot layer immutable. Keeping a component undisableable is a compliance argument, not a
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

## The four boot layers

**Intended populations, not current ones.** Nothing is on `compliance` or `core` today,
and nothing on `bundled` is declared anywhere shipped — see "Read this first".

```
boot_layer: compliance   (immutable, boot-fail on error)  — 0 instances
  ├─ Audit writer + hash chain (L16)
  ├─ Consent gate (L18)
  ├─ Flow guard (L34)
  ├─ House rules (L44)
  └─ Erasure orchestrator (L36)

boot_layer: core         (bundled reference implementations, degrade + audit on error)  — 0 instances
  ├─ A2A instance coordination (L38)
  ├─ TDE routing (L22)
  ├─ Conversation recall (L28)
  ├─ ACS manager · Compute worker · Delegation router · Workflow engine
  ├─ Engine control (L22 / ADR-0181)
  ├─ Voice summary
  └─ Admin control plane
  → extensible via extension points, replaceable via `replaces:`

boot_layer: bundled      (shipped in the wheel, enable/disable per tenant)  — 0 declared
  ├─ Bridges: discord, slack, telegram, whatsapp, signal, teams, email
  ├─ Web UI
  └─ CLI
  → disabled is a quiet no-op, never an error

boot_layer: installed    (user-installed into ~/.corvin/tenants/<id>/plugins/)  — the only
                         reachable value today
  ├─ Replacements for boot_layer=core components
  ├─ Licensed add-ons (STT, ML classification, OKTA, Postgres) — gated by ADR-0156 tier
  └─ Community plugins (origin=community, support_class=community)
  → failure stays tenant-local
```

Orthogonal to `boot_layer`: `tier` (A/B/C, ADR-0156, capability + licensing),
`origin` (builtin/vetted/community, ADR-0233 D7, provenance), and `support_class`
(product/infrastructure/community, ADR-0235, maintenance contract).

### Current state (re-measured 2026-07-27)

Filter: **`*.py` only, excluding `node_modules/`, `.venv/`, `site-packages/`.** The
filter is part of the number — without it the figure is not reproducible six months from
now.

| Path | LOC |
|---|---|
| `operator/bridges/shared` | 181,127 |
| `core/console/corvin_console` | 65,305 |
| `core/plugins` | 19,151 |
| `core/plugins/corvin_plugins` (the contract itself) | 7,781 |
| `core/gateway` | 15,285 |
| `core/compliance` | 2,911 |

The `63,734` / `12,660` figures earlier revisions carried were the ADR-0236-era baseline
and had drifted; `core/plugins` in particular is now half again as large, which is
Phases 1–5 landing.

Where the compliance building blocks actually live today: house rules, consent gate and
erasure orchestrator in `operator/bridges/shared/`; audit writer, hash chain and the boot
tripwire in `core/compliance/corvin_compliance_reports/`. **None of**
`core/compliance/audit_writer.py`, `core/session/middleware.py` or
`core/routing/http_router.py` exists — earlier drafts of ADR-0236 cited them as if they
did. They are target paths.

---

## Phase Plan

| Phase | Objective | Status | Reach today |
|---|---|---|---|
| 0 | ADR consolidation | ✅ done | — |
| 1 | Boot-layer-aware registry (additive, no file moves) | ✅ done (c455516) | privileged values have **0 instances** |
| 2 | Boot order + scoping | ✅ done (c455516) | `bootstrap_global()` → `[]` everywhere; the tenant-downgrade guard *is* live |
| 3 | Extension points | ✅ **all 4 wired** | ADR-0251, 2026-07-27 |
| 4 | Admin control plane (REST) | ✅ done | six routes behind `admin_control_plane` |
| 5 | Bridge supervisor plugins | ✅ **classes only** | **nothing declares them** |
| 6 | Headless API-only boot | ◑ **partial** | browser surfaces suppressed; **no boot reorder**, **no presets** |
| 7 | Directory move + shims, docs, v0.11.0 | ⬜ open | `core/core_plugins/` does not exist |

### Measured test counts (2026-07-27)

Earlier revisions of this table carried planning targets (~25 / ~30 / …) in a "Tests"
column. Those were never measurements. These are:

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

## Phase 1 — Boot-layer-aware registry ✅ implemented, 0 privileged instances

**Scope:** `core/plugins/corvin_plugins/` only. No file moves.

### 1.1 Manifest (`manifest.py`)

```python
class BootLayer(str, Enum):
    COMPLIANCE = "compliance"
    CORE = "core"
    BUNDLED = "bundled"
    INSTALLED = "installed"


@dataclass
class PluginRecord:
    ...
    # Defaults to the LEAST privileged value: a record that does not say what it
    # is must not land somewhere privileged.
    boot_layer: BootLayer = BootLayer.INSTALLED
    # plugin_id of the boot_layer=core reference implementation this plugin takes over
    replaces: Optional[str] = None

    def can_disable(self) -> bool:
        """False for the compliance boot layer, True for every other one."""
        return self.boot_layer is not BootLayer.COMPLIANCE
```

Two gates ship with it:

- **`origin=community` may not claim a privileged boot layer** (`compliance` or `core`).
  Without that gate a community manifest could buy itself boot priority and permanent
  undisableability with one YAML line.
- **`boot_layer=compliance` may not declare `replaces`** at all — compliance mechanisms are
  not replaceable.

A manifest written before ADR-0243 keeps working: an absent `boot_layer` reads as `installed`.

### 1.2 Registry (`registry.py`)

Implemented API — `boot_layer_of()`, `plugins_by_boot_layer()`, `can_disable()`, `disable()`,
`replace()`, and `unregister(operator_initiated=)`:

```python
def unregister(self, plugin_id: str, *, operator_initiated: bool = False) -> None:
    """Shutdown, hot-reload and replacement are machinery and may unload anything.
    An operator action routed through the Console or the admin API may not switch
    off a compliance-boot-layer plugin — without the distinction the admin surface could
    reach past disable() and unload the audit writer by calling the primitive."""
    if operator_initiated and not self.can_disable(plugin_id):
        raise PluginDisableRefused(...)
```

`replace()` accepts a `boot_layer=core` target only, and it does **not** roll back: the old
plugin's `on_unload` has already run, so "restoring" it would hand callers a torn-down
object. An empty slot plus an audit record is the honest outcome.

**`replace()` is structurally unreachable today.** No plugin is on the `core` boot layer,
so there is no legal target; every call would raise `PluginReplacementRefused` before
touching anything. The rule is tested, never exercised in production.

Every registration and every unregistration writes an audit event carrying the boot layer.

**Tests (measured 2026-07-27):** `test_layer_registry.py` 38 passing, `test_manifest.py`
covers manifest round-trip with and without `boot_layer`, `replaces` parsing,
`can_disable()` per boot layer, the community/privileged refusal, and the audit events.

---

## Phase 2 — Boot order + scoping ✅ implemented, global pass is a no-op

### 2.1 Global bootstrap (`bootstrap.py`)

```python
def bootstrap_global(*, tenant_id: str, corvin_home: Path, **registries) -> list[str]:
    """Load the bundled global plugins, compliance boot layer first.

    NOT behind a feature flag, and that is deliberate rather than an oversight:
    the boot layer it exists to load is the compliance boot layer, and CLAUDE.md
    forbids putting a compliance mechanism behind a switch. With no bundled
    global plugins registered this is a no-op returning [] — which is what it
    does on EVERY install today.

    * compliance failure — raises GlobalComplianceLoadFailed; the boot aborts.
    * core failure       — logged, audited, skipped; the platform boots degraded.

    Both branches are unreachable in production until a global plugin exists.
    """
```

`register_global_plugin(class_path, *, boot_layer)` declares a bundled global plugin; it
refuses anything other than `compliance` or `core`, because global plugins ship in the
wheel and tenant-scoped boot layers are declared in tenant config. It has **no production
caller** — `test_layered_boot.py::test_register_global_plugin_still_has_no_production_caller`
fails the day one appears.

**There are no global entry points.** A `corvin.global_plugins` group was implemented and
then removed before it had a single user: any third-party wheel could have published
`compliance:whatever` and been loaded first, undisableable, with no `PluginRecord`, and a
raise in its `__init__` would have permanently aborted the platform boot.
`bootstrap.GLOBAL_ENTRY_POINT_GROUP` is `None`. Earlier revisions of this document
described the group as a shipping feature; it is not one, and re-adding discovery needs
signature verification plus an allowlist, not an entry-point name.

### 2.2 The tenant/global trust boundary

`_declared_boot_layer()` resolves the layer of a tenant-declared plugin. A tenant scope may
declare **`bundled` or `installed` only**. A privileged claim coming from
`tenant.corvin.yaml` or `registry.yaml` is **downgraded to `installed` and audited**
(`plugin.boot_layer_rejected`) rather than honoured — on both the declarative and the runtime
path. The asymmetry is the point: if tenant config could declare `boot_layer: compliance`,
undisableability would be self-service.

### 2.3 Boot order

`bootstrap_all()` runs **global → declarative → runtime**, deduplicated, and is called
from `core/gateway/corvin_gateway/app.py`. A compliance abort survives all three passes;
it is not swallowed by the wrapper.

**Tests:** `core/plugins/tests/test_layered_boot.py` (25 passing, measured 2026-07-27)
covers ordering, the compliance boot-fail, the core degrade path, the privileged-claim
downgrade, **and the call site** — `bootstrap_global()` being correct is worth nothing if
nothing invokes it. It additionally carries
`TestTheTopOfTheAxisHasNoProductionInstance`, which pins the "zero instances" claim of
this document so it fails loudly instead of aging into a false statement.

---

## Phase 3 — Extension points ✅ bus shipped, all 4 call sites wired

**Flag:** `plugin_extension_points` (default `false`)

**Step 1 — document what already exists.** ✅ Eight provider registries are implemented
under `core/plugins/corvin_plugins/providers/`: `router_backend`, `recall_backend`,
`summary_provider`, `audit_backend`, `user_backend`, `stt_provider`, `data_connector`,
`notification_backend` (ADR-0033). Documented in
[EXTENSIBLE_CORE_PLUGINS.md](EXTENSIBLE_CORE_PLUGINS.md) §3. These *are* live — they
predate the bus and are not reached through it.

**Step 2 — add 3–5 new ones.** ✅ Four exist in `extension_points.py`, namespaced because
the bus has one flat key space:

- `engine.model_selection` and `engine.engine_selection` (ADR-0181)
- `delegation.route_selection_policy`
- `workflow.workflow_gate` (the only fail-closed one)

**Reach today: 1 of 4 (2026-07-27).** `engine.engine_selection` is wired into
`delegation_policy.resolve_worker_engine` (ADR-0251 D1/D2): a hook may confirm the bundled
routing answer or de-escalate to `native`, never escalate, and a refusal is audited.
A hook on the other three is inert regardless of the flag.
`test_extension_point_call_sites.py` fails when a point gains a caller **or loses one**,
naming the docs that must be updated with it. Wiring `engine.model_selection` (the
provider/model selection path), `delegation.route_selection_policy` (the classifier) and
`workflow.workflow_gate` is the remaining work.

**Step 3 — replacement** via the manifest field `replaces:` from Phase 1. Implemented and
**structurally unreachable**: `replace()` requires a `core` target and none exists.

**Immutable — no extension point, ever:** hash-chain audit write, Ed25519 verification
(A2A), token accounting (TDE), and every compliance gate. These are named in
`extension_points.py::_NEVER_EXTENSIBLE` and refused with `ImmutableExtensionPoint`, a
distinct error from "unknown point". `audit_backend` stays additive-only: it receives a
COPY after the core write commits and can never suppress or rewrite it (ADR-0233). An
extension point on an immutable mechanism is not a feature request, it is a compliance
regression.

**Tests (measured 2026-07-27):** `test_extension_points.py`, **54 passing** — both flag
states, fail-closed semantics, PII rules, refusals, the conflict rule, tenant isolation,
and the no-call-site guard.

---

## Phase 4 — Admin control plane ✅ implemented (REST)

**Flag:** `admin_control_plane` (default `false`)

**REST only. gRPC is deferred — not in Phase 1, not in this plan** (ADR-0239). It is a
pure additional dependency with no consumer asking for it, and REST over the existing
session auth covers every known caller. Revisit only when a concrete consumer exists that
REST cannot serve.

The router declares `/api/admin/*`; the gateway mounts the Console router at
`/v1/console`, so the effective path is `/v1/console/api/admin/*`.

| Route | Purpose |
|---|---|
| `GET /api/admin/plugins` | list, with `boot_layer`, `origin`, health — **no `tier` field** |
| `GET /api/admin/plugins/{id}` | detail, plus declarations + settings surface |
| `POST /api/admin/plugins/{id}/enable` | enable |
| `POST /api/admin/plugins/{id}/disable` | disable — **refused for `boot_layer=compliance`** (403) |
| **`PUT`** `/api/admin/plugins/{id}/config` | replace stored settings, schema-validated |
| `GET /api/admin/health` | **aggregated** health, with `by_boot_layer` |

Three corrections against earlier revisions of this table, all verified against
`core/console/corvin_console/routes/admin.py`:

* config is **`PUT`**, not `POST`;
* there is **no per-plugin health endpoint** (`GET /api/admin/plugins/{id}/health` does not
  exist) — health is the one aggregated route, which calls into every loaded plugin;
* the response carries **no `tier` field**, deliberately: `tier` is ADR-0156's capability
  boundary, not a lifecycle property, and putting it into a lifecycle response is exactly
  the conflation the `boot_layer` rename exists to prevent.

- Auth via the **existing `SessionRecord`** — no new auth surface.
- Tenant from `rec.tenant_id`, **never** an env var (ADR-0007).
- Every mutating call writes an audit event.
- Disable routes through `registry.disable()` / `unregister(operator_initiated=True)`, so
  the compliance refusal cannot be bypassed by calling the primitive. That refusal has
  never fired in production — nothing is on the compliance boot layer to refuse.

### Admin dashboard (planned — not built)

The Console panel is **not** part of what shipped; the REST routes are. The sketch below
is a mock-up, and every plugin id in it is **hypothetical**: no `audit-compliance`,
`consent-gate`, `a2a-orchestration` or `tde-routing` plugin exists, and `discord-bridge`
exists as a class but is declared nowhere.

```
Plugins Tab (mock-up — none of these plugin ids exists today):

┌──────────────────────────────────────────────────────────────────┐
│ Plugin                    boot_layer   Status    Actions         │
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

**Tests (measured 2026-07-27):** `core/console/tests/test_admin_route.py`, **24 passing** —
each route; disable refused for `compliance`; cross-tenant isolation; audit event per
mutation; flag-off = routes absent (404), not error.

---

## Phase 5 — Bridge supervisor plugins ✅ classes shipped, nothing declares them

**Flag:** `bridge_supervisor_plugins` (default `false`)

### The bridges are Node daemons, not Python modules

An earlier draft assumed bridges were Python modules under `adapters/discord_adapter`
that could be refactored into `CorvinPlugin` subclasses. That is wrong on both counts:
`adapters/` does not exist, and all seven bridges — **discord, slack, telegram, whatsapp,
signal, teams, email** — are **Node.js daemons** at `operator/bridges/<name>/daemon.js`.
A Python supervisor already exists at `operator/bridges/bridge_manager.py`.

**No rewrite.** Each bridge gets a thin Python **supervisor plugin** (`boot_layer=bundled`)
that starts, stops and health-checks the **existing** daemon as a subprocess, delegating
process management to `bridge_manager.py` rather than reimplementing it. The real shape,
from `corvin_plugins/bridges/supervisor.py`:

```python
class BridgeSupervisorPlugin:
    plugin_type = "bridge_channel"

    def __init__(self, channel: str, *, bridge_manager=None, ...):
        self.plugin_id = f"{channel}-bridge"      # e.g. "discord-bridge"

    def on_load(self, ctx): ...      # six-condition start gate, never raises
    def on_unload(self): ...         # SIGTERM → 5 s → SIGKILL → 2 s → abandon
    def health_check(self): ...      # reaps, reports, never guesses


class DiscordBridgePlugin(BridgeSupervisorPlugin): ...   # + 6 siblings
```

Earlier revisions of this document showed a `DiscordBridgeSupervisor` class with
`plugin_id = "discord-bridge-supervisor/1.0.0"`, calling `bridge_manager.start()`,
`.stop()` and `.health()`. **None of those four names exists.** The functions the
supervisor actually uses are `channel_daemon_running()`, `adapter_running_pid()` and
`start_channel_detached()`.

The supervisor is the plugin; the daemon is the payload. The daemons' wire protocol and
their inbox/outbox contract are untouched.

**Reach today: none.** `bridges/registry_entries.declaration_entry()` *generates* the
`spec.plugins.installed` block, but nothing in the shipped tree contains one and the
shipped `tenant.corvin.yaml` template has no bridge section. Two independent switches are
required to start a daemon — the flag **and** the declaration — and the second one has
never been written anywhere. So no supervisor loads on any install, flag state
irrelevant. Closing this needs either a shipped declaration or a Console Settings action
that writes it.

**Tests (measured 2026-07-27):** `test_bridge_supervisor.py`, **78 passing** — identity +
declaration, both flag states, start argv/cwd/skip reasons, the duplicate-start probe in
all four `via` modes, the bounded shutdown ladder, health semantics including the half
bridge, registry landing on `bundled`, and a secrets check that greps every log line and
audit detail for a planted token.

---

## Phase 6 — Headless API-only boot ◑ partial

**Flag:** `headless_api_mode` (default `false`)

**What shipped.** The flag suppresses exactly three **browser surfaces**:

1. `mount_static()` does not mount the SPA and registers no fallback route — `/console`
   404s, including the friendly "not built" placeholder, because a placeholder is still a
   browser surface;
2. the gateway does not register the `/local-stats` HTML dashboard;
3. `GET /` answers `{"status": "ok", …, "ui": "headless"}` instead of redirecting to
   `/console/`.

It does **not** switch off bridges — that is `bridge_supervisor_plugins`, and the two are
independent on purpose, because coupling them would make "core + CLI + bridges, no browser
UI" unreachable. `test_headless_mode.py::TestNoHiddenCoupling` fails if the bridge
supervisor ever reads `headless_api_mode`.

**What did NOT ship**, and is what this phase originally promised:

- the reordered boot sequence `compliance → core → HTTP server → bundled (async)`.
  `bootstrap_all()` still runs global → declarative → runtime to completion before the
  gateway serves. "A bridge that never comes up cannot block readiness" is true today only
  because no bridge supervisor is ever loaded — luck, not design.
- **deployment presets A–D as a mechanism.** There is no `preset` key, no resolver and
  nothing to select; the four models are documentation of two flags' four combinations.

**Tests (measured 2026-07-27):** `core/console/tests/test_headless_mode.py`, **9
passing** — flag resolution including "unreadable flag = serve the UI", mount behaviour in
both states, coverage of every browser surface, and the no-hidden-coupling guard.

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
| 6 | `headless_api_mode` | `false` | SPA mounted, `/local-stats` served, `/` redirects to `/console/`. The **boot sequence is identical in both states** |

All four are registered in `core/console/corvin_console/feature_flags.py`, each with an
owner and a target release in which it either flips to default-on or the feature is
removed. Flags are not permanent architecture.

### Explicitly NOT flagged

**`bootstrap_global()` carries no flag, in either direction — deliberately.** The boot
layer it exists to load is the compliance boot layer, and `CLAUDE.md` forbids a switch on a
compliance mechanism; a default-off flag there is the same violation as an env kill-flag.
The path is safe unflagged because it is a **no-op until a global plugin is registered**:
`_GLOBAL_SPECS` is empty today and no production call site invokes
`register_global_plugin()`. Both facts are pinned by
`test_layered_boot.py::TestTheTopOfTheAxisHasNoProductionInstance`, not only asserted here.

The same holds for the Phase 1 registry work: the `boot_layer` field is additive and defaults
to the least privileged value, so flag-off and flag-on behavior would be identical.

Untouched by every phase above, and never flag-gated: the audit hash chain, the boot
tripwire ("no override — no env var, no flag", ADR-0232/0233), the consent gate, the L10
path gate, the L44 house-rules gate, the L34 flow guard, and the licensing gates.

---

## Key Principles for Admin & Extensibility

### 1. Hierarchy of control

```
Admin cannot disable:
  boot_layer=compliance — hardcoded, tripwired, no flag in either direction
                          (0 instances today; the refusal has never fired)

Admin can configure and extend:
  boot_layer=core       — extension points; full replacement via `replaces:`
                          (0 instances today; replace() therefore unreachable)
  boot_layer=bundled    — enable/disable per tenant (0 declared today)
  boot_layer=installed  — install/remove; licensed add-ons gated by the ADR-0156 tier
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
boot_layer = when it loads and whether it can be switched off   (ADR-0243)
tier       = what it may do, and the licensing gate on that     (ADR-0156)
origin     = where it came from                                 (ADR-0233 D7)
```

Never use undisableability as a commercial lever. `compliance` is undisableable because
regulation says so, not because it is valuable.

---

## Implementation Checklist

`[x]` means **the mechanism exists and its tests pass**. Where a mechanism has no reach,
that is stated on the line rather than hidden behind the tick.

- [x] **Phase 1:** Boot-layer-aware registry
  - [x] `BootLayer`, `PluginRecord.boot_layer`, `.replaces`, `.can_disable()`
  - [x] `origin=community` refused a privileged boot layer
  - [x] `boot_layer_of`, `plugins_by_boot_layer`, `can_disable`, `disable`, `replace`
  - [x] `unregister(operator_initiated=)`
  - [x] Audit event per registration / unregistration
  - [ ] **any instance on `compliance` or `core`** — none exists, so `replace()` and the
        compliance refusal are both unreachable

- [x] **Phase 2:** Boot order + scoping
  - [x] `bootstrap_global()` — compliance first, abort on failure; core degrades + audits
        (both branches unreachable: `_GLOBAL_SPECS` is empty)
  - [x] `register_global_plugin()` — **no production caller**
  - [x] `_declared_boot_layer()` — tenant scope limited to `bundled` / `installed`; this is
        the one privileged-boot-layer guard that is genuinely live
  - [x] `bootstrap_all()` global → declarative → runtime, wired into the gateway
  - [x] Call-site test, not only unit tests
  - **NOT** `corvin.global_plugins` entry points — the group was implemented and removed
    before it had a user; `GLOBAL_ENTRY_POINT_GROUP` is `None`

- [x] **Phase 3:** Extension points
  - [x] Document the 8 existing provider registries
  - [x] Add `engine.model_selection`, `engine.engine_selection`,
        `delegation.route_selection_policy`, `workflow.workflow_gate`
  - [x] `engine.engine_selection` → `delegation_policy.resolve_worker_engine`
  - [x] `delegation.route_selection_policy` → `delegation_policy.resolve_delegation_route`
  - [x] `engine.model_selection` → `model_selector.resolve_step_model`
  - [x] `workflow.workflow_gate` → `routes/workflows.py::_stream_run`
    (all four ADR-0251, 2026-07-27; guard-tested per point in both directions)
  - [ ] Replacement path exercised end-to-end — impossible while no `core` target exists
  - [x] Immutable-mechanism hook attempts fail (`_NEVER_EXTENSIBLE`)

- [x] **Phase 4:** Admin control plane
  - [x] REST routes under `/api/admin/plugins` (list, detail, enable, disable,
        **PUT** config, aggregated health)
  - [x] Auth via existing `SessionRecord`, tenant from `rec.tenant_id`
  - [x] Audit event per mutation
  - [ ] Console panel + per-plugin config forms — **not built**; the REST plane is

- [x] **Phase 5:** Bridge supervisor plugins (7 bridges, daemons untouched)
  - [x] Seven classes, 78 passing tests, both flag states
  - [ ] **A shipped declaration** — nothing puts them into `spec.plugins.installed`, so
        none ever loads

- [◑] **Phase 6:** Headless API-only boot
  - [x] SPA, `/local-stats` and the `/` redirect suppressed; independent of the bridge flag
  - [ ] Reordered boot sequence — **not implemented**
  - [ ] Presets A–D as a mechanism — **not implemented**, and arguably should be dropped

- [ ] **Phase 7:** Directory move behind import shims, both-state flag tests, docs, v0.11.0

---

## Success Criteria

Reach, not existence. A ✅ means the thing is used, not that the code compiles.

**Architecture**
- ✅ The registry expresses `boot_layer`, and `compliance` is undisableable through it —
  mechanism only; no compliance-boot-layer plugin exists to be refused
- ✅ A tenant declaration cannot claim a privileged boot layer — **genuinely live**, on both
  the declarative and the registry path
- ⬜ Admin can see `boot_layer`, `origin` and disableability for every plugin — the API
  does; there is no Console panel. (`tier` is deliberately **not** in this response)
- ⬜ `core/core_plugins/{compliance,core,bundled}/` layout reached (Phase 7)

**Control**
- ⬜ Admin can disable `bundled` / `installed` plugins from a dashboard — API yes, dashboard no
- ✅ Admin cannot disable `compliance` — refused at the registry, not only in the UI
  (mechanism; never exercised)
- ✅ Admin can configure any plugin that has a registry record — `PUT .../config`
- ✅ Registry mutations are audited

**Extensibility**
- ✅ The 8 existing provider registries are documented, and they are live
- ⬜ 3–5 new extension points exist **and fire** — they exist; **nothing fires them**
- ✅ Replacement targets `boot_layer=core` only — and therefore targets nothing

**Honest non-goals**
- ADR-0236's core extraction is not in this plan, and no task above references it
- gRPC is deferred, not planned
- Test counts in this document are **measured** (2026-07-27), not targets; the earlier
  "~160 tests" success criterion has been dropped as meaningless

---

## Example: replacing a `boot_layer=core` component

**Hypothetical.** `delegation-router/1.0.0` does not exist, nothing is on the `core` boot
layer, and this call would raise `PluginReplacementRefused` today. The snippet shows the
shape the mechanism expects once a `core` target exists.

```python
# A user-installed plugin that takes over routing entirely.
# manifest: boot_layer: installed, replaces: "delegation-router/1.0.0"

from corvin_plugins import CorvinPlugin

class KubernetesDelegationRouter(CorvinPlugin):
    plugin_id = "k8s-delegation-router/1.0.0"

    def on_load(self, ctx):
        self.ctx = ctx
        ctx.logger.info("k8s delegation router active")
```

The registry's `replace()` refuses this if the named target is not on `boot_layer=core`, and
refuses it outright for anything on `boot_layer=compliance`. What stays immutable regardless of
who replaces what: the hash-chained audit write, Ed25519 verification, token accounting,
and every compliance gate.

---

## Conclusion

**What changed from the first draft:**
1. The numbered "tier" values 0–3 → `layer` → **`boot_layer`**: `compliance | core |
   bundled | installed`. Two renames, because "layer" turned out to be four-way overloaded
2. `boot_layer=core` is replaceable; only `compliance` is immutable, and for regulatory reasons
3. Licensing moved off the load axis onto ADR-0156's `tier`
4. Bridges are supervised Node daemons, not rewritten Python plugins
5. gRPC deferred; REST only
6. The directory move is last, not first
7. ~8 weeks for the additive work, excluding the ADR-0236 extraction. Test counts in this
   document are now **measured**, not targets

**Why this works — and what it does not yet do:**
- Compliance is immutable and unflagged (`boot_layer=compliance`) — enforced today by the
  tripwire and the existing call sites, not by any plugin on that boot layer
- Defaults are ours, but nothing above `compliance` is a lock-in — in principle; there is
  currently no `core` component to replace
- Admin control is explicit and audited — the REST plane is live
- Every new surface ships dark behind a default-`false` flag — held for all four flags

**The honest summary:** Phases 1–5 built a boundary. Nothing has moved onto it yet. The
guard tests are what stop that sentence from quietly becoming false while nobody looks.
