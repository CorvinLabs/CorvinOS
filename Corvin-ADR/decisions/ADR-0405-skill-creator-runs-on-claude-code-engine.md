---
id: ADR-0405
status: ACCEPTED
depends_on: []
relates_to:
  - ADR-0323
paths:
  - operator/skill_forge/llm_client.py
  - operator/skill_forge/skill_creator.py
  - operator/skill_forge/six_phase_orchestrator.py
  - core/console/corvin_console/routes/skill_creator_api.py
  - core/console/corvin_console/web-next/src/components/SkillCreatorPanel.tsx
  - core/console/tests/test_skill_creator_e2e.py
  - operator/skill_forge/tests/test_llm_client.py
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
   `operator.skill_forge.skill_creator`, and `operator/` deliberately has no
   `__init__.py`, so collection died with `ModuleNotFoundError`. Defects 1 and
   2 had therefore never been observed.

## Decision

**The Skill-Creator's default LLM engine is the Claude Code CLI.**

`operator/skill_forge/llm_client.py` provides `ClaudeCodeClient`, duck-typed
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
* Skills still land in `~/.claude/skills/`, not in the tenant-native skill
  tree (ADR-0007). Unchanged by this ADR and still owed.

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

* `operator/skill_forge/tests/` — 50 tests (import shim added; the suite had
  never executed).
* `operator/skill_forge/tests/test_llm_client.py` — 14 tests: envelope
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
