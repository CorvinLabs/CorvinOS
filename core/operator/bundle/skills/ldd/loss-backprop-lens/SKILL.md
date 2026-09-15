---
name: loss-backprop-lens
description: Use when deciding whether to edit code at all, how large the edit should be, or whether a "working" fix actually generalizes. Applies when iterating on a failing test/run, chasing a flake, committing a sequence of small patches, or tempted to ship on one data point. Frames code changes as gradient steps and forbids overfitting to a single sample or a single test.
---

# Loss-Backprop-Lens

## Overview

Working on a codebase is **gradient descent on code**. A test / CI run / E2E is a **forward pass**. The difference between expected and actual is the **loss**. Every edit is a **step** toward lower loss. This is not a metaphor — it dictates which edits are admissible and which are noise injected into your parameters.

**Core principle:** A fix that reduces training loss (the current failing test) but raises generalization loss (future unseen inputs or sibling tests) is **overfitting** and is rejected, regardless of whether CI turns green.

This skill is a *mental frame*, not a rigid procedure. Apply it when one of the triggers below fires; combine with `root-cause-by-layer` for the mechanics of diagnosis.

## When to Use

- You have **one failing signal** and are tempted to ship a fix
- You've made **3+ small patches to the same area** in a short window
- A test is **flaky** and you want to retry / widen / skip
- A fix **works for this test** but you aren't sure about siblings
- You're deciding between a **local tweak** and a **structural change**

Do **not** use for: greenfield feature work, pure refactors with no failing signal, single-concept bug fixes where the contract is obvious.

## The Mapping

| ML concept | Code equivalent |
|---|---|
| Forward pass | Running the test / job / E2E |
| **Loss** | Difference between expected and actual output |
| Backprop | Tracing symptom → structural origin (use `root-cause-by-layer`) |
| Parameters (θ) | The code: logic, prompts, rules, tests, configs |
| Gradient | The causal story "symptom → which parameter, in which direction" |
| Learning rate | Edit aggressiveness: local tweak vs. redrawing a boundary |
| **Overfitting** | Symptom patch that works **here** but doesn't generalize |
| Regularization | Contracts, layer integrity, invariants — what keeps edits honest |
| Training set | Tests you've already seen |
| **Test set** | Inputs / tests you **haven't** seen |
| Batch noise | LLM, network, filesystem non-determinism — one sample is noisy |
| Local minimum | "Green" state that's fragile: all tests pass, small input change breaks it |

## The Four Rules

### 1. No update on a single sample

One failing run is a **noisy gradient**. It may be a bug; it may be LLM format drift, infra flake, race condition, cosmic ray. Before editing code:

- Reproduce the failure. If it doesn't reproduce in ≥2 of the next N runs, you have noise, not signal — do not edit.
- If it does reproduce: now you have a gradient, walk `root-cause-by-layer`.

**Exception:** The single sample itself is unambiguous signal (a clear contract violation diagnosable from the log, e.g. `ValueError: unsupported operand type(s) for +: 'Decimal' and 'NoneType'` on a known-migrated column). Then the gradient is in the log, not in repetition.

### 2. Gradient before edit

You do not have an edit until you have a causal story: **symptom → mechanism → contract → structural origin → conceptual origin** (see `root-cause-by-layer`). Without the gradient, any edit is a random direction in parameter space — on average, it raises generalization loss even when it happens to lower training loss.

### 3. Learning rate must match the loss structure

The size of your edit must match the scope of the loss:

| Loss pattern | Admissible edit size |
|---|---|
| One-off, surface-level symptom | Local tweak at the named layer |
| Same defect recurring across tests / iterations (≥3 in a short window) | **Architectural** edit — redraw the boundary, introduce a missing abstraction, even if it costs more lines today |
| One "structural" bug affecting one call site | Fix at the boundary; do not escalate to architecture |

**Red flag:** 3+ consecutive commits in the same function, each fixing a different small symptom. You are inside a local minimum doing gradient descent on the wrong objective. Stop, lift your head, consider an architectural edit or deleting the feature.

### 4. Regularization beats local fix

A fix that makes the current test green but violates a contract, a layer boundary, an invariant, or a published rule **raises generalization loss**. Rejected, even when the gate is satisfied.

Examples of regularizers (things you do not trade for a green gate):
- Explicit contracts between components
- Layer boundaries (domain ≠ transport ≠ persistence)
- Single source of truth
- Invariants (documented, enforced, tested)
- "Explicit over implicit"

If a proposed fix violates any of these, it is an overfit even if it passes locally.

## Red Flags — STOP, you are overfitting

- "One failing run, let me patch it now" → single sample
- "I'll add a retry to handle the flakiness" → training on noise
- "It's a 3-line change, safer than a refactor" → ignoring the loss pattern
- "Each fix is small but the bugs keep coming" → local-minimum trap, step size too small
- "This fix passes THE test" → check the *unseen* test
- "The test is wrong, let me adjust the assertion" → fitting test to code instead of code to spec

## How to Apply — checklist

1. **Is the signal real?** Reproduce or find the unambiguous fingerprint in the log. If neither, do not edit.
2. **What's the pattern?** One-off or recurring? Look at the last 3 commits in the touched area.
3. **Pick the right step size:** local for one-off at a clear boundary; architectural for recurring or for a repeated local-minimum dance.
4. **Check generalization:** "If a sibling input / sibling test were in the suite, would my fix still be correct?"
5. **Regularize:** Does the fix respect contracts, boundaries, invariants? If not, it's overfit — reject even if green.

## Common Rationalizations

| Excuse | Reality |
|---|---|
| "One sample is enough, I can see the bug" | Sometimes — only when the log itself is unambiguous signal. "I can see it" is not the same as "I checked reproducibility." |
| "Small diff is safer" | A small diff in a local-minimum loop is 5 small diffs you haven't written yet. The architectural edit has a smaller total diff. |
| "This is just a quick fix, not architecture" | Five consecutive quick fixes IS architecture — the kind nobody designed. |
| "The retry handles the flake" | You just trained on noise. The real gradient is still there. |
| "I'll step back after I ship this one" | You won't. You're in gradient descent with a miscalibrated learning rate; each step lowers the bar for the next. |

## Related

- **`root-cause-by-layer`** — mechanics of computing the gradient (the 5-layer ladder).
- **`docs-as-definition-of-done`** — regularization via documentation: a fix is not done until the doc-level mental model is current.
- **`dialectical-reasoning`** — used at step 4 to force a counter-case on the proposed fix before shipping.

## Real-World Cue

If your recent commit log in one function reads:
```
fix: strip whitespace
fix: handle empty string
fix: lowercase before compare
fix: null-check result
fix: strip quotes     ← you are here
```
…you are not fixing bugs. You are climbing an ungoverned gradient. Stop, step back, write the missing abstraction (a normalizer pipeline, a contract, a typed input), then delete the five patches.
