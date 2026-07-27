# Plugin System — Activation Plan

**Date:** 2026-07-27
**Status:** Proposed
**Author:** Claude Code
**Audience:** maintainer (solo) + Claude Code sessions
**Decisions of record:** [ADR-0233](../../../Corvin-ADR/decisions/0233-plugin-system-consolidation.md),
[ADR-0242](../../../Corvin-ADR/decisions/0242-implementation-plan-phase-1.md),
[ADR-0243](../../../Corvin-ADR/decisions/0243-core-vs-plugins-architecture.md),
plus the two this plan calls for: ADR-0250 (tenant-scoped provider registries) and
ADR-0251 (extension-point call sites).

**Relationship to the existing plans.** [`PLUGIN_SYSTEM_IMPLEMENTATION_PLAN.md`](PLUGIN_SYSTEM_IMPLEMENTATION_PLAN.md)
covers ADR-0233 Phases 0–4 and is complete; it is **not** superseded. This document
covers a different axis: the mechanisms built since then that have no call site.
[`PHASE_1_SPRINT_EXECUTION_PLAN.md`](PHASE_1_SPRINT_EXECUTION_PLAN.md) remains superseded.

---

## 1. The finding this plan exists for

Nineteen ADRs (0231–0249) describe the plugin system. Verified against the tree on
2026-07-27, the mechanisms they specify are largely **built**. What most of them lack
is a **caller**.

This is the defect class ADR-0233 named in the prototype it retired, then found twice
in its own implementation (dead tripwire, unbuilt `PluginContext`). It is now present
a third time, at larger scale. The pattern is stable enough to state as a rule:

> A unit test proves a mechanism *works*. It does not prove the mechanism is *reached*.
> Every mechanism in this plan ships with a test that asserts its call site exists.

### Verified gaps

| # | Gap | Evidence (2026-07-27) |
|---|---|---|
| G1 | **Four extension points never fire.** `ep.invoke()` appears only in `core/plugins/tests/`. No production path calls `engine.model_selection`, `engine.engine_selection`, `delegation.route_selection_policy` or `workflow.workflow_gate`. | repo-wide grep for the point names → `tests/` only |
| G2 | **`user_backend` has no consumer.** The CLAUDE.md invariant *"failure/timeout/rejection = deny, never guest"* runs through code no auth path reaches. | `surface_map.py:132-147`, no hit in console/adapter auth |
| G3 | **Three handles are never populated.** `bootstrap_all(**registries)` forwards `compute_registry` / `engine_factory` / `channel_registry` correctly; the sole production caller passes none, so every template's `if ctx.<handle> is not None:` skips silently. | `core/gateway/corvin_gateway/app.py:163-167` |
| G4 | **`stt_provider` / `data_connector` have no consumer.** Registry and handle exist; nothing outside `corvin_plugins` calls `get_active()`. | `surface_map.py:148-177` |
| G5 | **Bridge supervisors are never registered.** `bridges/registry_entries.py` exists and is imported by nothing outside the package. | `grep -rn registry_entries` outside `core/plugins/` → empty |
| G6 | **Provider slots are process-wide.** One active provider per process across all tenants. Documented in ADR-0233's addendum, not fixed. | `providers/*.py` module-level state |
| G7 | **`corvin plugin install` is absent**, although ADR-0248 describes the install flow through it. | `plugin_cmd.py` — `types` / `check` / `new` only |
| G8 | **No trust anchor is deposited**, so `origin=vetted` is unreachable in practice. | `trust.py:153-179` reads a file that does not exist |
| G9 | **Eight feature flags have no owner and no target release**, which CLAUDE.md requires. | `core/console/corvin_console/feature_flags.py` |
| G10 | `boot_layer=compliance`/`core` have zero instances — a mechanism with no users. | `bootstrap.py:243` (`_GLOBAL_SPECS = []`) |

G10 is **not** a defect. CLAUDE.md already describes it as "a MECHANISM, today with
zero instances above `bundled`", and the guard tests in `test_layered_boot.py` fail on
the first real instance. It is listed so nobody closes it by accident.

### Explicitly out of scope — deferred by decision, not by omission

Closing these would reverse a maintainer decision, not complete an implementation:

| Item | Deferred by |
|---|---|
| Plugin process isolation / sandboxing | ADR-0249 § "Not decided here" |
| gRPC admin transport | ADR-0239 |
| ADR-0236 core extraction out of `operator/bridges/shared/` | ADR-0236 status, ADR-0242 § Overview |
| ADR-0231 Stage 4 (LDD-tuned healing policies) | needs production MTTR data |
| `corvin plugin list` | ADR-0244 — dropped, not deferred |
| Marketplace downloader / `install <name>` / search | ADR-0233 D3, ADR-0248 |
| Provider modules for `compute_engine` / `worker_engine` / `bridge_channel` | ADR-0245 § Alternatives — owed its own decision |
| Templates for `stt_provider` / `data_connector` | ADR-0246 — a scaffold for a dead registry manufactures the silent failure |
| Signature revocation | ADR-0249 — known gap, stated as such |

Note the interaction: the two deferred templates in ADR-0246 become *unblocked* by
Stage 4 of this plan, because "deferred until something consumes them" is exactly the
condition Stage 4 changes. They are still not in this plan's scope.

---

## 2. The sequencing decision

ADR-0242 ends at Phase 7 with the directory move to `core/core_plugins/`. **This plan
inverts that and drops it.**

**Activate before relocating.** Building Phase 7 now would move dead code across the
path that resolves the GDPR audit writer. ADR-0242 already rates that move the
single highest-risk change in the set and its only benefit is layout conformance with
ADR-0243's target diagram. Risk high, benefit cosmetic, and every stage below makes
the eventual move *smaller* by proving what is actually reachable. It is therefore
removed from this plan; if it happens it is a project with its own migration gate and
its own `voice-audit verify` before/after evidence.

**Ordering constraint that is load-bearing:** Stage 1 must precede Stage 2. Reviving
`user_backend` while the provider slots are process-wide means a plugin installed by
tenant A authenticates for every tenant. That is strictly worse than the dead state
it replaces.

---

## 3. Stages

### Stage 0 — The call-site gate (0.5 d)

**Deliverable:** one test module in the mandatory suite, red on arrival.

- For every member of `KNOWN_EXTENSION_POINTS`: assert a production call site exists
  (an `invoke()` reference outside `core/plugins/`).
- For every `surface_map` entry with `consumed_by is not None`: assert that the named
  file actually references the registry's `get_active()`.
- For every entry with `consumed_by is None`: assert `dead_reason` is set, so the map
  cannot go quietly stale in the other direction.

**Gate:** the test is red at the start of Stage 1 and green at the end of Stage 5.
It is the regulator for every stage below — no stage counts as done while its row is
red.

**Why first:** without it, every stage below can be declared complete on unit tests
alone, which is the exact failure this plan exists to correct.

---

### Stage 1 — Tenant-scoped provider registries (1.5 w)

**ADR:** 0250 (new). **Flag:** none for the refusal gate — see the ADR.

Two-part answer, deliberately not one:

1. **Immediate, this stage:** a fail-closed refusal. A non-builtin provider plugin
   may not be enabled on an install with more than one tenant. Refusal is audited.
   This protects today's installs without touching the provider protocol.
2. **Target, its own migration:** key the eight registries by tenant, threaded through
   `PluginContext` rather than module globals.

**Tests:** refusal fires on a 2-tenant install; a builtin passthrough provider is
unaffected; single-tenant installs behave exactly as today; the refusal writes an
audit event to the refusing tenant's chain.

**Docs:** `docs/claude-ref/layer-plugins.md` § "Known limit" changes from *"do not
install a third-party provider plugin on a multi-tenant install"* (an instruction the
software did not enforce) to a description of an enforced refusal.

---

### Stage 2 — `user_backend` reaches the auth path (1 w)

**Closes:** G2. **Flag:** `plugin_extension_points` is wrong here; this is a provider
registry, gated by `plugin_runtime_lifecycle` like the others.

- Call site in the console session-establishing path, after the existing local-auth
  resolution and before guest admission.
- The ADR-0233 invariant is enforced at the call site, not only inside the provider:
  exception, timeout, or `None` → **deny**. Auth *denials* still must not open the
  circuit breaker (ADR-0233 § round 1) — only infrastructure failures do.

**Tests:** a backend that raises admits nobody; one that times out admits nobody; one
that returns `None` admits nobody; three wrong passwords do not open the breaker; with
no backend installed, login behaves exactly as today.

**Why this is the highest-priority stage after Stage 1:** it is the only gap in this
plan where a compliance invariant is documented as held and is not held.

---

### Stage 3 — Extension-point call sites (1.5 w)

**ADR:** 0251 (new). **Closes:** G1. **Flag:** `plugin_extension_points`.

| Point | Call site | Constraint the call site enforces |
|---|---|---|
| `engine.engine_selection` | shared `delegation_policy` | A hook may not widen the operator's `spec.web_chat.worker_engine` choice |
| `engine.model_selection` | per-step model selector (ADR-0181) | `None` = no opinion = no hook |
| `delegation.route_selection_policy` | delegation classifier | Every degrade ladder still ends at `native` |
| `workflow.workflow_gate` | before the first node executes | `fail_closed`: a raising hook denies; see ADR-0251 for what a non-`bool` return means |

**Tests:** each point fires from the real path; a hook returning an engine the operator
did not select is refused **by the call site**; a raising hook on `workflow_gate`
denies the run; a raising hook on `model_selection` costs the custom model, not the
turn; flag-off = no hook fires anywhere.

---

### Stage 4 — Populate the three handles (1 w)

**Closes:** G3, and G4 in part. **Flag:** none — passing a registry that was already
being forwarded changes nothing until a plugin of that type is installed.

- `core/gateway/corvin_gateway/app.py` passes `compute_registry`, `engine_factory` and
  `channel_registry` into `bootstrap_all()`.
- `surface_map.py` rows for `compute_engine`, `worker_engine`, `bridge_channel` gain a
  `consumed_by`; the conformance test forces this in the same commit.
- `stt_provider` and `data_connector` (G4) are a **different** problem — the handle is
  populated, nothing calls `get_active()`. Decide per type: wire the consumer (STT is
  reachable via L23) or leave `dead_reason` accurate. Do not pretend the row is live.

**Tests:** a template plugin of each of the three types registers and is invoked once
through its subsystem; with none installed, boot is unchanged.

---

### Stage 5 — Register the bridge supervisors (1 w)

**Closes:** G5. **Flag:** `bridge_supervisor_plugins`.

- `bridges/registry_entries.py` is reached from the boot path.
- ADR-0238's two fail-closed defaults stay: no automatic restart, and
  `confident=False` means the supervisor does **not** start.
- The Node daemons are untouched.

**Tests:** start/stop/health per supervisor; a dead daemon surfaces as unhealthy
without taking down the core; flag-off = `bridge_manager.py` manages bridges exactly as
today; the ADR-0242 guard test that fails if the supervisor ever reads
`headless_api_mode` stays green.

**Before commit:** `bash operator/bridges/run-all-tests.sh` (CLAUDE.md). Budget it —
the previous run exceeded 15 minutes and buffers output until exit.

---

### Stage 6 — `install` and the trust anchor (1 w)

**Closes:** G7, G8.

- `corvin plugin install <path>`: **local path only**, never a URL, never a name
  resolution (ADR-0248). It writes the `class_path` into `tenant.corvin.yaml` and
  prints the `auto_discover_entry_points` step in the same words the generated README
  uses (ADR-0246).
- Under `plugin_trust_enforcement`, an `origin=community` install requires the explicit
  per-plugin confirmation from ADR-0249 and writes the audit event (id, version,
  digest, origin, deciding operator).
- Generate a maintainer Ed25519 key, deposit the public half in
  `<corvin_home>/global/plugin_trust_anchors.txt`, and document custody. Without this,
  `origin=vetted` is a value nothing can ever legitimately carry.

**Explicitly not built:** revocation. ADR-0249 names it as an open gap; inventing a
revocation channel inside an install command is how it would get the wrong shape.

**Tests:** a URL argument is refused; a `community` plugin without confirmation does
not load; a `vetted` claim with an unpinned key is **refused, not downgraded**; the
confirmation audit event carries no PII beyond the operator identifier the chain
already records.

---

### Stage 7 — Flag lifecycle (0.5 d)

**Closes:** G9. CLAUDE.md: *"every flag gets an owner and a target release in which it
either flips to default-on or the feature is removed. Flags are not permanent
architecture."* Eight flags currently have neither.

Add `owner` and `target_release` to each registry entry, and a test that a new flag
cannot be registered without them. Proposed dispositions:

| Flag | Proposed |
|---|---|
| `plugin_health_monitoring` | flip default-on at v0.12 |
| `plugin_runtime_lifecycle` | flip default-on at v0.12 |
| `plugin_console_surface` | flip default-on at v0.12 |
| `plugin_extension_points` | stays off until Stage 3 has one release of production use |
| `admin_control_plane` | flip default-on at v0.12 |
| `bridge_supervisor_plugins` | stays off — ADR-0238's non-restart posture wants soak time |
| `headless_api_mode` | permanent operator choice, not a rollout flag → document as such |
| `plugin_trust_enforcement` | flip default-on at v0.12 — enforcement off is the weaker default |

`headless_api_mode` is the one honest exception: it is a deployment shape, not a
staged rollout. Recording that is better than pretending it has a flip date.

---

## 4. Summary

| Stage | Est. | Closes | Flag | Blocking |
|---|---|---|---|---|
| 0 Call-site gate | 0.5 d | — | none | precedes everything |
| 1 Tenant-scoped providers | 1.5 w | G6 | none (refusal is a compliance gate) | **must precede Stage 2** |
| 2 `user_backend` call site | 1 w | G2 | `plugin_runtime_lifecycle` | after Stage 1 |
| 3 Extension-point call sites | 1.5 w | G1 | `plugin_extension_points` | — |
| 4 Populate three handles | 1 w | G3, part of G4 | none | — |
| 5 Bridge supervisors | 1 w | G5 | `bridge_supervisor_plugins` | — |
| 6 `install` + trust anchor | 1 w | G7, G8 | `plugin_trust_enforcement` | — |
| 7 Flag lifecycle | 0.5 d | G9 | — | last |
| **Total** | **~7 w** | | | |

**Dropped from ADR-0242:** Phase 7 directory move (§ 2 above).
**Deferred, unchanged:** everything in § 1's out-of-scope table.

---

## 5. Risks

| Risk | Mitigation |
|---|---|
| A stage ships green unit tests and stays unreached | Stage 0's gate is a merge condition, not a nice-to-have |
| Stage 2 admits a guest through a path the test did not model | The invariant is enforced at the call site *and* in the provider; both are tested independently |
| Stage 1's refusal breaks a working single-tenant install | The refusal keys on tenant count, and single-tenant is the default install; both states tested |
| Stage 3's `workflow_gate` fails open under an unexpected return type | ADR-0251 fixes the non-`bool` return semantics before the call site is written |
| Stage 5 destabilizes messaging | Supervisors wrap the existing `bridge_manager.py`; `run-all-tests.sh` before commit |
| Trust anchor key is lost | Custody documented in Stage 6; loss means no new `vetted` plugin, not a broken install |
| Fixes introduce their own defects | Each stage gets a refutation round against its own commits — ADR-0233 found two fix-induced defects that way |

---

## 6. Docs and diagrams

Per CLAUDE.md, docs and diagrams update in the same commit as the code. Targets:

| Stage | Docs | Diagrams |
|---|---|---|
| 1 | `docs/claude-ref/layer-plugins.md` § Known limit; CLAUDE.md plugin block | plugin boot/scoping SVG |
| 2 | `docs/plugin-system.md`, `docs/claude-ref/layer-16-security.md` | auth flow SVG |
| 3 | `docs/plugin-architecture.md`, delegation-routing.md | delegation flow SVG |
| 4 | `docs/extending.md`, generated `corvin plugin types` output | — |
| 5 | `docs/claude-ref/layer-plugins.md`, bridge docs | bridge supervision SVG |
| 6 | `docs/extending.md`, `Corvin-Marketplace/plugins/README.md` | — |
| 7 | CLAUDE.md § Feature Flags | — |
