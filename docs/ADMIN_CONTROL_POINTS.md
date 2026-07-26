# CorvinOS Admin Control Plane

**What an admin can and cannot do, and the API that does it.**

**Date:** 2026-07-26
**Audience:** Operators, enterprise admins, anyone driving CorvinOS without the SPA
**ADRs** (sibling repo `Corvin-ADR/decisions/`):
`0239-admin-api-vs-web-ui.md` (admin API vs. web UI),
`0243-core-vs-plugins-architecture.md` (the `layer` axis),
`0233-plugin-system-consolidation.md` (plugin lifecycle)
**Code:** `core/console/corvin_console/routes/admin.py` ·
**Tests:** `core/console/tests/test_admin_route.py`

---

## Status

| | |
|---|---|
| **Feature flag** | `admin_control_plane` — **off by default** (ships dark, CLAUDE.md § Feature Flags) |
| **Mutations also need** | `plugin_runtime_lifecycle` — likewise off by default |
| **Implemented** | the six routes in [API reference](#api-reference) below |
| **gRPC** | **deferred, not planned** (ADR-0239). REST over the existing session auth covers every known caller; a second transport would be a pure dependency with no consumer. Revisit only when a concrete consumer exists that REST cannot serve. |
| **Not implemented** | hook registration over HTTP, license-gated installs over HTTP, a `corvinctl` CLI. Earlier drafts of this document showed those as examples; they are ideas, not endpoints. |

With `admin_control_plane` off, **every route below answers 404** — not 403, not 500.
The surface is genuinely absent, not merely hidden in the UI. Turn it on in
**Console → Settings → Features**, or set `spec.features.admin_control_plane: true`
in `tenant.corvin.yaml`.

---

## Vocabulary: `layer`, not "tier"

This document talks about **layers** (ADR-0243). The word "tier" is reserved
repo-wide for ADR-0156's capability/licensing boundary (Tier A/B/C) and is *not*
reused here; provenance is a third, separate field (`origin`).

| `layer` | What it is | Disableable? |
|---|---|---|
| `compliance` | GDPR / EU AI Act mechanisms — audit writer, consent gate, path gate, house rules, flow guard | **Never.** Structural, not configurable |
| `core` | Bundled reference implementation of an extension point | Yes; also *replaceable* by a plugin declaring `replaces:` |
| `bundled` | Ships with CorvinOS, opt-out per tenant (bridges, UI) | Yes |
| `installed` | Operator-installed third party | Yes |

| `origin` | Meaning |
|---|---|
| `builtin` | Ships with CorvinOS |
| `vetted` | Reviewed by the maintainer |
| `community` | Third-party, unreviewed — needs explicit consent before enable, and must declare its egress hosts (L35) |

---

## Permission matrix

| Action | `compliance` | `core` | `bundled` | `installed` |
|---|---|---|---|---|
| View status / health | ✅ | ✅ | ✅ | ✅ |
| Read config | ✅ | ✅ | ✅ | ✅ |
| Change config | ❌ **403** | ✅ | ✅ | ✅ |
| Enable | ❌ 409¹ | ✅ | ✅ | ✅ |
| Disable | ❌ **403** | ✅ | ✅ | ✅ |

¹ A `compliance` plugin is loaded by the boot path and normally has no per-tenant
registry record, so there is no enable flag to flip: that call answers **409**,
meaning "there is nothing here to change", not "you may not". Enabling a
mandatory mechanism is not a threat in any case — switching it off or
reconfiguring it is, and both of those answer **403**.

Config is refused for the same reason disable is: "where does the audit writer
write" is not an operator setting. A route that refuses to switch the mechanism
off while happily letting it be reconfigured would be the same hole with an
extra step.

### Why disable is refused structurally

If an admin could switch the audit writer off, the compliance guarantees collapse
(GDPR Art. 30/32, EU AI Act Art. 50). The refusal therefore lives in the registry,
not in a policy file:

* `PluginRecord.can_disable()` is `False` for `layer=compliance`;
* `PluginRegistry.can_disable()` is `False` for a plugin registered on that layer;
* `PluginRegistry.disable()` — the operator-initiated entry point — raises
  `PluginDisableRefused`, and the admin route maps that to 403;
* the admin route **never calls `PluginRegistry.unregister()`**, which is the
  machinery path (shutdown, hot-reload, replacement) that bypasses the guard by
  design;
* the boot tripwire (ADR-0232/0233) re-asserts at every start that the core audit
  writer is reachable and its chain verifies. **No override — no env var, no flag.**

The admin plane checks the layer **twice**, and the two checks are a fail-closed
conjunction: if the on-disk record and the loaded object disagree about the layer,
the stricter answer wins. A disagreement can never widen permissions.

---

## Authentication and tenancy

**No new auth surface.** The admin plane uses the same authenticated
`SessionRecord` as every other Console route (ADR-0239):

| | |
|---|---|
| Reads | session cookie `corvin_console_sid` → 401 without it |
| Mutations | session cookie **plus** `X-CSRF-Token` → 401/403 without either |

**The target tenant is `rec.tenant_id` from that session, and nothing else**
(CLAUDE.md § Multi-tenant axis, ADR-0007). It is never read from:

* `CORVIN_TENANT_ID` or any other env var,
* a query parameter,
* a request header,
* a request body field — the request models are `extra="forbid"`, so a body
  carrying `tenant_id` is **rejected with 422** rather than silently ignored.
  Silently dropping it would look identical to honouring it from the outside.

Two sessions for two tenants in the same process see two different registries;
that is pinned by a test, not just asserted here.

---

## What the admin plane shows

Two sources are merged, because neither is complete on its own:

1. **`<corvin_home>/tenants/<tid>/plugins/registry.yaml`** — the operator's
   declared state for this tenant (install / enable / settings).
2. **The in-process plugin registry** — what is actually running, with the
   authoritative `layer` of the loaded object.

`compliance`, `core` and `bundled` plugins are loaded by the boot path and
typically have **no** registry record. A registry-only view would answer 404 for
exactly the plugin whose disable must be refused with 403, which would leave the
compliance guard unreachable. Each entry therefore reports `source`:
`registry` | `runtime` | `both`.

An `installed`-layer plugin that is loaded in the process but has no record in
*this* tenant belongs to another tenant and is **not** listed — the in-process
registry is global, the admin plane is not.

---

## API reference

**Base path.** The router declares `/api/admin/*`. The gateway mounts the Console
router at `/v1/console`, so in a default install the effective path is
`/v1/console/api/admin/*`. A headless deployment that mounts
`corvin_console.app.router` at the root serves `/api/admin/*` verbatim. The
examples below use the default install.

### `GET /api/admin/plugins`

List every plugin this tenant administers.

```http
GET /v1/console/api/admin/plugins
Cookie: corvin_console_sid=…
```

```json
{
  "plugins": [
    {
      "plugin_id": "audit-writer",
      "version": "1.0.0",
      "display_name": "Audit Writer",
      "plugin_type": "audit_backend",
      "layer": "compliance",
      "origin": null,
      "enabled": true,
      "runtime_loaded": true,
      "can_disable": false,
      "source": "runtime",
      "health": {"ok": true, "message": ""}
    },
    {
      "plugin_id": "acme-notify",
      "version": "1.0.0",
      "display_name": "Acme Notify",
      "plugin_type": "notification_backend",
      "layer": "installed",
      "origin": "vetted",
      "enabled": false,
      "runtime_loaded": false,
      "can_disable": true,
      "source": "registry",
      "health": null
    }
  ],
  "total": 2,
  "tenant_id": "_default",
  "lifecycle_enabled": true
}
```

Field notes:

* `origin` is `null` for a runtime-only plugin — provenance is genuinely unknown
  then, and reporting `builtin` would be a claim the surface cannot support.
* `enabled` is the record's flag, or "it is running" when there is no record.
  `runtime_loaded` is separate on purpose: the two diverge after self-healing
  contains a plugin (healing must not rewrite operator configuration).
* `health` is `null` when the plugin is not loaded — the admin plane never
  reports health it did not measure.

### `GET /api/admin/plugins/{plugin_id}`

Same shape plus the declarations and the settings surface: `pii_risk`,
`locality`, `network_egress`, `egress_hosts`, `requires_consent`, `settings`,
`settings_schema`, `dependencies`, `replaces`, `installed_at`,
`last_error_type`.

For a runtime-only plugin those fields stay at their defaults — settings live in
the registry record, and inventing an empty schema would suggest one exists.

Unknown `plugin_id` → **404** `{"detail": "plugin not installed"}`.

### `POST /api/admin/plugins/{plugin_id}/enable`

```http
POST /v1/console/api/admin/plugins/acme-notify/enable
X-CSRF-Token: …
Content-Type: application/json

{"consent_granted": true}
```

Returns the detail view with `enabled: true`. The consent gate
(`community` origin or `pii_risk: high` or an explicit `requires_consent`) and
the L34/L35 flow-declaration gate both apply — this route does not narrow them.

* consent missing → **409**
* community plugin with `network_egress: external` and no `egress_hosts` → **409**
* plugin has no registry record for this tenant → **409**
* unknown plugin → **404**
* `plugin_runtime_lifecycle` off → **409**

### `POST /api/admin/plugins/{plugin_id}/disable`

```http
POST /v1/console/api/admin/plugins/acme-notify/disable
X-CSRF-Token: …
```

On a `compliance` plugin:

```json
HTTP/1.1 403 Forbidden
{
  "detail": "audit-writer is on the compliance layer and cannot be disabled (GDPR Art. 30/32, EU AI Act Art. 50)"
}
```

The plugin stays loaded and stays enabled, and a `console.action_denied` event
with `reason: "compliance-layer"` is written to the audit chain. **403 means
refused — never 200 with a silent no-op.**

Other outcomes: still-enabled dependents → **409**; unknown plugin → **404**;
`plugin_runtime_lifecycle` off → **409**.

Order of operations: the plugin is unloaded through `registry.disable()` first,
then the record is flipped. That order is what keeps the compliance guard
reachable — persisting first would hot-unload through the machinery path and
leave `registry.disable()` with nothing to refuse. The cost is a narrow window:
if the persist step is then refused (an enabled dependent), the plugin is already
unloaded while the record still says enabled. That divergence is *visible* in the
API — `runtime_loaded: false, enabled: true` — and the call answers 409.

### `PUT /api/admin/plugins/{plugin_id}/config`

```http
PUT /v1/console/api/admin/plugins/acme-notify/config
X-CSRF-Token: …
Content-Type: application/json

{"settings": {"channel": "alerts"}}
```

The payload replaces the stored settings and is validated against the plugin's
own JSON Schema (`settings_schema`) **before** the registry lock is taken, so a
rejected write never reaches disk.

```json
HTTP/1.1 422 Unprocessable Entity
{"detail": "settings rejected at $.channel: fails type='string'"}
```

The 422 detail names the **offending key and the violated constraint** and does
not echo the submitted value. (`jsonschema`'s own message does the opposite —
it quotes the value and omits the key — so the route reads `json_path` and
`validator` off the chained error instead. The constraint that *is* quoted comes
from the plugin's schema, not from the request.)

Other outcomes: `layer=compliance` → **403** + `console.action_denied` (its
configuration is immutable); plugin has no registry record → **409**; unknown
plugin → **404**; `plugin_runtime_lifecycle` off → **409**.

### `GET /api/admin/health`

```json
{
  "ok": true,
  "tenant_id": "_default",
  "total": 2,
  "healthy": 1,
  "unhealthy": 0,
  "unchecked": 1,
  "by_layer": {"compliance": 1, "installed": 1},
  "plugins": {
    "audit-writer": {
      "checked": true, "ok": true, "message": "",
      "layer": "compliance", "runtime_loaded": true, "can_disable": false
    },
    "acme-notify": {
      "checked": false, "ok": null, "message": "",
      "layer": "installed", "runtime_loaded": false, "can_disable": true
    }
  }
}
```

`ok` is true when nothing that *could* be checked reported a problem. A plugin
that is not loaded is `checked: false` with `ok: null` — never "healthy by
default". Each check runs under the plugin's circuit breaker: an open breaker is
reported as contained rather than called.

This calls into every loaded plugin on each request. That is deliberate — a
cached number would let the API report health it never measured — and it is
affordable because the whole surface sits behind an operator-enabled flag.

---

## Error shapes

| Status | Means | Examples |
|---|---|---|
| **401 / 403** | Not authenticated / no valid CSRF token | missing session cookie, missing `X-CSRF-Token` |
| **403** | Refused by the compliance layer | disable or config on `layer=compliance` |
| **404** | The route does not exist for you, or the plugin does not | `admin_control_plane` off; unknown `plugin_id` |
| **409** | Authorised, but the installation is not in a state that accepts this | `plugin_runtime_lifecycle` off; consent missing; undeclared egress; enabled dependents; no registry record |
| **422** | Unprocessable input | settings violate the schema; unknown body field (including a smuggled `tenant_id`) |
| **500** | The registry file is unreadable | corrupt `registry.yaml` — fails closed, is never silently reset |
| **503** | This installation ships without `core/plugins` | only reachable while the flag is on |

While the flag is off, even a malformed body answers **404**, not 422: the gate is
a FastAPI dependency, and dependencies resolve before body validation. A dark
feature must be indistinguishable from an absent one.

---

## Audit trail

Every mutating call writes a console audit event into the tenant's hash-chained
`audit.jsonl` (GDPR Art. 30):

| Outcome | Event | `reason` |
|---|---|---|
| Success | `console.action_performed` | — |
| Refused by policy | `console.action_denied` | `compliance-layer` |
| Failed | `console.action_failed` | `lifecycle-disabled`, `consent-required`, `egress-not-declared`, `not-installed`, `invalid-input`, `refused`, `internal-error`, `no-registry-record` |

```json
{
  "ts": 1785000000.0,
  "event_type": "console.action_denied",
  "severity": "WARNING",
  "details": {
    "action": "admin.plugin_disable",
    "target_kind": "plugin",
    "target_id": "audit-writer",
    "sid_fingerprint": "9f2c1ab34de5",
    "tenant_id": "_default",
    "reason": "compliance-layer"
  }
}
```

Content rules, all load-bearing:

* the actor is a **session fingerprint**, never a name or an email — no PII in
  audit details;
* `reason` is a **closed vocabulary of slugs**, never `str(exc)`: a plugin error
  message routinely carries a path or a host, and an append-only chain cannot be
  redacted afterwards;
* the plugin lifecycle records settings **key names only** (`keys_before` /
  `keys_after`) and the admin plane adds nothing beyond the plugin id — a
  settings **value** can be a token or a webhook URL and never enters the chain.
  A test asserts this by writing a distinctive value and grepping the chain for it.

The audit trail outlives the plugin: uninstalling never removes history.

---

## Extension hooks

Plugins extend a `core` reference implementation through the extension-point bus
(ADR-0237, feature flag `plugin_extension_points`), and a plugin can take over a
`core` component entirely by declaring `replaces:`. Both are **registration-time**
mechanisms — a plugin declares them; there is no HTTP endpoint that registers a
hook, and the admin plane does not expose one.

What extension can never reach, in any layer:

* the audit hash-chain and what gets written to it,
* the consent gate's deny-by-default answer,
* Ed25519 verification and the A2A denial path,
* the L34 flow guard and the L35 egress allowlist,
* the licensing gate.

`layer=compliance` is not replaceable at all: `PluginRecord.__post_init__`
refuses a record that declares both `layer: compliance` and `replaces:`, and
`PluginRegistry.replace()` refuses any target that is not on the `core` layer.

---

## Scenario: emergency maintenance

The Discord bridge is failing and must be stopped now.

```bash
BASE=http://127.0.0.1:8765/v1/console
curl -s -X POST "$BASE/api/admin/plugins/discord-bridge/disable" \
  -b cookies.txt -H "X-CSRF-Token: $CSRF"
# → 200, {"enabled": false, "runtime_loaded": false, …}
```

What is still true afterwards: the audit writer still writes, consent is still
enforced, the house-rules gate still runs. Those are on the `compliance` layer
and the same call against them would have answered 403.

Re-enable when the maintenance is done:

```bash
curl -s -X POST "$BASE/api/admin/plugins/discord-bridge/enable" \
  -b cookies.txt -H "X-CSRF-Token: $CSRF" -d '{}' -H 'Content-Type: application/json'
```

---

## Summary

**An admin is empowered to** see every plugin and its layer, understand *why*
something is mandatory, configure and enable/disable everything outside the
compliance layer, and do all of it without the SPA.

**An admin is prevented from** disabling *or reconfiguring* a compliance
mechanism, reaching past the layer guard through the machinery unload path,
moving the target tenant with a parameter, or getting a settings value into the
audit chain.

That split is the whole design: operators stay in control of their installation,
and the compliance guarantees stay structural rather than discretionary.
