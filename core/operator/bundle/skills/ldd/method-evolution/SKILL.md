---
name: method-evolution
description: Use when the same rubric violation, the same kind of symptom-patch, or the same escalation happens across 3+ distinct tasks — meaning the problem is in the method (skill, prompt, rubric), not in the tasks. Forces a disciplined outer-loop step: name the pattern, propose a skill/rubric change, measure Δloss before and after, roll back on regression.
---

# Method-Evolution

## Overview

If your agent keeps making the same kind of mistake across completely different tasks, the mistake is not in the tasks — it's in the **method** that's instructing the agent. That means a skill is too vague, a rubric is too permissive, a prompt lets a rationalization slip through.

Updating the task fixes *this* bug. Updating the method fixes the bug *and* prevents the next 10.

**Core principle:** method-evolution is SGD on the **θ-axis** (the skills, prompts, rubrics themselves), not the inner-loop code axis or the refinement y-axis. It is the rarest and most load-bearing of the three loops — and the only one that can actually improve the bundle over time.

See [`../../docs/ldd/convergence.md`](../../docs/ldd/convergence.md) §1 for the three-loop model and §3.4 for the "moving-target loss" anti-pattern that this skill prevents.

## When to Use

Invoke **only** when **all** of:

- You have observed the **same rubric violation / symptom-patch / escalation** in ≥ **3 distinct tasks** across different domains
- The method's Red Flags list does not already cover this case
- Patching each task individually would not prevent the next occurrence
- You have a **task suite** (≥ 5 tasks, varied) you can re-run to measure Δloss across the method change

Do **not** use when:

- You saw the pattern once or twice — that's noise, not signal. Wait for 3 distinct occurrences.
- The pattern is specific to a single task/domain — update the task, not the method.
- You don't have a suite to measure against — changing the method without measurement is drift, not evolution.
- The problem is that the method is verbose or ugly — refactoring style is not evolution. Evolution requires a behavior change backed by measurement.

## The Evolution Step

A single method-evolution step has a rigid structure. Treat it as an experiment, not a refactor.

### 1. Pattern identification

Name the pattern concretely:

> "In tasks T1 (root-cause debugging on a contract error), T2 (dialectical recommendation on a retry design), and T3 (refinement of a technical doc), the agent bypassed the [specific rubric item R] via the rationalization [specific phrase]. The skill's Red Flags list does not currently cover this phrase."

No hand-waving. The pattern must be specific enough that a reader can grep for its occurrence in future runs.

### 2. Method-change proposal

Propose **one** change. Not three. One.

| Valid changes | Invalid changes |
|---|---|
| Add a Red Flag phrase to the skill's list | "General rewrite of the skill" |
| Add a Rationalization → Reality row | "Make it shorter" |
| Tighten a rubric item's wording | "Add more examples" (unless each example addresses a measured gap) |
| Add a counter-case to an Anti-Pattern table | "Reorganize sections" |
| Strengthen a description's trigger phrase | "Make it sound better" |

If you can't name which line of the method-artifact changes and which rubric item it enforces, you don't have a proposal — you have an urge.

### 3. Measurement (required)

Compute `Δloss_method` across the task suite:

```
Δloss_method = (Σ_t loss(t | method_before)) / |suite|  −  (Σ_t loss(t | method_after)) / |suite|
```

This requires:
- The task suite fixtures (stored, reusable)
- The original method-artifact version (git archive before edit)
- The edited method-artifact
- A way to re-run each task under each method and score against rubrics

On Claude Code, `scripts/evolve-skill.sh` (optional tooling) can automate the rerun. On other platforms, run the suite manually.

Acceptance criteria:
- `Δloss_method > 0` on the task that motivated the change
- `Δloss_method ≥ 0` on every other task in the suite (no regressions)
- **Otherwise roll back and halve the learning rate** (make a smaller change and re-try)

### 4. Commit

The commit for a method-evolution step has a specific shape:

```
evolve(skill-name): <one-line pattern>

Observed pattern across tasks T1, T2, T3:
  <the rationalization / violation, verbatim>

Change: <what line changed in the skill-artifact>

Measured Δloss_method across suite of N tasks: +X.Y
Regressions: none / [task T4: +0.Z, rolled back]

Suite fixtures: tests/fixtures/*/
```

No anonymous "improve skill" commits. Each evolution step is auditable and reversible.

## Red Flags — STOP, this isn't evolution

- "I want to make the skill clearer" → refactoring prose, not evolving behavior
- "The skill feels long, let me trim it" → style, not evolution; don't pretend otherwise
- "I'll update the rubric to match what the agent did" → that's **moving-target loss** (see `docs/ldd/convergence.md` §3.4), forbidden
- "We saw this pattern once last week" → not enough signal; wait for 3 occurrences
- "I don't need to measure, the change is obviously better" → exactly the symptom this skill prevents in code; same rule applies to the method itself
- Editing the skill without running the suite → your edit is a random direction in method-space

## Rollback discipline

Rollback is not a failure state — it is the **most common** outcome of a method-evolution step. Most proposed changes don't improve mean loss across the suite; they improve one task and regress another.

- If `Δloss_method ≤ 0` on the motivating task → the change doesn't work; revert.
- If `Δloss_method > 0` on motivating task but a regression appears on another → revert, then propose a narrower change.
- Track every rollback. The pattern of rollbacks across evolution attempts is itself a signal — if five proposals all rolled back, the skill may be at a local optimum and you need `iterative-refinement` on the skill itself (refinement of its prose), not further behavior evolution.

## Anti-Patterns

| Pattern | Why wrong | Do instead |
|---|---|---|
| Edit a skill in response to one bad run | Noise, not signal | Wait for 3 distinct occurrences |
| "Improve" a skill without a suite | No measurement = drift | Build the suite first, even a minimal 3-task one |
| Bundle multiple method changes in one step | Cannot attribute which change caused `Δloss` | One change per step |
| Edit the rubric to match current behavior | Moving target loss | Edit the behavior to match the rubric; rubrics are stable artifacts |
| Skip the suite-rerun "because it's expensive" | Method change without measurement is worthless | Either pay the cost or don't change the method |
| Evolve without version control | You cannot roll back | Every method-evolution step is a git commit, tagged |

## Relation to other skills

- **`loop-driven-engineering`** dispatches this skill when the same failure recurs — "same gate rejecting with the same reason ≥ 2 iterations → stagnation" is an inner-loop signal; "same stagnation pattern across 3 tasks" is an outer-loop signal.
- **`iterative-refinement`** is the y-axis counterpart — refinement polishes *a deliverable*; method-evolution evolves *the skills that produced deliverables*.
- **`drift-detection`** feeds this skill — patterns found by drift scans are candidates for evolution steps.
- **`dialectical-reasoning`** runs over every proposed evolution step: thesis (proposed change), antithesis (what it might regress on, who would disagree), synthesis (narrower change or replaced).

## How to Apply — checklist

1. **Collect evidence.** Three distinct tasks, same pattern. Cite them with commits/artifacts.
2. **Name the specific pattern.** One sentence, ideally greppable.
3. **Propose one change.** Name the file, name the line, name the rubric item.
4. **Measure.** Run the suite under method_before and method_after. Record `Δloss_method`.
5. **Decide.** `Δloss > 0` no regressions → commit with the canonical message shape. Otherwise → roll back, halve learning rate, retry with a narrower change.
6. **Log to outer-loop history.** Keep a record of evolution attempts — accepted and rolled-back. Future evolution decisions depend on this history.

## Common Rationalizations

| Excuse | Reality |
|---|---|
| "Measuring takes too long" | Not measuring is how methods silently rot. Allocate the time. |
| "Only one task showed the pattern, but I'm sure it generalizes" | Sure is not evidence. Wait for the second and third occurrence. |
| "The suite doesn't exist yet, I'll evolve first and build the suite later" | Evolution without measurement is drift. Build even a 3-task suite first. |
| "My change is obviously better, no measurement needed" | The bundle's own `loss-backprop-lens` forbids this exact move at the code level. Same rule at the method level. |
| "If I rollback, the team will think I failed" | Most proposals roll back. That's the skill working. Silent non-rollback is how the method drifts. |

## Real-World Cue

A healthy method-evolution history is mostly failed experiments. If every evolution step you logged was accepted without rollback, you're not being rigorous — you're either measuring wrong, skipping measurements, or only proposing trivial changes. A bundle like LDD should show ~1 accepted evolution step per 3–5 attempts.
