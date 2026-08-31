# skill-forge

Sister plugin to `forge`: where forge generates **executable tools** (sandboxed
code), `skill-forge` generates **skills** — markdown knowledge that gets
prompt-injected at the persona/sub-agent level. Both share the four-scope
mechanic (`task` / `session` / `project` / `user`) and the hash-chain audit
log under `.corvinOS/<scope>/audit.jsonl`.

## Layout per scope

```
<scope_root>/
├── audit.jsonl              <- shared with forge (same hash-chain)
├── forge/                   <- forge tool workspace (existing plugin)
└── skill-forge/             <- this plugin's workspace
    ├── skills_registry.json
    └── skills/<name>/
        ├── SKILL.md
        └── meta.json
```

`scope_root` is `forge.scope.scope_root(<scope>).parent` — i.e. the plugins
sit side by side under each scope, so the parent dir is the natural anchor
for the shared audit log.

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
   - `sessions` removes `~/.corvinOS/sessions/<chan>/skill-forge/` older
     than `--ttl-days` (default 30).
   - `ungraded` walks task/session/project, deletes every skill with zero
     grades older than `--ttl-days` (default 7). User scope is never
     pruned.

## Linter

Fail-closed errors block `create()`:

- prompt-injection substrings (`ignore previous instructions`,
  `disregard the above`, `you are now`, `<|im_start|>`, `<|im_end|>`,
  `system:` at line start, base64-like blocks of ≥64 chars)
- secret patterns (AWS access keys, GitHub PATs, Anthropic-style
  `sk-…` keys, PEM private-key headers)
- persona-boundary phrases (`you can now use bash`, `bypass permissions`,
  `--dangerously-skip-permissions`, `you may execute`)
- body > 8192 bytes

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
