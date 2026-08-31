# Plugins Reference (Forge L6, SkillForge L7, Cowork L4, Routing L5)

> Load when working on forge tools, skills, personas, or the auto-router.
> Quick summary in CLAUDE.md § Forge plugin.

## Cowork plugin (layer 4) — optional, on top of voice

The sister plugin `operator/cowork/` turns the single coder agent into a
multi-persona hub: a different role per chat (research, inbox, coder, ...).

**What you, as Claude Code, need to know when editing:**

- A `chat_profile` may now contain an optional `persona: "<name>"` field.
  The adapter resolves it only when cowork is installed (graceful fallback).
- Persona fields merge with chat_profile fields: lists union, scalars →
  profile wins, `mcp_servers` shallow-merged, `append_system` concatenated.
- The adapter consumes two NEW profile fields voice didn't know before:
  `mcp_servers` (dict → temp JSON file → `--mcp-config`) and `add_dirs`
  (list → multiple `--add-dir` flags). Both go through the `_cowork`
  helper and are silent no-ops when cowork is missing.
- READ / WRITE path rules from voice apply unchanged — the cowork resolver
  is called _inside_ `_resolve_chat_profile`, so it benefits from
  hot-reload automatically.
- Personas live in `operator/cowork/personas/<name>.json` (bundle) and
  `<repo>/.corvin/cowork/personas/<name>.json` (user override; legacy
  callers reach it via the back-compat symlink at
  `~/.config/claude-cowork/personas/`; `.corvinOS/` resolves identically
  until Phase 7).
- Standalone CLI: `operator/cowork/bin/cowork {list,show,run,bind,unbind,add,rm}`
  is the implementation behind the slash commands AND a standalone tool
  with no bridge dependency.

### Console persona management (web UI)

The owner console (`core/console`) is the GUI for persona CRUD at
`/app/personas` (route file `corvin_console/routes/personas.py`,
page `core/console/corvin_console/web-next/src/pages/personas.tsx`):

- **List / detail** — `GET /personas`, `GET /personas/{name}` enumerate bundle
  + per-tenant user personas. The bundle dir is resolved via
  `_resolve_bundle_dir()` (source tree → vendored `_vendor/operator` fallback);
  WITHOUT the fallback a fresh `pip install` showed an EMPTY persona list
  (the bundle ships with the wheel but was looked up at the wrong path).
- **Create / edit** — `PUT /personas/{name}` is create-or-replace for a
  user-scope persona (a fresh name creates it; a bundle name requires
  `POST /personas/{name}/copy-from-bundle` first — bundle files are read-only).
- **Engine assignment** — `GET/PUT /personas/{name}/engine` pins
  `engine`/`os_model`/`worker_model`/`engine_lock` per persona (ADR-0123 M3).
- **Delete** — `DELETE /personas/{name}` removes a user-scope override only
  (CSRF + re-auth). Deleting an override that shadows a bundle persona reverts
  to the bundle copy.
- **Deactivate / reactivate** — `POST /personas/{name}/disable|enable` toggle a
  per-tenant **name registry** at `<tenant>/cowork/personas/.disabled.json`
  (a hidden file, NOT a JSON field on the persona) so it works uniformly for
  bundle and user personas without mutating the shipped file. `list_available`
  in `resolver.py` reads the same registry and EXCLUDES disabled names, so a
  deactivated persona is dropped from runtime auto-routing — an explicit
  per-chat pin via `resolver.load(name)` still resolves (deactivate means
  "don't offer it", not "brick an active chat"). The registry read fails open
  (a corrupt file never hides every persona).

**What you must NOT do:**

- Voice must **not hard-import cowork** — cowork is optional. The only
  permitted form is the `_cowork is not None` guard inside the adapter.
- The current `chat_profiles` default path (no profile → max-open via
  `--dangerously-skip-permissions`) **must not be given up**, even with
  cowork installed. There is a bundle persona `coder` that codifies that
  mode explicitly — it activates only when a chat opts in via
  `chat_profiles[<chat>].persona = "coder"`.

## Auto-routing (layer 5) — the default since cowork v0.2

When a chat has **no** explicit persona pinned and cowork is installed,
the adapter calls `_apply_auto_routing()`, which in turn asks
`router.route()` (Haiku) and merges the result into the profile — before
`_build_claude_args`.

**What you need to know when editing:**

- `bridges/shared/router.py` is the backend layer with three modes:
  - `off` — never route, return None.
  - `heuristic` — keyword matcher only (0 ms, no API key, no LLM call).
    DEFAULT — works on Max-subscription setups without an
    `ANTHROPIC_API_KEY`.
  - `auto` — heuristic first, then anthropic SDK if `ANTHROPIC_API_KEY`
    is set. The `claude -p` CLI fallback (slow, times out on
    Max-subscription) is **opt-in only via `ROUTER_ALLOW_CLI=1`** —
    by default we skip it silently, because it would burn 12 s per
    request on every Max-subscription user.
  - `ROUTER_FAKE=1` overrides everything for tests.
  `route()` returns `{persona, confidence, why}` or `None`.
- Heuristic patterns live in `_HEURISTIC_PATTERNS` (lowercased regex,
  two-token rule for ambiguous verbs: "open" alone is too generic;
  only "open … URL/link/page/site" routes to a web persona). When adding a persona that should
  be router-pickable, add a tight pattern there — false positives are
  worse than misses (the assistant fallback handles misses fine).
- The adapter calls the router only when:
  - cowork + router are both importable
  - no `profile.persona` is set
  - `routing.mode` ≠ `"off"` (in shared/settings.json or via env
    `ADAPTER_ROUTING_MODE=off`)
- Low confidence OR router returns None → `fallback_persona`
  (default `assistant`, defined in `_ROUTING_DEFAULTS`).
- The final reply is prefixed in `process_one` with `[<persona>] `
  (only the first chunk) when `_routing_show_prefix` is true.
- Tests that verify legacy max-open behaviour MUST set
  `ADAPTER_ROUTING_MODE=off` — otherwise the default routing collides
  with the expectation.
- The generalist `assistant.json` is marked with `routing_exclude: true`,
  so the router doesn't "route" to the generalist persona (that would be
  self-reference); it is only active via the fallback.

**What you must NOT do:**

- Don't rename the default generalist without adjusting
  `_ROUTING_DEFAULTS.fallback_persona` in parallel.
- Tests must NEVER overwrite the LIVE `bridges/shared/settings.json`.
  Use `ADAPTER_ROUTING_MODE=off` env or a sandbox file instead.

### ACS-X — Autonomous Command Selector (ADR-0155)

Runs after persona routing. `bridges/shared/acs_classify.py` classifies the
incoming task into one of six execution primitives:
`LOOP | WORKFLOW | GOAL | COMPUTE | DELEGATE | DIRECT`.

Two-stage: heuristic keyword match (< 1 ms, always runs) → optional Haiku-4.5
fallback when confidence < 0.70. The adapter injects the result as an
`<acs_directive>` block into the system prompt (lines 2514–2539 of `adapter.py`).

**Block content** (per primitive):

- `LOOP` — convergence criteria + **ADR-0164 loop engineering invariants**:
  loss-signal-first, explicit convergence, K_MAX=5, central dedup.
- `WORKFLOW` — adversarial verify hint + **ADR-0164 workflow engineering invariants**:
  structured output schema, fan-out before fix, ≥1 verifier per CRITICAL/HIGH,
  dry-streak convergence.
- `GOAL / COMPUTE / DELEGATE` — short actionable hints.
- `DIRECT` → empty string (no block injected).

`ACSBlueprint.ldd_skills` maps each primitive to the required LDD skills.

**Must NOT do:**
- Don't call `classify()` for worker personas that can't execute WORKFLOW/DELEGATE
  (use `render_directive_block(persona=<name>)` — it returns "" for suppressed primitives).
- Don't add signal patterns that match too broadly — false-positive WORKFLOW for
  simple tasks burns multi-agent cost.

### ATO — Autonomous Task Orchestration (ADR-0164)

Builds on ACS-X. Three components:

| Component | File | What it does |
|---|---|---|
| M1 SkillForge skill | `code.task_orchestrator` (project scope) | Explains HOW to set up loops/workflows with the 4 invariants each |
| M2 ACS-X extension | `acs_classify.render_directive_block()` | Injects engineering invariants directly into the ACS directive block |
| M3 Forge tool | `code.task_intake` (session scope) | Deterministic structured plan: task_type, goal template, K_MAX, LDD skills |
| M4 Loss tracking | `bridges/shared/ato_loss.py` | EMA tracking of convergence/goal-revision/strategy-correction per task_type |

**ato_loss.py storage:** `<corvin_home>/tenants/<tid>/global/ato/loss_stats.json`
(mode 0600, atomic write, thread-safe via `_write_lock`).

**Advisory alerts** (L16 WARNING when ≥5 samples and threshold crossed):
- `task_orchestrator.convergence_low` — conv_rate < 0.60
- `task_orchestrator.goal_template_weak` — goal_revision_rate > 0.30
- `task_orchestrator.strategy_drift` — strategy_correction_rate > 0.20

**Must NOT do:**
- Don't import `anthropic` from `ato_loss.py` (CI AST lint enforces).
- Don't emit task text or goal text in audit details.
- Don't auto-tune K_MAX or goal templates from loss signals — advisory only.

## Forge plugin (layer 6) — runtime tool generation

The newest plugin `operator/forge/` lets a chat-pinned persona register
and execute schema-bound tools at runtime, sandboxed.

**What you, as Claude Code, need to know when editing:**

- Forge is OPTIONAL — voice and cowork must not hard-import it (mirror
  the existing cowork rule). The only permitted form is the
  `_se is not None` guard inside `bridges/shared/audit.py`.
- Generated tools land under `<repo>/.corvin/` — split across four
  scopes (`task`, `session`, `project`, `user`) selected by
  `bridges/shared/paths.py`. The default user-scope dir is
  `<repo>/.corvin/global/forge/`. Override the whole root via
  `CORVIN_HOME` env (legacy alias `CORVIN_HOME` still accepted until
  Phase 7). `.corvin/` is gitignored — the workspace is per-user
  and survives plugin updates.
- The forge persona must NEVER be promoted to `bypassPermissions` mode.
  `personas/forge.json` ships with `permission_mode: "default"` plus
  Bash/Edit/Write/MultiEdit on the disallowed_tools list. Operator can
  tighten further but not loosen — a chat that overrides forge to
  bypassPermissions defeats the entire sandbox.
- `policy.json` is the only place where the workflow safety envelope
  is set; chat_profiles and personas cannot widen it. They can request
  a tighter `meta.budget` per call, but the operator's `max_budget`
  clamps anything wider.
- Tool name validation accepts alnum + `.` + `_` (the dot enables
  AWP-style namespaces like `csv.count`); rejects sequences containing
  `/`, `..`, or starting / ending with `.`. This is enforced in
  `forge/registry.py::create`.
- Hot-reload of policy.json: `_handle_tools_call` and
  `_handle_tools_list` re-check the file's mtime and re-load on
  drift. Don't cache policy across calls inside other code paths;
  always go through `self.policy` which the hot-reload refreshes.

**Capability is opt-in per persona, not per permission-mode.** Every
persona with `forge_enabled: true` auto-receives `forge_tool` /
`forge_promote` via `_inject_forge_capability` in the cowork resolver —
including personas running in `permission_mode: bypassPermissions`. The
gate is symmetrical to `_inject_skill_forge_capability` (only the
`forge_enabled` flag, no `zero_config` requirement); the historical
`zero_config` constraint was a dead-flag bug for `inbox.json` and is
gone. Safety holds because the layer-10 **path-gate PreToolUse hook**
structurally blocks direct `Write` / `Edit` / `Bash` writes to forge
workspaces, regardless of permission mode. The MCP server is therefore
the only writable path, exactly as intended.

When a persona gains forge or skill-forge capability, the resolver also
appends a **capability brief** to its `append_system`. The brief is
**runtime-built per persona** — it reads the bundle policy.json's
`persona_namespaces` and `persona_sandbox_overrides`, so:

- A persona with a namespace entry gets the prefix rule
  (*"Tool name MUST start with `code.`"*); a wildcard persona (no entry,
  e.g. the `forge` persona itself) gets a "no namespace gate" note instead.
- A persona with `network: allow` (research) gets
  *"shares host network namespace — loopback + outbound HTTP/HTTPS …"*
  rather than the strict *"no network"* default.
- The brief mentions same-turn visibility (`tools/list_changed`
  notification fires after registration), and a *Discovery first*
  rule that points to `mcp__forge__forge_list` / `mcp__skill_forge__skill_list`
  before creating new artifacts.

The brief is appended idempotently (re-resolve does not duplicate it)
and only fires when the corresponding capability actually injects.

**Real-Claude verification** (opt-in): `test_persona_uses_forge_live.py`
spawns `claude -p` with the resolved coder profile + materialized MCP
config and a clear "forge me a tool" prompt, parses the stream-json
transcript, and asserts that `mcp__forge__forge_tool` was called with a
name starting with `code.`. Skipped by default; set `CLAUDE_LIVE_E2E=1`
to spend the API credits and verify that the personas don't just have
the maschinerie wired — they actually use it.

**Persona-aware sandbox.** The default sandbox is strict for every
persona (no network, no subprocess, fresh /tmp, ro /usr). Policy can
relax single axes per persona via `persona_sandbox_overrides` in
`operator/forge/forge/policy.json` or any workspace-level `policy.json`:

```jsonc
{
  "persona_sandbox_overrides": {
    "research": {"network": "allow"}
  }
}
```

Today only the `network` axis is configurable. Personas listed here have
their forged tools run with `--share-net` (host network namespace shared,
loopback + outbound, plus DNS + TLS via the bound `/etc/resolv.conf` and
SSL roots). Personas not listed keep the strict deny — their forged
tools cannot even reach 127.0.0.1. Workspace policy.json entries replace
bundle entries per persona, so an operator can flip a default-allow
persona back to deny: `{"research": {"network": "deny"}}`.

The runner reads `FORGE_PERSONA` from env (set by the cowork resolver
per chat) and consults `Policy.network_for_persona(persona)`; missing
env or persona-not-in-overrides → strict default. The `sandbox_label`
in the run manifest flips to `bwrap+net` when network was permitted, so
the audit trail makes the relaxation explicit.

Real-E2E coverage: `operator/forge/tests/test_persona_sandbox.py`
spawns a local HTTP stub, forges a `urllib.request.urlopen` tool, runs
it under `FORGE_PERSONA=research` (succeeds, body matches) and under
`FORGE_PERSONA=coder` (fails with `Connection refused`). The test
skips with a clear marker when `bwrap` is missing — without a real
namespace there is no enforcement to test.

**Interpreter binding on uv installs.** The sandbox runs the tool with
`sys.executable`, so that interpreter must be visible inside the jail.
`runner.py` binds the venv root read-only, but a **uv-managed** venv
symlinks `bin/python3` through several hops to an interpreter that lives
OUTSIDE the venv (e.g. `~/.local/share/uv/python/cpython-3.11-…` →
`cpython-3.11.15-…`). Binding only the venv root left an intermediate hop
dangling — `bwrap: execvp …/python3: No such file` — which silently broke
**every** forged tool on the default `uv`/`pip`-into-uv-venv install path.
The runner now also binds the interpreter version STORE (the parent that
holds both the short-name symlink dir and the real `cpython-X.Y.Z` dir)
read-only, guarded so it never binds a system-wide or home-root directory.
A classic `python -m venv` (whose `bin/python` resolves under `/usr`) is
unaffected. Regression coverage lands via the whole forge sandbox suite
(`test_forge.py`, `test_output_streaming.py`, `test_persona_sandbox.py`,
`test_cache.py`, `test_envelope.py`), which only executes real tools when
this binding is correct.

**Output streaming on truncation (S8).** When a forged tool's stdout
exceeds `output_cap` (default 4 MiB, policy-clamped), runner.py:
preserves the full stdout as `<run_id>/artifacts/full_stdout.bin`
before truncation, surfaces meta on the envelope —
`meta.stdout_truncated: true`, `meta.stdout_truncated_at_bytes: <cap>`,
`meta.stdout_total_bytes: <true length>`, `meta.stdout_full_artifact:
<absolute path>`. The existing `RunResult.stdout_truncated` boolean
stays as the structural flag for backward compat. The artifact write
is best-effort: a disk-full / FS error doesn't change the truncation
semantics, the caller still gets the truncated envelope plus the
flag.

Downstream consumers that need the missing bytes can read the
artifact directly via the `Read` tool (the path is absolute and the
file is in the run's artifacts dir, which the bwrap sandbox already
rw-binds for the tool itself). A future `mcp__forge__forge_chunk(run_id,
offset, length)` MCP tool can wrap the same read for clients that
prefer JSON-RPC over filesystem access.

Real-E2E: `operator/forge/tests/test_output_streaming.py` forges an
8 MiB-stdout tool with a 1 MiB cap, verifies all four meta fields,
reads the artifact, asserts byte-identity, and reads the
`[cap, 2*cap)` chunk to prove the bytes that *would have been*
truncated are recoverable. A small-output tool in the same test
confirms the strict default behaviour is unchanged.

**What you must NOT do:**

- Don't bypass the MCP server. There is no other supported path to run
  a forged tool — direct `python tools/<name>.py` invocations skip
  the static check, the policy clamp, the rate limiter, the breaker,
  and the hash-chain audit.
- Don't disable or weaken the path-gate hook
  (`operator/voice/hooks/path_gate.py`). It is the structural enforcement
  that makes "forge on every persona" safe. If you must touch it, every
  Bash vector (>, >>, tee, mv, cp, install, sed -i, dd of=, python -c
  open, rsync, eval / exec / `$(...)` fail-closed) needs a fresh E2E
  in `test_path_gate.py`.
- Don't write to `<repo>/.corvin/global/forge/policy.json` from the
  bridge or adapter code. Operator-only file. Tests must use
  `CORVIN_HOME` (or legacy `FORGE_ROOT`) to point at a tempdir if
  they need a custom policy.

## SkillForge plugin (layer 7) — runtime skill generation

The newest plugin `operator/skill-forge/` is the sister to forge: where
forge generates **executable tools** (sandboxed code), skill-forge
generates **skills** — markdown knowledge that gets prompt-injected into
sub-agents. Both share the four-scope mechanic and the hash-chain audit
log, so the same `audit-verify` command covers both plugins' lifecycle
events.

**What you, as Claude Code, need to know when editing:**

- SkillForge is OPTIONAL — voice, cowork and forge must not hard-import
  it. The MCP server is reached via the chat-pinned persona only.
- Workspaces sit alongside forge: each scope_root contains both a
  `forge/` and a `skill-forge/` directory, plus the **shared**
  `audit.jsonl` at scope_root level. SkillForge therefore writes its
  audit events ONE LEVEL UP from its own workspace
  (`<scope_root>/audit.jsonl`, NOT `<scope_root>/skill-forge/audit.jsonl`)
  so the hash-chain is unified with forge.
- Scope detection reuses `forge.scope.detect_scope()` and
  `forge.scope.scope_root()` — there is no skill-forge-specific
  detector. Tests must therefore set `CORVIN_FORCE_SCOPE` exactly
  like forge tests do.
- There is **no separate `skill-forge` persona file** anymore — the
  unified generator persona is `forge` (Tools AND Skills via
  `skill_forge_enabled: true` in `personas/forge.json`). The historical
  name `skill-forge` resolves to `forge` through the resolver's
  `_PERSONA_ALIASES` table, so existing `chat_profiles` pinning
  `persona = "skill-forge"` keep working without operator action.
  The forge persona ships `permission_mode: default` plus
  Bash/Edit/Write/MultiEdit/NotebookEdit on `disallowed_tools` —
  layer-10's path-gate hook is what keeps the sandbox structural
  regardless of permission mode.
- The linter (`skill_forge/linter.py`) is the only safety layer before
  a SKILL.md hits disk — fail-closed for prompt-injection, secrets,
  persona-boundary, and oversized bodies. Warnings (e.g. code-density >
  40 %) are logged but do not block. **Don't catch `LinterError` and
  retry "with cleanup later"** — the linter rejecting a body is the
  signal to redesign the skill, not to silence the gate.
- Promotion has stricter gates than forge: task→session needs ≥1
  positive grade, session→project needs ≥3 grades with mean≥0.5,
  project→user needs `force=True`. These gates are the LDD twist —
  promotion is the explicit "this skill survived its loss-curve" step.
- The `ungraded` cleanup mode (`scripts/skill_cleanup.py ungraded
  --ttl-days 7`) is the auto-purge for skills that never got graded —
  treat it like forge's task TTL but for the knowledge layer. User
  scope is NEVER pruned.

**What you must NOT do:**

- Don't write to scope_root's `audit.jsonl` from skill-forge code with
  a different schema or a separate hash-chain — the unified chain only
  works because both plugins go through `forge.security_events.write_event`.
  If the forge package isn't on `PYTHONPATH`, SkillForge falls back to a
  plain JSONL writer (no chain) — that fallback is for standalone tests
  only and MUST NOT be the production path.
- Don't bypass the linter. There is no allowlist of "trusted callers" —
  every body goes through `lint()` regardless of where it originated.
  The layer-10 path-gate hook keeps this guarantee intact even when a
  persona runs in `bypassPermissions`: direct `Write` / `Edit` / `Bash`
  on `<scope>/skill-forge/**` and on the slot-mirror under
  `operator/skill-forge/skills/dyn/**` is blocked, so the only write
  path is the MCP server, which itself routes everything through
  `lint()`.
- The persona-level opt-in `skill_forge_enabled: true` is the supported
  way to give a persona skill-creation ability. There is no
  `zero_config` requirement — any persona with the flag gets it (so
  `inbox`, with `zero_config: false`, can still create skills). The
  unified `forge` persona remains as an opinionated specialist for
  explicit "I want to generate" sessions, not as a load-bearing safety
  boundary.

### Engine-native skill loading via plugin-slot mirror

Every successful `SkillRegistry.create()` persists the skill **twice**:

1. **Canonical** in the scope workspace at
   `<scope_root>/skill-forge/skills/<name>/SKILL.md` — full SkillForge
   front-matter (`name`, `type`, `description`, `claim`, `references`),
   plus `meta.json` with grades and provenance. This is the
   source-of-truth and the file the registry reads back.
2. **Engine-facing slot mirror** at
   `<repo>/operator/skill-forge/skills/dyn/<sanitized>/SKILL.md` — only
   `name` + `description` in the front-matter, body verbatim. The dot in
   dotted names is replaced by underscore (`trading.score_reviews` →
   `trading_score_reviews`) because the engine prefers undottered names.
   This is a projection — extra SkillForge keys would only confuse the
   engine's plugin-skill loader.

**Why two files:** the engine discovers skills via the standard
plugin-skill convention (a `skills/<name>/SKILL.md` per registered
plugin, with a YAML `name`+`description` front-matter). By keeping the
canonical SkillForge artifacts unchanged AND mirroring a stripped
projection into the plugin tree, the next claude subprocess picks the
dynamic skill up via the `Skill` tool API — with zero plugin-API work
on our side.

**Slot-path resolution** (`registry.plugin_slot_dir()`):

1. `CORVIN_PLUGIN_SLOT_DIR` env override — the sole test-isolation signal.
   A dedicated, single-purpose variable used nowhere else in this codebase,
   so its mere presence is unambiguous; every test that exercises
   `create()`/`delete()` sets it explicitly.
2. Walk-up from `registry.py`'s location for a `.corvin_repo`/`plugins/`
   marker → `<repo>/operator/skill-forge/skills/dyn/` — the real
   production path, confirmed by `test_engine_visibility.py`'s actual
   `claude -p` subprocess run to be what the native engine loader scans.
3. Fallback `~/.corvin/plugin-slot/` (no repo marker found — e.g. a
   pip-installed wheel with no `plugins/` directory on disk).

**2026-08-02 fix:** a prior step 2 read `CORVIN_HOME` and, if set,
redirected to `<CORVIN_HOME>/plugin-slot/` — reasoning that this "kept
test sandboxes... from polluting the real plugin tree." But `CORVIN_HOME`
is the canonical runtime root set in *every* real CorvinOS session, not
only tests, so that step silently redirected every production install's
slot mirror away from the path the engine actually scans — every
freshly-created project/user-scope skill was invisible to Claude Code's
own plugin loader in production. Removed the `CORVIN_HOME` branch
entirely (every existing test already sets the more specific
`CORVIN_PLUGIN_SLOT_DIR` directly, so this changed zero test behavior).
Found via adversarial review of the Concept Gate mechanism; see
`Corvin-ADR/concepts/0001-self-learning-project-concept-archive.md`'s
Production-Readiness Roadmap, item P0-1.

**Lifecycle hooks:**

- `create()` writes the slot after the canonical workspace write
  succeeds. Slot write failures are best-effort — they never invalidate
  the canonical write.
- `delete()` purges the slot by default. `MultiSkillRegistry.promote()`
  passes `purge_slot=False` to the source-side delete because the
  target-scope `create()` already wrote the now-authoritative slot.
- The linter is unchanged — bodies that fail `lint()` never reach the
  slot, because the slot write is reached only after the canonical write
  has committed, which itself is gated on the linter.

**Gitignore:** `operator/skill-forge/skills/dyn/` is gitignored — dynamic
skills are ephemeral and never land in commits. Static plugin-shipped
skills (e.g. the `cowork` and `voice` skills) live one directory level
above the `dyn/` subtree, so they remain tracked.

**Limit — visibility is one subprocess delayed:** the engine reads
plugin skills at subprocess boot. A skill created mid-turn is therefore
visible from the **next** claude subprocess (every bridge turn spawns a
fresh one), not from the same process that called `skill_create`. This
matches the existing voice / cowork convention that settings hot-reload
between subprocess boots, not within them.

**Test isolation:** any test that exercises `SkillRegistry.create()` /
`delete()` MUST set `CORVIN_PLUGIN_SLOT_DIR` before importing —
`CORVIN_HOME` alone no longer redirects the slot (2026-08-02 fix, above),
so setting only `CORVIN_HOME` would leave the walk-up fallback writing
into the real `operator/skill-forge/skills/dyn/` and pollute the
workspace. The existing tests in `operator/skill-forge/tests/` set
`CORVIN_PLUGIN_SLOT_DIR` at module load via
`tempfile.mkdtemp(prefix="sf-slot-test-")`.

### Adapter-injection layer (live skill availability)

A skill in SkillForge is now reachable through **three** parallel paths,
each with a different latency / mechanism:

1. **Canonical workspace** — `<scope_root>/skill-forge/skills/<name>/SKILL.md`
   plus `meta.json`. Source of truth for grade / promote / purge.
2. **Plugin-slot mirror** — `operator/skill-forge/skills/dyn/<sanitized>/SKILL.md`.
   Engine-discoverable via the standard plugin-skill loader, but the engine
   caches the plugin list at subprocess boot — visible only on the **next**
   claude subprocess.
3. **Adapter-injection** — the bridge adapter merges the active skills into
   the claude subprocess' `--append-system-prompt` per inbox-message, so
   the worker has the skill knowledge **on the very next bridge turn**.
   Implemented in `operator/bridges/shared/skill_inject.py`; voice
   imports it via `try: import skill_inject` and stays usable when the
   module is absent (mirrors the cowork pattern).

**Default filter:** only skills with at least one grade and `mean_score > 0`
are eligible. Sorted by `mean_score` desc, then `created_at` desc. Capped
at 5 by default.

**Profile flags** (live in `chat_profile` or in a persona JSON; the cowork
resolver passes them through):

| Flag                   | Default | Effect                                    |
|------------------------|---------|-------------------------------------------|
| `inject_skills`        | true    | set `false` to suppress the block         |
| `inject_ungraded`      | false   | set `true` to lift the grade gate         |
| `max_injected_skills`  | 5       | cap on how many skills land in the prompt |

**Personas that opt out:** `forge` and `skill-forge` ship with
`inject_skills: false` — they have dedicated MCP tools for managing
forged tools / skills, so dragging skill bodies into their prompt is
ballast.

**Hot-reload:** the adapter calls `collect_active_skills()` per inbox
message. There is no caching — a skill created mid-session via
`mcp__skill_forge__skill_create` followed by a grade is picked up on
the next bridge turn without restart.

**Auto-grade after bridge turn (S7):** after every successful bridge
turn the adapter calls `skill_inject.auto_grade_from_output(...)`,
which scans the LLM's reply for either a name variant of an active
skill (underscore / hyphen / spaced) or the first 80 characters of its
body. Each match writes a grade with score 0.7 and notes
`"auto-grade ({name|body} match) turn=<msg_id>"` into the skill's
`meta.json`. Best-effort: failures log but never break the turn. The
same `profile.inject_skills=false` flag that opts out of injection also
opts out of auto-grade — a chat that doesn't see skills doesn't
generate grades for them either. This closes the lifecycle gap that
otherwise lets ungraded session-skills get TTL-purged after 7 days
even when they were genuinely useful.

**Test isolation:** `test_adapter_skill_inject.py` runs with
`CORVIN_HOME` and `CORVIN_PLUGIN_SLOT_DIR` redirected to a tempdir
per case. Each case spawns the adapter under `ADAPTER_FAKE_CLAUDE=1`
and asserts against the dumped `--append-system-prompt`. The opt-in
`test_engine_visibility_inject.py` (set `SKILL_FORGE_ENGINE_E2E=1`) is
the live confirmation: it spawns a real `claude -p` with the
constructed block and checks for a magic string in stdout.

**Outcome-grounded grading (Phase 1, layer 15):** auto-grade tells
whether a skill was *used* (mention / paraphrase) but cannot tell
whether the use *helped*. The Phase-1 extension closes that gap: when
the next user turn carries an approval / rejection / rephrase signal,
the skills active in the previous turn receive an absolute outcome
grade. The registry validates `score ∈ [0.0, 1.0]`, so signals map to
absolute targets, not deltas:

| Signal     | Target | Mean with auto-grade (0.3) | Promotion gate (>0.5) |
|------------|--------|----------------------------|-----------------------|
| approval   | 0.9    | 0.6                        | eligible              |
| rejection  | 0.1    | 0.2                        | blocked               |
| rephrase   | 0.3    | 0.3                        | blocked, soft hint    |

Precedence is rejection > approval > rephrase, so "thanks but actually
wrong" lands as rejection. Detection is purely substring-based against
two curated phrase lists in `skill_inject._OUTCOME_APPROVAL_PHRASES` /
`_OUTCOME_REJECTION_PHRASES` (German + English); rephrase uses
`difflib.SequenceMatcher.ratio() ≥ 0.6` against the previous user
text.

**State machine:** after a bridge turn auto-grades any skills, the
adapter records `_last_turn_skills[chat_key] = {run_id, skills,
user_text, ts}`. The next user turn pops that snapshot via
`_pop_last_turn_skills()` (one-shot consumer; TTL = 30 min via
`ADAPTER_OUTCOME_SNAPSHOT_TTL`) and calls
`skill_inject.grade_from_user_followup(...)` BEFORE invoking claude.
Every detected signal writes one grade per prev-turn skill into
`meta.json` with notes `"outcome ({signal}) prev_run=<msg_id>"` and
emits a `skill.outcome_graded` audit event into the unified hash
chain.

**Profile opt-outs:** `profile.outcome_grading: false` disables
outcome grading without disabling auto-grade or injection.
`profile.inject_skills: false` disables all three (parity with
auto-grade). Forge / skill-forge personas inherit the existing
`inject_skills: false` and therefore see neither injection nor
auto-grade nor outcome-grading.

**Snapshot hygiene — `/reset`, `/cancel`, and the periodic sweep:**
The prev-turn snapshot is per-chat session state and MUST be cleared
when the session resets, the running task is cancelled, or the chat
falls silent for too long. Three integration sites enforce this:

- `/new` / `/clear` / `/reset` — `process_one` calls
  `_pop_last_turn_skills(chat_key)` after wiping the on-disk
  conversation state. After a reset the next user message belongs to a
  fresh task; an approval/rejection signal must NOT silently grade
  skills from the abandoned conversation.
- `/stop` / `/cancel` — same pop, after `_cancel_chat()` SIGTERMs the
  running claude subprocess (WA-10: or calls `.cancel()` on a registered
  subprocess-less engine — Hermes/OpenCode/Codex have no Popen to kill).
  The user is moving on; a follow-up "danke" must not retroactively grade
  the cancelled turn's skills.
- Periodic sweep — `_cleanup_last_turn_skills()` runs alongside
  `_cleanup_in_flight()` and `_cleanup_chat_locks()` every
  `CLEANUP_INTERVAL` seconds (default 300 s) and drops snapshots whose
  `ts` is older than `OUTCOME_SNAPSHOT_TTL` (default 30 min). Without
  this, a chat that auto-graded once and then never came back would
  leave the snapshot in memory forever — `_pop_last_turn_skills`
  filters stale entries on access, but only the periodic sweep keeps
  the dict bounded.

**Per-subtask E2E (load-bearing):**
`test_skill_outcome_grading.py` covers the full path — pure-function
detection in 20+ phrase cases (German + English + precedence + edge
cases), `grade_from_user_followup` against a real `MultiSkillRegistry`
with sandboxed `CORVIN_HOME`, an in-process adapter E2E that seeds
`_last_turn_skills`, calls `process_one()` with an approval text, and
asserts the grade landed on disk with the correct score and notes,
the snapshot was consumed, and the outbox envelope was written, plus
two hygiene sections: `/reset` + `/cancel` clear the snapshot through
`process_one`, and the periodic `_cleanup_last_turn_skills()` reaps
backdated entries while keeping fresh ones intact. Wired into
`run-all-tests.sh` next to `test_skill_auto_grade.py`.

**What you, as Claude Code, must NOT do:**

- Don't widen the score range. The registry validates `[0.0, 1.0]`;
  treating outcome signals as signed deltas would break that contract
  AND require coordination with every other consumer of `mean_score`.
- Don't move the outcome-grading call AFTER `call_claude_streaming`.
  The whole point is to apply the prev-turn signal BEFORE the next
  turn runs — reordering would let the signal lag by one turn and
  pollute the next response with stale state.
- Don't make the snapshot multi-shot. Outcome grading consumes the
  prev-turn snapshot exactly once. A user follow-up that says nothing
  about the prev turn (random new question) still invalidates the
  snapshot — otherwise an "unrelated" turn would let an approval six
  turns later silently grade a long-stale skill set.
- Don't add new approval / rejection phrases without also extending
  the test phrase list. The detection is curated, not stemming-based,
  and false positives ("danke schön" inside a longer skeptical
  message) need explicit test coverage when added.

---

## MCP Plugin Manager (ADR-0096) — user-installable external MCP tools

**Status:** Implemented (M1–M4 complete).  
**Module:** `operator/mcp_manager/`  
**CLI:** `corvin-mcp install|activate|deactivate|list|show|remove|update|search|secrets`

The MCP Plugin Manager lets users install and activate external MCP servers
(from npm, pip, GitHub, Docker, or local paths) without operator JSON edits or
adapter restarts. It follows the same security stack as forge and skill-forge.

### Storage layout

```
~/.corvin/tenants/<tid>/global/mcp-tools/
├── catalog.json           # installed tools + SHA256 pins (file-locked)
├── active.json            # user + tenant scope activations (hot-reloaded)
└── installs/
    ├── <tool-id>/         # GitHub/Docker extracted artifact
    └── <tool-id>.tar.gz   # pinned GitHub tarball
```

Session-scope activations (ephemeral):
```
~/.corvin/tenants/<tid>/sessions/<bridge>:<chat>/mcp-session-active.json
```

Project-scope activations (in the project working directory):
```
<project_dir>/.corvin/mcp-active.json
```

### Activation scopes

| Scope | Storage | Lifecycle |
|---|---|---|
| `session` | `sessions/<key>/mcp-session-active.json` | Ephemeral — cleared by `/new /clear /reset` |
| `project` | `<project_dir>/.corvin/mcp-active.json` | Persists with project directory |
| `user` | global `active.json` (user key) | Persists until explicit deactivate |
| `tenant` | global `active.json` (tenant key) | Operator CLI only — not via Discord |

Merge order at spawn: tenant → user → project → session.  
The persona's `mcp_plugins_allowed` list restricts which catalog tools are
injected at spawn time; absent/null means no restriction.

### Installation sources

| Source | Example | Pinning |
|---|---|---|
| `npm:pkg@ver` | `npm:@modelcontextprotocol/server-brave-search@0.6.2` | npm lockfile |
| `pip:pkg@ver` | `pip:mcp-server-sqlite@1.0.0` | version pin |
| `github:o/r@tag` | `github:anthropics/mcp-sqlite@v1.2.3` | SHA256 of tarball |
| `docker:image:tag` | `docker:ghcr.io/owner/tool:v1` | image repo digest |
| `local:./path` | `local:~/my-tools/mcp-weather` | dev-only, no pinning |

Branch-head GitHub installs require `--allow-unpin` (supply-chain protection).

### Compliance integration

Every security layer is enforced:

| Layer | Mechanism |
|---|---|
| **L10 Path-Gate** | `mcp-tools/` and `mcp_manager/` are protected paths — no LLM-directed writes |
| **L16 Audit** | `mcp_plugin.installed/activated/deactivated/removed/spawn_blocked` events, hash-chained |
| **L34 Data Classification** | `compliance.locality` checked at activation time; fail-closed |
| **L35 Egress Gate** | Declared hosts in `compliance.hosts` checked against tenant EgressGate |
| **Vault** | Secrets injected as `${VAR}` templates at spawn via bwrap env; values never in catalog |
| **SHA256 / Docker digest** | GitHub tarball and Docker image digests verified on every spawn |

Fail-closed reasons for `mcp_plugin.spawn_blocked`:
`missing_secret`, `sha_mismatch`, `l34_locality`, `l35_egress`, `docker_digest_mismatch`.

### Adapter integration

`adapter._resolve_spawn_inputs()` calls:
```python
_mcp_manager_activate.get_active_mcp_servers(
    tid, session_key=chat_key, project_dir=os.environ.get("CORVIN_PROJECT_DIR")
)
```

The result is merged into `mcp_servers` **before** the persona JSON (persona wins on key conflict).

### Bundled manifest library

`operator/mcp_manager/mcp_manager/builtin_manifests/` contains curated manifests
for well-known tools: `brave-search`, `filesystem`, `github`, `sqlite`, `fetch`.
Use `corvin-mcp search <query>` to discover them.

### Session reset integration

`session_reset.py` calls `clear_session_scope(tid, session_key)` after writing
the `session.reset` audit event. This removes the ephemeral session activations
file. The call is best-effort (silent fallback if mcp_manager is absent).

### Must NOT do

- Auto-activate a tool on install — install and activation are always two explicit steps.
- Store secret values in `catalog.json` or any committed file.
- Allow `local:` source for tenant scope (dev-only, cannot be multi-user pinned).
- Skip SHA256 / Docker digest verification on spawn (mandatory per spawn).
- Make `mcp_plugin.spawn_blocked` advisory — it blocks the spawn or it is broken.
- Let a persona bypass `mcp_plugins_allowed` via `append_system`.
- Use `import anthropic` in any `operator/mcp_manager/` module (CI AST lint enforces).


---

## Console Web Surface Plugin (ADR-0356)

**Status:** Declared (P2.5). Dynamically loaded by plugin loader (P7, unbuilt).
**Module:** `core/plugins/corvin_plugins/console/plugin.py`
**Feature flag:** `console_web_surface_plugin` (ships dark, default OFF)

The CorvinOS Console is declared as a `web_surface` plugin—an explicit, loadable
component rather than hard-wired into the ASGI app. This prepares the Console to
be replaceable: a FrontendForge-built custom panel set or a third-party surface
can eventually take its place through the plugin loader (P7, unbuilt).

**What it does and does NOT do:**

- **Declares:** The Console SPA: `plugin_type='web_surface'`, `boot_layer='bundled'`
  (disableable, not a compliance mechanism), `origin='builtin'`, `mount_path='/console/'`.
- **Reports:** WHERE it mounts (`/console/`) and WHAT it serves (the built SPA in
  `web-next/dist/`, or None if unbuilt).
- **Does NOT:** start a server or mount anything itself. The ASGI app (in
  `standalone.py`) still performs the actual mount today. Wiring the mount
  *through* this plugin is the loader's job (P7, future).

**Integration:**

- **Ship-dark flag:** `FLAG_ID = "console_web_surface_plugin"` (default OFF).
  Mirrors `bridges/supervisor.py`'s own ship-dark approach — declaring the
  Console as a plugin must not change a default install, which still mounts
  the SPA the old hard-wired way until the loader (P7) mounts it through here.
- **on_load():** Records the `PluginContext` so `health_check()` can report
  against the Console. Deliberately side-effect-free — declaring the plugin
  never changes how the already-running Console is served.
- **Capabilities:** The Console publishes its mount path and SPA dist location
  through `capabilities.py::_loaded_web_surfaces()` so the registry knows what
  surfaces are active (ADR-0365 P7).

**When editing:**

- **Declare a new web_surface plugin:** Copy `core/plugins/corvin_plugins/console/`
  and change `plugin_id`, `display_name`, `mount_path`, and `spa_dist_dir()`.
- **Register a feature flag:** Add an entry to `core/console/corvin_core/feature_flags.py`
  with `id="<plugin_id>_web_surface_plugin"` (e.g., `"custom_panel_web_surface_plugin"`).
- **Don't:** hard-wire a new surface into `standalone.py` — declare it as a plugin
  and let the loader (P7) mount it.

---

## Plugin registry — `corvin_plugins` (ADR-0030 + ADR-0033 + ADR-0233)

**Status:** Implemented. This is the ONE lifecycle contract for extensions.
**Module:** `core/plugins/corvin_plugins/` · **Templates:** `core/plugins/templates/` (9)

ADR-0233 consolidated the plugin work here after an audit found five different
things called "the plugin system". The retired `core/orchestration/plugin_system/`
prototype (unwired, `api.py` not importable, three 0-byte modules, 22
`pytest.skip`s standing in for tests) is gone; its data model was salvaged into
`manifest.py`.

### Module map

| File | Role |
|---|---|
| `protocol.py` | `CorvinPlugin` (on_load / on_unload / health_check), `PluginContext`, `HealthStatus`, the ADR-0033 provider protocols, plus `AuditBackend` + `UserBackend` (ADR-0233). `KNOWN_PLUGIN_TYPES` is the single taxonomy. |
| `registry.py` | Runtime registration + `health_check_all()` (now breaker-aware) |
| `loader.py` | Discovery helpers: `corvin.plugins` entry points or explicit `class_path`. `discover_and_load()` **is** called — by `bootstrap.py::bootstrap_declared` for the ADR-0030 `spec.plugins.installed` path. (It had no caller until that landed; an earlier revision of this file still said so, and contradicted its own § "Two load paths" further down.) |
| `manifest.py` | `PluginRecord`, `BootLayer` (ADR-0243), `PluginDependency`, `DependencyResolver`, `SettingsValidator`, `plan_settings_migration` |
| `state.py` | Per-tenant `registry.yaml` + `PluginLifecycle` (install/enable/settings/disable/uninstall) |
| `circuit_breaker.py` | Per-`plugin_id` breaker: closed → open → half-open |
| `providers/` | One active provider per type: notification, recall, summary, router, **audit**, **user**, **stt**, **data_connector** |
| `bootstrap.py` | Boot wiring: `boot_platform()` (the one sequence BOTH hosts call) = `assert_compliance()` (fail-closed tripwires) → `bootstrap_all()` → `assert_post_boot()`; plus `bootstrap_global()` (bundled compliance/core plugins, ADR-0243), `bootstrap_declared()`, `bootstrap_tenant()` (the three precedence-ordered loaders) and `build_context()` |
| `health.py` | `HealthCollector` (interval polling, flag-gated) + `render_prometheus()` |
| `healing.py` | `HealingOrchestrator` — Stage 3, **ships dark** behind `plugin_self_healing` |

### Vocabulary — `boot_layer` vs `tier` vs `origin` (ADR-0233 D7, ADR-0243)

Three orthogonal axes. They answer three different questions and none of them is
a synonym for another:

| Axis | Question it answers | Values | Defined in |
|---|---|---|---|
| `boot_layer` | When is it loaded, and may it be switched off? | `compliance` · `core` · `bundled` · `installed` | ADR-0243, `manifest.py::BootLayer` |
| `tier` | What is it allowed to do, and what does the license gate? | Tier A/B/C | ADR-0156 |
| `origin` | Where did it come from? | `builtin` · `vetted` · `community` | ADR-0233 D7, `manifest.py::PluginOrigin` |

"Tier A/B/C" means **ADR-0156's capability boundary + license gate**, repo-wide.
Three different Tier A/B/C meanings existed before that rule; do not reintroduce
one. The boot-layer axis added in ADR-0243 is deliberately **not** called `tier`
for exactly this reason — the draft ADRs spelled it `tier_0 / tier_1_core /
tier_2_bundled`, which would have been the fourth meaning of the same word.

#### Why `boot_layer` and not plain `layer` (rename, 2026-07-27)

The axis shipped briefly as `layer` / `PluginLayer`, and that was the **same collision
class** the move off "tier" existed to prevent — only worse, because "layer" was already
carrying **four** meanings in this repo:

| Existing meaning | Where |
|---|---|
| the L1–L44 security/compliance layer stack | CLAUDE.md § Layer Stack Overview, `docs/claude-ref/layer-*.md` |
| ADR-0124 **audit layers** | `core/console/corvin_console/routes/audit_layers.py` |
| the ADR-0142 **layer-extension API**, which answers 403 `reason="core_layer_immutable"` | `core/console/corvin_console/routes/extensions.py` |
| **quality layers** | `routes/quality_layers.py`, `operator/bridges/shared/quality_layers.py` |

So the axis is **`boot_layer`**, enum **`BootLayer`**. Values unchanged:
`compliance` | `core` | `bundled` | `installed`. Renamed surface — all of it live in code:

- registry: `boot_layer_of()`, `plugins_by_boot_layer()`, `register(..., boot_layer=)`,
  `replace(..., boot_layer=)`
- bootstrap: `_declared_boot_layer()`, `register_global_plugin(class_path, boot_layer=)`
- manifest / config: `PluginRecord.boot_layer`, JSON+YAML key `boot_layer`
- audit: `plugin.boot_layer_rejected` (was `plugin.layer_rejected`), detail keys
  `boot_layer` / `declared_boot_layer`
- admin API: response field `boot_layer`, health aggregate `by_boot_layer`

One thing deliberately **not** renamed: the audit `reason` slug `compliance-layer` in
`routes/admin.py`. The chain is append-only, and revising the vocabulary of events already
written to it is not possible.

Three orthogonal axes remain: **`boot_layer`** (load order + disableability) ·
**`tier`** (ADR-0156 capability boundary) · **`origin`** (provenance).

### Boot layers — load order and the two trust boundaries (ADR-0240, ADR-0243)

```
boot
 ├─ global scope   (from the wheel, every tenant)          ← EMPTY on every install
 │   ├─ boot_layer=compliance  → load failure ABORTS the boot   (0 instances)
 │   └─ boot_layer=core        → load failure degrades + audits (0 instances)
 └─ tenant scope   (operator-writable, per tenant)
     ├─ declarative: spec.plugins.installed in tenant.corvin.yaml
     └─ runtime:     registry.yaml, gated on `plugin_runtime_lifecycle`
```

**Status: mechanism present, zero instances above `bundled`.** `_GLOBAL_SPECS` is empty,
`register_global_plugin()` has no production caller, `bootstrap_global()` returns `[]`,
and no plugin anywhere claims `boot_layer=compliance` or `boot_layer=core`. Everything in
this section is implemented and tested; only property 1 below has ever run on a real
install. Guard tests in
`core/plugins/tests/test_layered_boot.py::TestTheTopOfTheAxisHasNoProductionInstance`
pin that statement so it fails loudly instead of aging into a false claim.

Two properties carry the compliance weight:

1. **A tenant may not claim a privileged boot layer.** `bootstrap.py::_declared_boot_layer`
   accepts only `bundled` and `installed` from tenant scope; a config or
   registry record claiming `compliance`/`core` is **downgraded to `installed`
   and audited** (`plugin.boot_layer_rejected`), never honoured — `state.py` applies the
   same downgrade on the `registry.yaml` path. Without that, any operator-writable YAML
   could mint a plugin that is undisableable and loads before everything else.
   `PluginRecord.__post_init__` adds a second, independent gate: `origin=community` may
   not claim a privileged boot layer at all. **This one is live** — it is the guard that
   lets the two privileged boot layers stay empty without being unsafe.
2. **The compliance boot layer has no off switch.** `registry.disable()` and
   `registry.unregister(..., operator_initiated=True)` raise
   `PluginDisableRefused` for it. The `operator_initiated` distinction exists so
   shutdown and hot-reload can still unload everything, while an admin route
   cannot reach past `disable()` to the primitive. **Mechanism only** — there is nothing
   on that boot layer to refuse, so this has never fired.

Global plugins are contributed **from code only** —
`bootstrap.register_global_plugin(class_path, boot_layer=...)`, called by a module that
ships in this wheel. There is **no entry-point discovery**: a `corvin.global_plugins`
group was implemented and removed before it had a single user, because any third-party
wheel on the machine could publish `compliance:whatever` and be loaded first,
undisableable, with no `PluginRecord` (and therefore past the privileged-boot-layer gate,
the consent prompt and the L34/L35 fields), and could abort the boot permanently by
raising in `__init__`. `bootstrap.GLOBAL_ENTRY_POINT_GROUP` is `None`; re-adding discovery
needs signature verification plus an allowlist, not an entry-point name.

`bootstrap_global()` is **not** behind a feature flag, deliberately: the boot layer it
loads is the compliance boot layer, and CLAUDE.md forbids a switch on those. With no
global plugins registered it is a no-op, so the flagless path currently changes nothing
anywhere.

### Replacement — only the `core` boot layer (ADR-0237)

`registry.replace(plugin, ctx, replaces="<plugin_id>")` swaps a bundled
reference implementation for an alternative. It refuses anything that is not
`boot_layer=core`: compliance is not pluggable, and bundled/installed plugins are
disabled and uninstalled rather than replaced. A record declares its intent with
`PluginRecord.replaces`.

**Structurally unreachable today.** No plugin is on the `core` boot layer, so there is no
legal target and every call raises `PluginReplacementRefused` before doing anything. The
rule is tested; it has never run against a real target. This is pinned by the same guard
test class as above.

The swap is **not atomic and does not roll back**: the old plugin's
`on_unload()` has already run when the new one's `on_load()` fails, so restoring
it would hand callers a torn-down object. The documented outcome is an empty
slot plus an audit record — the operator re-enables the default explicitly.

### Extension points — all four are wired (ADR-0237 Phase 3, ADR-0251)

`extension_points.py` defines four named points behind `plugin_extension_points`
(default off). As of 2026-07-27:

| Point | Call site | What a hook may do | Fail-closed |
|---|---|---|---|
| `engine.engine_selection` | `shared/delegation_policy.py::resolve_worker_engine` | confirm the bundled route or de-escalate to `native` | no |
| `delegation.route_selection_policy` | `shared/delegation_policy.py::resolve_delegation_route` | suppress delegation — never cause it | no |
| `engine.model_selection` | `shared/model_selector.py::resolve_step_model` | name any model in the engine's registry | no |
| `workflow.workflow_gate` | `routes/workflows.py::_stream_run` | deny a run — never permit one the core refused | **yes** |

`test_extension_point_call_sites.py` holds the record in both directions:
`_WIRED_POINTS` (all four today), `_UNWIRED_POINTS` (empty), a partition test so
a point cannot fall out of both, and an assertion per set. The reverse one is the
useful half now: a point that **loses** its call site fails the suite, because
that regression looks identical to a point that was never wired.

**What the wired point enforces (ADR-0251 D2).** A hook is an input to the
bundled rule, never a replacement for it. `resolve_worker_engine` runs the pure
`worker_engine_target` first and then admits only two answers from a hook: the
bundled one (confirm) or `native` (de-escalate). A hook may **never escalate** —
not to an engine the operator did not select, and not back to the operator's own
`mode` over an availability degrade the hook cannot observe. Refusals are
audited as `plugin.extension_engine_refused` with the operator mode and the
rejected engine id. The refusal lives at the call site, not in the bus: the bus
knows a hook returned a `str`, only `delegation_policy` knows which strings are
engines.

**Return values (D3), everywhere.** `None` is abstention on every point
including the fail-closed one — the default runs. A wrong TYPE is a defect, not
an abstention, and is handled exactly like a raising hook: the default on an
ordinary point, `ExtensionPointDenied` on the gate. Only the type NAME is
audited, never the value.

**Latency (D5).** Each point carries a soft budget; an overrun is logged and
audited (`plugin.extension_hook_slow`, once per tenant/point/plugin) with the
elapsed time. It is **not** enforced — a synchronous in-process hook cannot be
interrupted without a thread or subprocess, and claiming otherwise would be
ADR-0249's manifest-as-sandbox mistake in a different costume.

Names that may never get a point are in `_NEVER_EXTENSIBLE` and are refused with
`ImmutableExtensionPoint` — a distinct error from "unknown point", so the attempt reads as
"this may never have one" rather than "you misspelled it": the audit hash chain and write,
Ed25519 / A2A attestation verification, TDE token accounting, the consent gate, the L44
house-rules gate, the L10 path gate, the L34 flow guard, the bot-disclosure card, and the
L36 erasure orchestrator.

The **eight provider registries** in `providers/` are a separate, older mechanism and are
NOT reached through the bus — those are live. See `docs/EXTENSIBLE_CORE_PLUGINS.md` §3.

### Bridge supervisors — the boot path declares the bundled seven (ADR-0238 Phase 5)

`bridges/supervisor.py` ships `BridgeSupervisorPlugin` plus seven subclasses
(`DiscordBridgePlugin` … `TeamsBridgePlugin`), each with
`plugin_id = f"{channel}-bridge"` and `plugin_type = "bridge_channel"`, landing on
`boot_layer=bundled`. Process management is delegated to
`operator/bridges/bridge_manager.py` — `channel_daemon_running()`,
`adapter_running_pid()`, `start_channel_detached()` — never reimplemented.

**Since 2026-07-27** `bootstrap._bundled_bridge_declarations()` injects the seven
declarations from `bridges/registry_entries.py` when `bridge_supervisor_plugins`
is on. Before that the flag was one of two switches and nothing in the shipped
tree wrote the other, so no supervisor loaded on any install — the generator, the
classes and the start gate were all complete and unreached.

The boot declares them rather than the operator because `bundled` means "ships
with CorvinOS, opt-out per tenant": a dotted class path in `tenant.corvin.yaml`
is the `installed` contract, and it goes stale on the first rename.

- **The operator's entry wins.** A channel already in `spec.plugins.installed` is
  skipped, so `{id: discord-bridge, config: {enabled: false}}` parks it and an
  explicit `class_path` overrides the bundled one.
- **Off is total.** Flag off ⇒ nothing injected, nothing instantiated. The
  supervisor re-checks the same flag in its own gate; that one is load-bearing,
  this one keeps a default install from constructing seven no-ops.
- **Declaring is not starting.** Whether a daemon runs is still the
  six-condition gate: flag on, not parked, credentials present
  (`channel_configured`), no duplicate already running, runtime provisioned,
  Node ≥20 present. No automatic restart, and `confident=False` means the
  supervisor does **not** start.

### The perimeter is attribution, not security (load-bearing)

**An in-process plugin is part of the process.** Everything the registry, the
extension bus and the provider registries do about "which plugin is this" is an
**attribution** layer — it makes honest plugins behave correctly and makes their
actions traceable. It is **not** a security boundary against a hostile plugin,
and it cannot be made into one.

This was established the expensive way: five adversarial rounds, each of which
broke the previous round's identity guard.

| Round | "Who is this" was derived from | Broken by |
|---|---|---|
| 3 | the object in the provider slot | a plugin that installs a helper object |
| 4 | a `plugin_id` parameter | passing someone else's id |
| 5 | `loading.current()` (a ContextVar) | `with loading.loading("victim", …)` — one line; the setter is public. Also: `threading.Thread` does not inherit ContextVars, `asyncio.create_task` copies them and outlives the unload, and `copy_context().run()` manipulates them freely |

There is no sixth answer. In CPython every property of a caller is settable by
that caller: a stack walk can be faked with a trampoline, a module allowlist
with `sys.modules` patching, and any in-process guard is an object the attacker
can reach. Do not add another derivation — it will be broken the same way, and
the effort is better spent below.

**What follows from this:**

* **Anything that must hold against a hostile plugin belongs in a subprocess.**
  That is the direction the headless-core work already takes (ADR-0241, ADR-0238
  bridge supervisors); it is the only mechanism here that is not in the
  attacker's address space.
* **The one place an in-process guard is worth writing is the boot tripwire**
  (`core/compliance/corvin_compliance_reports/tripwire.py`): it is
  non-overridable by ADR-0232/0233 and runs before any plugin code.
* **Say "attributed", not "verified".** The audit field `tenant_check` records
  `attributed` / `unattributed`, because the hash-chained GDPR Art. 30 record is
  permanent and must not claim a verification that did not happen.
* Keep the correctness guards anyway. They stop honest plugins from breaking
  each other, which is most of the value and all of the everyday failures.

### Known limit — provider slots are process-wide, not tenant-scoped

The control plane is tenant-separated: the admin API only shows and mutates
plugins belonging to the caller's tenant, `_deactivate()` refuses to unload an
instance another tenant owns, and `_activate()` refuses to claim one.

**The data plane is not.** Each of the eight provider registries holds ONE
active provider per process (module-level `_registry`). Consequences, all
current behaviour rather than bugs to be fixed casually:

- an `audit_backend` installed by tenant A receives a copy of **every** tenant's
  audit events — `audit_event()` calls one process-wide sink with whatever
  `tenant_id` the event carries;
- the same holds for `user_backend` (authenticates for all tenants),
  `recall_backend` (all tenants' turns land in the path A configured) and
  `router_backend`.

On a single-operator install — the default, and what `_default` means — this is
invisible, because there is one tenant. It becomes a data-protection question
the moment a second tenant exists with a different operator, and it is not
something a per-call-site patch can fix: it needs the registries to be keyed by
tenant, which changes the ADR-0033 provider contract.

#### Enforced since 2026-07-27 (ADR-0250 D1)

The line above used to end with an instruction — *"do not install a third-party
provider plugin on a multi-tenant install"* — which nothing enforced while the
admin API accepted the operation. It is now a refusal.

`corvin_plugins/tenant_scope.py` refuses a provider-type plugin that is not
`origin=builtin` when the install has more than one tenant. It runs in
`bootstrap._register_instance()`, the one point BOTH load paths pass through, and
it runs **before** `on_load()` — so the slot is never taken, not taken-and-freed.

| Case | Outcome |
|---|---|
| Single tenant (the default install) | allowed — unchanged behaviour |
| Plugin type that takes no provider slot | allowed |
| `origin=builtin` (shipped in the wheel) | allowed |
| `origin=vetted` on a multi-tenant install | **refused** — a signature attests who wrote it, not that it is tenant-aware |
| `origin=community` or unknown, multi-tenant | **refused** |
| Tenant set cannot be enumerated | **refused** — "could not check" is not "one tenant" |

The refusal is audited as `plugin.provider_slot_refused` with the tenant *count*
and never the other tenants' ids. It carries **no feature flag**: a `false` would
be an operator switch re-enabling a cross-tenant data path.

The declarative `spec.plugins.installed` path supplies no `origin`, so it is
treated as not-builtin. That is deliberate — writing a class path into a tenant
config is an explicit opt-in for *that* tenant and says nothing about the others
whose data the slot would reach.

Keying the registries by tenant (ADR-0250 D2) is still the real fix and is still
a separate change. When it lands, this refusal stops firing on its own and stays
as the backstop for the next registry added without keying.

### Additive backends — extension never replaces core (ADR-0233 D4)

`audit_backend` and `user_backend` are the first two backends for *mandatory* core
mechanisms, and they are deliberately **additive only**:

- **Audit.** Core writes every event to its own hash-chained `audit.jsonl` first
  and unconditionally. `audit.py::audit_event` then calls
  `providers.audit_backend.fanout(...)` — *after* the core write has committed —
  so a plugin sees a copy and can forward it to Postgres/S3/a SIEM. A backend
  cannot suppress, rewrite, reorder or delay a compliance record. `fanout()`
  never raises into the caller; failures are logged (exception class only) and
  the breaker stops calling a dead sink.
- **Users.** `providers.user_backend` has **no default backend**:
  `get_active()` returns `None` meaning "core auth is responsible". Its
  `authenticate()` collapses exception, timeout, non-dict and missing `user_id`
  into `None` = **deny**, and strips secret-shaped keys from the principal.
  A rejected credential counts as a *working* backend — only infrastructure
  failures may open its breaker, otherwise three wrong passwords would lock out
  every user.
  **None of this is reached (verified 2026-07-27).** `get_active()` is never
  called for `user_backend`, because CorvinOS has no credential auth path: the
  only live login is localhost-only and credential-less (the TCP peer is the
  authorisation), `console_api.py`'s `/auth/login` is dead demo code imported by
  nothing, and OIDC is unbuilt. Do not "fix" this by consulting the backend from
  local-login — it would be handed empty credentials, a correct backend rejects
  those, and deny on the only login path locks the operator out. The semantics
  above bind the first credential login that gets built. See
  `docs/implementation/PLUGIN_SYSTEM_ACTIVATION_PLAN.md` Stage 2.

### Boot tripwire (ADR-0233 D5)

`core/compliance/corvin_compliance_reports/tripwire.py::assert_all()` fails the
boot closed when a mandatory mechanism is unavailable. **One tripwire per mandatory
mechanism of ADR-0232**, seven in total:

| Layer | Tripwire | Fails when |
|---|---|---|
| L16 | `audit_writer_reachable` | audit dir not writable |
| L16 | `audit_chain_intact` | existing chain does not verify |
| L16 | `core_audit_owns_the_trail` | audit provider grew a trail-owning API |
| L18 | `consent_gate_denies_by_default` | `is_granted` admits an unknown uid, or has no TTL cap |
| L34 | `flow_guard_present` | `DataFlowGuard` / `DataFlowDenied` missing |
| L44 | `house_rules_gate_intact` | policy integrity hash fails |
| L36 | `erasure_orchestrator_present` | subject-id validator accepts empty |

They are deliberately cheap — no model call, no network. `consent_gate_denies_by_default`
unpacks `is_granted`'s `(granted, reason)` tuple: a truthiness test on the tuple is
always true, which would fail every boot. **A fail-closed check with inverted logic
is a denial of service, not a safety net** — that mistake was made and caught here.
There is **no override** — no env var, no config key, no flag. A test or dev box
redirects `VOICE_AUDIT_PATH` instead, which leaves the tripwire fully armed.

### Hot-reload — enable/disable take effect immediately (ADR-0124 Inv. 6)

`enable()` loads and registers the plugin in the same call; `disable()` unregisters
it and clears its provider slot. Previously both only wrote a flag, so the Console
showed a plugin as on while it stayed inert until the next process boot — a silent
false display. Consequences worth knowing:

- A failed `on_load()` **rolls the enable back on disk** and raises. The registry
  never claims an active plugin that isn't running (audit: `plugin.enable_failed`).
- A record without `class_path` enables without loading — legitimate for an entry
  point already loaded at boot. The audit event carries `activated: false`.
- `bootstrap_tenant()` is idempotent: a plugin already registered in this process
  counts as loaded rather than raising `PluginAlreadyRegistered`.
- A broken `class_path` in an existing registry (package removed by an upgrade) is
  skipped at boot with an error log; other plugins still load.

### Three load paths, one precedence rule (ADR-0030 Phase 7, ADR-0240)

`bootstrap_all()` runs all three, in this order:

| Path | Source | Gate |
|---|---|---|
| global | `_GLOBAL_SPECS`, from code (`register_global_plugin`) | none — it loads the compliance boot layer. **Empty on every install**, so this pass returns `[]` |
| declarative | `spec.plugins.installed` in `tenant.corvin.yaml` | none — writing it into a version-controlled config IS the ADR-0030 opt-in |
| runtime | `<tenant>/plugins/registry.yaml` | `plugin_runtime_lifecycle` |

**The declaration wins.** A plugin in a reviewed, version-controlled config is a
stronger statement of intent than a Console click; a plugin present in both loads
once, from the declaration, and the registry pass logs it as already-registered.

`auto_discover_entry_points: true` additionally loads every installed
`corvin.plugins` entry point. It stays default-false — on a machine with
third-party packages around, flipping it means loading code nobody listed.

Until the declarative path landed, `loader.discover_and_load()` had no caller at all: the
config format was documented in ADR-0030, the loader implemented it, and an operator who
wrote the documented YAML got no plugins and no error. It **does** have a caller now —
`bootstrap_declared()`. The module map above used to still say "no caller", contradicting
this paragraph; that has been corrected.

### Self-healing (ADR-0231 Stage 3) — ships dark

`healing.HealingOrchestrator`, behind `plugin_self_healing` (default **off**).
ADR-0231 gates Stage 3 on Stage 2 being stable for a release; a default-off flag is
how that gate is honoured — the mechanism is present and testable, the operator
turns it on when they have the evidence.

Three **reversible** actions, chosen per plugin:

| Policy | Action | Default for |
|---|---|---|
| `circuit_break_only` | refuse calls for the cooldown | `audit_backend`, `user_backend`, `compute_engine`, `recall_backend`, `bridge_channel`, `data_connector` — anything that could lose state or evidence |
| `soft_restart` | `on_unload()` → `on_load()`, same context | `stt_provider`, `summary_provider`, `notification_backend` |
| `disable_and_degrade` | unregister + detach the provider slot | `router_backend`, `worker_engine` — the platform runs fine without them |
| `none` | opt out entirely | — |

An **unknown** plugin type defaults to containment, not to the most permissive
option. Bounds that make it safe to leave on once enabled:

- at most `max_heals_per_hour` actions per plugin (default 3);
- a failure within 60 s of a restart escalates instead of restarting again —
  a restart that did not help means the fault is systematic, and healing a logic
  error only hides it;
- the health collector drives it (one poller in the system, not two);
- every action is audited as `plugin.healing_action`; NOOPs are kept in the
  in-memory history so "why did nothing happen" is answerable.

**Never**: hard kill, force delete, data mutation — and it never rewrites
`registry.yaml`. An autonomous action must not edit the operator's configuration;
re-enabling is a human act. A test greps the module for `os.kill`, `SIGKILL`,
`rmtree`, `TenantRegistry` and friends so this cannot regress.

Stage 4 (LDD-tuned healing policies) is **not** built: it needs MTTR data from
production that does not exist yet, and inventing it would be the "fabricated
benchmark" failure this repo has already had once.

### Flow declarations — locality + egress (ADR-0124 Inv. 3)

Every record declares where it runs and what it talks to, using **L34's exact
vocabulary** (`data_classification.Locality` / `NetworkEgress`) rather than a second
one:

| Field | Values | Default |
|---|---|---|
| `locality` | `local` · `eu_cloud` · `us_cloud` · `unknown` | `unknown` |
| `network_egress` | `none` · `local` · `external` | `external` |
| `egress_hosts` | declared hosts (L35) | `[]` |

The defaults are the **least trusted** combination: a plugin that declares nothing
is treated as unclassified with internet access, never as safe. Two refusals at
`enable()`:

- **community + external egress + no declared hosts** → `EgressNotDeclared`.
  "Talks to the internet, hosts unknown" is the shape an exfiltration path takes.
  A vetted/builtin plugin may leave the list empty — the maintainer reviewed it,
  and that asymmetry is what `origin` is for.
- **`pii_risk: high` + `locality: unknown`** → refused. Personal data with no
  answer to "in which jurisdiction".

A cloud locality combined with `network_egress: none` is rejected at construction —
ADR-0124 lists that contradiction as a MUST NOT.

### Per-tenant registry

```
<corvin_home>/tenants/<tid>/plugins/
├── registry.yaml            # PluginRecord.to_dict(), atomic write, mode 0600
└── instances/<plugin_id>/   # per-plugin state, removed on uninstall
```

Tenant resolution always goes `current_tenant()` → `validate_tenant_id()` →
`tenant_home()`; console routes pass `rec.tenant_id` from the authenticated
`SessionRecord`, never an env var. A registry that does not parse raises
`RegistryCorrupt` — it is never treated as "no plugins" and overwritten.

Audit events: `plugin.installed`, `plugin.enabled`, `plugin.enable_denied`,
`plugin.config_changed`, `plugin.disabled`, `plugin.disable_denied`,
`plugin.uninstalled` — all real, hash-chained, and `config_changed` records
**key names only** (setting values can contain a webhook URL or an account id).

### Stage 2 — health collection + metrics (ADR-0231)

`health.HealthCollector` polls `health_check_all()` on an interval and keeps the
latest snapshot; the gateway lifespan starts it **only** when
`plugin_health_monitoring` is on, and stops it before unloading plugins so a poll
cannot land on a plugin midway through `on_unload()`. With the flag off no timer is
created at all — the health route still answers from breaker state, which costs
nothing.

- **Alerting writes an audit event, not a notification.** `plugin.health_alert`
  after N consecutive failures (default 3), once per streak, plus
  `plugin.health_recovered` when it clears. Routing to email/Slack would be a
  second delivery path next to the ADR-0033 notification provider; that provider
  can fan the event out if the operator installed one.
- **`GET /plugins/metrics`** serves Prometheus 0.0.4 text: `corvin_plugin_health_ok`,
  `..._consecutive_failures`, `..._check_duration_ms`, `corvin_plugin_breaker_open`,
  `..._failures_total`, `..._refused_total`. Same conventions as
  `gateway/audit_metrics.py`: no SDK dependency, label allowlist, no PII, read-only.
  Breaker numbers are real even with polling off. `plugin_id` is a label (operator-
  supplied and charset-validated, so no PII) but capped at 64 distinct values —
  past that it collapses to `other`, because unbounded cardinality is its own outage.

### Structured logging (ADR-0231 Stage 1)

`core/observability/corvin_logging/` — `CorvinLogger` emits one JSON record per
event with the ADR-0231 schema (timestamp, level, component, plugin_id, tenant_id,
correlation_id, operation, duration_ms, error_code, recovered, message, context).
The plugin registry logs `on_load` / `on_unload` / failed `health_check` through it.

Three deliberate deviations from `docs/design/STRUCTURED_LOGGING_SYSTEM.md`:

| Sketch | Here | Why |
|---|---|---|
| `core/logging/` | `core/observability/corvin_logging/` | a package named `logging` shadows the stdlib once its parent is on `sys.path` — the failure that once killed `corvin-webui.service` via an `operator` package |
| `threading.local()` | `contextvars.ContextVar` | the gateway/console/adapter are asyncio: many tasks share one thread, so a thread-local correlation id leaks between concurrent requests |
| scrubber raises `ValueError` | scrubber **redacts** and marks `pii_redacted: true` | a logger that raises fails the work it was describing; the personal data still never reaches the log |

`error_code` carries the exception CLASS, never `str(exc)`. The scrubber is a
backstop for a mistake, not a licence to log payloads — the rule stays "log
metadata, not content".

### Console surface

Routes in `core/console/corvin_console/routes/plugins.py`, page at `/app/plugins`
with `JsonSchemaForm` generating the settings form from the plugin's schema.
Three flags, all default-off:

| Flag | Effect when off |
|---|---|
| `plugin_console_surface` | every `/plugins` route 404s; the page says the feature is off |
| `plugin_runtime_lifecycle` | registry is read-only at runtime; mutations 403 |
| `plugin_health_monitoring` | no polling, no metrics; breaker state still readable |

The surface gate is a **FastAPI dependency**, not an in-body check: FastAPI
validates the request body only after dependencies resolve, so an in-function
check would answer 422 on a malformed POST while the flag is off — telling the
caller the route exists.

### Boot wiring — where it is actually called

**CorvinOS ships TWO FastAPI hosts, and both run the same sequence.** Getting this
wrong is the defect described at the end of this section, so name them explicitly:

| Host | Started by |
|---|---|
| `corvin_console.standalone:create_app` | `corvinos-serve` / `corvin-serve` / `corvin serve` — and `install.sh`'s final step |
| `corvin_gateway.app:app` | `corvin-service` (ADR-0184 Stufe 2) and the installer's console step |

The sequence itself lives once, in `bootstrap.boot_platform()`, and each host's
`_lifespan` calls it:

1. `bootstrap.assert_compliance()` — **not** wrapped in `except: pass`, unlike the
   best-effort startup steps around it. A failed tripwire aborts the boot.
   If the compliance package itself is unimportable it extends `sys.path` (like
   `audit.py`) and, failing that, runs the same core assertion inline — "the
   checker is missing" must never read as "the check passed".
2. `bootstrap.bootstrap_all(...)` — the declarative `spec.plugins.installed` path
   plus the runtime registry, the latter gated on `plugin_runtime_lifecycle`, so a
   fresh install loads nothing. Instantiates each `class_path` and registers with
   a context built by `build_context()` — which populates EVERY provider handle. A
   single bad plugin is logged and skipped; it never blocks the boot. The one
   exception is `GlobalComplianceLoadFailed`, which is re-raised.
3. `tripwire.assert_post_boot()` — asks whether anything claimed the compliance
   boot layer that `bootstrap_global()` did not grant. Can only be asked after the
   plugins are loaded, which is why it is not folded into step 1.
4. `bootstrap.shutdown(loaded)` on lifespan exit, so provider slots are detached
   before in-flight requests finish.

**This has now been the same defect three times, each one level further out.**
First the tripwire and the context builder existed and nothing invoked them.
Then four extension points had no call site (ADR-0251). Then, found on
2026-07-27 by booting an actual wheel install: the sequence was inlined in the
GATEWAY only, and `corvin_console.standalone` — the host `install.sh` launches —
had copied the license-load block above it and stopped short. A console with a
deliberately corrupted audit hash chain booted and answered requests; the same
tripwire, called by hand inside that same process, refused correctly. Every unit
test stayed green, and so did the guard tests, because they read
`corvin_gateway/app.py` — one of the two hosts.

`core/plugins/tests/test_boot_platform_call_site.py` pins the fixed shape: every
host in that table must import AND call `boot_platform`, the sequence must carry
no override switch, and the three steps must stay in that order. Adding a third
host means adding a row there — that is the point of the test.

### Plugin-Builder — assisted authoring (ADR-0253)

`core/plugins/plugin_builder/` is a build-time-only companion to `corvin_plugins`
— an interview-driven tool that helps an author design a plugin BEFORE they
write code, entered via the `/plugin-builder` console command (behind the
`plugin_builder_enabled` feature flag, default off). It restates ADR-0244's
"emits artifacts, never loads them" constraint for a second tool: a plugin it
scaffolds depends only on `corvin_plugins`, never on `plugin_builder`, and
deleting the package leaves every previously generated plugin working.

Four phases (`interview.py`, a transport-agnostic state machine — the same
`InterviewSession.ask()`/`.answer()` pair drives it from a pytest, a CLI loop,
or the console command):

1. **Problem Understanding** — five free-text questions.
2. **Auto-Classification** (`classifier.py`) — keyword-scored, not ML, into six
   kinds: `mcp_server` (Tier C) | `skill` (Tier A) | `hook` (Tier B) | `provider`
   (Tier B, resolves to a REAL `KNOWN_PLUGIN_TYPES` entry — never an invented
   one) | `integration` | `custom`. Every classification carries a rationale
   string and `risk_flags` — including surfacing, at classification time, that a
   guessed `plugin_type` is one of the six unconsumed ones (ADR-0245) or has no
   shipped template.
3. **Dependencies & Constraints** — six more questions.
4. **Review & Confirmation** — the one phase that waits for `confirm` /
   `restart` / `cancel` rather than free text, because writing to disk is the
   only side-effecting step.

`generators/` produces four Markdown documents (Idea, Architecture, ADR, Build
Plan) plus a code scaffold (`generators/scaffold.py`), which picks one of two
strategies:

* **A `PluginKind.PROVIDER` whose type ships an official template** — reuses
  `ops.launcher.corvin.plugin_cmd.cmd_new` in-process (the SAME AST-based
  rewriting path `corvin plugin new` uses), so this tool never carries a second
  copy of that logic.
* **Everything else** (MCP-Server, Skill, Hook, a Provider type with no
  official template yet — today `data_connector`/`stt_provider` — Integration,
  Custom) — fills in one of `plugin_builder/templates/` via plain placeholder
  substitution. These are Builder-owned scaffolds, not ADR-0246 official
  templates: no conformance-test coverage from `test_template_conformance.py`,
  and no claim that a capability protocol is fully specified for a type that
  doesn't have one yet (`data_connector`/`stt_provider`).

`/plugin-builder` is the one STATEFUL command in
`corvin_console/slash_commands.py` — a multi-turn interview needs session
state, held in `plugin_builder.session_store` (in-memory, TTL-evicted, keyed by
`(tenant_id, session_key)`). Every other command in that dispatcher is a pure
function of its arguments; this is the deliberate, documented exception.
`_plugin_builder_continue` checks the (cheap, in-memory) session store BEFORE
the feature flag — `feature_flags.is_enabled()` reads `features.json` from
disk uncached on every call, and the common case on every plain-text turn,
flag on or off, is "no interview is active"; checking the session first means
that common case costs one dict lookup, not a disk read, on every chat turn
in the console.

**Reach: Console AND messenger bridges (2026-07-27).** The interview state
machine, artifact writing and reply text live in `plugin_builder.turn` — a
transport-agnostic module both callers drive, keyed by `(tenant_id,
session_key)`. The console's `session_key` is its chat-CONVERSATION `sid`
(the WebSocket path parameter in `routes/chat.py`'s `/chat/sessions/{sid}/
stream`) — NOT `fingerprint` (the login-cookie hash `handle()` also carries,
used only for display/audit, e.g. `/whoami`). Keying on `fingerprint` was the
original (2026-07) shape and is a genuine bug: `fingerprint` is shared by
EVERY chat conversation the same browser login has open, so an interview
started in one tab would capture plain-text turns typed in any other tab/
saved chat under that login. Fixed 2026-08-01; regression test:
`test_interview_does_not_leak_across_chat_tabs_with_same_login`. The bridge's
`session_key` is `f"{channel}:{chat_key}"` — NOT bare `chat_key` (`chat_id or
sender`). Bare `chat_key` is just one messenger's id space, and two different
channels can produce the identical string (a Discord numeric id and a
Telegram numeric id colliding, a WhatsApp fallback-to-sender matching some
other channel's sender) — an adversarial review (2026-07-27) reproduced
exactly this as a cross-channel session hijack (one user's plain message
silently consumed as the answer to a DIFFERENT user's in-progress interview
on another channel) before the `channel:` prefix was added. Same discipline
this file already applies to session directories via `_session_dir(channel,
chat_key, ...)` — never namespace a bridge identity by `chat_key` alone.
Regression test: `test_same_chat_id_on_two_channels_does_not_collide`.

`slash_commands.py`'s `_plugin_builder_continue`/`_plugin_builder_command`
are thin wrappers around `plugin_builder.turn`;
`operator/bridges/shared/adapter.py`'s `_plugin_builder_bridge_reply` (defined
just above `process_one`) is the bridge-side twin, called from the plain-text
`else` branch of `process_one` right after `prompt` is finalized — guarded to
skip audio/image/document/video turns (a transcription/caption is an
engineered prompt, not the user's literal words) AND to skip any OTHER
`/`-leading text (mirroring the console's `handle()`, which only ever routes
non-slash text into `_plugin_builder_continue` — a stray/unrecognized slash
command must fall through to the engine, never get silently recorded as an
interview answer; regression test:
`test_unrelated_slash_command_mid_interview_is_not_read_as_an_answer`). Same
ordering discipline as the console: check the (cheap, in-memory) session
store before the (disk, uncached) feature flag. The literal `/plugin-builder`
command itself always gets an answer — even flag-off, it returns the "Plugin
Builder is off" pointer rather than falling through to the engine, matching
`slash_commands.py`'s "never leak a command to the model" rule; only OTHER
plain text (not the command, no active session) reaches the engine unchanged.
Both `turn.drive`/`turn.command` calls on the bridge path are wrapped in a
broad `except Exception` that resets the session and returns a safe fallback
message — a bridge message-queue turn has no natural HTTP-response error
boundary the way a console request does, so an uncaught exception here would
otherwise silently quarantine the message with no reply at all (the exact
"silent drop" failure class this repo's own incident history flags).

One process-wide caveat inherited from the bridge architecture generally (see
"Worker Engine Selection" in CLAUDE.md): `tenant_id` for a bridge turn is
`CORVIN_TENANT_ID` or `_default` for the WHOLE adapter process — there is no
per-Discord-guild/per-Slack-workspace tenant mapping, so a scaffold from any
channel on one adapter process lands under that one tenant's `plugin-builder/`
dir and index.

`InterviewSession.answer()` holds a per-instance lock for its whole body — two
overlapping calls on the SAME session (a client retry, a double-submit) are
serialized rather than racing on `_answer_index`/`_answers`. Adversarial review
(2026-07-27) found this corrupting answers under concurrency before the lock
was added; see `test_concurrent_answers_do_not_interleave_state`.

A written scaffold's free-text `plugin_name` is sanitized (`_display_name()` in
`generators/scaffold.py` — strips `"`, `\`, newlines) before it is spliced into
a template's `display_name = "..."` string literal or docstring header. Plain
placeholder substitution has no contextual escaping, so an un-sanitized name
containing a quote could break out of the literal and inject Python that runs
the moment the scaffold is imported (which the tool's OWN generated Build-Plan
doc tells the author to do next, via `corvin plugin check`). Also found by the
2026-07-27 adversarial review; see
`test_scaffold_display_name_cannot_break_out_of_python_string_literal`.

**Scaffold visibility (ADR-0253, 2026-07-27):** every successful
`write_artifacts()` call from the console command is also recorded — best
effort, never raised back into the interview turn — by
`plugin_builder.index_store` into a tenant-scoped `plugin_builder_index.json`
(same read-fail-open / atomic-write-then-rename shape as `feature_flags.py`'s
`features.json` overlay, bounded at `index_store.MAX_ENTRIES`). The Console's
Plugins page (`GET /plugins/scaffolded`, gated by `plugin_builder_enabled`
independently of `plugin_console_surface`) reads it back and renders a
"Scaffolded by Plugin-Builder" section — read-only, no enable/disable/settings,
because a scaffold was never registered (ADR-0244 still holds: recording it in
the index is bookkeeping, not loading).

### Plugin-Builder V2 — idea-first interview, checkpoint, generated tests, `--ideas` (ADR-0262/0263, 2026-07-30)

Four independent, default-off flags layered on top of `plugin_builder_enabled`,
each degrading to the exact ADR-0253 shape above when off:

* **`plugin_builder_idea_first_interview`** — swaps the fixed 5+6-question
  form for one open question (`idea_text`) plus a short-name question
  (`interview.py`'s new `IDEA` phase). `classifier.extract_dependency_hints()`
  tries to resolve the four safety-relevant Dependencies fields (external
  libs, auth, network egress, egress hosts — the exact ADR-0247 Validation
  Gate inputs) from that free text via deterministic keyword/regex matching,
  same "keyword-scored, not ML" discipline as `classify()`. Only the fields
  it can't resolve get asked, in a new `CONFIRM_GAPS` phase — zero unresolved
  fields skips that phase entirely. Session language (`de`/`en`) is detected
  once from the first `IDEA`-phase answer and pinned for the rest of the
  session (`language.py::LanguagePin`) — every later idea-first prompt is
  translated, the legacy question bank is not (no such promise for it).
* **`plugin_builder_checkpoint_review`** — inserts a new `CHECKPOINT` phase
  between Review's `confirm` and scaffold-writing: the four docs are written
  first (`generators.write_idea_docs`), a text+voice summary is built
  (`checkpoint.py::build_checkpoint` — risk flags and low-confidence
  classification carry VERBATIM, never paraphrased, reusing the lesson from
  the `voice-summary-drops-critical-warnings` incident), and a SECOND
  `confirm` is required before `generators.write_scaffold_after_checkpoint`
  runs. **Known cut:** that split-path scaffold write always uses the
  Builder-owned generic template, never the `corvin plugin new` reuse
  `write_artifacts()` (checkpoint off) still gets — the reuse path assumes
  it owns creating its `dest` directory, which the checkpoint's doc-write
  step already did. **Known cut:** `build_checkpoint()`'s `voice_text` is
  real and unit-tested, but no live turn reaches it — neither
  `slash_commands.py` nor `adapter.py`'s `_plugin_builder_bridge_reply`
  (which writes straight to the bridge outbox) currently extracts a
  `<voice>` tag (`voice_tag.py`) from a Plugin-Builder reply at all; wiring
  that in is unstarted, named rather than silently claimed.
* **`plugin_builder_generate_e2e_tests`** — `generators/e2e_tests.py` writes
  edge-case tests (module imports, class instantiates, `health_check()`,
  `on_unload()`) plus, for a `PluginKind.PROVIDER` type the Extension-Surface
  Map marks `consumed` (looked up LIVE via `surface_map.surface_for()` at
  generation time, never a hardcoded list), a real wiring test —
  `set_active()`/`get_active()` against the REAL `corvin_plugins/providers/`
  registry module, proving the plugin's own registration call-site works
  (not that the real consumer invokes it — that still needs a live boot).
  For an unconsumed type: an honest `pytest.mark.skip(reason=...)` citing the
  live `dead_reason`, never a fabricated pass. The scaffold's class name is
  found via `ast.parse()` on the written file, never assumed.
* **`plugin_builder_ideas_mode`** (ADR-0263) — `/plugin-builder --ideas`
  (`ideation.py`), a separate, smaller session store (own TTL/bound, mirrors
  `session_store.py`'s shape) driving a bounded round loop: a plain-language
  acknowledgment gate, then up to `ideation.ROUND_CAP` rounds offering
  proposals grounded ONLY in live signals (`surface_map.unconsumed_types()`,
  Marketplace category sparsity when a `Corvin-Marketplace` checkout is
  present as a sibling repo) — no code path can produce an ungrounded
  suggestion. Convergence (a numeric pick, or any other free text, read as
  the user's own contribution) hands the idea text into a REAL, normal
  idea-first `InterviewSession` via `session_store.start()`, pre-answering
  its `idea_text` question — `--ideas` is a front door onto the same
  pipeline above, never a parallel copy. Starting `--ideas` replaces any
  in-progress plain interview for the same caller (and says so), same
  "replacing any prior one" contract `session_store.start()` already has for
  the symmetric direction.

Both the console (`slash_commands.py`) and the bridge adapter read all four
flags and pass them through identically — `--ideas` and the three interview
flags are reachable from both transports, not console-only.

**Cross-store discipline (hardened across review rounds 3–7, see ADR-0262's
"Review Log" for the full account, kept out of this file to avoid
duplicating it):** a plain interview (`session_store`) and an `--ideas`
dialogue (`ideation.py`'s own store) are mutually exclusive per caller,
serialized by `session_store.cross_store_lock` across every writer to
either store (`session_store.start()`, `ideation.start()`,
`ideation._handoff_to_interview()`, `turn.command()`). `turn._checkpoint_state`
is keyed by `id(session)` (object identity), not the deterministic
`session_id` string every `InterviewSession` for one caller shares.
`session_store.clear()`/`ideation.clear()` take an `expected=` parameter —
an atomic, single-lock-acquisition check-and-delete — used by every caller
that has a specific session object in hand, so a stale/delayed cleanup (a
cancelled or finished session, a bridge retry) can never destroy a newer,
legitimate session for the same caller. `session_store.register_removal_hook()`
lets a dependent (`turn.py`, for `_checkpoint_state`) learn about EVERY way
a session ever leaves the store — `clear()`, TTL eviction, the
`MAX_SESSIONS` bound, and `start()`'s own replace-in-place — not just the
paths a caller remembers to pair with its own cleanup call, closing the
`id()`-reuse gap `clear()`'s `expected=` alone didn't cover.
`ideation.py`'s own `_sessions` store gained the same `_remove_locked()`
single-deletion-point shape (round 7) — no hook registry yet, since
nothing keys off `id(IdeationSession)` today, but a future dependent only
needs to add hooks there instead of a new removal path first. Round 7 also
closed a subtler gap in the hook itself: `turn._checkpoint_reply()` writes
its cache entry only AFTER `write_idea_docs()` (unbounded disk I/O)
returns, so it now re-checks `session_store.get(tenant_id, session_key) is
session` inside the same locked write — without that, a concurrent
`session_store.start()` replacing the session while the write was still in
flight could fire the removal hook as a no-op (nothing to remove yet) and
then have this function write an entry the hook would never fire for
again, a permanent orphan the hook's own promise doesn't cover on its own.
`tests/test_clear_call_sites_completeness.py` mechanically enforces the
`expected=` half of this: it AST-parses every `.clear()` call site and
fails if one lacks `expected=` and isn't in a small, reasoned allowlist —
with a documented scope cut (import aliases, indirect/`getattr` calls, a
fixed file list) named explicitly in its own docstring rather than
silently assumed airtight.

**Diagram:** `docs/diagrams/plugin-builder-v2-flow.svg` — the two entry
points (Console, bridge), `--ideas`' hand-off into the `IDEA` phase, and the
phase chain through `CHECKPOINT`/`DONE`, with the four independent flags
noted. Added round 3 of the ADR-0262/0263 review after an initial "no
diagram, named skip" write-up was itself flagged as not meeting
testing-and-docs.md's placeholder-diagram escalation step for a new
architecture feature — the right response to that rule is a minimal SVG,
not a longer explanation for skipping it.

### The runtime CLI half, and the wall it sat behind (2026-07-28)

`corvin plugin` has two halves with two different owners.
`ops/launcher/corvin/plugin_cmd.py` is the OFFLINE, ADR-0244 half (`types`,
`new`, `check` — it never touches a registry), and
`ops/launcher/corvin/plugin_runtime_cmd.py` is the RUNTIME half (`install`,
`uninstall`, `list`, `enable`, `disable` against
`corvin_plugins.tenant_plugins.TenantPluginRegistry`).

Until 2026-07-28 **the whole runtime half was unreachable**: all five commands
imported `core.plugins.tenant_plugins`, a module that has never existed under
that name (`core/` has no `__init__.py`; the package ships and is imported as
`corvin_plugins`). Each one hit its own `except ImportError` and exited 2 with
`plugin system not available: No module named 'core.plugins.tenant_plugins'`.

Two further defects were sitting *behind* that wall, unreachable and therefore
untested, and only appeared once the import was fixed and the round trip
actually ran:

* `corvin plugin new` emitted the legacy `layer:` key, so `corvin plugin check`
  warned about the manifest it had itself just generated — the "scaffolds
  contradicted their own manifest" class again (cf. 51f7f8a). The key is
  `boot_layer`; see the boot-layer axis section above for why `layer` is a name
  this repo cannot use.
* `TenantPluginEntry.installed_at` was `datetime.now(timezone.utc).isoformat()
  + "Z"` → `…+00:00Z`, two timezone designators and not valid RFC 3339.

The lesson is the one `surface_map.py` states one level up, restated for a CLI:
an `except ImportError` that prints a plausible sentence is indistinguishable
from a feature that is merely off. A dead mechanism needs a call-site test —
here, a test that runs the command.

### Must NOT do

- Don't add a second `PluginRegistry`, lifecycle, or plugin taxonomy.
- Don't import a `core.*` dotted path from the launcher. `core/` is not a
  package; every shipped module under it is remapped to a TOP-LEVEL name by
  `[tool.hatch.build.targets.wheel.sources]` (`corvin_plugins`, `plugin_builder`,
  `corvin_compute`, …). `core.plugins.tenant_plugins` is what made all five
  runtime plugin commands dead code.
- Don't read a runtime asset that the wheel's `**/*.md` exclude eats.
  `plugin_builder/templates/skill_plugin.md` was the whole SKILL scaffold path
  and shipped in no wheel; non-`.py` assets need an explicit `force-include`
  entry, which bypasses `exclude`.
- Don't wrap `assert_compliance()` in `except: pass`, and don't move it after the
  first request is served.
- Don't build a `PluginContext` by hand at a new call site — use
  `bootstrap.build_context()`, or the next added provider handle will be `None`
  in exactly one place and nobody will notice.
- Don't let an `audit_backend` reach the core chain: no `set_writer`,
  no `replace_writer`, no writing to `audit.jsonl`. The tripwire enforces this.
- Don't translate a `user_backend` failure into a guest/anonymous session.
- Don't wire `user_backend` into `local-login` to "activate" it — that path has no
  credentials to pass, and deny on the only login locks the operator out.
- Don't count auth *denials* as circuit-breaker failures (self-inflicted DoS).
- Don't put setting VALUES, principals, or `str(exc)` into audit details or
  `HealthStatus.message` — exception class names only.
- Don't add an override switch to the tripwire in any form.
- Don't build a new marketplace downloader: distribution goes through ADR-0096
  (`mcp_manager`, per-spawn SHA256/digest verification) or ADR-0142/0156.
- Don't let `plugin_builder` import the registry/loader/bootstrap to LOAD what
  it scaffolds — ADR-0244's constraint applies to this tool too: it emits
  artifacts, it never loads them.
- Don't add a `plugin_type` to the classifier's keyword table that isn't in the
  live `KNOWN_PLUGIN_TYPES` — that would be exactly the second taxonomy this
  file already forbids, one layer up.
