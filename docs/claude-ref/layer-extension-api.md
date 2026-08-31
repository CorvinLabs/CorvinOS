# Layer Extension API (ADR-0142)

A formal API for adding user/operator layers (hooks, filters, routing,
integrations) WITHOUT touching core files. The companion of ADR-0141: core
layers (`corvin.*`) are cryptographically immovable; the extension surface
(`ext.<vendor>.*`) is freely open.

## Two-class model

| Class | Namespace | Manifest | Removable | Override core deny? |
|---|---|---|---|---|
| Core | `corvin.*` | RS256-signed (ADR-0141) | No (boot-fails) | — (baseline) |
| Extension | `<vendor>.<name>` | user `layer.yaml` | Yes | No — **deny-wins** |

The `layer_integrity_hash` (ADR-0141 Tier 2) covers **only core files**; adding
or removing extensions never changes it.

## Public import path (implementation seam)

The ADR writes `from corvin.extension import ...` conceptually, but there is **no
importable top-level `corvin` package** (`operator/` is deliberately unpackaged —
it shadows the stdlib `operator`). The real modules live in
`operator/bridges/shared/`, imported after a `sys.path` insert (the same
convention as `engine_trust`, `flow_cli`):

- `extension_api.py` — `ExtensionHook` (ABC, `handle(tool_name, tool_input, ctx)
  -> HookResult`), `HookResult` (`.allow()` / `.deny(reason)`, `.is_deny`,
  `.reason`), `HookContext` (tenant_id/session_id/channel/chat_key/persona/config
  + `ctx.audit_write(event, details)` routed through the L16 chain with the
  ext.* allow-list, + a no-op-safe `ctx.metrics`).
- `extension_registry.py` — `ExtensionRegistry` (load/validate `layer.yaml`,
  namespace gate, five-scope resolution, `requires` check, `run_pre_tool_use`
  deny-wins pipeline). Module-level `validate_name`, `parse_manifest`,
  `check_requires`; errors `ExtensionError` / `ExtensionManifestError` /
  `ExtensionNamespaceError` / `ExtensionDependencyError`.
- `layer_cli.py` + `ops/launcher/layer_entry.py` — the `corvin-layer` CLI
  (`pyproject.toml [project.scripts]: corvin-layer = "ops.launcher.layer_entry:main"`).

## Manifest (`layer.yaml`)

`name` (must contain a `.`, must NOT start with `corvin.`, charset
`[a-z0-9_][a-z0-9._-]{0,127}`), `version`, `description`, `author`, `license`,
`scope` (task|session|project|user|tenant), `hooks[]` (`event`, `script`,
`priority`), `provides[]`, `requires[]` (e.g. `corvin.audit >= 1.0`),
optional `mcp_tools[]`.

## Hook pipeline — deny-wins

1. Core hooks (priority 0) run first.
2. Extension hooks run sorted by priority desc, then name asc.
3. ALL hooks run; final result = **deny if ANY hook denied**. An extension can
   never un-deny a core deny. A raising core hook is fail-closed (deny); a raising
   extension hook is non-blocking but audited (`ext.hook_denied`).

## Scopes (ADR-0007)

Resolution order session → task → project → user → tenant. Storage:
`tenant` `<corvin_home>/tenants/<tid>/extensions/<name>/`,
`project` `.corvin/extensions/<name>/`,
`user` `<corvin_home>/global/extensions/<name>/`,
`session` `<corvin_home>/tenants/<tid>/sessions/<id>/extensions/<name>/`,
`task` in-memory only.

## Core-capability seam (decoupling from ADR-0141)

`ExtensionRegistry(core_capabilities=...)` is an injectable `{name: version}`
dict, defaulting to a built-in 8-core-layer set. This keeps ADR-0142 decoupled
from ADR-0141's `security_capabilities.py`; production wiring passes the live
registry through this seam.

## `corvin-layer` CLI

`add` (installs **disabled by default**), `remove`/`enable`/`disable` (reject
`corvin.*` with the protected-layer error), `list` (CORE + EXTENSIONS),
`info`, `validate`, `upgrade`, `export`.

## Console (M5)

REST under `/v1/console/extensions` (`core/console/corvin_console/routes/extensions.py`)
+ React page `/app/extensions` (`web-next/src/pages/extensions.tsx`,
nav: Build → Extensions). Endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | `/extensions` | List all layers — `{core: [...], extensions: [...]}` |
| GET | `/extensions/{name}` | Detail + manifest (404 if unknown) |
| POST | `/extensions` | Install from a directory `{source, scope?, enable?}` — disabled by default |
| PUT | `/extensions/{name}` | `{enabled: bool}` — core layer → 403 |
| DELETE | `/extensions/{name}` | Remove — core layer → 403 |
| GET/POST | `/extensions/validate` | Lint a `layer.yaml` (query `manifest_yaml=` or body) |

GET routes use `require_session`; mutations use `require_csrf`. Tenant comes
from `rec.tenant_id` (session-bound, never an env var). The route reuses
`layer_cli.install_dir / set_enabled / remove` (REST-friendly wrappers that
raise `PermissionError` for `corvin.*`, `KeyError` when not installed,
`ValueError`/`ExtensionError` for validation). Source install: a local
directory path is the must-have; `http(s)://` / `github:` sources return 501
(documented TODO). Core layers render locked with no action buttons (lock
icon); extensions get Enable/Disable/Remove/Details + Add Extension.

**Dual audit:** the shared registry/CLI emits the `ext.*` lifecycle events to
the L16 chain; the console route ALSO emits metadata-only
`console.action_performed` / `console.action_failed` (`action="extension.install|enable|disable|remove"`,
`target_kind="extension"`, `target_id=<name>`). The `action` string is
free-form in `audit.py::_ALLOWED_FIELDS`, so no new allow-list entry is needed.

## Audit events (metadata only)

`ext.installed` / `ext.removed` / `ext.enabled` / `ext.disabled` (INFO),
`ext.hook_denied` / `ext.load_failed` (WARNING),
`ext.core_namespace_rejected` (CRITICAL). Allow-list: `name, version, scope,
event_type, hook, reason` — NEVER hook input/output content.

## Must NOT do

- Let an extension declare `name: corvin.*` (namespace gate is CRITICAL).
- Let an extension write directly to `audit.jsonl` (only via `ctx.audit_write`).
- Auto-enable after `corvin-layer add` (disabled until explicit enable).
- Degrade silently on a missing `requires: corvin.*` (must fail-to-load).
- `import anthropic` from `extension_registry.py` / `extension_api.py`.
