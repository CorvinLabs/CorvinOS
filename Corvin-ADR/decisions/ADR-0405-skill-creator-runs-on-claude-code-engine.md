---
id: ADR-0405
status: ACCEPTED
depends_on: []
relates_to:
  - ADR-0323
paths:
  - operator/skill_creator/llm_client.py
  - operator/skill_creator/registry_bridge.py
  - operator/skill_creator/skill_creator.py
  - operator/skill_creator/skill_creator.py
  - operator/skill_creator/six_phase_orchestrator.py
  - core/console/corvin_console/routes/skill_creator_api.py
  - core/console/corvin_console/web-next/src/components/SkillCreatorPanel.tsx
  - core/console/tests/test_skill_creator_e2e.py
  - operator/skill_creator/tests/test_llm_client.py
docs:
  - docs/concepts/CONCEPT-SKILL-CREATOR.md
---

# ADR-0405: Skill-Creator runs on the Claude Code engine, not on an API key

## Context

Skill generation from the console (`/console/app/skills` → Skill Creator panel)
failed on every attempt with

```
Could not resolve authentication method. Expected one of api_key, auth_token,
or credentials to be set. Or for one of the `X-Api-Key` or `Authorization`
headers to be explicitly omitted
```

Each of the four Skill-Creator phases constructed its own client with
`anthropic.Anthropic()`. That constructor raises when no `ANTHROPIC_API_KEY`
is present — and a CorvinOS install has none. Every other engine-driven path
in this repo (console web-chat, ACS runtime, TDE workers, the L44 house-rules
gate, CEL's LLM synthesis stage) already drives the **Claude Code CLI**, which
authenticates with the operator's Claude subscription. The Skill-Creator was
the one subsystem that assumed a raw API key.

Three further defects sat behind the auth error and would each have failed a
run that got past it:

1. **The LDD loop could not converge by construction.** `_measure_loss`
   scored a prose evaluation by substring — `"scope" in text → +0.3`,
   `"dependencies" in text → +0.2` — while the prompt that produced the text
   *asked* about scope and dependencies. Any well-formed review therefore
   floored the loss above the convergence threshold, and the loop always ran
   to `k_max` and raised `LDDIterationError`.
2. **The validator rejected almost any generated skill.** The forbidden-pattern
   list stored `r"<|im_start|>"` as a REGEX. Unescaped, that is the alternation
   `<` | `im_start` | `>`, so a single angle bracket anywhere in a skill body
   was read as prompt injection.
3. **The unit tests could not run at all.** They imported
   `operator.skill_creator.skill_creator`, and `operator/` deliberately has no
   `__init__.py`, so collection died with `ModuleNotFoundError`. Defects 1 and
   2 had therefore never been observed.

## Decision

**The Skill-Creator's default LLM engine is the Claude Code CLI.**

`operator/skill_creator/llm_client.py` provides `ClaudeCodeClient`, duck-typed
against the slice of the Anthropic SDK the phases use —
`client.messages.create(model=…, max_tokens=…, messages=[…])` returning an
object with `.content[0].text`. Each call is a single-turn, tool-free
`claude -p --output-format json --max-turns 1 --disallowedTools "*"` in a
throwaway cwd. `resolve_llm_client()` is the one resolution point:

| Order | Engine | When |
|---|---|---|
| 1 | `claude_code` | default — the operator's Claude subscription |
| 2 | `api` | `ANTHROPIC_API_KEY` set **and** `CORVIN_SKILL_CREATOR_ENGINE=api` |
| 3 | `None` | nothing reachable → local template generation |

The orchestrator resolves **once** and injects the same client into all four
phases, so a run cannot half-execute across two backends.

Supporting decisions:

* **Loss is a scored rubric, not prose matching.** The tester asks for JSON
  (`clarity`, `executability`, `scope`, `coupling`, `notes`), weighted
  0.35/0.35/0.20/0.10. An unparseable reply scores **1.0** — fail-high, so an
  unverified skill can never pass as converged.
* **Non-convergence degrades for operator-facing runs.** `SkillTester` still
  raises `LDDIterationError` at `k_max` by default (the LDD contract). The
  console orchestrator passes `escalate_on_k_max=False` and subtracts 0.2 from
  `quality_score` instead, rather than discarding several minutes of engine
  work.
* **Phase 1 output is normalised before Phase 2 judges it.** Generation is
  free-form and validation is fail-closed; with nothing in between, a
  formatting slip discarded a run that had already spent minutes of engine
  time. Two live failures came from exactly this gap — `assistant.json-syntax-check`
  (hyphens) and `Purpose length 201 outside range [20, 200]` (one character).
  `normalize_spec` applies only meaning-preserving fixes: name separators →
  `_`, whitespace collapse, code-fence and leading-blank-line stripping, and
  an over-long purpose trimmed at a sentence boundary (or a word boundary
  with an ellipsis). Too-SHORT is never padded — that is a real defect.
* **One repair round for what normalisation cannot fix.** `SkillValidator`
  gained `collect_violations()`, which returns every violation instead of
  raising on the first; the orchestrator feeds that whole list back to the
  planner for a single corrective pass, then validates again. The gate stays
  fail-closed — this only adds two chances to reach a valid spec before it
  rejects, and a deterministic fix never spends an engine call.
* **The bounds are declared once.** `SKILL_NAME_RE`, `PURPOSE_LEN`,
  `METHOD_LEN` are read by the Phase 1 prompt, the normaliser and the
  validator alike, so the model is told the exact limits it will be judged
  against.
* **Injection markers are escaped literals.** `re.escape("<|im_start|>")`,
  plus line-anchored role markers (`^\s*system\s*:`). Strictly narrower than
  the accidental alternation, and strictly closer to the rule's intent.
* **The route reports real phase progress.** `progress_cb` fires as each of
  `planning · validation · ldd_iteration · review · promotion` starts; the
  status payload carries `engine`, `phases` and `error`.

## Amendment 2026-08-20 — promotion, reachability, and the quality scale

Three defects surfaced once operators started generating skills for real.

### A generated skill was never reachable

`SkillPromoter` wrote a flat `~/.claude/skills/<name>.md`. **Nothing in this
system reads that path.** Skill availability runs through the SkillForge
REGISTRY: `SkillRegistry.list()` reads a manifest (not a directory), the
engine plugin slot expects `<name>/SKILL.md`, and `skill_inject`'s gate drops
anything with `n_grades < 1 or mean_score <= 0`. Claude Code's own loader
also wants `<name>/SKILL.md`, so the flat file was inert there too. Every
generated skill existed and could never be injected into a turn — the
"looks wired, isn't" class `e2e-wiring-proof` exists to catch, reached by
skipping that gate on a promotion path.

Promotion now goes through `registry.create()` (manifest entry, fail-closed
linter, plugin-slot mirror for `user`/`project` scope, hash-chained skill
audit) and immediately seeds the bootstrap grade the injection gate requires
— capped at 0.3 and disclosed in its notes as a seed rather than earned
usage, per CLAUDE.md. A freshly created skill has no organic path to its own
first grade, because auto-grading only scores skills that were already
injected.

`registry_bridge.py` loads the registry by explicit file location under a
private module name. TWO packages in this repo are named `skill_forge`
(`operator/skill_creator/` = the Skill-Creator, `operator/skill-forge/skill_forge/`
= the registry) and the console route puts the former first on `sys.path`, so
neither a plain import nor a `sys.path` insert is dependable.

### The quality score carried no information

`1.0 - (confirmed * 0.3 + plausible * 0.1)` saturates at four confirmed
findings. The adversarial reviewers are instructed to FIND problems and
routinely return five to ten (measured live: 5, 6, 10, 5), so every run
reported 0%. Scoring is now per review DIMENSION — clean 1.0, plausible-only
0.5, any confirmed 0.0, averaged over the panel, minus 0.2 for a
non-converged LDD loop. The findings themselves were counted into that number
and discarded; they now travel with the artifact and reach the console, so
"Quality: 0%" comes with its reasons.

A CONFIRMED verdict is the reviewer's own claim — there is no independent
verification pass — and the code now says so instead of promising an
"LDD re-entry in production" that never happened.

### The registry root was the wrong directory

The first attempt wrote to `<tenant>/global/skill-forge`, copied from
`skills_manual.py`. The tenant contract is
`<corvin_home>/tenants/<tid>/{global,sessions,forge,skill-forge,...}` —
`skill-forge` is a SIBLING of `global`, and `<tenant>/skill-forge` is what
`MultiSkillRegistry._root_for("user")` resolves, hence the only root
`skill_inject` ever reads. The skill was written, registered, listed and
reported `injectable` into a registry nothing consumes. Every cheap check
passed while the answer to "will this be used?" was still no. `skills_manual.py`
has the same defect and is out of scope here.

### Two packages were named `skill_forge`, and this one shadowed the other

`operator/skill_forge/` (the Skill-Creator) and
`operator/skill-forge/skill_forge/` (the registry) both imported as
`skill_forge`. `skill_creator_api.py` did `sys.path.insert(0, operator/)` at
import time, so from the moment the console loaded that route, every
`from skill_forge.multi_registry import ...` in the process resolved into the
Skill-Creator and raised `ModuleNotFoundError` — breaking `skill_inject`,
`promote.py` and `adapter.py`'s session cleanup, none of which have anything
to do with skill generation.

The Skill-Creator package is therefore renamed to **`skill_creator`**; the
registry keeps the name it has had all along and that ~20 modules import. The
route now APPENDS to `sys.path` instead of inserting, so a future name clash
cannot silently win against an installed package.

**Do not name anything under `operator/skill_creator/` `skill_forge` again.**

### The route had no tenant and no session

Generation spends the operator's subscription and was callable
unauthenticated. The route now depends on `require_session`, derives the
registry root from `rec.tenant_id` (never an env var — the console
tenant-routing rule), and passes that tenant explicitly into the worker
thread, which has no session of its own. `GET /skill-creator/skills/{name}`
backs the console's previously inert "View" button, and `/skills` reads the
registry manifest with an `injectable` flag per skill instead of globbing a
directory.

## Consequences

* Skill generation works on a stock CorvinOS install with no API key. A live
  run takes roughly 6–7 minutes (one CLI call per phase step) and is charged
  to the operator's subscription, not to per-token API billing.
* `ANTHROPIC_API_KEY` alone no longer switches the engine — an install that
  has one keeps using the subscription unless `CORVIN_SKILL_CREATOR_ENGINE=api`
  says otherwise. This is deliberate: a stray key in the environment must not
  silently redirect billing.
* The engine selector is an **environment escape hatch**, not an operator
  setting. It is deliberately NOT the `spec.web_chat.worker_engine` mechanism,
  which governs chat-turn execution and must stay single-source. Surfacing the
  Skill-Creator engine in Console → Settings is left open.
* Skills land in the tenant-native SkillForge registry at
  `<tenant-global>/skill-forge/` (ADR-0007), reached through the
  authenticated session's tenant.
* Skills generated BEFORE this change sit in `~/.claude/skills/*.md` and are
  inert. They are not migrated automatically — the operator can regenerate or
  move them; nothing reads them where they are.
* `skills_manual.py` writes skill directories WITHOUT a manifest entry AND
  under `<tenant>/global/skill-forge`, so manually authored skills have the
  same reachability gap, twice over. Out of scope here, same defect.
* The reachability claim is now tested against the real consumer:
  `test_promoted_skill_reaches_the_injection_block` generates a skill through
  the HTTP route and asserts it appears in `skill_inject.collect_active_skills()`.
  Every weaker check (file written, manifest entry, `injectable: true`) passed
  while the skill was unreachable, twice, for two different reasons.

## Alternatives considered

* **Rewrite the phases against an async engine interface.** Cleaner long-term,
  but it invalidates every existing `messages.create` test mock and touches
  four classes to fix one authentication decision. The duck-typed adapter buys
  the same outcome with a diff a reviewer can hold in their head.
* **Keep `anthropic.Anthropic()` and document that operators need a key.**
  Rejected: it contradicts how every other engine path in this repo already
  authenticates, and it puts a second billing relationship in front of a
  feature the subscription already covers.
* **Relax the name validator to accept hyphens.** Rejected: the contract is
  load-bearing for skill lookup. Normalising the input is the narrower change.

## Verification

* `operator/skill_creator/tests/` — 50 tests (import shim added; the suite had
  never executed).
* `operator/skill_creator/tests/test_llm_client.py` — 14 tests: envelope
  parsing, `is_error` envelopes raising rather than returning error text as
  output, binary resolution, engine precedence.
* `core/console/tests/test_skill_creator_e2e.py` — 5 tests over the real HTTP
  boundary plus `test_live_generation`, opt-in via `CORVIN_E2E_LIVE_ENGINE=1`,
  which ran green against the real CLI in 6:03.
* `web-next/tests/e2e/skill-creator.spec.ts` — 7 Playwright tests, green
  against the live console.
* Live proof: `POST /v1/console/skill-creator/generate` →
  `run-3db2b5b17a41` → `success`, engine `claude_code`, skill
  `assistant.check_json_syntax`, quality 1.0, 3 LDD iterations.
* Live proof after the normalisation slice: `run-5cac1780b27c` → `success`,
  skill `assistant.python_code_audit`, purpose 152 chars, quality 0.8,
  5 iterations — `k_max` reached without convergence, so the run kept its
  best iterate and paid the 0.2 quality penalty instead of being discarded.
