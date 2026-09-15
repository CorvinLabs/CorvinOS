---
name: iterative-refinement
description: Use when a deliverable (document, diff, design, report, code module) is complete but "good enough, not great" and you want to improve it with a targeted re-iteration instead of starting over. Forbids re-running the task from scratch when refinement is more efficient. Operates on the y-axis (output) not the θ-axis (code or method).
---

# Iterative-Refinement

## Overview

Most agents, when told "this is okay but can be better," do one of two wrong things:

1. **Re-run the task from scratch.** Wastes budget, loses whatever was good in the first attempt, gambles on random variation.
2. **Polish by gut feel.** No loss signal, no gradient, no stopping criterion. Converges to the agent's aesthetic prior, not to a better deliverable.

**Core principle:** refinement is a *second* gradient-descent pass, on the **y-axis** (the deliverable itself), using a **specific gradient** derived from the previous output's defects. The inputs, the task, the code, the skills — all held fixed. Only the deliverable changes.

This is the skill-level equivalent of `awp refine` (AWP's normative refinement mode). See [`../../docs/ldd/convergence.md`](../../docs/ldd/convergence.md) §1 for the three-loop model.

## When to Use

Invoke when **all** of:

- A deliverable (document, code module, design artifact, plan) is **complete** (not partial, not stuck)
- The deliverable is **usable but imperfect** — passes the minimum bar, fails the bar you actually want
- Re-running the task from scratch would **lose** specific good parts of the current output
- You can **name** the defects concretely (missing sections, weak examples, ambiguous language, missed requirements, incomplete coverage)

Do **not** use when:

- The deliverable is broken (use `loop-driven-engineering` + `root-cause-by-layer` — that's an inner-loop problem, not refinement)
- You don't know what's wrong with it (invoke `dialectical-reasoning` first to find the defects)
- The right answer is a completely different approach (refinement preserves the approach; you want re-planning)
- Budget is tight and a second pass is unaffordable (refinement is never free — document the trade-off)

## The Refinement Gradient

The gradient is **derived, not imagined.** Build it from three concrete sources on the previous output:

1. **Critique defects.** Enumerate the specific problems: missing content, weak arguments, wrong details, unclear structure. No hand-waving; each defect points to a location in the output.
2. **Gate rejections.** If the deliverable went through any automated gates (tests, lint, schema, rubric), the rejections are direct gradient signal.
3. **Evaluation deltas.** If the deliverable was scored (by a human, an eval harness, or a rubric), the delta from target score is a numeric component of the gradient.

Compose into a structured prefix for the next iteration:

```
Refinement gradient:
  Defects identified in the previous output:
    - [defect 1 with location]
    - [defect 2 with location]
    - ...
  Gate rejections:
    - [rejection 1 with rule]
    - ...
  Evaluation deltas:
    - Target: X. Actual: Y. Gap: Z.
Produce a revised deliverable that addresses these and does not regress on what was already good.
```

Inject this gradient into iteration 1's context **only**. Subsequent iterations build on the iteration-1 output with their own fresh critique, not the original.

## Budget and Stopping

Refinement runs **under a budget** — unlike code fix-loops, refinement is asymptotic by nature (every deliverable could be slightly better). The budget prevents infinite polish:

| Budget dimension | Default |
|---|---|
| Maximum iterations | 3 (clamped to `[1, 10]`) |
| Budget halving per iteration | Token and time budget halved on each iteration |
| Wall-time cap | 2× the seed deliverable's original production time |
| Stop conditions (any) | regression × 2, plateau × 2, wall-time exhausted, max iterations, empty gradient |

**Empty gradient** = no concrete defects can be named. That is a stop signal, not an invitation to imagine defects. If nothing is wrong that you can name, the deliverable is done.

**Regression** = iteration k's loss > iteration k-1's loss. Immediate revert to k-1 and stop.

**Plateau** = |Δloss| < noise threshold for 2 consecutive iterations. The gradient is exhausted.

## Red Flags — STOP, this isn't refinement

- "I'll just regenerate from scratch" → that's re-planning, not refinement; different skill
- "This could be slightly better" → without named defects you're polishing, not refining
- "Let me run 10 refinement iterations" → budget violation; at most 10, realistic is 2–3
- "The gradient is: 'make it better'" → not a gradient; specific defects only
- "Iteration 2 is worse but iteration 3 might fix it" → refinement is monotonic or stop; no wishful middle
- Re-running the full task when you could preserve 80% of the previous output → you conflated refinement with re-planning

## Distinguishing from the other loops

| Situation | Right loop | Why |
|---|---|---|
| Code is buggy, test fails | Inner loop (`loop-driven-engineering`) | θ = code. Fix it. |
| Code is fine, doc output is weak | **Refinement** | θ = the doc. Revise it. |
| Same rubric violation across 5 projects | Outer loop (`method-evolution`) | θ = the skill. Evolve it. |
| Agent produced a plan that's almost right | **Refinement** | θ = the plan. Sharpen it. |
| Agent produced a plan for the wrong problem | Re-plan via `dialectical-reasoning` | Problem framing is wrong; don't refine. |
| README says X but code does Y | `docs-as-definition-of-done` | Contract violation, not a refinement target. |

If the right loop is not obvious, stop. Running the wrong loop is worse than no loop — it wastes budget and may *regress* the deliverable.

## Anti-Patterns

| Pattern | Why wrong | Do instead |
|---|---|---|
| Regenerate entire doc because "the introduction felt off" | Loses the good sections; burns full budget | List defects, revise targeted sections, preserve the rest |
| Ask the agent "make it better" without specifics | No gradient, output is aesthetic drift | Build the structured gradient (defects + rejections + deltas) |
| Run 10 refinement iterations "for safety" | Budget violation, diminishing returns, likely regression past iter 3 | Hard cap at 3, stop on plateau |
| Accept iter-3 even if it regressed from iter-2 | Non-monotonic refinement is drift | Revert to the best iteration so far; hard-link it as final |
| Mix "fix the code" and "improve the doc" in one refinement pass | Conflates two different axes | Separate: inner loop for code, refinement for doc |

## How to Apply — checklist

1. **Verify you're in refinement scope.** The deliverable exists and is usable. You have concrete defects. Re-running isn't cheaper.
2. **Build the gradient.** Enumerate defects, collect rejections, compute eval deltas.
3. **Set the budget.** Max iterations (default 3), budget halving, wall-time cap (2× original).
4. **Inject the gradient into iteration 1's context.** Subsequent iterations use fresh critique.
5. **Run.** After each iteration: measure loss, check stop conditions.
6. **Terminate.** Hard-link the best iteration as final. If iter-k regressed from iter-(k-1), the winner is k-1.
7. **Log.** Record each iteration's gradient and loss. This is your audit trail and your outer-loop data (see `method-evolution`).

## Common Rationalizations

| Excuse | Reality |
|---|---|
| "Starting over is cleaner" | You lose the prior work and gamble on random improvement. Refinement preserves what worked. |
| "Refinement is pedantic" | Refinement is the difference between shipping B+ and shipping A. For user-facing artifacts that matters. |
| "The budget is tight, I'll just commit what I have" | Fair — document the trade-off. Refinement is always optional, never mandatory. |
| "I can refine forever if I want" | No. Budget halving + plateau detection exists because aesthetic polishing is unbounded. |
| "Use an LLM to auto-refine on any text" | Only with the structured gradient; "make it better" is not a gradient. |

## Real-World Cue

If you're about to ship a document / diff / design that you know is "C+", consider: is the defect list finite and named? If yes, one refinement pass almost always moves it to B+ at 50% additional cost. If no, refinement won't help — you need `dialectical-reasoning` to find the defects first, then refine.
