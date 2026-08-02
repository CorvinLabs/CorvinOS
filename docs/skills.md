# skills.md — runtime knowledge factory

> **Authoring a skill?** See [skill-format.md](skill-format.md) for the canonical
> `SKILL.md` format (required fields, linter constraints, lifecycle metadata) and
> [skill-template.md](skill-template.md) for a copy-paste scaffold. This document
> covers the runtime lifecycle (grading, promotion, injection).

## Mental model

`skill-forge` is the **knowledge sister** of `forge`. Where forge
generates *executable* artifacts (sandboxed Python tools), skill-forge
generates *prose* artifacts (markdown SKILL.md files that get
prompt-injected into the next turn). Both share the four-scope
mechanic and the unified hash-chained audit log, but the failure modes
are different — a buggy tool segfaults, a buggy skill **lies in the
prompt context** until it gets caught.

Three properties hold by construction:

- **Linted before written.** Every body passes through a fail-closed
  linter (`skill_forge.linter`) that rejects prompt-injection patterns,
  persona-boundary phrases ("ignore previous instructions"), embedded
  secrets, and oversized bodies. NFKC + confusable normalization runs
  *before* matching, so cyrillic homoglyph attacks (а/е/о/р/с/у/х/і/ј)
  don't slip past the substring checks.
- **Graded by usage.** Skills are not "static knowledge artifacts" —
  they earn or lose their place in the prompt through automatic grades
  derived from how the agent and the user actually react. No grade in
  7 days → TTL-purged.
- **Promoted by survival.** A skill moves from task → session → project
  → user scope only by clearing strict gates (≥1 positive grade,
  ≥3 grades with mean ≥0.5, explicit force). Surviving scope is the
  signal of "this skill is genuinely useful," not the act of writing it.

The plugin lives in `operator/skill-forge/`. It imports nothing from
voice or cowork. The MCP server is reached only via a chat-pinned
persona that has `skill_forge_enabled: true`.

```
operator/skill-forge/
├── SKILL.md                 # the agent-facing reference (when to create skills)
├── skill_forge.py           # thin entry point
├── skill_forge/             # the actual modules
│   ├── mcp_server.py           # MCP wire layer (skill_create / skill_promote / skill_grade / skill_list)
│   ├── registry.py             # canonical workspace writer + plugin-slot mirror
│   ├── multi_registry.py       # cross-scope read + promote
│   ├── linter.py               # fail-closed body linter (incl. NFKC + confusables)
│   ├── auditing.py             # SHA-chained audit via forge.security_events
│   └── paths.py                # CORVIN_HOME resolver (delegates to forge.scope)
├── skills/dyn/              # engine-facing slot mirror (gitignored)
├── examples/                # canonical demo skills
└── tests/                   # unit + e2e
```

The path-gate hook (Surface 5 in [security.md](security.md)) keeps
`<scope>/skill-forge/**` and the slot mirror writable only via the
MCP server, regardless of the persona's `permission_mode`.

## When to create a skill (vs a tool)

| Need | Right artifact |
|---|---|
| Capture **how to approach** a kind of problem | skill (markdown) |
| Capture **a deterministic function** | tool (forge) |
| Establish a project convention | skill |
| Compute statistics over a dataset | tool |
| Prevent a recurring mistake | skill (the rule lands in the next prompt) |
| Save a result for later | tool (deterministic cache) |
| Teach the agent a new heuristic | skill |
| Bind a path safely | tool (`x-bind: ro/rw`) |

Discovery first: every persona's capability brief tells the agent to
call `mcp__skill_forge__skill_list` before creating a new skill, so
existing artifacts in scope get reused / extended instead of
duplicated.

## Lifecycle of one skill

### 1. Create

```python
mcp__skill_forge__skill_create({
    "name": "csv-diff-rules",
    "description": "Conventions for diffing CSVs in this repo.",
    "body": "When diffing CSVs, always sort by the first column "
            "before comparing. The pandas reader treats blank rows "
            "as NaN — drop them with .dropna(how='all').",
    "claim": "Reduces false-positive diffs by ~40% on this codebase.",
    "references": ["docs/data-flow.md"]
})
```

What happens, in order:

1. **Linter pass** (`linter.py`). The body is NFKC-normalized and
   confusable-folded into a *match input*; the original body is stored
   unchanged. The match input is checked for:
   - prompt-injection patterns (e.g. "ignore previous instructions",
     "system:", "you are now")
   - persona-boundary phrases (anything trying to redefine the agent's
     identity)
   - embedded secrets (API key shapes, JWT shapes, cookie shapes)
   - body size (≤ configurable max bytes)
   - code density (warning at >40% code lines, not blocking)

   Fail-closed: any rejected body raises `LinterError`. **Don't** wrap
   `LinterError` in a try/except and retry with cleanup — the
   linter rejecting a body is the signal to redesign the skill, not to
   silence the gate.

2. **Canonical workspace write**.
   `<scope_root>/skill-forge/skills/<name>/SKILL.md` gets the full
   front-matter (`name`, `type`, `description`, `claim`, `references`)
   plus `meta.json` with `created_at`, `grades: []`, `mean_score: null`.
   This is the source-of-truth.

3. **Plugin-slot mirror** (only `project` and `user` scope —
   the **scope-gate** for cross-chat leak prevention). Writes a
   stripped projection (just `name` + `description` front-matter,
   body verbatim) to `<repo>/operator/skill-forge/skills/dyn/<sanitized>/SKILL.md`.
   Task-scope and session-scope skills do *not* get a slot mirror;
   they stay reachable via adapter-injection in the originating chat
   (see step 2 of the use-phase below).

4. **MCP notification.** The MCP server emits no separate notification
   — engine-discoverable skills appear at the *next* claude subprocess
   boot (every bridge turn spawns a fresh one). For mid-session
   visibility the adapter-injection path is the load-bearing one.

5. **Audit append.** `skill.created` event with name, scope, schema
   fingerprint of the front-matter, persona, sha-chain.

### 2. Use (per bridge turn)

The adapter calls `skill_inject.collect_active_skills(...)` once per
inbox message:

1. Walk every scope readable from this chat (task → session → project
   → user, in that order).
2. Filter by Layer-14 LDD-toggle — skills whose name maps to an OFF
   layer get dropped before they reach the prompt.
3. Default eligibility: `mean_score > 0` AND at least one grade.
   `profile.inject_ungraded: true` lifts the grade gate.
4. Sort by `mean_score` desc, then `created_at` desc.
5. Cap at 5 (`profile.max_injected_skills` overrides).
6. Concatenate the bodies into a single `--append-system-prompt`
   block.

Profile flags:

| Flag | Default | Effect |
|---|---|---|
| `inject_skills` | true | set false to suppress the block entirely |
| `inject_ungraded` | false | true lifts the "must have ≥1 grade" gate |
| `max_injected_skills` | 5 | cap on how many bodies land in the prompt |

The forge / skill-forge personas ship with `inject_skills: false` —
they have dedicated MCP tools for the generation work, so dragging
skill bodies into their prompt is ballast.

Hot-reload: there is no caching. A skill created mid-session via
`mcp__skill_forge__skill_create` followed by a grade is picked up
on the *next* bridge turn without restart.

### 3. Grade (auto + outcome)

Every successful bridge turn auto-grades, and every follow-up user
turn outcome-grades. Two layers, two gradients:

#### Auto-grade (Layer 7)

After the LLM reply, `skill_inject.auto_grade_from_output(...)` scans
the reply for either a name variant of an active skill (underscore /
hyphen / spaced) or the first 80 characters of its body. Each match
writes a grade with score **0.7** and notes
`"auto-grade ({name|body} match) turn=<msg_id>"` into the skill's
`meta.json`. Best-effort: failures log but never break the turn. The
`profile.inject_skills: false` flag also opts out of auto-grade.

#### Outcome-grade (Layer 15)

After auto-grade, the adapter records
`_last_turn_skills[chat_key] = {run_id, skills, user_text, ts}`.
The *next* user turn pops that snapshot via
`_pop_last_turn_skills()` (one-shot consumer; TTL = 30 min via
`ADAPTER_OUTCOME_SNAPSHOT_TTL`) and calls
`skill_inject.grade_from_user_followup(...)` **before** invoking
claude. Detection runs against two curated phrase lists in
`skill_inject._OUTCOME_APPROVAL_PHRASES` /
`_OUTCOME_REJECTION_PHRASES` (German + English); rephrase uses
`difflib.SequenceMatcher.ratio() ≥ 0.6` against the previous user
text.

| Signal | Target score | Mean with auto-grade (0.7) | Promotion |
|---|---|---|---|
| approval | 0.9 | 0.8 | eligible |
| rejection | 0.1 | 0.4 | blocked |
| rephrase | 0.3 | 0.5 | borderline |

Precedence: rejection > approval > rephrase, so "thanks but actually
wrong" lands as rejection. Each detected signal writes one grade per
prev-turn skill into `meta.json` with notes
`"outcome ({signal}) prev_run=<msg_id>"` and emits a
`skill.outcome_graded` audit event.

Snapshot hygiene: `/reset`, `/cancel`, and a periodic sweep
(`_cleanup_last_turn_skills` every `CLEANUP_INTERVAL` seconds) clear
stale snapshots. Without this a chat that auto-graded once and then
never came back would leave the snapshot in memory forever.

### 4. Promote

```python
mcp__skill_forge__skill_promote({"name": "csv-diff-rules"})
```

The promotion gates are **stricter** than forge's, because skill
content directly shapes the agent's reasoning:

| Source → target | Gate |
|---|---|
| `task` → `session` | ≥1 positive grade (`score > 0`) |
| `session` → `project` | ≥3 grades AND `mean_score ≥ 0.5` |
| `project` → `user` | `force=True` argument (operator-only) |

`MultiSkillRegistry.promote()` calls the target-scope `create()` which
re-runs the linter (defense in depth — bodies that passed once may not
pass an updated linter version) and writes the slot mirror at the new
scope. The source-side delete uses `purge_slot=False` because the new
scope already wrote the authoritative copy.

`skill.promoted` event in the audit chain. PIN-elevation (Layer 16):
`mcp__skill_forge__skill_promote` is denied by the
`auth_elevation_gate.py` PreToolUse hook unless an active elevation
grant exists for the chat (engaged via `/auth-up <pin>`).

### 5. Purge / TTL

Skills with no grades after `--ttl-days N` (default 7) get purged by
`scripts/skill_cleanup.py ungraded`. **User scope is never pruned** —
human-promoted skills are durable. A scheduled run via
`bash operator/bridges/bridge.sh up` ties the cleanup to the same
03:30 daily timer that handles session timeouts.

`skill.purged` event with reason
(`"ungraded_ttl"`, `"explicit"`, `"session_reset"`).

## Linter — the only safety layer before disk

Fail-closed for four classes:

| Class | Pattern source | Example |
|---|---|---|
| **Prompt-injection** | curated regex list | "ignore previous instructions", "system:" |
| **Persona-boundary** | curated regex list | "you are now", "from now on you act as" |
| **Embedded secrets** | shape-based detectors | `sk-...` API key shapes, JWT shapes |
| **Body size** | byte cap | configurable, default 8 KiB |

NFKC normalization + cyrillic-confusable folding (а→a, е→e, о→o,
р→p, с→c, у→y, х→x, і→i, ј→j and uppercase) runs *before* matching.
The original body is stored unchanged; only the match input is
transformed. Closes the homoglyph-bypass class for prompt-injection /
persona-boundary detectors. False positives are essentially zero —
NFKC is lossless for ASCII, and the confusable map only collapses
pre-existing look-alike pairs.

Warnings (e.g. code density >40%) log but do not block. The linter
output is included in the `skill.created` audit event.

## Slot-mirror — engine-native skill loading

Every successful `SkillRegistry.create()` persists the skill **twice**:

1. **Canonical** in the scope workspace at
   `<scope_root>/skill-forge/skills/<name>/SKILL.md` — full
   front-matter, source-of-truth for grade / promote / purge.
2. **Engine-facing slot mirror** at
   `<repo>/operator/skill-forge/skills/dyn/<sanitized>/SKILL.md` —
   only `name` + `description` in the front-matter, body verbatim.
   Dotted names get sanitised (`trading.score_reviews` →
   `trading_score_reviews`). This file is what the standard
   plugin-skill loader picks up at the next claude subprocess boot.

**Scope-gate (Layer 16):** the slot mirror is only written for
`project` and `user` scope. Task- and session-scope skills stay
reachable via adapter-injection in the originating chat, but cannot
leak across chats through the engine's plugin-skill loader.

**Slot-path resolution** (`registry.plugin_slot_dir()`):

1. `CORVIN_PLUGIN_SLOT_DIR` env override — the sole test-isolation signal;
   a dedicated, single-purpose variable every test that exercises
   `create()`/`delete()` sets explicitly.
2. Walk-up from `registry.py` for a `.corvin_repo`/`plugins/` marker →
   `<repo>/operator/skill-forge/skills/dyn/` — the real production path.
3. Fallback `~/.corvin/plugin-slot/` (no repo marker found).

2026-08-02: a prior step-2 redirected to `<CORVIN_HOME>/plugin-slot/`
whenever `CORVIN_HOME` was set — but `CORVIN_HOME` is the canonical
runtime root in every real session, not just tests, so that step
silently misrouted every production install's slot mirror away from the
path the engine actually scans. Removed; see
`Corvin-ADR/concepts/0001-self-learning-project-concept-archive.md`'s
Production-Readiness Roadmap item P0-1.

**Limit — visibility is one subprocess delayed:** the engine reads
plugin skills at subprocess boot. A skill created mid-turn via
`skill_create` is therefore visible to the *next* claude subprocess.
Adapter-injection (the per-bridge-turn path) closes that gap for the
running session.

## Per-persona enable + Layer-14 LDD profile

Capability is opt-in per persona via `skill_forge_enabled: true` in the
persona JSON. The cowork resolver injects the corresponding MCP tools
(`mcp__skill_forge__skill_create / _promote / _grade / _list / _get /
_diff / _purge`), the MCP server wiring, and a runtime-built capability
brief into the persona's `append_system`.

The brief is built fresh per resolve, so it never lies about what the
runtime actually permits. It mentions:
- Promotion gates (≥1 positive for task→session, ≥3 with mean≥0.5
  for session→project, explicit `force` for project→user)
- Discovery-first rule (`mcp__skill_forge__skill_list` before creating)
- Same-subprocess vs. next-subprocess visibility
- The linter is fail-closed and not bypassable

LDD profiles per persona (Layer 14) shape what the persona does *with*
its skill ability — `coder` runs full LDD discipline, `forge` runs
`quick + reproducibility_first`, `inbox` runs almost-off plus
dialectical_reasoning. See [personas-and-routing.md](personas-and-routing.md)
§Bundled personas for the full table.

## Audit events

| Event | When |
|---|---|
| `skill.created` | A new skill was registered (after linter pass) |
| `skill.linted_rejected` | A body failed the linter and was *not* written |
| `skill.graded` | Auto-grade or manual-grade landed |
| `skill.outcome_graded` | Layer-15 outcome signal updated meta.json |
| `skill.promoted` | A skill moved up the scope ladder |
| `skill.purged` | A skill was deleted (ungraded TTL / explicit / session reset) |
| `skill.namespace_denied` | A persona tried to write outside its prefix |

Bridge events (chat lifecycle, `/persona`, `/all`, `/stop`, `/btw`)
chain into the *same* file via `bridges/shared/audit.py`. One
`voice-audit verify` covers tools, skills, and chats together.

## Worked example

```python
# Discovery first
existing = mcp__skill_forge__skill_list({"scope": "session"})
# → [{"name": "csv-import-conventions", "mean_score": 0.83}, …]
#   nothing about CSV diffing — proceed to create

mcp__skill_forge__skill_create({
    "name": "csv-diff-rules",
    "description": "Conventions for diffing CSVs in this repo.",
    "body": "When diffing CSVs, always sort by the first column "
            "before comparing. Treat blank rows as NaN. Drop rows "
            "where every cell is empty.",
    "claim": "Reduces false-positive diffs by ~40% on this codebase.",
    "references": ["docs/data-flow.md"]
})
# → {scope: "session", path: "<.corvin/sessions/...>/skills/csv-diff-rules/"}

# (next bridge turn)
# adapter injects the skill body via collect_active_skills(...)
# → user message "diff these two CSVs" arrives with the skill's
#   conventions in the system prompt

# (LLM uses the convention)
# → reply mentions sorting + NaN handling
# → auto_grade_from_output detects the body match → score 0.7

# (next user turn)
# user: "perfect, that worked!"
# → grade_from_user_followup detects approval → score 0.9
# → mean_score now 0.8

# After 3 such grades the skill becomes promotable to project scope.
```

## Race conditions and how skill-forge handles them

| Race | What happens | How skill-forge handles it |
|---|---|---|
| Two threads create the same name | Atomic write to `<name>/SKILL.md.tmp` then rename | Second writer hits `EEXIST` → `error.kind = "name_taken"` |
| `skill_create` returns before next subprocess boots | Direct skill listing might miss it | Adapter-injection picks it up on the next bridge turn regardless |
| Grade arrives mid-turn | `_last_turn_skills` snapshot is per-chat | The current turn keeps using the prev mean; the next turn sees the updated value |
| Linter rejects a body that *was* accepted before | Older skill in the registry, fresh promotion fails | Promotion re-runs the linter at the target scope; aged-out bodies get blocked at promotion, not at use |
| Slot-mirror write fails after canonical write | Canonical exists, slot doesn't | Best-effort: canonical is source-of-truth; slot writes are retried implicitly on the next subprocess boot via the create-write path |

## Standalone vs. integrated

### Standalone (no voice, no cowork)

Install the MCP server in `.claude/mcp_servers.json`:

```jsonc
{
  "mcpServers": {
    "skill-forge": {
      "command": "python3",
      "args": ["/abs/path/to/operator/skill-forge/skill_forge.py"]
    }
  }
}
```

`mcp__skill_forge__skill_create` is callable; no bridge, no audit-log
unification with chat events. The audit chain still works *inside* the
skill-forge plugin (its own `audit.jsonl`), so promote / grade events
chain locally.

### With cowork (no bridges)

Same as standalone, plus a persona that declares
`skill_forge_enabled: true`. The resolver injects the MCP tools + the
capability brief. Promotion still works; auto-grade and outcome-grade
do not (those are bridge-side, layer 7 + layer 15).

### Full Corvin

Everything above, plus `bridges/shared/audit.py` chains bridge events
into the same audit file. Auto-grade fires after each bridge turn,
outcome-grade fires on each user follow-up. Promotion gates use the
unified mean_score that aggregates all signal sources.

## Testing

Skill-forge tests are part of `bash operator/bridges/run-all-tests.sh`.
Highlights:

- `test_linter_normalisation.py` — NFKC + confusable folding closes the
  homoglyph-bypass for every detector class
- `test_skill_inject.py` — `collect_active_skills` filters by grade,
  scope ladder, layer toggle; `auto_grade_from_output` writes grades
- `test_skill_outcome_grading.py` — full Layer-15 path: detection
  (20+ phrase cases), `grade_from_user_followup` against a real
  `MultiSkillRegistry`, in-process adapter E2E with seeded
  `_last_turn_skills`, snapshot hygiene on `/reset` / `/cancel` /
  periodic sweep
- `test_plugin_slot.py` — scope-gate (task / session do not write the
  slot, project / user do), promote-path correctly hands the slot off

The discipline is **per-subtask fictional E2E**: real subprocess for
MCP, real filesystem for workspaces, real `bwrap` where the test
depends on namespace isolation.

## Next

- [forge.md](forge.md) — the runtime *tool* factory; same scope
  mechanic, same audit chain, different artifact type.
- [security.md](security.md) — the linter as Surface 3, the path-gate
  as Surface 5, the audit chain as the cross-cutting band.
- [personas-and-routing.md](personas-and-routing.md) — which personas
  ship with `skill_forge_enabled: true` and what their LDD profiles
  do to skill discipline.
