# CorvinOS Compartmentalization Plan
## Open Core Architecture with Headless Core Engine

**Status:** 9 ADRs (ADR-0235 through ADR-0243) · Phases 0–5 shipped, Phase 6 partial,
Phase 7 open — with the reach qualifiers in [Phase Plan](#phase-plan-07)
**Timeline:** ~8 weeks for the additive work (does NOT include the ADR-0236 core extraction)
**Target Release:** v0.11.0 (after Phase 7)

---

## Read this first: the axis has a mechanism, not a population

The `boot_layer` axis is implemented and tested, and **its top two values have zero
production instances**. `_GLOBAL_SPECS` is empty, `register_global_plugin()` has no
production caller, `bootstrap_global()` returns `[]` on every install, and **no plugin
loads with `boot_layer=compliance` or `boot_layer=core`** — which makes
`registry.replace()` structurally unreachable, since it only accepts a `core` target.

Everything below is therefore "mechanism present, zero instances" unless it says
otherwise. That is not a defect; it is the honest reading of an axis that was built as a
boundary before it had a population. The claim is pinned by guard tests in
`core/plugins/tests/test_layered_boot.py::TestTheTopOfTheAxisHasNoProductionInstance`, so
the first real instance breaks the build and forces this file to be updated in the same
commit.

---

## Quick Summary

CorvinOS transforms from monolithic to compartmentalized:

```
OLD: Bridge-centric Monolith
  └─ One crash = everything down

NEW: Headless Core + Open Core Plugins
  ├─ boot_layer=compliance  = immutable regulatory mechanisms (Audit, Consent, Flow Guard, House Rules, Erasure)
  ├─ boot_layer=core        = two classes:
  │   ├─ immutable, License-gated: A2A Orchestration, Continuance (Recall), TDE, Delegation, Workflows
  │   └─ replaceable reference implementations: Engine Control, Voice Summary, Admin API
  ├─ boot_layer=bundled     = shipped in the wheel, enable/disable per tenant
  └─ boot_layer=installed   = user-installed into the tenant plugin directory
```

---

## Taxonomy: why the axis is called `boot_layer`, not `layer`, not `tier`

The first draft of this plan called the axis "tier" and numbered its values 0–3. That
name is **already taken twice in this repository**, and reusing it would have created
exactly the second taxonomy that `CLAUDE.md` forbids:

- **"Tier A/B/C" is reserved, repo-wide, for ADR-0156's capability boundary** — what a
  plugin is *permitted to do*, and the licensing gate on it. No document in this series
  redefines it.
- **ADR-0233 D7 deliberately replaced the prototype's `tier` field with `origin`**
  (`builtin` | `vetted` | `community`) for provenance. That field is unchanged.

The second draft called it `layer` — and walked into the same collision from the other
side. "Layer" was already carrying **four** meanings here: the L1–L44 layer stack, ADR-0124
audit layers (`routes/audit_layers.py`), the ADR-0142 layer-extension API that answers 403
with `reason="core_layer_immutable"` (`routes/extensions.py`), and quality layers
(`routes/quality_layers.py`). Renaming away from "tier" to land on an even more overloaded
word solved nothing. The axis is therefore **`boot_layer`**, enum `BootLayer`, values
unchanged.

So three axes exist, and they are **orthogonal**:

| Axis | Values | Question it answers | Defined by |
|---|---|---|---|
| `boot_layer` | `compliance` / `core` / `bundled` / `installed` | When is it loaded, and may it be switched off? | ADR-0243 |
| `tier` | A / B / C | What is it permitted to do (capability + licensing gate)? | ADR-0156 |
| `origin` | `builtin` / `vetted` / `community` | Where did it come from? | ADR-0233 D7 |

A plugin carries all three independently: a bundled Discord supervisor is
`boot_layer=bundled`, `origin=builtin`, and whatever `tier` its capability set demands.
ADR-0235 adds a fourth, purely contractual axis — `support_class`
(`product` / `infrastructure` / `community`) — named that way precisely so it cannot be
read as ADR-0156's tiers either.

**Reference:** ADR-0243 § "Naming: why not `tier`".

**Renamed API surface:** `registry.boot_layer_of()` · `registry.plugins_by_boot_layer()` ·
`register(..., boot_layer=)` · `replace(..., boot_layer=)` ·
`bootstrap._declared_boot_layer()` · `register_global_plugin(class_path, boot_layer=)` ·
`PluginRecord.boot_layer` · the JSON/YAML key `boot_layer` · the audit event
`plugin.boot_layer_rejected` (detail keys `boot_layer` / `declared_boot_layer`) · the
admin-API field `boot_layer` and the health aggregate `by_boot_layer`.

### Migration table (numbered draft values → current)

| Draft value | Current |
|---|---|
| 0 (compliance) | `boot_layer: compliance` |
| 1 (core infrastructure) | `boot_layer: core` |
| 2 (bundled) | `boot_layer: bundled` |
| 3 (premium) | `boot_layer: installed` (licensing is the separate ADR-0156 `tier` gate) |

The rename applies to the target directory paths too: the numbered plugin directories
become `core/core_plugins/{compliance,core,bundled}/` (Phase 7, not yet created).

It does **not** produce a `plugins.bundled` config key — earlier revisions of this
document said it would. No such key has a reader anywhere in the repo. The one config key
that exists is **`spec.plugins.installed`**, whose entries each carry a `boot_layer:`
field; the `installed` name is historical (ADR-0030) and is not the `installed` boot-layer
value.

---

## The four boot layers

Sizes below are **LOC** (not bytes) and are **target magnitudes** for the split described
in ADR-0236 — not measurements of today's tree. See "Current state (measured)".

The "instances today" column is the load-bearing one.

### `boot_layer: compliance` — Mandatory Compliance (~2.4k LOC target)
- Intended population: Audit, Consent, Flow Guard, House Rules, Erasure
- **Instances today: none.** These mechanisms exist and are enforced, but as ordinary
  in-process modules plus the boot tripwire — not as plugins on this boot layer
- Hardcoded, tripwired, regulatory (GDPR / EU AI Act)
- ❌ Cannot disable, cannot replace, never flag-gated — rules that apply the moment the
  first instance exists
- Failure policy: **boot fails** (existing tripwire). Unreachable today

### `boot_layer: core` — Core Infrastructure (~6.6k LOC target)
- Intended population: A2A, TDE, Recall, ACS, Compute, Delegation, Workflows, Engine,
  Voice Summary, Admin
- **Instances today: none** — which makes `registry.replace()` structurally unreachable,
  because it accepts only a `core` target
- ✅ Can customize via extension points: the 8 provider registries are live; the four
  Phase-3 bus points exist but **have no wired call site**
- ✅ Can replace entirely via the manifest field `replaces: <plugin_id>` — tested, never
  yet exercised against a real target
- Failure policy: **degrade + audit event**, the platform stays up. Unreachable today

### `boot_layer: bundled` — Bundled Bridges and Front-ends (~3–4k LOC target)
- Intended population: Discord, Slack, Telegram, WhatsApp, Signal, Teams, Email, Web UI, CLI
- **Instances today: none loaded.** The seven bridge supervisor classes exist and are
  tested, but nothing in the shipped tree declares them in `spec.plugins.installed`
- Failure policy: disabled = **quiet no-op**, never an error

### `boot_layer: installed` — User-installed Plugins
- Installed into `~/.corvin/tenants/<tenant_id>/plugins/`, including replacements
- **The only boot layer a plugin actually reaches today**, and only if an operator wrote
  the declaration themselves; it is also the value every downgraded privileged claim
  lands on
- Licensed features (STT, ML classification, OKTA, Postgres) are gated by ADR-0156's
  `tier`, not by this axis
- Failure policy: **tenant-local failure only**

---

## Current state (measured)

The real tree does not look like the target, and the gap is roughly two orders of
magnitude. **Re-measured 2026-07-27**, filter: **`*.py` only, excluding `node_modules/`,
`.venv/`, `site-packages/`**. An unqualified line count is not reproducible — for
`core/console/corvin_console` a Python-only count and an all-source count differ by
more than 2x — so the filter must always be quoted with the number.

| Path | LOC (`*.py`, no `node_modules`/`.venv`/`site-packages`) |
|---|---|
| `operator/bridges/shared` | 181,127 |
| `core/console/corvin_console` | 65,305 |
| `core/plugins` | 19,151 |
| `core/plugins/corvin_plugins` (the contract itself) | 7,781 |
| `core/gateway` | 15,285 |
| `core/compliance` | 2,911 |

Earlier revisions of this file quoted 63,734 / 12,660 as current; those were the
ADR-0236-era baseline and had drifted. The `core/plugins` figure has roughly grown by
half since, which is Phases 1–5 landing.

Where the building blocks actually live today:

| Mechanism | Actual location today |
|---|---|
| House rules gate (L44) | `operator/bridges/shared/` |
| Consent gate (L18) | `operator/bridges/shared/` |
| Erasure orchestrator (L36) | `operator/bridges/shared/` |
| Audit writer + hash chain (L16) | `core/compliance/corvin_compliance_reports/` |
| Boot tripwire (ADR-0232/0233) | `core/compliance/corvin_compliance_reports/tripwire.py` |

**None of** `core/compliance/audit_writer.py`, `core/session/middleware.py`, or
`core/routing/http_router.py` exists. Earlier drafts of ADR-0236 cited them as if they
did; they are *target* paths. Moving the compliance mechanisms out of the 181k-LOC
`operator/bridges/shared/` tree is the reason ADR-0236 is **not** part of this plan: it
touches the live GDPR hash chain and the running bridges at the same time, and it gets
its own plan, its own ADR, and its own migration gate.

---

## Key Innovation: Open Core Philosophy

**NOT:**
- "You must use our ACS"
- "Voice Summary is locked in"
- "Bridges are mandatory"

**YES:**
- "Here's our battle-tested default"
- "Want to customize? Use extension points"
- "Want to replace? Ship a plugin with `replaces: <plugin_id>`"
- "All bridges included, choose what to enable"

The one thing that is *not* open: `boot_layer=compliance`. It is never disableable, never
replaceable, and never gets a feature flag in either direction.

---

## Deployment Models

These are **descriptions of flag combinations, not a selectable mechanism.** There is no
preset key, no preset resolver and no `deployment_mode` setting anywhere in the code; each
model is just `headless_api_mode` × `bridge_supervisor_plugins` plus whatever the operator
wrote into `spec.plugins.installed`.

| Model | Use Case |
|-------|----------|
| **Complete** | All bridges (Discord, Slack, Telegram, WhatsApp, Signal, Teams, Email, Web UI, Forge, SkillForge) |
| **Typical** | Web + Chat (Web UI, Discord, Slack, Forge, SkillForge) |
| **API-Only** | Enterprise backend (no bridges, pure REST API) |
| **Custom UI** | CLI-only or custom dashboard |

---

## Phase Plan (0–7)

Additive work first; the directory move is the **last** structural step, not the first.
A move that relocates the audit writer touches the live GDPR hash chain, its path
resolution, and the boot tripwire — it is the highest-risk change in the plan, so it
lands at the end and with import shims.

**Status column reads "code exists and its tests pass". The reach column is what tells you
whether anything uses it.**

| Phase | Objective | Status | Reach today |
|---|---|---|---|
| 0 | ADR consolidation (renumber to 0243, `tier` → `boot_layer`, `support_class`, maintainer/SLA, bridge + gRPC corrections) | ✅ done | — |
| 1 | Boot-layer-aware registry, purely additive, no file moves | ✅ done (c455516) | the two privileged values have **zero instances** |
| 2 | Boot order + scoping (global before tenant, tenant may not claim privileged boot layers) | ✅ done (c455516) | `bootstrap_global()` returns `[]` on every install; the tenant-downgrade guard *is* live |
| 3 | Extension points (document the 8 existing provider registries, add 3–5 new) | ✅ **all 4 wired** | ADR-0251, 2026-07-27 |
| 4 | Admin control plane (REST only) | ✅ done | six routes live behind `admin_control_plane`; gRPC deferred |
| 5 | Bridge supervisor plugins (one Python supervisor per Node daemon) | ✅ **wired** | the boot path declares the bundled seven when `bridge_supervisor_plugins` is on (2026-07-27) |
| 6 | Headless API-only boot path | ◑ **partial** | browser surfaces suppressed; **no reordered boot sequence**, **no preset mechanism** |
| 7 | Directory move with import shims, docs, v0.11.0 release | ⬜ open | `core/core_plugins/` does not exist |

### Measured test counts (2026-07-27, not targets)

Earlier revisions of this table carried planning targets (~25 / ~30 / …) in a column
labelled "Tests". Those were never measurements. These are:

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

### What Phases 1–2 actually shipped

In `core/plugins/corvin_plugins/` (commit c455516):

- **`manifest.py`** — `BootLayer`, `PluginRecord.boot_layer`, `PluginRecord.replaces`,
  `PluginRecord.can_disable()`. A record without `boot_layer` reads as `installed`: something
  that does not say what it is must not land somewhere privileged. `origin=community`
  may not claim a privileged boot layer (`compliance` / `core`), otherwise a community
  manifest could buy boot priority and permanent undisableability with one YAML line.
  `boot_layer=compliance` may not declare `replaces` at all.
- **`registry.py`** — `boot_layer_of()`, `plugins_by_boot_layer()`, `can_disable()`, `disable()`,
  `replace()` (only a `boot_layer=core` implementation is replaceable — and none exists,
  so this entry point is currently unreachable), and `unregister(operator_initiated=)`,
  which separates machinery from operator action: shutdown may unload anything, an admin
  route may not reach past `disable()` and unload the audit writer.
- **`bootstrap.py`** — `bootstrap_global()` loads bundled global plugins
  **compliance-first, then core**: a compliance failure raises and aborts the boot, a
  core failure degrades and writes an audit event. **Both branches are unreachable today**
  because `_GLOBAL_SPECS` is empty. `register_global_plugin()` declares a bundled global
  plugin and its boot layer (compliance/core only) and **has no production caller**.
  `_declared_boot_layer()` lets a tenant-scoped declaration claim `bundled` or `installed`
  only — a privileged claim from `tenant.corvin.yaml` or `registry.yaml` is **downgraded
  to `installed` and audited** (`plugin.boot_layer_rejected`) rather than honoured, on both
  the declarative and the runtime path. That guard is the one part of the privileged-layer
  machinery that is genuinely live. `bootstrap_all()` runs global → declarative → runtime,
  deduplicated, and is called from `core/gateway/corvin_gateway/app.py::_lifespan`.

**No entry-point discovery for global plugins.** A `corvin.global_plugins` group was
implemented and removed before it had a user — any third-party wheel could have published
`compliance:whatever` and been loaded first and undisableable, with no `PluginRecord`.
`bootstrap.GLOBAL_ENTRY_POINT_GROUP` is `None`. Earlier revisions of this file and of
`layer-plugins.md` described the group as if it shipped.

The directory layout is still the pre-move one; `core/core_plugins/{compliance,core,bundled}/`
does not exist yet.

---

## Feature Flags

Every new feature sits behind a named flag in `spec.features.<flag_id>` of
`tenant.corvin.yaml`, defaults to **`false`**, is toggleable from Console →
Settings → Features without a restart, degrades quietly to the pre-feature path when off,
and carries tests for **both** states.

| Phase | Flag ID | Default | Off-path behavior |
|---|---|---|---|
| 3 | `plugin_extension_points` | `false` | Bundled defaults run; registered hooks are ignored |
| 4 | `admin_control_plane` | `false` | `/api/admin/*` routes are absent (404) |
| 5 | `bridge_supervisor_plugins` | `false` | Bridges managed exactly as today by `bridge_manager.py` |
| 6 | `headless_api_mode` | `false` | SPA mounted, `/local-stats` served, `/` redirects to `/console/` — i.e. today's behaviour. The **boot sequence is identical in both states** |

All four are registered in `core/console/corvin_console/feature_flags.py`.

### The deliberate exception

**`bootstrap_global()` carries no flag, in either direction.** The boot layer it exists to
load is the compliance boot layer, and `CLAUDE.md` forbids a switch on a compliance
mechanism — a default-off flag there is the same violation as an env kill-flag. The path
is safe to ship unflagged because it is a **no-op until a global plugin is registered**:
`_GLOBAL_SPECS` is empty today, no production call site invokes
`register_global_plugin()`, and `bootstrap_all()` therefore loads nothing new. Both facts
are pinned by `test_layered_boot.py::TestTheTopOfTheAxisHasNoProductionInstance` rather
than only asserted here.

The same reasoning covers the Phase 1 registry work: adding the `boot_layer` field is additive
and defaults to the least privileged value, so the flag-off and flag-on behavior would be
identical.

---

## Related Documents

| ADR | Focus |
|----------|-------|
| **ADR-0235** | Plugin classification — defines `support_class` (NOT ADR-0156's tiers) |
| **ADR-0236** | Minimal core specification (~2,400 LOC) — long-term target, explicitly not in this plan |
| **ADR-0237** | Extensible core plugins + replacement pattern (`replaces:`) |
| **ADR-0238** | Bundled bridges — one Python supervisor per Node daemon |
| **ADR-0239** | Admin control plane vs. Web UI (gRPC deferred) |
| **ADR-0240** | Plugin scoping (global vs. tenant) |
| **ADR-0241** | Headless core architecture |
| **ADR-0242** | Implementation plan (Phases 0–7, ~8 weeks) |
| **ADR-0243** | Core vs. plugins architecture — defines `boot_layer` |

ADR-0243 was originally drafted as "ADR-0234"; it was renumbered because ADR-0234
(Audit-Chain Boot-Gate Semantics) already holds that number and is referenced from
`CLAUDE.md`.

## Technical Docs (in this directory)

- `HEADLESS_CORE_ARCHITECTURE.md` — API-driven core, subprocess isolation
- `EXTENSIBLE_CORE_PLUGINS.md` — Extension-point patterns, replacement examples
- `BUNDLED_BRIDGES_STRUCTURE.md` — Bridge supervisor interface, enable/disable
- `PLUGIN_DIRECTORY_STRUCTURE.md` — Global vs. tenant plugin layout
- `IMPLEMENTATION_PLAN_PHASE_1.md` — Detailed phase-by-phase plan

---

## Success Criteria (end of Phase 7)

Target state, not current state. A ✅ here means "reached", not "the code exists".

- ✅ Directory structure moved to `core/core_plugins/{compliance,core,bundled}/` behind import shims — ⬜ **not reached** (Phase 7)
- ✅ Plugin registry expresses `boot_layer` with enable/disable and replacement — mechanism ✅, but replacement is **structurally unreachable** while no `core` target exists
- ✅ Admin control plane (REST) live behind `admin_control_plane` — **reached**, six routes, 24 passing tests
- ⬜ All bridges supervised as `boot_layer=bundled` plugins, still optional — classes ✅, **declarations ⬜**, so no bridge is supervised on any install
- ⬜ Isolation: bridge crash ≠ core crash — not demonstrated, because no supervisor loads
- ⬜ `voice-audit verify` exits 0 on the same chain before and after the directory move
- ⬜ Documentation + diagrams updated in the same commits as the code
- ⬜ Ready for v0.11.0 release, every new flag still default-`false`

A latency target for the admin API has not been measured or agreed; none is claimed here.
The earlier "~160 target tests" criterion has been dropped: the measured counts above make
a target number meaningless as a gate.

---

## Why This Matters

### For Community
- Open core = no fork penalty
- All features included
- Can customize without forking
- Clear upgrade path

### For Enterprise
- Headless mode (REST API-driven) — today: no browser surface; the boot path is unchanged
- Replace any `boot_layer=core` component (custom ACS, recall, routing) — **not yet
  possible**: no component is on the `core` boot layer, so there is nothing to name as a
  replacement target
- Tenant isolation (multi-tenant, secure)
- Compliance always enforced (GDPR tripwired, never flag-gated) — by the tripwire and the
  existing call sites, not by a `boot_layer=compliance` plugin

### For Maintainers
- Clear separation (`compliance` / `core` / `bundled` / `installed`)
- Easier to test (core works without bridges)
- Easier to scale (modular)
- Easier to extend (open plugin system)

CorvinOS is maintained by **Corvin Labs (solo maintainer)**. Support for every class of
plugin is **best-effort, with no contractual SLA** — there are no hour-denominated
response commitments anywhere in this plan, because a solo maintainer cannot honour them.

---

## Next Steps

The remaining work is no longer "build the next phase" — it is **closing the gap between
the mechanisms that shipped and anything using them**:

1. ~~**Wire the four extension points** into their call sites.~~ Done
   2026-07-27 (ADR-0251). `test_extension_point_call_sites.py` now guards the
   reverse direction — a point that LOSES its caller fails the suite.
2. ~~**Declare the bridge supervisors** somewhere shipped.~~ Done 2026-07-27:
   `bootstrap._bundled_bridge_declarations()` injects them from
   `bridges/registry_entries.py`. Declaring is not starting — ADR-0238's
   six-condition start gate is untouched.
3. **Finish or retire the Phase 6 boot reorder** and the preset idea — one or the other,
   not a standing promise.
4. **First instance on a privileged boot layer**, which is what turns Phases 1–2 from a
   boundary into a mechanism with reach. The guard tests fail on that day and force every
   "zero instances" line in this doc set to be updated in the same commit.
5. **Phase 7** directory move behind import shims.

**Reference:** ADR-0242 implementation plan.
