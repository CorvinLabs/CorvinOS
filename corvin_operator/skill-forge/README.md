# skill-forge

Sister plugin to `forge`: where forge generates **executable tools** (sandboxed
code), `skill-forge` generates **skills** — markdown knowledge that gets
prompt-injected at the persona/sub-agent level. Both share the four-scope
mechanic (`task` / `session` / `project` / `user`). Every skill event is a
link in the TENANT CORE audit chain —
`<corvin_home>/tenants/<tid>/global/forge/audit.jsonl` — the chain the boot
tripwire and the compliance reports verify.

## Layout per scope

```
<scope_root>/
├── forge/                   <- forge tool workspace (existing plugin)
└── skill-forge/             <- this plugin's workspace
    ├── skills_registry.json <- canonical manifest (SkillSpec per name)
    ├── manifest.json        <- ADR-0420 resolver manifest, same generation
    │                           ({"skills": [{"name", "metadata"}]}) — what
    │                           core.skills.corvin_skills.resolver reads
    └── skills/<name>/
        ├── SKILL.md
        └── meta.json
```

`scope_root` is `forge.scope.scope_root(<scope>).parent` — the plugins sit
side by side under each scope. Audit does NOT live there: `MultiSkillRegistry`
binds every scope's registry to the tenant core chain via
`forge.paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"`
(a bare `SkillRegistry(root)` in standalone use defaults to
`<root>/../audit.jsonl`).

Lookup order across scopes is HIGHEST first (user → project → session →
task) so a curated user-scope skill is never shadowed by a task-scope copy.

## Lifecycle

1. **Create** — `SkillRegistry.create(name, type, body_md, description, claim)`
   runs the linter (prompt-injection / secrets / persona-boundary / length)
   and writes `SKILL.md` + `meta.json` atomically. Linter rejection raises
   `LinterError` — no file is written.
2. **Grade** — `grade(name, run_id, score, notes)` appends a
   `{run_id, score, ts, notes}` row to `meta.json` and to the manifest.
   Score is in `[0.0, 1.0]`.
3. **Promote** — `MultiSkillRegistry.promote(name, to=<scope>, force=False)`
   moves a skill to a higher scope, gated by the grade history:
   - `task -> session` — needs ≥1 grade with score > 0
   - `session -> project` — needs ≥3 grades, mean ≥ 0.5
   - `project -> user` — operator-only, requires `force=True`
   Failed gates raise `PromotionGateError`. Promotion is move-semantics —
   the source-scope copy is removed once the new copy is durable.
4. **Cleanup** — `scripts/skill_cleanup.py {tasks,sessions,ungraded}`:
   - `tasks` removes `/tmp/.corvinOS/tasks/<id>/skill-forge/` older than
     `--ttl-hours` (default 1).
   - `sessions` removes `<corvin_home>/tenants/<tid>/sessions/<chan>/skill-forge/`
     (every tenant; the legacy `<corvin_home>/sessions` alias is de-duplicated
     by real path) older than `--ttl-days` (default 30).
   - `ungraded` walks task + every tenant's sessions (+ project ONLY when
     `CORVIN_PROJECT_ROOT` names the repo — project scope lives in
     `<repo>/.corvin/skill-forge`, which the script cannot enumerate), and
     deletes every skill with zero grades older than `--ttl-days`
     (default 7). User scope (`<tenant_home>/skill-forge`) is never pruned —
     the previous version pruned exactly that directory under the name
     "project" (the compat symlink made it look home-level).

## Linter

Fail-closed errors block `create()`:

- prompt-injection substrings (`ignore previous instructions`,
  `disregard the above`, `you are now`, `<|im_start|>`, `<|im_end|>`,
  `system:` at line start, base64-like blocks of ≥64 chars)
- secret patterns (AWS access keys, GitHub PATs, Anthropic-style
  `sk-…` keys, PEM private-key headers)
- persona-boundary phrases (`you can now use bash`, `bypass permissions`,
  `--dangerously-skip-permissions`, `you may execute`)
- body > 16384 bytes (ADR-2274)

Warning-only (does not block):

- code-block density > 40 % (suggests "use forge tool instead")

## MCP server

`python3 -m skill_forge.mcp_server` exposes seven tools over stdio:
`skill_create`, `skill_promote`, `skill_grade`, `skill_list`, `skill_get`,
`skill_purge`, `skill_diff`. Every response is wrapped in a
`{ok, data?, error?}` envelope (in `structuredContent`).

## Persona

There is **no separate `skill-forge` persona file** — the unified
generation persona is `forge` (in `operator/cowork/personas/forge.json`).
That persona ships `permission_mode: default`, allows
`mcp__forge__*` and `mcp__skill_forge__*`, and blocks
`Bash` / `Edit` / `Write` / `MultiEdit` / `NotebookEdit`. The historic
name `skill-forge` resolves through `_PERSONA_ALIASES` in
`operator/cowork/lib/resolver.py` so existing `chat_profiles` pinning
`persona = "skill-forge"` keep working.

Beyond the dedicated `forge` persona, **any persona with
`skill_forge_enabled: true`** in its JSON gains the seven `skill_*` MCP
tools — including `assistant`, `coder`, `browser`, `research`, `inbox`.
The path-gate hook
(`operator/voice/hooks/path_gate.py`) keeps the structural safety
boundary in place regardless of the persona's `permission_mode`.

### Namespace gate — who may mutate what

The server reads its caller persona from its own env (`CORVIN_CALLER_PERSONA`,
else `SKILL_FORGE_PERSONA`) and gates every mutating tool on it:

| Caller | Namespace | Force-promote, project/user create, organic grades |
|---|---|---|
| no persona | none — every mutation refused (LRN-1) | no |
| persona with a `persona_namespaces` entry (`assistant`, `coder`, …) | that prefix | no |
| any other persona — `delegate` (an unnamed delegating caller), operator-created personas, … | its OWN name, non-`[A-Za-z0-9_]` mapped to `_` (`my-persona` → `my_persona.`) | no |
| `skill-forge` / `forge` WITH a resolver launch record | wildcard (the operator) | yes |
| `skill-forge` / `forge` WITHOUT one (e.g. a server a worker started itself) | its own name | no |

**One exception, for GRADING only: an ACS run's skills.** An `acs.*` skill is forged by an
ACS run, not by a persona (no persona can mint `acs.*`: its namespace is `<persona>.`), and
ACS records `created_by="acs"`, `meta.origin="acs"` and the persona that started the run
(`acs_delegate` passes its caller) in `meta.persona`. That persona — and only it — may
`skill_grade` the skill (`SkillRegistry.owns_acs_skill`, checked by the server and again by
the registry's own gate). The grade is still a namespaced persona's: non-organic, clamped to
`AUTO_GRADE_CAP_MAX` (0.3). Purge, promote and update stay behind the gate. Before this the
gate refused every grade of an `acs.*` skill, so it stayed ungraded and the injection gate
(`n_grades >= 1`) never let a later run use it. A skill with no `meta.persona` (an ACS run
outside `acs_delegate`) is the operator's to grade.

The wildcard is the operator's only for a server the cowork resolver launched:
`resolver._skill_forge_launch` writes `<tenant>/skill-forge/launches/<sha256(nonce)>.json`
(0600) before the spawn and puts `<tenant>:<nonce>` into the server's
`CORVIN_SKILL_FORGE_LAUNCH`; the server honours the wildcard only when that record
names the same persona and tenant and is younger than 24 h, and removes it at exit
(`skill_forge/launch_record.py`). The registry's own defence-in-depth gate gets the
same rule. Until 2026-11-05 every named persona without a namespace entry was the
wildcard (refutation RL-5/RL-6). `path_gate` denies a Bash command that starts the
server itself (`python3 -m skill_forge.mcp_server`, `…/skill_forge/mcp_server.py`,
`forge.py mcp`).

**Boundary (stated, not hidden): this is ATTRIBUTION, not isolation.** The nonce
travels in the MCP config and in the server's environment, and the record is a
file of the same OS user the worker runs as. A worker that deliberately reads the
nonce or writes a record — or evades `path_gate`, a syntactic guard — can still
present itself as the operator. Integrity against that needs OS-level isolation of
the worker (ADR-0241/0238).

## Auto-grade after bridge turn

The bridge adapter runs `skill_inject.auto_grade_from_output(...)` after
every successful turn. The function scans the LLM's reply for either a
**non-negated** name variant of an active skill (underscore / hyphen /
spaced) or the first 80 characters of its body, and writes a
`score=0.7` grade for every match. The negation filter looks 30 chars
before and 20 chars after each occurrence for words like *"not"*,
*"won't"*, *"skip"*, *"instead of"*, *"nicht"*, *"statt"*, etc. — so a
sentence like *"I won't use csv_diff_workflow"* does **not** count as
positive use. Outputs shorter than 40 characters are skipped entirely.
Best-effort: a grade-write failure logs but never breaks the bridge
turn. The same `profile.inject_skills: false` flag that opts out of
injection also opts out of auto-grade.

## Tests

```bash
python3 operator/skill-forge/tests/test_linter.py
python3 operator/skill-forge/tests/test_registry.py
python3 operator/skill-forge/tests/test_multi_scope.py
python3 operator/skill-forge/tests/test_grading.py
python3 operator/skill-forge/tests/test_cleanup.py
python3 operator/skill-forge/tests/test_plugin_slot.py
python3 operator/skill-forge/tests/test_namespace_gate.py
python3 operator/skill-forge/tests/test_mcp_notification.py
python3 operator/skill-forge/tests/test_e2e_demo_task.py
# opt-in (real claude subprocess, costs API credits):
SKILL_FORGE_ENGINE_E2E=1 python3 operator/skill-forge/tests/test_engine_visibility.py
SKILL_FORGE_ENGINE_E2E=1 python3 operator/skill-forge/tests/test_engine_visibility_inject.py
```

The E2E demo runs the full lifecycle (linter reject -> create -> grade ->
promote across all 4 scopes -> TTL prune -> hash-chain verify) without
an LLM in the loop. The `test_mcp_notification.py` test pins the
wire-level guarantee that `notifications/tools/list_changed` arrives
after `skill_create` / `skill_purge` so a freshly created skill is
visible from the same MCP session.
