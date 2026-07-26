# Plugin System — Implementation Plan

**Date:** 2026-07-26
**Status:** **Phases 0–4 implemented** (2026-07-26). Phase 5 not started by design.
**Author:** Claude Code (dialectical review of the 2026-07-26 design docs)
**Audience:** maintainer (solo) + Claude Code sessions
**Decision of record:** [ADR-0233](../../../Corvin-ADR/decisions/0233-plugin-system-consolidation.md)

## Delivery status

| Phase | State | Evidence |
|---|---|---|
| 0 Ground truth | ✅ done | prototype retired, model salvaged to `corvin_plugins/manifest.py`, 44 real tests replace 22 skips, `tsc` green again |
| 1 Additive backends | ✅ done | `AuditBackend`/`UserBackend` protocols, passthrough providers, 2 templates, boot tripwire, 33 tests incl. hostile-backend chain verification |
| 2 Fault isolation | ✅ done | `circuit_breaker.py`, breaker state in `health_check_all()`, `plugin_health_monitoring` flag, 31 tests |
| 3 Runtime lifecycle | ✅ done | `state.py` per-tenant registry (atomic, 0600, fail-closed) + real chained audit events, `plugin_runtime_lifecycle` flag, 45 tests |
| 4 Console surface | ✅ done | `routes/plugins.py`, `/app/plugins` page, `JsonSchemaForm`, `plugin_console_surface` flag, 17 route tests + **9/9 Playwright specs green** |
| 5 Distribution | ⛔ not started | blocked on the D7 tier-vocabulary rename and its own ADR, by design |
| **Refutation round** | ✅ done | found two DEAD mechanisms in the phases above — the tripwire was never called and `PluginContext` was never built — plus a PII leak in `health_check_all` and a breaker-as-auth-DoS. All fixed; `bootstrap.py` + 18 tests added. See ADR-0233 § Findings from the adversarial pass. |

**Test totals after implementation:** 319 green across `core/plugins/tests`, the
console plugin-route and feature-flag suites, and `core/orchestration`; 106 forge
audit/chain tests still green; 223 gateway tests green (the 3 failures there are
pre-existing — verified against a clean `git worktree` at HEAD, not caused by the
lifespan wiring); `tsc --noEmit` clean; `npm run build` succeeds.

**Playwright:** 9/9 green. Getting there required fixing `playwright.config.ts`,
which could not start its managed dev server at all: `webServer.url` probed
`http://localhost:3000` while `vite.config.ts` serves on **5173** under the
`/console/` base path, so every run without an externally started server died on
"Timed out waiting 60000ms from config.webServer". Both are now derived from
`CONSOLE_BASE_URL` with the correct default. The spec mocks auth and
`/setup/status` (SetupGate renders a `fixed inset-0 z-50` overlay that swallows
every click while setup is incomplete), so it runs against the dev server alone —
no gateway, no session, no fixture data.

**Still not executed:** `operator/bridges/run-all-tests.sh` — it exceeded a
15-minute budget twice and buffers all output until exit. The `audit.py` fan-out
change is covered instead by three adapter tests plus 106 forge audit/chain tests
run directly.

---

## 1. Decision

**Do not build a fifth extension system. Put the plugin work on the two paths that
already carry production traffic and already pass the compliance gates.**

### Thesis (rejected)

Finish `core/orchestration/plugin_system/` (the Marketplace prototype from
`docs/concepts/ADR-0XXX-PLUGIN_SYSTEM.md`), wire its REST router into the Console,
register the `/plugins` page, and continue with the Phase 3 roadmap
(E2E suite → Tier A migration → form generator → ratings UI).

### Antithesis

1. **The prototype has almost no salvageable runtime.** 2477 LOC, but
   `dependency_resolver.py`, `registry_manager.py` and `settings_manager.py` are
   **0 bytes**; all real logic sits in `models.py` (527 LOC). `api.py` is not even
   importable (`NameError: name 'router' is not defined` at line 155 — the Phase-2a
   install endpoint was appended *after* `return router`, outside the factory).
   What is genuinely valuable is the data model: enums, `Plugin`, registry-YAML
   round-trip, topological sort, `SettingsValidator`. That is a library, not a platform.
2. **Every compliance promise in the ADR is absent from the code.** `AuditEvent` is a
   dataclass with `to_dict()` and **no connection to the hash-chained `audit.jsonl`** —
   the `hash_chain` field from the ADR's own example is never computed.
   `lifecycle_manager.install()` verifies no checksum, no signature, no consent, and
   starts no sandbox. Quota is a dataclass; cgroups, bwrap, signing, and the gradated
   permission model exist only as prose.
3. **The capability already exists, hardened.** ADR-0096 (MCP Plugin Manager,
   `operator/mcp_manager/`, M1–M4 shipped) is a working marketplace: install from
   npm/pip/GitHub/Docker/local, SHA256 + Docker-digest pinning verified **on every
   spawn**, four activation scopes, L10 path-gate, L16 audit events, L34 locality and
   L35 egress checks, vault secret injection, and fail-closed `mcp_plugin.spawn_blocked`.
   ADR-0142 (**ACCEPTED**) + ADR-0156 add `ext.<vendor>.*` layers with a Tier-A/B/C
   capability boundary and a license gate. The Marketplace ADR references neither.
4. **Asymmetric, irreversible risk.** A half-finished installer that downloads and
   executes third-party code is the most dangerous feature class in this repo; the
   failure mode is RCE, and it stops being reversible the moment a user has installed
   a plugin (registry migration). Wiring is the hazardous step — not the refactor.
5. **Tier semantics already collide.** Marketplace Tier A/B/C = trust/provenance.
   ADR-0156 Tier A/B/C = capability boundary + license gate. Same letters, different
   meaning, both user-visible. Shipping both guarantees a support incident.
6. **A reasonable maintainer would reject it** on the repo's own rules: "Goal: a stable
   CorvinOS core", ship-dark-by-default, and a compliance baseline where sandbox,
   consent, audit-chain and signing are non-negotiable and non-flaggable.

### Synthesis (the decision)

The **product goal stays**: runtime install/enable/configure/uninstall with a Console
surface. The **implementation strategy changes**: build *on top of* the working
lifecycle contract and the hardened distribution path instead of beside them.

| Concern | Owner (load-bearing) | Not |
|---|---|---|
| Lifecycle + runtime registration (`on_load`/`on_unload`/`health_check`) | **ADR-0030 `core/plugins/corvin_plugins/`** — live, imported by `adapter.py:556-559`, 55 tests green | a second `PluginRegistry` |
| Distribution / install / pinning / spawn verification | **ADR-0096 `operator/mcp_manager/`** for tool-shaped extensions; **ADR-0142/0156** for layer-shaped ones | a new downloader without gates |
| Manifest, settings schema, dependency order, registry persistence | **salvaged** from `models.py` into `corvin_plugins` | left in an unwired parallel package |
| Fault isolation, health, structured logging, healing | **Compartmentalization ADR** stages 1–4, *additive* on `corvin_plugins` | extraction of L16/L18-21 |

`core/orchestration/plugin_system/` is **retired**, not finished (§ Phase 0).

---

## 2. Target architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Console: Settings → Plugins   (flag: plugin_console_surface)  │
│   list · enable/disable · JSON-Schema-generated settings form  │
└───────────────────────────┬──────────────────────────────────┘
                            │ REST, tenant from SessionRecord (never env)
┌───────────────────────────▼──────────────────────────────────┐
│ corvin_plugins  (ADR-0030 + ADR-0033, LOAD-BEARING)           │
│   protocol.py    CorvinPlugin · PluginContext · HealthStatus  │
│   registry.py    register/unregister/get/health_check_all     │
│   loader.py      entry_points + explicit class_path           │
│   manifest.py    NEW — manifest + settings schema + dep order │
│   providers/     notification · recall · summary · router     │
│                  NEW: audit_backend · user_backend            │
└───────────────────────────┬──────────────────────────────────┘
                            │ plugin.on_load(ctx) self-registers
┌───────────────────────────▼──────────────────────────────────┐
│ Layer registries (authoritative routing, unchanged)           │
│   engine_factory · ComputeEngineRegistry · channel_registry   │
└───────────────────────────┬──────────────────────────────────┘
                            │ artifacts arrive only via
┌───────────────────────────▼──────────────────────────────────┐
│ Distribution (hardened, existing)                             │
│   ADR-0096 mcp_manager   — pinning, spawn verify, L34/L35     │
│   ADR-0142/0156 layers   — ext.* namespace, Tier A/B/C, gate  │
└──────────────────────────────────────────────────────────────┘
                            │ every transition
┌───────────────────────────▼──────────────────────────────────┐
│ Mandatory core (hardcoded, tripwired, never pluginified)      │
│   L16 audit hash-chain · L44 house rules · consent · L10 gate │
└──────────────────────────────────────────────────────────────┘
```

**Invariant that makes this safe:** a plugin may *add* a backend or a rule. It can never
replace, disable, or weaken a mandatory mechanism. Core writes its own audit record
regardless of which audit backend is installed (see `ADR-0XXX-COMPLIANCE_HARDENING.md`
§ "Extension: Custom Audit Backends" — that document is right and this plan adopts it).

---

## 3. What this plan does NOT build

Named explicitly so it cannot be reintroduced as scope creep:

- No second plugin registry, no second lifecycle contract.
- No new HTTP downloader. Artifacts arrive through `mcp_manager` (pinned, spawn-verified)
  or through the layer-extension path. No `marketplace.corvinlabs.com` client until a
  real, signed backend exists.
- No community Tier C, no revenue share, no ratings UI, no key-rotation authority. Those
  are the *last* mile, not the first, and none is reachable before § Phase 5.
- No cgroup quota enforcement written from scratch. Resource limits reuse the forge-bwrap
  path or they are not claimed.
- **No extraction of L16 audit or L18-21 auth out of core.** Phase 1 of the
  Compartmentalization plan is rewritten from "extract" to "additive backend behind a
  guaranteed core" — extraction would violate the compliance baseline.

---

## 4. Phases

Effort is stated in **sessions** (one focused Claude-Code session, K_MAX = 5 iterations),
not engineer-weeks: this is a solo repo. Every phase is independently shippable and ends
green.

### Phase 0 — Restore ground truth (1 session, no new features)

The repo currently documents a state that does not exist. Nothing else may start until
the docs and the code agree.

| Task | Detail |
|---|---|
| Fix or delete the broken router | `core/orchestration/plugin_system/managers/api.py:155` — the module cannot be imported. Since the package is being retired, **delete** `api.py` + `test_api.py` rather than repair them. |
| Salvage, then retire | Move `models.py` (enums, `Plugin`, `PluginManifest`, registry YAML round-trip, `DependencyResolver`, `SettingsValidator`) to `core/plugins/corvin_plugins/manifest.py`. Port its tests. Then delete `core/orchestration/plugin_system/` including the three 0-byte manager files. |
| Quarantine the dead frontend | `PluginsPanel.tsx`, `MarketplaceInstall.tsx`, `usePlugins.ts`, `pages/plugins.tsx` are unreachable (no route, no nav) and call a nonexistent `/api/plugins`. Keep them, but mark them as pre-wiring drafts in a header comment; they are re-adopted in Phase 4 against the real endpoint. |
| Correct the claims | `docs/concepts/PLUGIN_SYSTEM_PHASE3_ROADMAP.md` claims "Phase 1/1b/2a/2b complete, 56/56 green". Measured: 105 passed / 22 skipped **only with `test_api.py` excluded**; with it, collection aborts. Rewrite the status section to the measured state or delete the file in favour of this plan. |
| Move the ADRs | Five `ADR-0XXX-*.md` files sit in `docs/concepts/` and `docs/adr/`. CLAUDE.md: ADRs live in `Corvin-ADR/decisions/` only. Assign real numbers (next free: **0233**) — see § 6. |

**Gate:** `uv run pytest core/plugins/tests core/orchestration -q` collects without error
and is green. No import of a deleted module remains (`grep -rn plugin_system`).
**DoD:** no document in the repo claims a capability the code does not have.

---

### Phase 1 — Additive provider protocols: `audit_backend`, `user_backend` (2–3 sessions)

This is Compartmentalization Stage 1, reframed as additive. `audit_backend` is **already**
in `KNOWN_PLUGIN_TYPES` (`protocol.py:193`); only the protocol and the registry are missing.

**Files**

- `core/plugins/corvin_plugins/protocol.py` — add `AuditBackend` and `UserBackend`
  `Protocol`s; add `"user_backend"` to `KNOWN_PLUGIN_TYPES`; add `audit_registry` /
  `user_registry` handles to `PluginContext` (mirrors the ADR-0033 provider handles).
- `core/plugins/corvin_plugins/providers/audit_backend.py` — `get_active()` / `set_active()`,
  default = a no-op passthrough. **The default must be a passthrough, not the real chain**:
  core keeps writing `audit.jsonl` itself. An installed backend receives a *copy* of the
  event and may fan it out (Postgres, S3, SIEM).
- `core/plugins/corvin_plugins/providers/user_backend.py` — same shape. Local backend
  only; LDAP/OIDC are **out of scope** until someone asks for them (the sprint plan's
  "scaffolds" are speculative work with a HIGH risk score attached).
- `core/plugins/templates/audit_backend_plugin.py`, `..._user_backend_plugin.py` —
  matching the existing 7 templates.
- `core/compliance/tripwire.py` — boot assertion: the core audit writer is reachable and
  the chain verifies, **independent of** any installed backend. Boot fails closed if not.

**Hard invariants (review vetoes)**

- An `audit_backend` plugin can never suppress, rewrite, or reorder a core audit record.
  Fan-out failure is logged and swallowed; it never fails the caller and never fails core.
- `user_backend` never becomes an auth bypass: an exception, timeout, or `None` from a
  backend means **deny**, never "admit as guest". No auto-admit, no trusted-observer list.
- No PII in any provider log line, label, or audit detail — scrubbed signatures only.

**Tests:** protocol conformance (2 backends × both protocols), fan-out failure isolation,
tripwire fires when core writer is stubbed out, deny-on-error for auth, audit-chain still
verifies with a hostile backend installed. Reuse `tests/conftest.py`'s `VOICE_AUDIT_PATH`
redirect so no test writes into the real GDPR chain.
**Flag:** none — the passthrough default *is* today's behavior; no observable change.
**Docs:** `docs/claude-ref/layer-plugins.md` + `layer-16-security.md` in the same commit.

---

### Phase 2 — Fault isolation + health (2 sessions)

**Files**

- `core/plugins/corvin_plugins/circuit_breaker.py` — closed → open → half-open, per
  `plugin_id`, trip on timeout or N consecutive failures, explicit fallback value.
  Wraps every *provider* call site, so a sick plugin degrades instead of cascading.
- `core/plugins/corvin_plugins/registry.py` — `health_check_all()` exists; add breaker
  state and last-error (type name only) to its output.
- Health polling stays **pull-based and optional**: NerveFiber (ADR-0177) asks; nothing
  polls on its own. "Monitoring is optional; core works without it."

**Flag:** `plugin_health_monitoring` (default `false`) — when off, no polling, no metrics
endpoint, exactly today's behavior.
**Tests:** breaker state machine incl. concurrency, degrade-to-fallback per provider type,
health output carries no PII, flag-off = zero polling (assert no timer registered).
**Explicitly deferred:** Compartmentalization Stages 3 (self-healing) and 4 (LDD-tuned
policies). They are re-proposed only after Stage 2 has been stable for one release — as
the ADR itself says. Do not implement healing in this plan.

---

### Phase 3 — Manifest, settings schema, install/enable lifecycle (2–3 sessions)

The distribution-independent half of the Marketplace idea, on the surviving contract.

**Files**

- `core/plugins/corvin_plugins/manifest.py` (from Phase 0 salvage) — manifest parse +
  validate, JSON-Schema settings validation, semver dependency order (topological sort,
  cycle → error), breaking-change detection between `settings_schema_version`s.
- `core/plugins/corvin_plugins/state.py` — per-tenant registry at
  `<tenant_home>/plugins/registry.yaml` and per-plugin settings under
  `<tenant_home>/plugins/instances/<id>/config.json`. Tenant resolution via
  `current_tenant()` → `validate_tenant_id()` → `tenant_home()` — **never** an env var,
  keyword-only `tenant_id` (ADR-0007).
- Lifecycle: `install` / `enable` / `config_change` / `disable` / `uninstall` on top of
  `registry.register/unregister`, each emitting a **real** hash-chained audit event
  through `bridges/shared/audit.py` (not a dataclass). Disable drains in-flight work with
  a bounded timeout, then force-releases.

**Hard invariants**

- Registry writes are atomic + mode 0600; a corrupt registry fails closed (no plugins
  loaded) and is reported, never silently reset.
- `uninstall` deletes plugin state but **never** its audit trail (immutable per GDPR;
  answers open question #4 of the Marketplace ADR: no).
- Settings are validated against the schema *before* persistence, and a rejected write
  leaves the previous config intact.

**Flag:** `plugin_runtime_lifecycle` (default `false`). Off = plugins load exactly as
today (entry_points / `spec.plugins.installed`), no runtime mutation.
**Tests:** both flag states; cross-tenant isolation (tenant A cannot see/enable tenant B's
plugins); audit event lands in the chain and `voice-audit verify` still exits 0; corrupt
registry fails closed; dependency cycle rejected; breaking-change upgrade requires
explicit confirmation (answers open question #1: user approval, not automatic).

---

### Phase 4 — Console surface (2 sessions)

**Files**

- `core/console/corvin_console/routes/plugins.py` — `GET /plugins`,
  `POST /plugins/{id}/{enable,disable}`, `POST /plugins/{id}/config`. Tenant from the
  authenticated `SessionRecord.tenant_id`. CSRF + re-auth on mutations, matching
  `personas.py`. Registered in `app.py` **only when** `plugin_console_surface` is on.
- `web-next`: re-adopt `usePlugins.ts` against the real endpoint (via `lib/api.ts`, not
  raw `fetch`), register the `/plugins` route in `App.tsx`, add the nav entry, and build
  `JsonSchemaForm.tsx` (string → text, enum → select, integer+range → slider, boolean →
  toggle, nested → recursive, validation feedback).
- `npm run build` after every change to `core/console/web-next` — otherwise the served
  bundle silently keeps the old UI.

**Flag:** `plugin_console_surface` (default `false`).
**Tests:** route tests incl. cross-tenant denial and CSRF; Playwright E2E for the golden
path (list → enable → change setting → disable) behind the flag; **plus** a flag-off test
asserting the route 404s and the nav entry is absent.

---

### Phase 5 — Distribution (scope-gated, do not start before Phase 4 ships)

Only now does "install something new from outside" become tractable — and it reuses gates
instead of inventing them.

- **Tool-shaped extensions** → extend `operator/mcp_manager/` (already: pinning, per-spawn
  SHA256/digest verification, L34 locality, L35 egress, vault secrets, `spawn_blocked`).
  Deliverable is a *catalog surface* in the Console, not a new installer.
- **Layer-shaped extensions** → ADR-0142/0156 path (`ext.<vendor>.*`, Tier A/B/C, license
  gate). Reconcile the Tier vocabulary here: **one** meaning of Tier A/B/C repo-wide, or
  rename one of them. This is a prerequisite, not a nice-to-have.
- Signing, key rotation, community tiers, ratings, monetization: separate ADR, separate
  decision, needs a real signing authority and a real backend first. Not in this plan.

---

## 5. Sequencing and risk

| Phase | Sessions | Risk | Blast radius if wrong |
|---|---|---|---|
| 0 Ground truth | 1 | 🟢 low | deletes unused code; reversible via git |
| 1 Provider protocols | 2–3 | 🟠 medium | audit fan-out + auth deny path — compliance-adjacent, hence tripwire + deny-on-error |
| 2 Fault isolation | 2 | 🟢 low | flag-off = no behavior change |
| 3 Lifecycle | 2–3 | 🟠 medium | per-tenant registry on disk; atomic writes + fail-closed |
| 4 Console | 2 | 🟢 low | flag-gated surface |
| 5 Distribution | TBD | 🔴 high | executes third-party code — do not enter without its own ADR |

**Critical path:** 0 → 1 → 3 → 4. Phase 2 may run in parallel with 3 (no shared files).
**Total to a usable Console plugin surface:** ~9–11 sessions.

---

## 6. Cross-cutting gates (apply to every phase)

**Feature flags.** Every new surface gets an entry in
`core/console/corvin_console/feature_flags.py::REGISTRY` with `default=False`, an owner, a
`target_release`, and tests for **both** states. Flags introduced here:
`plugin_health_monitoring`, `plugin_runtime_lifecycle`, `plugin_console_surface`.
Phase 1 gets **no** flag — the passthrough default is current behavior — and the compliance
mechanisms (tripwire, audit chain, consent, deny-on-error) get no flag, ever.

**ADRs.** Destination is `Corvin-ADR/decisions/` only. Two of the three were already
migrated in `c2444f5` — only one new number was needed:

| Number | Title | State |
|---|---|---|
| 0231 | Compartmentalization system (stages 1–4) | existed; **corrected** by 0233 (Phase 1 is additive; house-rules/data-classification struck from Tier B; Tier naming superseded) |
| 0232 | Compliance hardening — mandatory core vs. extensible | existed; adopted as written and inherited by 0233 |
| **0233** | **Plugin system consolidation onto the ADR-0030 contract** | **written** — holds the § 1 decision + the measured findings |

0233 also inherits the six core invariants of **ADR-0124** (API-first, audit-first,
L34/L35 enforced, secrets are names, schema-validated manifests, hot-reload) instead of
restating them, and registers the pre-existing overlap with ADR-0096/0110/0142/0156.

`docs/concepts/ADR-0XXX-PLUGIN_SYSTEM.md` is **not** promoted to an ADR; it is retained as
a design study and its status header says so. Stages 3–4 (self-healing, intelligent
healing) are re-proposed later, per ADR-0231's own gate.

**Docs in the same commit** (no deferral): `docs/claude-ref/layer-plugins.md`,
`docs/plugin-system.md` (currently describes only Personas/Forge/SkillForge/Bridge —
needs a fifth section once Phase 4 ships), `docs/claude-ref/layer-16-security.md` for
Phase 1, plus the diagrams under `docs/assets/plugin-*.svg`.

**Test discipline.** `bash operator/bridges/run-all-tests.sh` before any commit touching
`adapter.py` / `daemon.js` / `shared/js/`. Audit-touching tests must inherit the
`conftest.py` `VOICE_AUDIT_PATH` redirect. No `git stash` in the live worktree for a lint
baseline — use `git show HEAD:file | ruff --stdin-filename`.

**LDD.** `loop-driven-engineering` before the first edit of each phase;
`e2e-driven-iteration` per bugfix iteration; `docs-as-definition-of-done` before declaring
any phase done; `root-cause-by-layer` before any fix whose cause is unknown. K_MAX = 5 per
phase; on the 5th non-converging iteration, stop and re-scope rather than patch.

---

## 7. Definition of done (per phase)

- All tests green, both flag states covered for every new flag.
- `voice-audit verify` exits 0; no PII in any new log line, label, or audit detail.
- Docs + diagrams updated **in the same commit**; no statement in the repo overstates the
  code.
- ADR written or the skip justified in one sentence.
- No new import of a retired module; no second registry, downloader, or lifecycle.

---

## 8. Superseded

These remain in git history as design input; they are no longer execution plans:

- `docs/implementation/PHASE_1_SPRINT_EXECUTION_PLAN.md` — 8 sprints × 2 weeks, 3–4 named
  engineers, merge monopoly, k8s/Grafana ops. Written for a team that does not exist; its
  Phase-1 framing ("extract audit out of L16") also conflicts with the compliance baseline.
- `docs/adr/PHASE_1_IMPLEMENTATION_PLAN.md` — same objection, week-by-week variant.
- `docs/concepts/PLUGIN_SYSTEM_PHASE3_ROADMAP.md` — builds on a retired package and states
  a test count that no longer holds.
- `docs/concepts/TIER_A_MIGRATION_GUIDE.md` — references
  `core/orchestration/plugin_system/plugins/` and a `corvin_plugin` SDK that do not exist.
  Rewrite against `corvin_plugins` when Phase 3 lands, or delete.
