---
name: e2e-driven-iteration
description: Use at the start of every inner-loop iteration where the goal is to fix a bug / close a failing test / reach a green E2E. Forbids editing code without first running the E2E to capture a fresh loss signal, and forbids declaring "done" without the E2E passing. Makes the cycle "E2E → loss → 5-why-by-layer → fix → E2E" the only admissible rhythm.
---

# E2E-Driven-Iteration

## Overview

The existing dach-skill `loop-driven-engineering` says "run E2E when warranted." That's too soft. Under time pressure, "warranted" silently becomes "never" and the iteration degrades into blind-patching based on stale assumptions.

**Core principle:** when you are in a fix-loop (trying to close a failing signal), **every iteration begins and ends with an E2E run of the failing case**. The E2E is the forward pass; its output is the loss; the delta between iterations is the gradient signal that tells you whether the last edit helped or regressed.

No E2E at the start → no loss measurement → your edit is speculative. No E2E at the end → you don't know if you converged or just stopped.

See [`../../docs/ldd/convergence.md`](../../docs/ldd/convergence.md) §5 for how this skill implements the loss trace across the test pyramid.

## When to Use

Invoke at the start of **every inner-loop iteration** whose purpose is:

- Fixing a failing test
- Closing a rejected gate
- Resolving a production incident
- Debugging an unexpected behavior
- Converging on any goal where "is it fixed yet?" is the question

Do **not** use for: new-feature work where there's no failing E2E yet (write the E2E first, then enter this loop), pure refactors (no behavioral E2E to run), documentation-only changes.

## The Five-Step Iteration

```
k-th iteration:
  1. Run E2E        → loss_k = measured rubric score / failing items
  2. Compare        → Δloss_k = loss_{k-1} − loss_k  (was the last edit a step or a random walk?)
  3. Diagnose       → invoke root-cause-by-layer, name layer 4/5
  4. Fix            → one focused edit, smallest coherent unit, at the named layer
  5. Run E2E again  → loss_{k+1} becomes loss_k of iteration k+1
```

**Do not skip step 1** — "I know what broke, I'll just fix it" is the anti-pattern. Until you measured the current state, you don't know if the last change helped, hurt, or was a no-op.

**Do not skip step 2** — if `Δloss_k ≤ 0`, the previous edit did not help. You are in oscillation or a local minimum. Invoke `loss-backprop-lens` before proposing another edit.

**Do not skip step 5** — without the post-edit run, "fixed" is a claim, not a measurement.

## Red Flags — STOP, the loop is degrading

- "I'll fix it then run the E2E at the end" → no loss measurement per step; you won't notice regressions
- "The E2E is slow, I'll skip it this iteration" → you lost the gradient
- "The last edit probably worked, don't need to re-run" → you don't know
- "E2E keeps failing the same way, let me try something else" → `Δloss` is not going down; step size wrong (invoke `loss-backprop-lens`)
- "I'll run the unit test instead, same signal" → only if the unit test covers the same failure surface; otherwise you're measuring a different loss
- "Let me batch 3 fixes and run once" → conflates 3 gradient steps; if one regressed, you can't attribute

## E2E selection — cheap before expensive

If the failure reproduces at a cheaper pyramid tier (unit, integration), iterate there first. **Only** escalate to full E2E when the cheap tier can't reproduce.

| Tier | Use as iteration gate when |
|---|---|
| Unit | The failure is localized to one function / class / module with no external deps |
| Integration | The failure crosses module boundaries but is deterministic with test doubles |
| E2E (fake externals) | The failure involves orchestration but external APIs can be mocked |
| E2E (real externals) | The failure involves real LLM / DB / network non-determinism |

Running full E2E for a typo catchable at unit tier is a pyramid violation; it wastes budget and slows the gradient signal. Running only unit tests for a cross-boundary contract bug misses the real loss surface.

## Budget interaction

This skill runs **inside** `loop-driven-engineering`'s `K_MAX = 5`. The E2E-per-iteration cost sets a floor on how fast iterations can happen; if one E2E takes 30 minutes, your total budget is 2.5 hours. Plan accordingly:

- Before entering the loop, estimate E2E cost and multiply by 5 — that's your budget ceiling
- If estimate > available time, either cheapen the E2E (subset of scenarios, fake externals) OR accept you'll hit K_MAX and escalate
- At K_MAX: escalation per `loop-driven-engineering`'s §Escalation — with the added requirement of attaching the `loss_k` trace across all iterations

## How to Apply — checklist

1. **Define the failing E2E.** Exact command, expected state, measured state. Save the output of iteration 0 as `loss_0`.
2. **Enter the loop.** At the start of each iteration: run E2E, capture `loss_k`, compare to `loss_{k-1}`.
3. **Interpret the delta.** Negative → progress, continue. Zero → no-op, rethink. Positive → regression, revert immediately and re-diagnose.
4. **Diagnose** via `root-cause-by-layer`. **Calibrate** via `loss-backprop-lens`.
5. **Edit** smallest coherent unit.
6. **Rerun E2E.** That's `loss_{k+1}`.
7. **Close** only when E2E is fully green AND the regularizers (contracts, docs) are honored — hand off to `docs-as-definition-of-done`.

## Common Rationalizations

| Excuse | Reality |
|---|---|
| "The E2E is slow, every iteration is expensive" | Yes. That's why K_MAX = 5 and you must think, not guess. Slow E2E is itself a gradient — it tells you to invest in a faster test surface. |
| "I'll skip the start-of-iteration run to save time" | You saved 5 minutes and lost the ability to tell if your last edit helped. Net loss. |
| "The loss barely moved, close enough" | "Close enough" is a claim, not a measurement. Either the rubric is too lax (outer-loop issue → `method-evolution`) or you're stopping before convergence. |
| "E2E passed once on try 2, ship" | You owe a `reproducibility-first` check. One pass is one sample. |
| "I'll fix all three issues in one iteration, E2E at end" | You cannot attribute which edit helped and which regressed. Run per edit. |

## Relation to other skills

- **`reproducibility-first`** runs before this skill enters the loop — you must have a reproducible signal to begin.
- **`root-cause-by-layer`** runs at step 3 of each iteration — diagnosis.
- **`loss-backprop-lens`** runs when `Δloss ≤ 0` to recalibrate step size.
- **`loop-driven-engineering`** is the parent — this skill is what it dispatches during a fix-loop.
- **`docs-as-definition-of-done`** runs at close — when the E2E is green, the loop isn't closed until docs are synced.

## Real-World Cue

If your iteration log for a bug fix doesn't show a `loss_k` trace — one number per iteration, visibly decreasing toward zero — you were not doing E2E-driven iteration. You were hoping.
