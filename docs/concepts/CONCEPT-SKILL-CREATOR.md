# CONCEPT: Skill-Creator — Self-Learning Skill-Generation System

**Status:** PROPOSED  
**Version:** 1.0  
**Author:** Claude Code  
**Last Updated:** 2026-08-20  
**Scope:** Console Quality Subsystem (console→quality)

---

## Overview

**Skill-Creator** is a meta-skill that generates, validates, and iteratively refines new skills through a deterministic five-phase workflow driven by Loss-Driven Development (LDD). When invoked with a natural-language request ("erzeuge mir einen Skill der X macht"), it:

1. **Plans** the skill architecture via dialectical reasoning
2. **Validates** the spec against quality gates (schema, syntax, edge cases)
3. **Iterates** using the LDD inner loop: measure → diagnose → fix → re-measure (5-iteration budget)
4. **Reviews** the skill adversarially (debate every design decision until 0 findings survive)
5. **Promotes** the skill to `project` scope if quality thresholds pass

**Key principle:** A skill is "done" when the skill-creator's adversarial reviewers *cannot refute it*, not when the LLM *claims* it's done.

---

## Engine — the Claude subscription, not an API key (ADR-0405)

Every phase that needs a model goes through `resolve_llm_client()` in
`operator/skill_creator/llm_client.py`. The default engine is the **Claude Code
CLI**, driven as `claude -p --output-format json --max-turns 1
--disallowedTools "*"` in a throwaway cwd — the same engine the console
web-chat, ACS runtime and TDE workers use, authenticated by the operator's
Claude subscription.

| Order | Engine id | Selected when |
|---|---|---|
| 1 | `claude_code` | default — nothing to configure |
| 2 | `api` | `ANTHROPIC_API_KEY` set **and** `CORVIN_SKILL_CREATOR_ENGINE=api` |
| 3 | `local` (client `None`) | no engine reachable, or `CORVIN_SKILL_CREATOR_ENGINE=local` — template generation, no LDD loop, no review |

A stray `ANTHROPIC_API_KEY` in the environment does **not** switch the engine
on its own: redirecting billing must be a deliberate act.

| Env var | Default | Meaning |
|---|---|---|
| `CORVIN_SKILL_CREATOR_ENGINE` | `claude_code` | `claude_code` · `api` · `local` |
| `CORVIN_SKILL_CREATOR_MODEL` | `claude-opus-5` | model passed to `--model` |
| `CORVIN_SKILL_CREATOR_TIMEOUT_S` | `180` | per-call CLI timeout |
| `CORVIN_CLAUDE_BIN` | resolved from PATH | absolute path to the `claude` binary |

The orchestrator resolves the client **once** and injects it into all four
phases, so a run cannot half-execute across two backends. The engine id is
reported in every `/skill-creator/generate` and `/status/<run_id>` payload and
rendered in the console panel.

Cost note: a live run is roughly 6–7 minutes and one CLI call per phase step,
charged to the subscription.

---

## Architecture

### Layer 1: Entry Point (console→quality)

**Placement:** Alongside `adr-gate` skill in the Console Quality subsystem.

```
Console Settings → Quality → [ADR Gate] [Skill Creator] [Concept Gate]
```

**Activation:** Triggered by user utterance patterns:
- "erzeuge mir einen Skill der X macht"
- "create a skill to X"
- "neuer Skill: X"
- `/skill-creator <description>`

**Default:** ON (enabled by default; can be toggled via `spec.features.skill_creator_enabled`)

### Layer 2: Five-Phase Workflow

```
┌─────────────────────────────────────────────────────────────┐
│ PHASE 1: PLANNING (Dialectical Reasoning)                  │
├─────────────────────────────────────────────────────────────┤
│ Input:   User request + context (prior skills, project)     │
│ Process: Generate thesis → antithesis → synthesis           │
│ Output:  SkillSpec (name, body, scope, dependencies)        │
│ Gate:    Spec review (30 sec, fail-fast on conflicts)       │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ PHASE 2: VALIDATION (Schema + Linting)                      │
├─────────────────────────────────────────────────────────────┤
│ Input:   SkillSpec                                          │
│ Checks:  - Markdown structure valid                         │
│          - No prompt-injection patterns (linter gate)        │
│          - Dependencies exist or can be auto-installed      │
│          - Scope prefix correct (assistant.*, project.*)    │
│ Output:  SkillSpec (validated) OR fix-list                  │
│ Gate:    ALL checks must pass; fail-closed (no skip)        │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ PHASE 3: LDD ITERATION (Inner Loop, k ≤ 5)                 │
├─────────────────────────────────────────────────────────────┤
│ k=1: Test skill in real turn → measure loss                │
│      - clarity loss (skill can't be understood)             │
│      - scope creep (skill does more than stated)            │
│      - coupling loss (too many dependencies)                │
│      Diagnose via `root-cause-by-layer` → fix              │
│                                                              │
│ k=2–4: Re-run E2E test + measure loss                       │
│        Converge via `e2e-driven-iteration`                  │
│                                                              │
│ k=5: Final skill state; if not converged, escalate           │
│ Gate: Loss must decrease each iteration (or stagnate ≤ 2it) │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ PHASE 4: ADVERSARIAL REVIEW (0-Finding Target)             │
├─────────────────────────────────────────────────────────────┤
│ Spawn 3 independent skeptical reviewers on distinct axes:   │
│  1. "Try to find edge cases / missing scope"                │
│  2. "Try to find overcomplexity / redundant logic"          │
│  3. "Try to find coupling issues / unmaintainability"       │
│                                                              │
│ Each reviewer returns CONFIRMED / PLAUSIBLE / REFUTED        │
│ Skill survives iff all findings are REFUTED                 │
│ (≥ 1 CONFIRMED finding → loop back to Phase 3)              │
│ Gate: 0 findings (CONFIRMED + PLAUSIBLE) → done             │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ PHASE 5: PROMOTION + REGISTRATION                           │
├─────────────────────────────────────────────────────────────┤
│ Actions:                                                     │
│  1. Write skill to disk: `.claude/skills/<name>.md`         │
│  2. Auto-grade with +0.3 (bootstrap seed)                   │
│  3. Register in SkillForge registry                         │
│  4. Emit concept-gate signal (if generalizable)            │
│  5. Return skill artifact + usage examples                  │
│ Gate: All registration steps atomic (all-or-nothing)       │
└─────────────────────────────────────────────────────────────┘
```

### Layer 3: Adversarial Dialectical Review

**Goal:** No skill is "done" until adversarial reviewers cannot find real flaws.

**Three Review Dimensions (run in parallel, vote = OR logic):**

| Dimension | Reviewer Prompt | What They Hunt |
|-----------|---|---|
| **Correctness** | "Try to find wrong, missing, or impossible instructions that would mislead a future user" | Off-by-one in examples; hidden assumptions; incomplete method |
| **Simplification** | "Try to find unnecessarily complex, overcomplicated, or redundant instructions that could be shortened" | Verbose patterns; copy-paste variants; deep nesting in instructions |
| **Scope Creep** | "Try to find instructions that do more, less, or different than the stated skill purpose — scope drift" | Method bleeds into other skills; unintended side effects; overcoupled to project state |

**Voting:**
- Each dimension = 1 independent agent
- Each agent returns: **REFUTED** (skill is sound) | **PLAUSIBLE** (maybe buggy, needs investigation) | **CONFIRMED** (real flaw found)
- **Survival rule:** All three must vote **REFUTED**, OR run another LDD iteration to fix

**Re-Entry:** If any dimension votes CONFIRMED or PLAUSIBLE, loop back to Phase 3 (LDD iteration) with the finding as the loss signal.

---

## Dialog Flow

### User Invocation

```
User: "Erzeuge mir einen Skill der meine lokalen Dateien analysiert."

Skill-Creator:
┌─ PHASE 1: PLANNING
│  Thesis: "Skill that uses `find` + file metadata to analyze local directories"
│  Antithesis: "Risky — direct file I/O needs sandboxing; shell escaping critical"
│  Synthesis: "Skill provides high-level patterns; user calls Bash tool for actual I/O"
│  
│  ✅ Spec generated:
│    Name: assistant.analyze_local_files
│    Scope: assistant (user-bound, no project-wide)
│    Dependencies: Bash tool (already available)
│    Body: 150 lines (instructions + examples)
│
│  User (auto-approve after review): ✓ Continue

├─ PHASE 2: VALIDATION
│  ✅ Markdown structure: PASS
│  ✅ No injection patterns: PASS
│  ✅ Dependencies exist: PASS (Bash already available)
│  ✅ Scope prefix correct: PASS (assistant.* OK)
│  
│  → Skill ready for LDD iteration

├─ PHASE 3: LDD ITERATION
│  k=1: Test skill
│    Scenario: User says "analyze ~/projects/ for .env files"
│    Skill output quality: MODERATE
│    Loss signal: "Skill doesn't mention security implications of .env exposure"
│    → Fix: Add security note in Method section
│    
│  k=2: Re-test
│    Scenario: Same
│    Skill output quality: GOOD
│    Loss signal: "None detected"
│    → Converged ✅

├─ PHASE 4: ADVERSARIAL REVIEW
│  Reviewer 1 (Correctness): "Skill assumes all `.env` are secrets — what about `.env.example`?"
│    Verdict: PLAUSIBLE (skill could be clearer on filtering)
│    → LDD iteration triggered
│  
│  [Phase 3 again: refine filtering logic]
│  
│  Reviewer 1 (retry): "Now it's clear and safe" → REFUTED ✓
│  Reviewer 2 (Simplification): "Instructions are concise" → REFUTED ✓
│  Reviewer 3 (Scope): "Stays within file analysis scope" → REFUTED ✓
│  
│  → All dimensions REFUTED: Skill passes ✅

└─ PHASE 5: PROMOTION
   ✅ Written to: ~/.claude/skills/assistant.analyze_local_files.md
   ✅ Auto-graded: +0.3 (bootstrap)
   ✅ Registered in SkillForge
   ✅ Available for next session
   
   Summary:
     • Skill name: assistant.analyze_local_files
     • Tests run: 5 (1 in k=1, 1 in k=2, 3 adversarial reviews)
     • Iterations: 2 (k=1 → fix → k=2 converged)
     • Final quality: CONFIRMED (all reviewers refuted flaws)
   
   Usage: "Du hast jetzt einen neuen Skill. Nutze ihn mit: 
           'analysiere ~/mein-projekt/ auf Performance-Issues'"
```

---

## LDD Integration Details

### Phase 3 Implementation (Inner Loop)

Each iteration follows the E2E-driven-iteration pattern:

```python
async def ldd_iteration(skill_spec: SkillSpec, iteration_k: int) -> LossSignal:
    """Run one LDD iteration: test → measure loss → diagnose → fix."""
    
    # 1. Test the skill in a real scenario
    test_result = await test_skill_in_scenario(
        skill_spec,
        scenario=generate_test_scenario(skill_spec.purpose)
    )
    
    # 2. Measure loss from the SCORED RUBRIC the tester requested
    #    (JSON: clarity / executability / scope / coupling / notes),
    #    weighted 0.35 / 0.35 / 0.20 / 0.10.
    #
    #    NOT substring matching on prose: the original implementation
    #    scored `"scope" in evaluation_text` against a prompt that ASKED
    #    about scope, so every well-formed review floored the loss above
    #    the threshold and the loop could never converge (ADR-0405).
    #
    #    An unparseable reply scores 1.0 — fail-high, so an unverified
    #    skill can never pass as converged.
    loss = measure_skill_loss(test_result, skill_spec)
    
    # 3. Diagnose via root-cause-by-layer
    if loss > LOSS_THRESHOLD:
        diagnosis = await root_cause_by_layer(loss, test_result, skill_spec)
        # Returns: layer_origin, fix_proposal
        
        # 4. Fix
        skill_spec = await fix_skill(skill_spec, diagnosis)
        
        # Re-loop (next iteration)
        return LossSignal(
            loss=loss,
            diagnosis=diagnosis,
            action="ITERATE",
            next_k=k+1
        )
    else:
        return LossSignal(
            loss=loss,
            diagnosis=None,
            action="CONVERGED",
            next_k=None
        )
```

**Hard rule:** k_max = 5. If not converged by k=5, escalate with:
- What was tried (each iteration's fix)
- What kept failing (the stagnant loss signal)
- Layer-4 diagnosis (via root-cause-by-layer)
- Step-size recommendation (via loss-backprop-lens)

### Review Integration (Phase 4)

```python
async def adversarial_review_skill(skill_spec: SkillSpec) -> ReviewResult:
    """Spawn 3 independent reviewers on distinct dimensions."""
    
    reviewers = [
        ReviewAgent(
            dimension="correctness",
            prompt="""Try to find wrong, missing, or impossible instructions.
                      Quote the line that's problematic and explain why."""
        ),
        ReviewAgent(
            dimension="simplification",
            prompt="""Try to find unnecessarily complex instructions that 
                      could be shortened without losing meaning."""
        ),
        ReviewAgent(
            dimension="scope_creep",
            prompt="""Try to find instructions that drift beyond the stated
                      skill purpose or create unwanted coupling."""
        ),
    ]
    
    findings = await parallel([
        reviewer.review(skill_spec) for reviewer in reviewers
    ])
    
    # Aggregate
    confirmed_count = sum(1 for f in findings if f.verdict == "CONFIRMED")
    plausible_count = sum(1 for f in findings if f.verdict == "PLAUSIBLE")
    
    if confirmed_count + plausible_count == 0:
        return ReviewResult(status="APPROVED", findings=[])
    else:
        # Re-enter Phase 3 with these findings as loss signals
        return ReviewResult(
            status="NEEDS_ITERATION",
            findings=findings
        )
```

---

## Console Integration

### Location: `/console/app/skills` → Skill Creator panel

**Shipped today** (`SkillCreatorPanel.tsx`, `routes/skill_creator_api.py`):

| Endpoint | Behaviour |
|---|---|
| `POST /v1/console/skill-creator/generate` | 202 + `{run_id, engine}`; the run executes on a worker thread |
| `GET /v1/console/skill-creator/status/{run_id}` | `{status, phase, progress, message, engine, phases, error, skill}` |
| `GET /v1/console/skill-creator/skills` | promoted skills on disk |
| `GET /v1/console/skill-creator/stats` | `{total_generated, avg_quality, total_iterations, last_generated_at}` |

The panel polls `/status` once a second and renders a five-step phase stepper
(`planning · validation · ldd_iteration · review · promotion`) plus the engine
that is running the work. `phases` is served by the backend so the stepper and
the orchestrator cannot drift apart. A failed run shows an operator-facing
message with the raw engine error behind a disclosure.

**Not yet built** (was described here as if shipped): an enable/disable toggle,
an approve/reject checkpoint after Phase 1, and the View/Delete actions in the
generated-skills list — those buttons are inert.

### Feature Flag

**Not implemented.** The keys below are the intended shape; nothing reads them
today. Engine selection is currently an environment escape hatch
(`CORVIN_SKILL_CREATOR_ENGINE`, see the Engine section) and deliberately NOT
the `spec.web_chat.worker_engine` mechanism, which governs chat-turn execution
and must stay single-source.

```yaml
# core/bundle/config-templates/tenant.corvin.yaml
spec:
  features:
    skill_creator_enabled: true  # Default ON
    skill_creator_max_iterations: 5  # ADR-0321 / LDD budget
    skill_creator_adversarial_reviewers: 3  # Parallel review agents
```

### Reachability — where a promoted skill goes, and why it matters

Promotion writes into the tenant's SkillForge **registry** at
`<corvin_home>/tenants/<tid>/skill-forge/` — a sibling of `global/`, not a
child of it, because that sibling path is what
`MultiSkillRegistry._root_for("user")` resolves and therefore the only root
`skill_inject` ever reads.

Three things must ALL hold before a skill is ever injected into a turn:

| Requirement | Provided by | Failure mode when missing |
|---|---|---|
| Manifest entry | `registry.create()` | `SkillRegistry.list()` reads the manifest, not the directory — a bare directory is invisible |
| `user`/`project` scope | promoter default `user` | Layer-16 scope gate: task/session scope never reaches the engine plugin slot |
| `n_grades >= 1`, `mean_score > 0` | bootstrap grade (0.3, disclosed as a seed) | `skill_inject` drops it; auto-grading only scores skills that were already injected, so there is no organic path to a first grade |

The console reports the combined answer as `injectable` per skill, and the
E2E asserts it against the real consumer — generate through HTTP, then look
for the skill in `skill_inject.collect_active_skills()`. Weaker checks (file
written, manifest entry, `injectable: true`) all passed at one point while
the skill was still unreachable.

**Known gap (2026-08-20):** 139 pre-existing `cel_*` skills in the registry
carry zero grades and are therefore inert. Same mechanism, unrelated origin.

### Artifact Output

When skill generation succeeds, the console displays:

```
✅ SKILL GENERATED

Name: assistant.analyze_local_files
Type: learned-experience
Quality: 0.8 (3/3 reviewers: REFUTED)

Methods:
  • E2E test in k=1 scenario
  • Root-cause diagnosis on loss signal
  • Adversarial review (0 CONFIRMED findings)

Generated: 2026-08-20T14:33:45Z
File: ~/.claude/skills/assistant.analyze_local_files.md

[Use Now] [View Code] [Schedule Review] [Share]
```

---

## Quality Gates

### Phase 1: Spec Review (fail-fast)

- ✅ Skill purpose is concrete and singular
- ✅ Name follows `<scope>.<name>` convention
- ✅ Doesn't duplicate existing skill (checked via `skill_list()`)
- ✅ Scope is valid (assistant / project / global)

**Fail:** User can edit spec and retry, OR reject and start over.

### Phase 2: Linting (fail-closed)

- ✅ Valid Markdown (method must open with a `#` heading)
- ✅ No prompt-injection patterns: `<|im_start|>` / `<|im_end|>` as ESCAPED
  literals, plus line-anchored role markers (`^\s*system\s*:`,
  `^\s*instructions\s*:`). Storing them unescaped made `<|im_start|>` the
  regex alternation `<` | `im_start` | `>`, which rejected any skill body
  containing a single angle bracket (ADR-0405).
- ✅ No embedded secrets (scan for API keys, passwords)
- ✅ Dependencies can be resolved (or auto-installed if safe)
- ✅ Name matches `SKILL_NAME_RE` = `^(assistant|project)\.[a-z_]+$`
- ✅ Purpose within `PURPOSE_LEN` (20–200), method within `METHOD_LEN` (100–5000)
- ✅ SkillForge's own linter (fail-closed) at registration time

**Normalise → validate → repair once → validate (ADR-0405).** Phase 1
generates freely and this gate is fail-closed, so without a step in between a
formatting slip destroys a run that already cost minutes of engine time. Both
live failures were exactly that: `assistant.json-syntax-check` and
`Purpose length 201 outside range [20, 200]`.

1. `normalize_spec` — meaning-preserving only: name separators → `_`,
   whitespace collapse, ```` ``` ````-fence and leading-blank-line stripping,
   over-long purpose trimmed at a sentence boundary (or a word boundary with
   `…`). Costs no engine call. Too-short is never padded.
2. `SkillValidator.collect_violations` — ALL violations, not just the first.
3. One `SkillPlanner.repair` call with that list, for what normalisation
   cannot fix (method length, forbidden patterns).
4. `validate` — raises exactly as before if anything survives.

**Fail:** Skill-Creator returns errors; user cannot override.

### Phase 3: LDD Convergence (5-iteration hard limit)

- ✅ Loss decreases from k=1 to k=5 (or stagnates ≤ 2 iterations)
- ✅ At least 1 E2E test scenario runs per iteration
- ✅ Diagnosis via root-cause-by-layer (not guesswork)

**Escalation if k_max hit:** Return findings + ask user for direction.

### Phase 4: Adversarial Review (0-finding target)

- ✅ 3 independent reviewers vote in parallel
- ✅ Verdict: ALL dimensions must be REFUTED (no CONFIRMED findings)
- ✅ PLAUSIBLE findings trigger Phase 3 re-entry (up to 1 more loop)

**Escalation if findings persist:** Skill held for manual review.

### Phase 5: Promotion (all-or-nothing atomic)

- ✅ Skill file written to `~/.claude/skills/<name>.md`
- ✅ SkillForge registry updated (atomic transaction)
- ✅ Auto-grade bootstrap seed: +0.3 (non-zero for injection)
- ✅ Concept-gate signal emitted (if generalizable pattern detected)

**Rollback on any step failure:** Clean up partial state, report error.

---

## Dialog Examples

### Example 1: Simple Utility Skill (converges k=2)

```
User: "Erzeuge einen Skill der JSON-Dateien validiert."

→ PHASE 1: PLANNING
  Spec: assistant.validate_json
  Purpose: "Check JSON files for syntax errors and report findings"
  Dependencies: none (uses Python json module)
  
→ PHASE 2: VALIDATION ✅ PASS
  
→ PHASE 3: LDD
  k=1: Test on sample invalid JSON → Loss: "Doesn't show line numbers in errors"
       Fix: Add line-number formatting to Method
  k=2: Re-test → Loss: none (converged) ✅
  
→ PHASE 4: ADVERSARIAL REVIEW
  Reviewer 1 (Correctness): REFUTED
  Reviewer 2 (Simplification): REFUTED (instructions are clear and concise)
  Reviewer 3 (Scope): REFUTED (only validates JSON, doesn't transform it)
  
→ PHASE 5: PROMOTION ✅
  Generated: ~/.claude/skills/assistant.validate_json.md
  Quality: 1.0 (converged k=2, all reviewers REFUTED)
```

### Example 2: Complex Skill (needs Phase 3 re-entry after review)

```
User: "Create a skill that analyzes code coverage."

→ PHASE 1: PLANNING
  Spec: assistant.analyze_code_coverage
  Purpose: "Parse coverage reports and identify gaps"
  Dependencies: regex (built-in)
  
→ PHASE 2: VALIDATION ✅ PASS

→ PHASE 3: LDD
  k=1: Test on sample coverage.xml → Loss: "Doesn't handle multiple report formats"
       Fix: Add format-detection logic
  k=2: Re-test → Loss: "Format detection is O(n²), slow on large reports"
       Fix: Use streaming parser
  k=3: Re-test → Loss: none (converged) ✅

→ PHASE 4: ADVERSARIAL REVIEW
  Reviewer 1 (Correctness): PLAUSIBLE - "Skill doesn't document which formats supported"
    → Phase 3 re-entry (k=4)
    Fix: Add explicit format list in Method
    k=4: Re-test ✓ Converged
    Reviewer 1 (retry): REFUTED ✓
  
  Reviewer 2 (Simplification): REFUTED
  Reviewer 3 (Scope): REFUTED
  
→ PHASE 5: PROMOTION ✅
  Generated: ~/.claude/skills/assistant.analyze_code_coverage.md
  Quality: 0.7 (converged k=4, 1 PLAUSIBLE → re-entry)
```

---

## Implementation Notes

### When to Use Skill-Creator

✅ **Good fit:**
- User wants a reusable method (e.g., "create a skill for analyzing X")
- Pattern will recur 2+ times across tasks
- Skill generalizes beyond the immediate task
- User wants it persisted for future sessions

❌ **Not a fit:**
- One-off workaround for this task only (just do it inline)
- Skill requires real-time ML training (scikit-learn, torch) — too heavyweight
- Skill needs access to private data (config secrets, personal files) — sandbox violation

### Interaction with Other Gates

| Gate | Interaction |
|------|-------------|
| **ADR-Gate** | Skill-Creator does NOT require ADR (skills are behavior, not architecture). If a skill-generated skill is SO generalizable it should be a project pattern, *then* Concept-Gate may fire and mint an ADR companion. |
| **Concept-Gate** | Fires *after* Skill-Creator completes, if the generated skill is generalizable across 3+ distinct tasks. Skill-Creator may recommend "promote to Concept" in output. |
| **E2E-Wiring-Proof** | Skill-Creator's Phase 3 includes E2E testing by construction. New skills are tested before promotion, so the gate is satisfied. |

---

## Operator Notes

### Monitoring

Watch for skills that:
- Repeatedly enter Phase 4 and fail adversarial review (quality issue)
- Hit k_max in Phase 3 without converging (may indicate loss function is noisy or fix space is narrow)
- Have 0 grades after 1 week (nobody used it; reconsider scope)

### Tuning

**Loss threshold:** In Phase 3, what counts as "loss"?
- Loss is the weighted mean of the scored rubric (clarity 0.35,
  executability 0.35, scope 0.20, coupling 0.10), each dimension in [0, 1].
- Converged at `loss <= SkillTester.convergence_threshold` (0.15) — one minor
  remark is tolerated, a perfect score is not required.
- At `k_max` without convergence: `SkillTester` raises `LDDIterationError` by
  default (the LDD contract). The console orchestrator passes
  `escalate_on_k_max=False`, keeps the best iterate and subtracts 0.2 from
  `quality_score` instead of discarding the run.

**Quality score:** scored per review DIMENSION — clean 1.0, plausible-only
0.5, any confirmed 0.0 — averaged over the panel, minus 0.2 for a
non-converged LDD loop. The earlier per-finding penalty
(`1.0 - confirmed*0.3`) saturated at four findings, and the reviewers
routinely return five to ten, so every run read 0%. The findings travel with
the artifact and are shown in the console, so a low score comes with its
reasons. A CONFIRMED verdict is the reviewer's own claim — there is no
independent verification pass behind it.

**Reviewer harshness:** How critical are adversarial reviewers?
- Default: ALL reviewers must REFUTE (strict)
- Option: MAJORITY REFUTED (lenient, faster promotion) — use for high-churn skills

**Bootstrap grade:** New skills start at +0.3; this is intentional (organic grading takes over after a few uses). Don't raise this or all skills will bypass early filtering.

---

## Next Steps (v1.1+)

- **Skill templates:** Pre-built skill patterns the user can customize ("create a skill from template: data-analysis")
- **Skill composition:** "Combine skills X and Y into a composite skill Z"
- **Feedback loop:** "This skill was helpful" → auto-grades +0.1; "This skill didn't work" → triggers re-review
- **Versioning:** Track skill evolution; allow rollback to prior versions
- **Sharing:** Export skills to gists / team repos (with permission controls)

---

## Related

- [[ADR-0405]] — Skill-Creator runs on the Claude Code engine (this document's implementation)
- [[ADR-0313]] — Skill Persistence & Architecture
- [[ADR-0318]] — Style Preferences & User Model
- [[CONCEPT-0001]] — Self-Learning Project Concept Archive (parent pattern)
- Skill: `loop-driven-engineering` (drives Phase 3 LDD iterations)
- Skill: `cel_adversarial_dialectical_review` (drives Phase 4 reviews)
- Skill: `e2e-wiring-proof` (validates skill reachability)

---

**Last Updated:** 2026-08-20  
**Status:** IMPLEMENTED — 5 phases live behind `POST /v1/console/skill-creator/generate`
(ADR-0405). Known gap: promoted skills land in `~/.claude/skills/`, not in the
tenant-native skill tree (ADR-0007).  
**Effort:** ~800 LoC (core phases), ~400 LoC (UI), ~600 LoC (tests)
