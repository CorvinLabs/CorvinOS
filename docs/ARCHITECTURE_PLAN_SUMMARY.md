# CorvinOS Compartmentalization Plan
## Open Core Architecture with Headless Core Engine

**Status:** 9 ADRs (ADR-0235 through ADR-0243) · Phases 0–2 implemented, Phases 3–7 open
**Timeline:** ~8 weeks for the additive work (does NOT include the ADR-0236 core extraction)
**Target Release:** v0.11.0 (after Phase 7)

---

## Quick Summary

CorvinOS transforms from monolithic to compartmentalized:

```
OLD: Bridge-centric Monolith
  └─ One crash = everything down

NEW: Headless Core + Open Core Plugins
  ├─ layer=compliance  = immutable regulatory mechanisms
  ├─ layer=core        = bundled reference implementations, replaceable
  ├─ layer=bundled     = shipped in the wheel, enable/disable per tenant
  └─ layer=installed   = user-installed into the tenant plugin directory
```

---

## Taxonomy: why the axis is called `layer`, not `tier`

The first draft of this plan called the axis "tier" and numbered its values 0–3. That
name is **already taken twice in this repository**, and reusing it would have created
exactly the second taxonomy that `CLAUDE.md` forbids:

- **"Tier A/B/C" is reserved, repo-wide, for ADR-0156's capability boundary** — what a
  plugin is *permitted to do*, and the licensing gate on it. No document in this series
  redefines it.
- **ADR-0233 D7 deliberately replaced the prototype's `tier` field with `origin`**
  (`builtin` | `vetted` | `community`) for provenance. That field is unchanged.

So three axes exist, and they are **orthogonal**:

| Axis | Values | Question it answers | Defined by |
|---|---|---|---|
| `layer` | `compliance` / `core` / `bundled` / `installed` | When is it loaded, and may it be switched off? | ADR-0243 |
| `tier` | A / B / C | What is it permitted to do (capability + licensing gate)? | ADR-0156 |
| `origin` | `builtin` / `vetted` / `community` | Where did it come from? | ADR-0233 D7 |

A plugin carries all three independently: a bundled Discord supervisor is
`layer=bundled`, `origin=builtin`, and whatever `tier` its capability set demands.
ADR-0235 adds a fourth, purely contractual axis — `support_class`
(`product` / `infrastructure` / `community`) — named that way precisely so it cannot be
read as ADR-0156's tiers either.

**Reference:** ADR-0243 § "Naming: why `layer`, not `tier`".

### Migration table (numbered draft values → current)

| Draft value | Current |
|---|---|
| 0 (compliance) | `layer: compliance` |
| 1 (core infrastructure) | `layer: core` |
| 2 (bundled) | `layer: bundled` |
| 3 (premium) | `layer: installed` (licensing is the separate ADR-0156 `tier` gate) |

The rename applies to directory paths and config keys too: the numbered plugin
directories become `core/core_plugins/{compliance,core,bundled}/`, and the numbered
config key becomes `plugins.bundled`.

---

## Architecture Layers

Sizes below are **LOC** (not bytes) and are **target magnitudes** for the layer split
described in ADR-0236 — not measurements of today's tree. See "Current state (measured)".

### `layer: compliance` — Mandatory Compliance (~2.4k LOC target)
- Audit, Consent, Flow Guard, House Rules, Erasure
- Hardcoded, tripwired, regulatory (GDPR / EU AI Act)
- ❌ Cannot disable, cannot replace, never flag-gated
- Failure policy: **boot fails** (existing tripwire)

### `layer: core` — Core Infrastructure (~6.6k LOC target)
- A2A, TDE, Recall, ACS, Compute, Delegation, Workflows, Engine, Voice Summary, Admin
- Reference implementations (our defaults)
- ✅ Can customize via extension points (8 provider registries exist today; 3–5 new ones in Phase 3)
- ✅ Can replace entirely via the manifest field `replaces: <plugin_id>`
- Failure policy: **degrade + audit event**, the platform stays up

### `layer: bundled` — Bundled Bridges and Front-ends (~3–4k LOC target)
- Discord, Slack, Telegram, WhatsApp, Signal, Teams, Email, Web UI, CLI
- Pre-installed, enable/disable per tenant config
- Failure policy: disabled = **quiet no-op**, never an error

### `layer: installed` — User-installed Plugins
- Installed into `~/.corvin/tenants/<tenant_id>/plugins/`, including replacements
- Licensed features (STT, ML classification, OKTA, Postgres) are gated by ADR-0156's
  `tier`, not by this axis
- Failure policy: **tenant-local failure only**

---

## Current state (measured)

The real tree does not look like the target, and the gap is roughly two orders of
magnitude. Measured 2026-07-26, filter: **`*.py` only, excluding `node_modules/`,
`.venv/`, `site-packages/`**. An unqualified line count is not reproducible — for
`core/console/corvin_console` a Python-only count and an all-source count differ by
more than 2x — so the filter must always be quoted with the number.

| Path | LOC (`*.py`, no `node_modules`/`.venv`/`site-packages`) |
|---|---|
| `operator/bridges/shared` | 181,127 |
| `core/console/corvin_console` | 63,734 |
| `core/plugins` | 12,660 |
| `core/compliance` | 2,911 |

*(`core/plugins` has since grown to ~14.5k LOC with the Phase 1–2 commit c455516, same
filter — the baseline above is the ADR-0236 measurement.)*

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

The one thing that is *not* open: `layer=compliance`. It is never disableable, never
replaceable, and never gets a feature flag in either direction.

---

## Deployment Models

| Model | Use Case |
|-------|----------|
| **Complete** | All bridges (Discord, Slack, Telegram, WhatsApp, Signal, Teams, Email, Web UI, Forge, SkillForge) |
| **Typical** | Web + Chat (Web UI, Discord, Slack, Forge, SkillForge) |
| **API-Only** | Enterprise backend (no bridges, pure REST API) |
| **Custom UI** | CLI-only or custom dashboard (`web_ui` disabled) |

---

## Phase Plan (0–7)

Additive work first; the directory move is the **last** structural step, not the first.
A move that relocates the audit writer touches the live GDPR hash chain, its path
resolution, and the boot tripwire — it is the highest-risk change in the plan, so it
lands at the end and with import shims.

| Phase | Objective | Status | Tests (target) |
|---|---|---|---|
| 0 | ADR consolidation (renumber to 0243, `tier` → `layer`, `support_class`, maintainer/SLA, bridge + gRPC corrections) | ✅ done | — |
| 1 | Layer-aware registry, purely additive, no file moves | ✅ done (c455516) | ~25 |
| 2 | Boot order + scoping (global before tenant, tenant may not claim privileged layers) | ✅ done (c455516) | ~30 |
| 3 | Extension points (document the 8 existing provider registries, add 3–5 new) | ⬜ open | ~35 |
| 4 | Admin control plane (REST only) | ⬜ open | ~30 |
| 5 | Bridge supervisor plugins (one Python supervisor per Node daemon) | ⬜ open | ~25 |
| 6 | Headless API-only boot path | ⬜ open | ~15 |
| 7 | Directory move with import shims, docs, v0.11.0 release | ⬜ open | both-state coverage |

**Test counts are planning targets, not commitments.** They size the work; they are not a
promise that exactly N tests will exist.

### What Phases 1–2 actually shipped

In `core/plugins/corvin_plugins/` (commit c455516):

- **`manifest.py`** — `PluginLayer`, `PluginRecord.layer`, `PluginRecord.replaces`,
  `PluginRecord.can_disable()`. A record without `layer` reads as `installed`: something
  that does not say what it is must not land somewhere privileged. `origin=community`
  may not claim a privileged layer (`compliance` / `core`), otherwise a community
  manifest could buy boot priority and permanent undisableability with one YAML line.
  `layer=compliance` may not declare `replaces` at all.
- **`registry.py`** — `layer_of()`, `plugins_by_layer()`, `can_disable()`, `disable()`,
  `replace()` (only a `layer=core` implementation is replaceable), and
  `unregister(operator_initiated=)`, which separates machinery from operator action:
  shutdown may unload anything, an admin route may not reach past `disable()` and unload
  the audit writer.
- **`bootstrap.py`** — `bootstrap_global()` loads bundled global plugins
  **compliance-first, then core**: a compliance failure raises and aborts the boot, a
  core failure degrades and writes an audit event. `register_global_plugin()` declares a
  bundled global plugin and its layer (compliance/core only). `_declared_layer()` lets a
  tenant-scoped declaration claim `bundled` or `installed` only — a privileged claim from
  `tenant.corvin.yaml` or `registry.yaml` is **downgraded to `installed` and audited**
  rather than honoured, on both the declarative and the runtime path. `bootstrap_all()`
  runs global → declarative → runtime, deduplicated.

Nothing in Phases 3–7 is implemented. The directory layout is still the pre-move one;
`core/core_plugins/{compliance,core,bundled}/` does not exist yet.

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
| 6 | `headless_api_mode` | `false` | Today's boot path; the Console is always started |

All four are registered in `core/console/corvin_console/feature_flags.py`.

### The deliberate exception

**`bootstrap_global()` carries no flag, in either direction.** The layer it exists to
load is the compliance layer, and `CLAUDE.md` forbids a switch on a compliance
mechanism — a default-off flag there is the same violation as an env kill-flag. The path
is safe to ship unflagged because it is a **no-op until a global plugin is registered**:
`_GLOBAL_SPECS` is empty today, no production call site invokes
`register_global_plugin()`, and `bootstrap_all()` therefore loads nothing new.

The same reasoning covers the Phase 1 registry work: adding the `layer` field is additive
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
| **ADR-0243** | Core vs. plugins architecture — defines `layer` |

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

Target state, not current state:

- ⬜ ~160 target tests across Phases 1–7, both flag states covered
- ⬜ Directory structure moved to `core/core_plugins/{compliance,core,bundled}/` behind import shims
- ✅ Plugin registry expresses `layer` with enable/disable and replacement
- ⬜ Admin control plane (REST) live behind `admin_control_plane`
- ⬜ All bridges supervised as `layer=bundled` plugins, still optional
- ⬜ Isolation: bridge crash ≠ core crash
- ⬜ `voice-audit verify` exits 0 on the same chain before and after the directory move
- ⬜ Documentation + diagrams updated in the same commits as the code
- ⬜ Ready for v0.11.0 release, every new flag still default-`false`

A latency target for the admin API has not been measured or agreed; none is claimed here.

---

## Why This Matters

### For Community
- Open core = no fork penalty
- All features included
- Can customize without forking
- Clear upgrade path

### For Enterprise
- Headless mode (REST API-driven)
- Replace any `layer=core` component (custom ACS, recall, routing)
- Tenant isolation (multi-tenant, secure)
- Compliance always enforced (GDPR tripwired, never flag-gated)

### For Maintainers
- Clear separation (`compliance` / `core` / `bundled` / `installed`)
- Easier to test (core works without bridges)
- Easier to scale (modular)
- Easier to extend (open plugin system)

CorvinOS is maintained by **Corvin Labs (solo maintainer)**. Support for every class of
plugin is **best-effort, with no contractual SLA** — there are no hour-denominated
response commitments anywhere in this plan, because a solo maintainer cannot honour them.

---

## Next Step

**Phase 3:** document the eight existing provider registries under
`core/plugins/corvin_plugins/providers/`, then add 3–5 new extension points behind
`plugin_extension_points`.
**Reference:** ADR-0242 implementation plan.
