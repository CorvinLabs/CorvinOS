# Custom Layer System (CLS) — ADR-0156

Three modules in `operator/bridges/shared/`:

| Module | Role |
|---|---|
| `custom_layer_registry.py` | M1 — On-disk registry, install/enable/disable/remove, namespace gate |
| `custom_layer_gate.py` | M2 — License gate for Tier-B/C layers (fail-closed) |
| `custom_layer_loader.py` | M3 — Tier-A prompt injection and skill registration per turn |

---

## Tier model

| Tier | Capability | License gate |
|---|---|---|
| A | `system_prompt.md` injection + `skills/*.md` registration | Always free |
| B | `tools/*.py\|.sh` (Forge-side) | Counted against `active_custom_layers_bc` limit |
| C | `mcp_server.py` | Counted against `active_custom_layers_bc` limit |

Free-tier limit for Tier-B/C: **1** active layer. Any error reading the license
is treated as free tier (fail-closed — never fail-open).

---

## On-disk layout

```
<corvin_home>/tenants/<tid>/custom-layers/<vendor>.<name>/
    layer.corvin.yaml   (REQUIRED)
    system_prompt.md    (Tier A)
    skills/*.md         (Tier A)
    tools/*.py|.sh      (Tier B)
    mcp_server.py       (Tier C)

<corvin_home>/tenants/<tid>/global/custom_layers.json   (registry)
```

---

## Namespace rules

- Full name format: `<vendor>.<layer_name>` — regex `^[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9_-]*$`
- Vendor segment CANNOT be `corvin`, `system`, or start with either prefix (impersonation guard)
- Namespace gate is CRITICAL: violations raise `CustomLayerNameError` before any files are copied

---

## Position constraint (EU AI Act Art. 50)

`prompt.position` in `layer.corvin.yaml` accepts only `"after_persona"` (default) or `"last"`.
`"before_persona"` is **structurally forbidden** — the disclosure card must precede any
vendor-injected content.  The loader enforces this at runtime; the manifest validator
enforces it at install time.

---

## Boot enforcement

`check_layer_boot()` (called from `check_boot_limit()` in the registry) runs at adapter
boot when the current license tier permits fewer active Tier-B/C layers than are currently
active (e.g. after a license downgrade).  Excess layers are **disabled** (never deleted),
oldest-first by `installed_at`.  Disabled layers are **not** auto-re-enabled on upgrade —
the operator must explicitly re-enable them.

---

## Audit events

All events land on the L16 hash chain via `audit_event()`.

| Event | Emitted by | Severity |
|---|---|---|
| `custom_layer.installed` | `install_layer()` | INFO |
| `custom_layer.enabled` | `enable_layer()` | INFO |
| `custom_layer.disabled` | `disable_layer()` | INFO |
| `custom_layer.removed` | `remove_layer()` | INFO |
| `custom_layer.boot_limit_exceeded` | `check_layer_boot()` | WARNING |
| `custom_layer.prompt_injected` | `load_tier_a_prompts()` | INFO |
| `custom_layer.skills_registered` | `load_tier_a_skills()` | INFO |
| `custom_layer.boot_limit_enforcement_failed` | `check_boot_limit()` error path | CRITICAL |

Allowed detail keys: `layer_name`, `tier`, `tenant_id`, `channel`, `reason`, `count`.
NEVER manifest contents, tool code, skill bodies, or system-prompt text.

---

## Loader behaviour (Tier-A, per turn)

`load_tier_a_prompts(tenant_id)` — called from `_resolve_spawn_inputs` in `adapter.py`.
Returns `[(content, position), ...]`.  Caps prompt content at 64 KiB per layer.
Wraps each block in `<custom_layer name="…">…</custom_layer>` framing.
Fails open per layer (broken layer is skipped with a WARNING; adapter is never blocked).

`load_tier_a_skills(tenant_id, channel_id)` — registers `skills/*.md` as
`scope="session"` skills via `MultiSkillRegistry`.  Skills disappear on `/new` / `/clear` / `/reset`.
Skill name format: `<vendor>_<layer>__<stem>` (non-alnum replaced with `_`, max 128 chars).
Caps skill body at 4 KiB per file.

---

## Must NOT do

- Use `"before_persona"` as `prompt.position` — structurally forbidden (EU AI Act Art. 50)
- Catch and ignore `LayerLimitExceeded` — it is a hard refusal
- Auto-re-enable layers after a license upgrade
- Delete layers at boot (only disable them)
- Accept vendor prefix `corvin` or `system` or any prefix starting with either
- Put manifest contents, tool code, skill bodies, or prompt text in audit `details`
- `import anthropic` from any CLS module (CI AST lint enforces)
- Allow a stale caller-supplied `limit` to override the live license read (`check_boot_limit` ignores the `limit` parameter)
- Write to `audit.jsonl` directly — use `audit_event()` from the shared audit module

→ ADR: `Corvin-ADR: decisions/0156-custom-layer-system.md`
