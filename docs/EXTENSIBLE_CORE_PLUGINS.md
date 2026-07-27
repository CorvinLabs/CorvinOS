# Extensible Core Plugins

**Date:** 2026-07-27
**Status:** Phase 3 of ADR-0242 — the extension-point bus exists, is tested, and
is **not yet wired into any call site**. Read the "Reach today" boxes before
planning against it.

**ADRs:** ADR-0237 (extensible core plugins) · ADR-0243 (the `boot_layer` axis) ·
ADR-0233 / ADR-0033 (the provider registries) · ADR-0181 (provider + model
selection).
**Code:** `core/plugins/corvin_plugins/` — `extension_points.py`, `registry.py`,
`manifest.py`, `providers/`.

The same qualifier applies to the *other* mechanism in this document:
**`registry.replace()` is structurally unreachable.** It accepts only a target on the
`core` boot layer, and no plugin anywhere is on it — `_GLOBAL_SPECS` is empty and
`register_global_plugin()` has no production caller, so `bootstrap_global()` returns `[]`
on every install. §2a therefore describes a tested mechanism with no legal input, pinned
by `core/plugins/tests/test_layered_boot.py::TestTheTopOfTheAxisHasNoProductionInstance`.

What *is* live in this document: **the eight provider registries of §3.** They predate the
bus, are not reached through it, and carry real traffic.

---

## 1. The principle

A `boot_layer=core` plugin is a **reference implementation**, not a locked component.
CorvinOS ships a default, and an operator may either override one named step of
it or replace it wholesale — without forking.

What is *not* negotiable is the compliance boot layer. Section 5 lists the mechanisms
that have no extension path at all, by construction rather than by policy.

Three axes, deliberately distinct, and mixing them is the most common mistake in
this area:

| Axis | Question it answers | Values | Defined in |
|---|---|---|---|
| `boot_layer` | When is it loaded, may it be switched off, may it be replaced? | `compliance` · `core` · `bundled` · `installed` | `manifest.py::BootLayer` (ADR-0243) |
| `tier` | Capability boundary + license gate | A / B / C | ADR-0156 |
| `origin` | Provenance | `builtin` · `vetted` · `community` | `manifest.py::PluginOrigin` (ADR-0233) |

"Tier A/B/C" means ADR-0156 repo-wide. It is **not** a synonym for `boot_layer`; the
older drafts of this document used `tier_0` / `tier_1_core` / `tier_2_bundled`
for the load-order axis, which collided with both other meanings. That
vocabulary is retired — see §8.

The axis also spent a short while named plain `layer`, which collided four ways: the
L1–L44 stack, ADR-0124 audit layers (`routes/audit_layers.py`), the ADR-0142
layer-extension API (403 `core_layer_immutable`, `routes/extensions.py`) and quality
layers (`routes/quality_layers.py`). Hence `boot_layer` / `BootLayer`, and the API names
`boot_layer_of()`, `plugins_by_boot_layer()`, `_declared_boot_layer()`,
`register_global_plugin(..., boot_layer=)`, the audit event `plugin.boot_layer_rejected`
and the admin aggregate `by_boot_layer`.

---

## 2. The two customisation mechanisms

### 2a. Full replacement — `replaces` + `registry.replace()`

**Reach today: none.** No plugin is on the `core` boot layer, so there is no legal
`replaces` target anywhere: the example below would raise `PluginReplacementRefused`
before touching anything, because `acs-manager` is not a registered `core` plugin (it is
not a plugin at all). The rules are implemented and tested; they have never been exercised
against a real target. The example shows the shape the mechanism expects.

A plugin declares the reference implementation it takes over from, and the
registry performs the swap.

```python
from corvin_plugins import PluginRecord, BootLayer, PluginOrigin, replace

record = PluginRecord(
    plugin_id="k8s-acs",
    version="1.0.0",
    display_name="Kubernetes ACS",
    plugin_type="compute_engine",      # from KNOWN_PLUGIN_TYPES
    boot_layer=BootLayer.CORE,
    origin=PluginOrigin.VETTED,        # community may not claim a privileged boot layer
    replaces="acs-manager",
)

replace(my_plugin, ctx, replaces="acs-manager")
```

Rules the registry enforces (`registry.py::replace`, `manifest.py::__post_init__`):

| Rule | Consequence of breaking it |
|---|---|
| Only a `boot_layer=core` target is replaceable | `PluginReplacementRefused` |
| A `boot_layer=compliance` plugin may not declare `replaces` | `PluginError` at record construction |
| A `boot_layer=compliance` plugin may not itself be replaced | `PluginReplacementRefused` |
| `origin=community` may not claim `boot_layer` `compliance` or `core` | `PluginError` at record construction |
| A tenant config may declare only `bundled` / `installed` | downgraded to `installed` + `plugin.boot_layer_rejected` audit |
| The replacing id must not already be registered | `PluginAlreadyRegistered` |

The swap is **not atomic**, and that is deliberate. The old plugin's
`on_unload()` runs first; if the new plugin's `on_load()` then fails, the slot is
left EMPTY rather than "restored", because the old object's teardown has already
completed and handing it back to callers would be a lie. The registry audits the
gap (`plugin.replaced`, `plugin.unloaded`); the operator re-enables the default
explicitly.

### 2b. Hook-based customisation — the extension-point bus

`core/plugins/corvin_plugins/extension_points.py`. A plugin overrides ONE named
step; everything else in the bundled default keeps running.

```python
from corvin_plugins import register_hook, unregister_all

class MyRoutingPlugin:
    plugin_id = "cost-aware-routing"
    ...
    def on_load(self, ctx):
        register_hook(
            "delegation.route_selection_policy",
            self.pick_route,
            plugin_id=self.plugin_id,
            tenant_id=ctx.tenant_id,
        )

    def on_unload(self):
        unregister_all(self.plugin_id)      # across every tenant

    def pick_route(self, turn: dict) -> str | None:
        return "acs" if turn.get("bytes", 0) > 10_000_000 else None
```

The call site — once wired — asks the bus and always states its own pre-feature
behaviour:

```python
from corvin_plugins import invoke

route = invoke(
    "delegation.route_selection_policy",
    turn,
    default=classify_natively,      # callable -> executed with the same args
    tenant_id=tenant_id,
)
```

**Divergence from ADR-0237 worth knowing:** the ADR describes hooks as "the
default implementation still loads; the hook is called before/after". The
implemented semantics are **override**, not before/after chaining: one hook per
`(tenant, point)`, and it decides that step. Chaining would need an ordering
rule and a merge rule for conflicting answers, neither of which the ADR
specifies, and both of which are far easier to get wrong than a single
attributable owner. Cross-cutting "observe every call" behaviour belongs in the
notification/audit backends, not here.

---

## 3. The eight provider registries (already shipping)

These are extension points too. They predate the bus, they are **not** reached
through it, and `register_hook("audit_backend", ...)` is refused with a message
pointing at the registry — routing an audit sink through the bus would bypass
the ordering guarantee that makes it safe.

Registration is always the same shape, from `on_load`:
`ctx.<handle>.set_active(self)`. `PluginContext` carries every handle
(`bootstrap.py::build_context`), so no plugin type is left without somewhere to
register.

| Registry | Protocol | `ctx` handle | With no plugin installed | Non-negotiable guarantee |
|---|---|---|---|---|
| `router_backend` | `RouterBackend.route()` | `router_registry` | `ChainRouterBackend` — the existing fake → heuristic → embeddings → SDK → CLI chain | `route()` must not raise; `None` means no match |
| `recall_backend` | `RecallBackend.index_turn/recall/forget()` | `recall_registry` | `SqliteRecallBackend` → `conversation_recall.py` | text handed to `index_turn` is **already PII-redacted by the caller**; a backend must not undo that. `forget()` is the GDPR Art. 17 path |
| `summary_provider` | `SummaryProvider.summarize()` | `summary_registry` | `ClaudeCliSummaryProvider` → `operator/voice/scripts/summarize.py` | must not raise (truncate instead); must not import the Anthropic SDK directly |
| `audit_backend` | `AuditBackend.fanout/verify_chain/enforce_retention()` | `audit_registry` | **none** — `get_active()` returns `None` | **additive-only.** Core writes its hash-chained record first and unconditionally; the backend receives a COPY afterwards and can never suppress, rewrite, reorder or delay it. `fanout()` is a hand-off to a bounded queue, never a call on the caller's thread — a slow sink drops the oldest *monitoring* copy rather than blocking an audited action. `verify_chain()` reports on the backend's own copy and is never consulted about the core chain |
| `user_backend` | `UserBackend.authenticate/get_user/list_users/enforce_quota()` | `user_registry` | **none** — core auth is responsible | **deny is the only failure.** Exception, timeout, non-dict return and missing `user_id` all collapse to `None` = deny. Never a guest session, never an anonymous admit, never a cached-credential admit. `enforce_quota` raises `QuotaUndeterminedError` on timeout — a directory outage must not become unlimited quota |
| `stt_provider` | L23 STT chain | `stt_registry` | **none** — the built-in L23 chain applies | transcription audit is **metadata only**, never transcript text (GDPR Art. 5) |
| `data_connector` | L24 data sources | `data_connector_registry` | **none** — the built-in DSI adapters apply | connector audit is **metadata only**, never row contents or query payloads |
| `notification_backend` | `NotificationBackend.notify()` | `notification_registry` | `LogNotificationBackend` (logger only) | non-blocking, < 100 ms; **metadata only**, never message content or PII |

Note the deliberate asymmetry in the "with no plugin installed" column. Four
registries carry a working default; four return `None`. `None` is the honest
third state for `audit_backend` and `user_backend` in particular — a default that
denied everything would lock out every install, and a default that admitted
anything would be an auth bypass.

A backend also runs behind a per-plugin circuit breaker
(`circuit_breaker.py`): repeated failures contain the plugin instead of retrying
into an outage, and the breaker state is visible in `health_check_all()`.

---

## 4. The four bus extension points (Phase 3)

**Reach today: none.** These are defined, specified and tested **bus-side**. No
call site calls `invoke()` yet — wiring them into the engine, delegation and
workflow paths is a follow-up phase. Until then a registered hook is inert no
matter how the flag is set, and `test_extension_points.py` carries a guard that
fails the moment a call site appears, so this paragraph cannot silently go stale.

ADR-0237 lists a longer backlog and says explicitly that Phase 3 adds 3–5 points,
not all of them. These four were chosen because each of their subsystems already
routes through a single shared decision function, so wiring them later is a call-
site change and not a redesign.

| Point | Signature | Without a hook | Fail-closed |
|---|---|---|---|
| `engine.model_selection` | `(request: dict) -> str \| None` | the bundled budget-aware selector decides; a hook returning `None` means "no opinion" | no |
| `engine.engine_selection` | `(request: dict) -> str \| None` | `spec.web_chat.worker_engine` decides, via the shared `delegation_policy` module | no |
| `delegation.route_selection_policy` | `(turn: dict) -> str \| None` | the bundled classifier decides; every degrade ladder still ends at `native` | no |
| `workflow.workflow_gate` | `(workflow: dict) -> bool` | the core's own gate decides; the bundled path is unchanged | **yes** |

Names are namespaced (`workflow.workflow_gate`, not `workflow_gate`) because the
bus has one flat key space across every subsystem, and ADR-0237's bare names
would collide the first time two components both wanted a "gate".

A hook may **not** widen what the operator selected. `engine.engine_selection`
cannot route a turn into an engine the operator did not enable, and
`delegation.route_selection_policy` cannot defeat an explicit `/delegate` — those
rules live at the call site (CLAUDE.md § Worker Engine Selection), not in the
plugin's answer.

### Behavioural contract

| Situation | Result |
|---|---|
| Flag off, or Console package absent | `default` is produced. No hook lookup, **no log line, no audit event** — this path runs on every turn of every default install |
| No hook registered | `default` |
| Hook returns | its value, verbatim (including `None`) |
| Hook raises, normal point | `default`; logged + audited with the exception **class** |
| Hook raises, fail-closed point | `ExtensionPointDenied`; logged + audited with `outcome: "deny"` |
| Unknown point name | `UnknownExtensionPoint` — at `register_hook` **and** at `invoke` |
| Name in `_NEVER_EXTENSIBLE` | `ImmutableExtensionPoint` |
| Provider-registry name | `UnknownExtensionPoint` naming the right `ctx` handle |
| `tenant_id` ≠ the tenant the plugin was loaded for | `CrossTenantHookRefused`, audited as `reason: tenant_mismatch` |

`default` is keyword-only and **required**. Every call site has to spell out its
pre-feature behaviour, because that behaviour is what runs on a default install.
A callable `default` is executed with the same `*args`/`**kwargs` the hook would
have received; anything else is returned as-is.

**What fail-closed means precisely.** It is scoped to a *registered, consulted*
hook. An operator who installs a workflow gate must not get **less** enforcement
the moment their gate breaks — so a raising hook there denies instead of falling
through to the permissive default. With the flag off, or with no hook installed,
the point is not consulted at all and the core's own gate decides exactly as it
did before the feature existed. That is the ship-dark requirement, not a
fail-open hole; the mechanisms that must never be reachable from a plugin in the
first place are in §5 and have no hook path at all.

**No PII, ever.** A failing hook is recorded as its exception **class**
(`ValueError`), never `str(exc)` — a plugin's message routinely carries a path, a
host or a prompt fragment, and the audit chain is append-only, so a leak there is
permanent. The denial raised at the call site uses `raise ... from None` for the
same reason: a printed traceback must not render the plugin's message either.
Author-supplied names in a rejection audit are clipped to
`MAX_AUDITED_NAME_CHARS` (64).

### Conflict rule: last registration wins, and it is audited

A second plugin registering on a point another plugin already owns **replaces**
it, and the bus emits `plugin.extension_hook_replaced` carrying both plugin ids.

*Why last and not first:* load order already runs global and bundled code before
operator-installed plugins, so "last wins" is the direction an override needs —
refusing the second registration would make ADR-0237's Approach 1 impossible for
anything a bundled default had already claimed.

*Why it is audited:* otherwise which plugin owns a point becomes an emergent
property of load order with no record anywhere. Silent takeover of
`workflow.workflow_gate` in particular has to be attributable.

A plugin re-registering its **own** hook (a hot reload re-runs `on_load`) is
idempotent and audited as a normal registration, not as a takeover.

### Audit events

| Event | When |
|---|---|
| `plugin.extension_hook_registered` | a hook is accepted (or re-registered by its owner) — carries `tenant_check: attributed \| unattributed` |
| `plugin.extension_hook_replaced` | a different plugin took over a point — carries `replaced_plugin_id` and `tenant_check` |
| `plugin.extension_hook_rejected` | `reason: unknown_point`, `never_extensible` or `tenant_mismatch` |
| `plugin.extension_hook_failed` | a hook raised — carries `error_type` and `outcome: default \| deny` |

### A hook belongs to the tenant its plugin was loaded for

`tenant_id` is keyword-**required** on `register_hook`, and it is **checked**
against the tenant the registering plugin was actually loaded for. Requiring it
without checking it only removed the *silent* path: a plugin loaded for tenant A
could still pass tenant B's id and, because last-registration-wins, take over
B's `workflow.workflow_gate` — a fail-closed point.

A mismatch raises `CrossTenantHookRefused` and is audited as
`reason: tenant_mismatch`. The rejection is written to the **registering
plugin's own** tenant chain; recording it in the target's would be the same
cross-tenant write the check denies.

**Callers the registry cannot resolve are allowed**, and audited as
`tenant_check: unattributed` rather than `attributed`. `PluginRegistry.register()`
populates the plugin's context *before* calling `on_load()`, so a real plugin
registering a hook during load is resolvable and gets attributed — an unresolvable
caller is bundled reference code, an embedding host, or a test. Refusing those
would break them and buy nothing: the same line that names a foreign tenant can
name an unknown `plugin_id`, and in-process code reaches the bus internals
regardless. This is an attribution guarantee, not a sandbox.

Registration is deliberately **not** gated on the flag: a plugin's `on_load` may
run before an operator flips it, and a hook whose registration had to be ordered
against a Console toggle would be a restart-shaped trap. The flag is checked at
`invoke()` time, so turning it on takes effect immediately and turning it off
leaves registered hooks inert. `describe(tenant_id)` reports what is
*registered*, which with the flag off is not what is *live*.

Hooks are keyed by `(tenant_id, point)`. There is no wildcard tenant: a hook
registered for `_default` does not fire for another tenant, and two tenants may
hold different hooks on the same point without either counting as a takeover.

---

## 5. What is NOT extensible

These have **no hook, by construction**. They are listed in
`extension_points.py::_NEVER_EXTENSIBLE` and refused with
`ImmutableExtensionPoint` — a distinct error from "unknown point", so the attempt
reads as "this may never have one" rather than "you misspelled it".

| Name | Mechanism | Regulation |
|---|---|---|
| `audit.hash_chain`, `audit.write_event` | the hash-chained audit write (L16) | GDPR Art. 30, 32 |
| `a2a.signature_verification`, `a2a.attestation_verify` | Ed25519 verification + instance attestation (L38) | — |
| `tde.token_accounting` | TDE token accounting | — |
| `consent.gate` | per-user consent gate (L16) | GDPR Art. 6, 7 |
| `house_rules.gate` | house-rules gate (L44) | EU AI Act Art. 5, 50 |
| `path_gate.check` | L10 path gate | GDPR Art. 32 |
| `flow_guard.classify` | L34 data-classification flow guard | — |
| `disclosure.bot_card` | bot-disclosure card | EU AI Act Art. 50 |
| `erasure.execute` | L36 erasure orchestrator | GDPR Art. 17 |

The denylist is redundant with the unknown-point check — none of these is in
`KNOWN_EXTENSION_POINTS` — and the redundancy is the point. It gives the refusal a
message that names the mechanism, and it makes any future attempt to *add* one of
these names collide with a named constant instead of quietly slipping into the
known set.

An extension point on one of these is not a feature request. It is a compliance
regression (ADR-0237 § Immutable vs. Extensible; CLAUDE.md § Compliance
Baseline). The same applies to the `boot_layer=compliance` plugins themselves:
`PluginRecord.can_disable()` returns `False` for them, `registry.disable()` raises
`PluginDisableRefused`, and they may be neither replaced nor named as a
replacement target.

---

## 6. Feature flag

| | |
|---|---|
| Flag id | `plugin_extension_points` |
| Default | **`false`** — off on a fresh install and off after an upgrade |
| Config key | `spec.features.plugin_extension_points` in `tenant.corvin.yaml`, or the Console overlay `tenants/<id>/global/features.json` under `flags` |
| Console | Settings → Features, no file editing, no restart |
| Owner / target | maintainer / 0.12.x |
| Registered in | `core/console/corvin_console/feature_flags.py` |

Off is a **quiet** path: `invoke()` returns the default without consulting a
hook, without a log line and without an audit event. Anything else would be a
per-turn cost and a log flood on every default install.

The flag is read through `corvin_console.feature_flags.is_enabled(...)` behind a
`try/except ImportError`. `core/plugins` must stay importable in a layout that
ships without the Console (headless core, ADR-0241), and an absent Console reads
as "off" — the pre-feature path — never as "assume on".

Both flag states are covered by tests (`TestFlagOff` / `TestFlagOn` in
`core/plugins/tests/test_extension_points.py`), toggled through the real
`CORVIN_HOME` + `features.json` resolution path rather than by patching the gate.
A flag only ever tested in one state rots.

Related flags: `plugin_runtime_lifecycle` (registry bootstrap),
`plugin_console_surface` (the Plugins page), `admin_control_plane`
(`/api/admin/*`), `plugin_health_monitoring`, `plugin_self_healing`. The
**global** plugin bootstrap (`bootstrap_global`) is deliberately flagless: the
boot layer it loads is the compliance boot layer, and a switch on that would be the same
violation as an env kill-flag.

---

## 7. Where the code actually lives

```
core/plugins/corvin_plugins/
├─ protocol.py          CorvinPlugin lifecycle, provider protocols, KNOWN_PLUGIN_TYPES
├─ manifest.py          PluginRecord, BootLayer, PluginOrigin, `replaces`, can_disable()
├─ registry.py          register / unregister / disable / replace / boot_layer_of
├─ bootstrap.py         bootstrap_global → bootstrap_declared → bootstrap_tenant
│                       (the global pass returns [] on every install)
├─ extension_points.py  the hook bus (this document, §4)
├─ loader.py            class-path + entry-point loading
├─ state.py             per-tenant registry.yaml
├─ circuit_breaker.py   per-plugin containment
├─ health.py, healing.py
└─ providers/           the eight registries of §3

core/plugins/tests/
└─ test_extension_points.py    54 tests (measured 2026-07-27): both flag states,
                               fail-closed, PII, refusals, conflict rule, tenant
                               isolation, and the no-call-site guard
```

Plugin scope (ADR-0240): **global** plugins ship in the wheel, apply to every
tenant and are the only ones allowed to carry `compliance` / `core`; **tenant**
plugins come from `tenant.corvin.yaml` or the Console and carry `bundled` /
`installed` only. That asymmetry is a trust boundary — if a tenant config could
declare `boot_layer: compliance`, any operator-writable file could mint an
undisableable plugin that loads before everything else.

---

## 8. Corrections to earlier drafts

This document previously described a design that does not exist in the code. The
differences are recorded here rather than deleted, because planning documents
elsewhere still cite them.

| Earlier draft said | Reality |
|---|---|
| `core/core_plugins/tier_0/`, `tier_1_core/`, `tier_2_bundled/`, `base/hooks.py` | none of these paths exist. The code is `core/plugins/corvin_plugins/`, and the load-order axis is `BootLayer`, not a directory tree |
| `plugin_type = "tier-1-core"` / `"tier-1-alternative"` | not in `KNOWN_PLUGIN_TYPES`; constructing such a record raises `UnknownPluginType`. `plugin_type` is the capability (`compute_engine`, `router_backend`, …); the load layer is the separate `boot_layer` field |
| `ctx.registry.registry["voice-summary/1.0.0"].on_unload(); del ...` | `PluginContext` has no `registry` attribute. Replacement is `registry.replace(plugin, ctx, replaces=...)`, which enforces the `boot_layer=core` rule and audits the swap |
| `plugins: tier_2_bundled: enabled: [...]` in `config.yaml` | the real key is `spec.plugins.installed` in `tenant.corvin.yaml`, plus `spec.plugins.auto_discover_entry_points` |
| `corvinctl plugin install X --tier-1-replace Y` | no such CLI. Replacement goes through the registry API; the Console surface is behind `plugin_console_surface` / `admin_control_plane` |
| A `HookManager` per plugin, `priority=` on registration | one process-wide bus keyed by `(tenant, point)`, one hook per point, no priorities. Priorities need a merge rule for conflicting answers that nothing specifies |
| `voice_summary.register_hook("summary_algorithm", …)` | that is the existing `summary_provider` registry (§3), not a bus point. ADR-0237 itself offers the alternative |
| Hooks are "called before/after" the default | override semantics; see §2b |
| The axis is called `layer`, field `layer`, enum `PluginLayer`, API `layer_of()` / `plugins_by_layer()`, audit `plugin.layer_rejected` | renamed to **`boot_layer`** / `BootLayer` / `boot_layer_of()` / `plugins_by_boot_layer()` / `plugin.boot_layer_rejected`. "Layer" was already four-way overloaded (§1) — the same collision class the move off "tier" was meant to end |
| `corvin.global_plugins` entry points contribute global plugins | the group was implemented and **removed before it had a user**: any third-party wheel could publish `compliance:whatever` and load first, undisableable, past every gate. `bootstrap.GLOBAL_ENTRY_POINT_GROUP` is `None`; global plugins come from code only |

`tier_1_alternatives` in the old config sketch and the "Tier System Redefined"
table are both retired: they used "tier" for the `boot_layer` axis, which CLAUDE.md
reserves for ADR-0156's capability boundary.

---

## 9. Open work

1. **Wire the four points** into their call sites (engine/provider selection,
   `delegation_policy`, the workflow gate). Until then §4 stays "bus-side only",
   and the guard test in `test_extension_points.py` enforces that this line and
   the code agree.
2. **Backlog points** from ADR-0237 not implemented: `a2a.routing.select_target`,
   `a2a.envelope.pre_send`, `tde.cost_model`, `acs.worker_selector`,
   `acs.task_prioritizer`, `compute.pre_exec`, `workflow.node_executor`,
   `admin.authorization_gate`. Each needs the same treatment: a spec entry, a
   fail-closed decision, and tests in both flag states.
3. **Console surface** for `describe()` — an operator cannot currently see which
   plugin owns which point without reading the audit trail.
