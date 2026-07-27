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

### Stage 2 — `user_backend` reaches the auth path — **BLOCKED, premise was wrong**

**Status 2026-07-27: not implementable as written, and building it as written would
be harmful.** The stage assumed CorvinOS has an authentication path that takes
credentials. It does not:

| Candidate | Reality |
|---|---|
| `GET /v1/console/auth/local-login` | The only live login. **Localhost-only and credential-less** — the TCP peer *is* the authorisation (`_is_localhost` deliberately ignores `X-Forwarded-For`). There are no credentials to hand a backend. |
| `POST /v1/console/auth/login` (`gateway/console_api.py`) | Hard-coded `test@example.com` / `password123`, unsalted SHA-256, in-process `_SESSIONS` dict — and **imported by nothing**. Dead demo code, not a path. |
| OIDC / OAuth | `auth_routes.py:16` and `gateway/auth.py:7`: *"wired in the cloud deployment phase"*. Not built. |

**Wiring it anyway would be worse than the dead state.** A `user_backend` consulted
from `local-login` would be handed empty credentials; a correctly-written backend
rejects those; the rejection means deny; and deny on the only login path locks the
local operator out of their own install. The dead mechanism admits nobody wrongly —
that version admits nobody at all.

Note also what the invariant presupposes. *"failure/timeout/rejection = deny, never
guest"* needs a path on which a guest could be admitted. On a localhost-only login
there is no guest, so on today's surfaces the invariant is not merely unenforced —
it has no subject.

**What is actually true**, and what ADR-0245's `dead_reason` should say: `user_backend`
is unconsumed because **its consumer does not exist**, not because someone forgot to
call it. That is a different finding from the other five dead types and wants a
different fix.

**Decided 2026-07-27 (maintainer): option 3.** `user_backend` stays dead and the tree
says so precisely. The correction is not a doc-only change — `surface_map.py:137` still
carries the *wrong* cause ("no caller exists outside `corvin_plugins`"), which reads as
"someone forgot to call it" and invites exactly the harmful wiring this stage rejects.
Options 1 and 2 remain open as separate projects, neither is in this plan.

**Options, none of them this stage** — a maintainer decision:

1. Build the credential login (OIDC or local password) that `user_backend` was
   designed for, and wire the backend into it. This is the honest consumer and it is
   a feature project, not an activation step.
2. Re-target the provider at `roles.effective_role()` (L18-21), which *is* a live
   identity-resolution point with a real deny (`"none"`). This changes what
   `UserBackend` means — from "authenticate a credential" to "resolve a principal" —
   and needs an ADR, because `authenticate()`/`get_user()` would no longer describe
   what the registry does.
3. Leave it dead and say so precisely, correcting the `dead_reason` from "nothing
   calls `get_active()`" to "no credential auth path exists to call it".

Option 3 is free and should happen regardless of which of 1 or 2 is chosen.

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

#### Progress 2026-07-27 — bus complete, 1 of 4 call sites

**Bus (prerequisite for all four).** D3 and D5 were specified but not built:
`invoke()` returned whatever the hook returned and measured nothing.

* **D3** — `ExtensionPointSpec` now declares `return_type`. `None` abstains
  everywhere (including the gate); a wrong TYPE is a defect handled exactly like
  a raising hook — default on an ordinary point, `ExtensionPointDenied` on the
  gate. Only the type NAME is audited, never the value: a misbehaving hook's
  return could be a credential, and the chain is append-only.
* **D5** — `latency_budget_ms` per point, measured around the hook, audited on
  overrun with the plugin id and elapsed time, deduplicated per
  (tenant, point, plugin). Not enforced, and the test says so in its name:
  asserting a slow hook is *cut off* would assert a guarantee the code does not
  provide.

**One behaviour change to an existing tested contract.** Until now the bus passed
a hook's `None` through verbatim, on the reasoning that a hook must be able to
express a deliberate `None`. D3 decided the opposite — and the point specs in
`extension_points.py` had always agreed with the ADR rather than with the test:
`engine.model_selection`'s `default_behavior` says a `None` return is "treated
exactly like no hook at all", and the old test cited that spec while asserting
its opposite. `None` is not a meaningful answer on any of the four points, so
pass-through bought nothing and cost every call site its own `is None` branch.

**Call site 1 of 4 — `engine.engine_selection`.**
`delegation_policy.resolve_worker_engine`, wired into `chat_runtime`. The pure
`worker_engine_target` stays pure — it is the shared routing matrix every surface
unit-tests against, and a hook inside it would make those tests depend on
process-wide bus state. D2's refusal lives at the call site: the bus knows a hook
returned a `str`, only `delegation_policy` knows which strings are engines.

`permitted_engines` admits exactly `{bundled, "native"}` — confirm or
de-escalate, **never escalate**. Deliberately narrower than "the operator's
`mode`": allowing `mode` would let a hook re-assert `tde` on a turn the rule
routed to `native` because TDE was unavailable, overriding an availability
degrade the hook cannot observe.

**Two guard tests fired on the good news, as designed.**
`test_unwired_point_is_still_unwired[engine.engine_selection]` went red and forced
the record to move to a new `_WIRED_POINTS` set, which now carries the reverse
assertion — a wired point that *loses* its caller fails too, since that
regression looks identical to a point that was never wired.
`test_extension_points.py::test_no_call_site_is_wired_yet` also went red; it had
named its own successor in a comment ("when the call sites land, THIS test goes
away"), so it was removed and a tombstone comment left in its place. Six
documents claiming "no call site calls `invoke()`" were corrected in the same
commit.

Verified by mutation: deleting the D2 refusal turns four call-site tests red.

#### Call sites 2–4, same day — Stage 3 complete

| Point | Call site | The bound, enforced at the call site |
|---|---|---|
| `delegation.route_selection_policy` | `delegation_policy.resolve_delegation_route` | may **suppress** delegation, never cause it |
| `engine.model_selection` | `model_selector.resolve_step_model` | may name any model in the engine's registry, nothing else |
| `workflow.workflow_gate` | `routes/workflows.py::_stream_run` | may **deny** a run, never permit one the core refused |

Each bound is different because each subsystem's vocabulary is different, and
that is the argument for keeping the check at the call site rather than in the
bus. The bus knows a hook returned a `str`; only `delegation_policy` knows which
strings are routes, only `model_selector` knows which are installed models.

**`route_selection_policy` needed a vocabulary bridge.** The bundled classifier
answers a boolean — "is this ACS-fan-out shaped?" — while the point's declared
type is a route string. `resolve_delegation_route` maps `True → "acs"`,
`False → "native"`, and admits only the bundled route or `native`. So a hook can
stop a delegation and can never start one. That direction is not symmetry for its
own sake: a hook answering `"acs"` on a declined turn would be a plugin spending
the operator's quota through a decision the operator's own classifier refused.

**`model_selection` has no `native`-shaped floor.** There is no ordering among
models, so "may not escalate" has no meaning; the operator's setting *is* the
engine registry, and membership is the whole bound. It is checked with
`engine_models.model_is_registered` — lifted out of
`resolve_model_for_workload`'s inner scope so the call site and the bundled tier
ladder apply one rule rather than two that agree until one is edited.

**The gate sits before the `dry_run` branch.** A gate answers "may this run at
all"; letting a denied workflow still be enumerated would make the answer depend
on a query parameter. It is handed a *structural* summary — wid, node ids, node
count, engine — and never `inputs` or the YAML: those carry the task text, and
passing them would make a gate hook a content tap, which is a different
capability from gating.

**Two defects found while writing the tests, both in code that was already
green:**

* `resolve_step_model` let an exception from the admissibility check propagate.
  The test asserting it was named `..._refuses_rather_than_admits` while pinning
  `assertRaises`, which made the mismatch obvious. Propagation is wrong twice: a
  broken check decides nothing, and the console call site turns an escaping
  exception into `model = None`, silently **downgrading** the turn to the CLI
  default. Now fail-closed locally rather than relying on every caller to wrap it.
* The console's `engine.model_selection` call site referenced `_os_engine`, which
  was assigned ~130 lines *later*. The surrounding `except Exception` would have
  turned the `NameError` into `_os_model = None` on every turn — a silent
  degradation with no log line. `_os_engine`'s resolution moved above the model
  block, which also removes a second `shutil.which` probe per turn.

Verified by mutation: ignoring the gate's deny, making the gate fail-open,
dropping the registry bound, and dropping the suppress-only rule each turn their
suite red.

**Stage 3 gate:** `_UNWIRED_POINTS` is now empty and `_WIRED_POINTS` holds all
four. The useful assertion from here is the reverse one — a point that *loses*
its call site fails the suite.

---

### Stage 4 — Populate the three handles — **one of three, not three**

**Closes:** G3 in part. **Flag:** none — passing a registry that was already being
forwarded changes nothing until a plugin of that type is installed.

The stage was sized as "pass three objects into `bootstrap_all()`". Verified
2026-07-27: only one of the three targets is an object that can be passed. ADR-0245
says all three types "have handles", and that is true of the `PluginContext` *field*;
it is not true of the thing the field would point at.

| Handle | Target | State |
|---|---|---|
| `compute_registry` | `corvin_compute.engine_registry.get_registry()` | **Exists**, has `register(engine)`, and the shipped template calls exactly that. Passing it is the one-line change the stage assumed. |
| `engine_factory` | `operator/bridges/shared/engine_registry.py` | Engines come from a hard-coded `_ENGINE_BUILDERS` dict with three entries and there is **no `register()`**. A plugin cannot enter itself. Wiring this means designing a registration API for L22 first. |
| `channel_registry` | — | **Does not exist.** The only references in the tree are in `templates/bridge_channel_plugin.py`, which calls `ctx.channel_registry.register(self)` against a class nobody wrote. |

So `bridge_channel` is not a populated-handle problem at all: its template targets an
API that was never built. `worker_engine` needs a new registration surface on a
subsystem that predates ADR-0033. Both are the design question ADR-0245 deferred
under *"Add the three missing provider modules now — deferred, not rejected. Each is
a real design question."* They are not activation work and should not be done under
cover of one.

**What this stage should actually do:**

1. Pass `compute_registry` in `app.py`, and verify the registered engine is reachable
   through the MCP bridge before claiming `consumed_by` — a filled handle proves
   registration, not invocation, which is the distinction this whole plan exists for.
2. Correct the two templates so they state that their target does not exist, instead
   of guarding with `if ctx.<handle> is not None:` and skipping in silence. That guard
   is what turns a missing API into no signal at all.
3. Leave `worker_engine` and `bridge_channel` in `unconsumed_types()` with a
   `dead_reason` naming the real cause.

`stt_provider` and `data_connector` (G4) remain a third case: handle populated,
registry live, nothing calls `get_active()`.

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

### Stage 7 — Flag lifecycle — **DONE, verified 2026-07-27**

**Closes:** G9. CLAUDE.md: *"every flag gets an owner and a target release in which it
either flips to default-on or the feature is removed. Flags are not permanent
architecture."*

Shipped ahead of its place in the sequence: all **15** entries in
`core/console/corvin_console/feature_flags.py` carry `owner` and `target_release`
(both mandatory fields on `FeatureFlag`), and
`core/console/tests/test_feature_flags.py::test_every_flag_has_owner_and_target_release`
is the guard that a new flag cannot be registered without them. The count moved from
the eight this plan was written against to fifteen; the dispositions below are the
proposals of record for the plugin-related subset, not a description of what shipped.

Original proposed dispositions:

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

## 4. Summary — re-verified against the tree 2026-07-27

| Stage | Est. | Closes | Flag | State |
|---|---|---|---|---|
| 0 Call-site gate | 0.5 d | — | none | ✅ done (`test_extension_point_call_sites.py`, 6 pass / 4 skip — the skips *are* Stage 3) |
| 1 Tenant-scoped providers | 1.5 w | G6 | none (refusal is a compliance gate) | ✅ **part 1 only** — the refusal gate (`tenant_scope.py`, 30 tests). Keying the eight registries by tenant is ADR-0250's own migration, not done |
| 2 `user_backend` call site | 0.5 d | G2 (as *recorded*, not closed) | — | ✅ **done** — option 3 shipped; cause corrected in `surface_map.py`, CLAUDE.md, 4 docs, ADR-0245 addendum |
| 3 Extension-point call sites | 1.5 w | G1 | `plugin_extension_points` | ✅ **done** — bus D3+D5 plus all four call sites |
| 4 Populate `compute_registry` | 1 w | part of G3 | none | ❌ `app.py:163` still passes no registry |
| 5 Bridge supervisors | 1 w | G5 | `bridge_supervisor_plugins` | ❌ `registry_entries` imported only by its own `__init__` |
| 6 `install` + trust anchor | 1 w | G7, G8 | `plugin_trust_enforcement` | ❌ CLI has `types`/`check`/`new` only; no anchor deposited |
| 7 Flag lifecycle | 0.5 d | G9 | — | ✅ **done** — 15 flags, all with `owner`+`target_release`, guard test present |
| A Truth-in-tree corrections | 0.5 d | — | — | ✅ **done** — see § 4.1 |
| E E2E spine | 1 w | — | — | ◑ **E1 done** (`test_lifecycle_e2e.py`); E2 rows follow their stages, E3 last |
| **Total remaining** | **~3.5 w** | | | |

**Dropped from ADR-0242:** Phase 7 directory move (§ 2 above).
**Deferred, unchanged:** everything in § 1's out-of-scope table.

### 4.1 Track A — the tree must state what the audit found (0.5 d)

Three findings from 27 July live **only in this document**. `surface_map.py` is what
`corvin plugin types` prints and what a plugin author reads, and it still states causes
the audit disproved. A wrong `dead_reason` is worse than none: it names a fix that would
be harmful (Stage 2) or that does not exist (Stage 4).

| Correction | Today | Must say |
|---|---|---|
| `user_backend` (`surface_map.py:137`) | "no caller exists outside `corvin_plugins`" | "no credential auth path exists to call it — the only live login is localhost-only and credential-less" |
| `worker_engine` (`:196`) | "Same as compute_engine: `engine_factory` is never passed" | "`engine_registry.py` has no `register()`; a plugin cannot enter itself. Needs an L22 registration API first (ADR-0245 deferral)" |
| `bridge_channel` (`:210`) | "Same as compute_engine: `channel_registry` is never passed" | "`channel_registry` does not exist; the template targets a class nobody wrote" |
| `templates/bridge_channel_plugin.py`, `worker_engine_plugin.py` | `if ctx.<handle> is not None:` → silent skip | state that the target does not exist; a scaffold must not manufacture silence |

**Test:** extend `test_surface_map.py` so a `dead_reason` that merely defers to another
type ("Same as …") fails — that phrasing is how the three wrong causes propagated.

**Why first:** it is half a day, it is the only track with no dependency, and every other
track is measured against a map that is currently wrong.

#### Shipped 2026-07-27

Two guards added to `test_surface_map.py`, red on arrival on exactly the two rows
that deferred:

* `test_dead_reason_stands_on_its_own` — refuses `"same as"`, `"see above"`,
  `"ditto"`, … A deferring cause is the one shape that goes stale invisibly: fix
  the row it points at and this row still reads as explained.
* `test_dead_reason_names_something_in_the_tree` — the cause must cite a file, a
  call or a config key. Deliberately weak; an unfalsifiable cause is how the
  wrong ones survived review.

**Known limit, stated rather than hidden:** the guards caught `worker_engine` and
`bridge_channel`. They do **not** catch `user_backend`, whose old cause was
self-contained, cited a real file, and was simply wrong. No cheap test
distinguishes "wrong but well-formed" from "right" — that one needed the audit.
Track E is the structural answer, not a third string check.

Also corrected: both templates' `else` branches now say the target does not
exist instead of logging a `None` handle, and the ADR-0245 addendum records the
four real causes. Verified through `corvin plugin types`, which is the surface an
author actually reads. 982 tests green.

### 4.2 Track E — the E2E spine (1 w, runs alongside Stages 3–6)

**Decided 2026-07-27 (maintainer): real lifecycle E2E *plus* un-mocked Playwright runs.**

The only plugin E2E today is `web-next/tests/e2e/plugins.spec.ts`, and it intercepts its
own routes. It proves the panel renders — it cannot prove a plugin ever ran. That is the
same "unit tests prove a mechanism, not its reach" defect this whole plan exists for,
one level up.

**E1 — lifecycle E2E (pytest).** One scenario, end to end, no mocks:
`corvin plugin new` → `corvin plugin install <path>` → gateway boots → the plugin's hook
fires **on a real turn** → the audit event lands in the chain and `voice-audit verify`
still passes. Runs against a tmp `CORVIN_HOME` (per `tests/conftest.py`'s
`VOICE_AUDIT_PATH` isolation — a plugin E2E writing into the live GDPR chain is not
acceptable).

**E2 — per-stage E2E rows.** Each activated stage adds one scenario to E1's harness:
Stage 3 a hook that changes a model selection and one that raises; Stage 4 a compute
engine reachable through the MCP bridge (registration is *not* invocation); Stage 5 a
supervisor whose daemon is dead; Stage 6 a `community` plugin without confirmation.

**E3 — un-mock the Playwright specs.** Run `plugins.spec.ts` against a real gateway
using the isolated-console harness rather than route interception. Budget it: the
isolated E2E console has a history of hanging in boot, and this is the expensive half
of Track E. If E3 cannot be made reliable it is dropped and said so here — it is not
allowed to become a green suite that boots nothing.

**Gate:** E1 is a merge condition for Stages 3–6 in the same way Stage 0's gate is.
A stage without its E2E row is not done.

#### E1 shipped 2026-07-27 — and what it could not yet be

`core/plugins/tests/test_lifecycle_e2e.py`. Six steps, no doubles at the seams:
the real `corvin` CLI scaffolds an `audit_backend`; the author's one TODO is
implemented; it is declared in a real `tenant.corvin.yaml`; `bootstrap_all()`
boots it as the gateway does; a real audited action goes through
`operator/bridges/shared/audit.py`; the core chain holds the record, the plugin
holds a copy, and `verify_audit()` still passes. A second scenario injects a
raising `fanout()` and proves the core record survives it — ADR-0233's
additive-only invariant measured on the real writer instead of asserted about a
double.

**The plan's E1 was not buildable as written, and the reason is instructive.**
It specified `corvin plugin install` and "the hook fires on a real turn". Neither
exists: `install` is Stage 6 and the extension points are Stage 3. E1 was
sequenced *before* the stages it depends on. So it runs the same spine through
the install path that does exist (the declarative `spec.plugins.installed` entry
the generated README already tells authors to write) against `audit_backend` —
one of the five surfaces with a live consumer, and the compliance-critical one.
`_STAGE_ROWS_OWED` in the module records the four rows still owed, and a test
asserts it, so closing a stage without extending the harness turns the suite red.

**Two defects the refutation round found in the harness itself**, both worth
recording because both were green first:

* The raising-backend scenario was **vacuous**. Removing the injected raise
  entirely left it passing — "a well-behaved plugin does not suppress the core
  record" is trivially true. It now asserts `provider.failure_count() > 0`, which
  is only non-zero if the backend was actually called and actually raised.
* The wait predicate was weaker than the assertion: it waited for the sink file
  to *exist*, then asserted on its contents. Boot writes `plugin.loaded` through
  the same fan-out, so the file appeared instantly and the loop exited before the
  event under test drained. It passed on timing luck and failed the moment
  another test ran first.

Verified by mutation, not by reading: four mutations (TODO unimplemented, raise
removed, `class_path` broken, `audit_event` not called) each turn the suite red.
A green E2E for a chain nobody had connected deserves that check.

#### The finding that outranks the harness: nothing ran the plugin suite

Checking where to register E1 turned up the fourth occurrence of this plan's
defect class, at the level of the gates themselves. **`core/plugins/tests/` was
run by no automation at all** — absent from `operator/bridges/run-all-tests.sh`
(the CLAUDE.md pre-commit gate) and absent from every `.github/workflows/` file.
`coverage.yml` lists `tests/`, `operator/bridges/shared/`, `core/console/tests/`,
`core/compute/tests/` and `operator/mcp_manager/tests/`, and stops there.

That is 950+ tests that ran only when somebody typed the path by hand. Among
them:

* Stage 0's call-site gate — this plan calls it "the regulator for every stage
  below" and "a merge condition, not a nice-to-have";
* `test_layered_boot.py`'s guards, which CLAUDE.md says "fail on the first real
  instance and force the docs to be updated in the same commit";
* the ADR-0233 tripwire and additive-only tests.

Every one of those is a mechanism whose value is that it fires unbidden. None
could. The plan was about to add a fifth gate to a set that nothing executed.

**Fixed:** `core/plugins/tests/` added to `coverage.yml`. Registering the gate is
part of building it — a merge condition that no merge runs is a comment.

### 4.3 Execution order

```
A  (0.5d, no deps)  ─┐
2  (0.5d, no deps)  ─┼─→  E1 harness (needs A's map to be true)
E1 (3d)             ─┘        │
                              ├─→ 3 (+E2 row) ─→ 4 (+E2 row)
                              ├─→ 5 (+E2 row)
                              └─→ 6 (+E2 row) ─→ E3 (last, droppable)
```

Stage 4 follows Stage 3 rather than running parallel: its remaining substance is
"prove the registered compute engine is *reached*", which is the same assertion shape
Stage 3 builds first.

**Not closed by this plan, by decision:** ADR-0250's registry-keying migration,
Stage 2 options 1/2, and everything in § 1's out-of-scope table. Completion means every
mechanism has a reachable call site **or** an honest recorded cause — not that every gap
is gone.

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
| A | `surface_map.py` docstrings (they *are* the doc — `corvin plugin types` prints them), `docs/PLUGIN_EXTENSION_POINTS.md` | — |
| E | `docs/claude-ref/testing-and-docs.md` § how to run the plugin E2E | — |
| 1 | `docs/claude-ref/layer-plugins.md` § Known limit; CLAUDE.md plugin block | plugin boot/scoping SVG |
| 2 | `docs/plugin-system.md`, `docs/claude-ref/layer-16-security.md`; CLAUDE.md's `user_backend` invariant needs the qualifier that it has no subject on today's surfaces | auth flow SVG |
| 3 | `docs/plugin-architecture.md`, delegation-routing.md | delegation flow SVG |
| 4 | `docs/extending.md`, generated `corvin plugin types` output | — |
| 5 | `docs/claude-ref/layer-plugins.md`, bridge docs | bridge supervision SVG |
| 6 | `docs/extending.md`, `Corvin-Marketplace/plugins/README.md` | — |
| 7 | CLAUDE.md § Feature Flags | — |

---

## 7. What only the maintainer can do

Two items block completion and cannot be finished by a coding session:

1. **Trust-anchor key custody (Stage 6).** Generating the maintainer Ed25519 key and
   deciding where the private half lives is an operator act. Until it exists,
   `origin=vetted` is a value nothing can legitimately carry, and Stage 6 ships with
   `vetted` claims **refused, not downgraded** — which is the correct fail-closed
   behaviour, but it means the origin axis stays two-valued in practice.
2. **ADR status transitions.** ADR-0250 and ADR-0251 are both `Proposed`. Stage 3 is
   the implementation of 0251; it should not merge while the decision it implements is
   still a proposal.
