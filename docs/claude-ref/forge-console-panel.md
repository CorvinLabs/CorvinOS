# Forge panel — Plugins, Audit and the routes that answer 501

**Source:** `core/console/corvin_console/routes/forge_unified.py` (mounted at `/v1/console/forge`),
`routes/forge_creator.py`, `routes/layer_forge.py`; UI `web-next/src/pages/forge.tsx`,
`components/forge/{PluginsTab,AuditTab,SkillsTab,LayersTab}.tsx`.
**Tests (real uvicorn sandbox, HTTP):** `tests/sandbox/test_gap_forge_wf_compute.py` (G1–G3).
Layer transitions: see [layer-forge.md](layer-forge.md) § Console frontend.

## Plugins tab — `GET /forge/plugins`

Lists this tenant's **staged** forged plugins: the directories Plugin Forge wrote under
`<tenant_home>/plugin-builder/` that carry a `FORGE_PROVENANCE.json` with
`surface: plugin_forge`. It reads through `forge_creator.forged_summaries()` — the same reader as
`GET /forge-creator/plugins` (Marketplace → Forged), so the two lists cannot differ. Rows carry
`id` (= directory name), `name`, `plugin_id`, `kind`, `tier`, `quality`, and `enabled: false`,
`installed: false`: a staged plugin is never installed or running. Install and delete happen in
Marketplace → Forged; the tab links there.

Until 2026-10-24 the route did not exist (404, swallowed by the page loader), so the tab always read
"No plugins yet". Its dead controls ("New Plugin" without a handler, a card `onClick={() => {}}`, a
CLI-only Download) were removed; the bundle Import stays.

## Audit tab — `GET /forge/audit[?since=ISO&event_type=prefix]`

Reads the tenant chain `<tenant>/global/forge/audit.jsonl` **newest-first, at most 64 MiB from the
end** (`_AUDIT_SCAN_BYTES`), returns at most 100 events (`_AUDIT_MAX_EVENTS`), and keeps a record
when it is a Forge event:

| Shape | Event names |
|---|---|
| own event type (prefix) | `forge.*` (e.g. `forge.tool_executed`), `forge_run.*`, `forge_bundle.*`, `forge_intake.*`, `layer_forge.*`, `tool.*` |
| own event type (exact) | `skill.create`, `skill.create_forced_scope`, `skill.delete`, `skill.grade`, `skill.promote`, `skill.namespace_denied`, `skill.scope_denied`, `skill.bootstrap_grade_failed` (SkillForge registry/MCP; the skill name is the record's `tool` field), `code.exec_attempt`, `policy.import_denied`, `policy.namespace_denied` |
| `console.action_performed` / `console.action_failed` | `details.action` starting with `forge`, `layer_forge`, `skill.`, `tool.`, `plugin.forged` |

`skill.*` is matched by exact name on purpose: `skill.executed` and `skill.model_selector.*` are
OS-skill telemetry written on every turn. Each event carries `type` (the action for console records,
else the event type), `resource_type` (`tool`, `skill`, `os-skill`, `plugin`, `layer`, `run`,
`bundle`, `intake`), `resource_id`, an 8-hex `hash` and `prev_hash` prefix (never the full hash), and
details reduced to metadata keys (`audit_tail._sanitize_details` plus a fixed list of id/status/count
keys — no free text, no user identifiers, no tool input or output).

`event_type` is an optional prefix on that `type`; the legacy default `forge_*` (what the UI sends)
means all Forge events. The response has `complete: false` when the 64 MiB window ended before the
`since` bound and the page was not full — the UI then says "older records exist beyond the window
read" instead of claiming a complete history.

Until 2026-10-24 the endpoint kept only `console.action_performed` with `details.action` starting
`forge_` and read only the last 256 KiB: on a chain with >1,500 `forge.tool_executed` records it
showed 0 events.

## Routes that answer 501 and write nothing

A route whose effect does not exist answers 501 and writes **no** audit record — a chain record of an
act that never happened is the one thing the chain must not contain.

| Route | Why 501 |
|---|---|
| `POST /forge/tools/{id}/enable`, `/disable` | the registry stores no disabled flag, the runner reads none |
| `POST /forge/skills/{id}/enable`, `/disable` | SkillForge stores no enabled flag, the injection gate reads none |
| `POST /forge/skills/{id}/rollback` | SkillForge keeps no version history (`versions` is always `[]`). Until 2026-10-24 it wrote `forge_skill_rollback` and answered `rolled_back`. The real canary rollback is `POST /autonomous-forge/rollback` |
| `POST /forge/os-skills/{id}/config` | one config writer, not two: Method Discovery owns the SkillAdapter config |
| `POST /layer-forge/gate-thresholds/apply` | no threshold store; see [layer-forge.md](layer-forge.md) |

The Skills tab no longer renders a Rollback button (it had an empty handler).
