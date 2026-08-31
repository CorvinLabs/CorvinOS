---
name: reproducibility-first
description: Use when you have observed a failing test, a flaky run, a surprising log line, or any single-sample signal you are about to treat as a gradient. Forbids code edits based on one observation unless the log itself is unambiguous signal. Applies before invoking root-cause-by-layer or proposing any fix.
---

# Reproducibility-First

## Overview

**One observation is not a gradient.** An LLM call is one sample of a distribution. A CI run is one sample of an intermittent fault. A failing test on your machine is one sample of your environment. Acting on a single sample is not SGD — it's noise injection.

**Core principle:** before you use a failure as the basis for a code edit, you must either **(a)** reproduce it at least once more, or **(b)** prove the log is unambiguous signal that does not require repetition.

See [`../../docs/ldd/convergence.md`](../../docs/ldd/convergence.md) §3.3 "Noisy SGD" for why this matters for convergence.

## When to Use

Invoke this skill **before** `root-cause-by-layer` whenever:

- A test failed once in CI but passed locally (or vice versa)
- An LLM returned a malformed response once
- A log message looks surprising but you can't explain why
- A gate rejected on something that seemed fine in review
- An "it's flaky" narrative is forming around a specific case
- You're about to retry "and see what happens"

## The Two Branches

### Branch A — Reproduce before editing

Run the failing case at least **twice more** in an environment as close as possible to the one that failed. Possible outcomes:

| N additional runs | Pattern | Interpretation | Action |
|---|---|---|---|
| 2 of 2 pass | transient | noise — **do not edit code** | Log the incident, check infra / rate limits / upstream outage, close |
| 1 of 2 fails | flaky, rate ≥ 33% | real but intermittent | You have a gradient; invoke `root-cause-by-layer` to find the non-determinism source |
| 2 of 2 fail | deterministic | real signal, robust gradient | Invoke `root-cause-by-layer`, fix at the named layer |

"Acceptable flake rate" is not a constant — it's a loss tolerance. For critical paths, `1 of 100 fails` already warrants diagnosis. For cosmetic gates, `1 of 10` may be tolerated with a tracking ticket.

### Branch B — Unambiguous-signal shortcut

Some logs are load-bearing enough that one sample is already a gradient. The signal must satisfy **all three** criteria:

1. **Deterministic cause.** The error message names the cause in a way that cannot be ambiguous: `TypeError: 'User' object is not subscriptable` at a known code path. There is no "it might also be X" reading.
2. **Explains the failure completely.** Every part of the observed outcome follows from the named cause. No residual "but why did Y happen" remains.
3. **Matches a known contract violation.** The cause maps to a contract (type, invariant, precondition) that either does or does not hold — a binary state, not a probabilistic one.

If any one fails, you're not on Branch B; go to Branch A.

## Red Flags — STOP, you are about to update on a single sample

- "It passed once, let's ship"
- "It failed once, let me patch"
- "The LLM was bad, retry"
- "CI was flaky, rerun"
- "It's probably [infrastructure], I'll patch defensively"
- "I don't have time to reproduce, just fix it"
- "The log looks like X, must be X" *(looks like ≠ is — check Branch B criteria)*
- You find yourself editing code based on what you *suspect* happened, not what the log *proves* happened

## Anti-Patterns

| Pattern | Why it fails | Do instead |
|---|---|---|
| Add retry loop on first flake | Trains on noise; normalizes non-determinism | Reproduce; if real, trace non-determinism to its source |
| `@pytest.mark.flaky` to unblock | Silences the gradient signal | Reproduce; diagnose or delete |
| "Probably a rate-limit blip, defensive catch" | Guesses are not evidence | Check the upstream's status; if no signal, do not edit |
| Ship a fix and watch whether it recurs | Production is not your test suite | Reproduce in dev first; ship only when you have a gradient |
| Rerun until green, then merge | Hiding noise under a commit | If the merge-matters run was noise, the failure is still unresolved |

## The Contract with `root-cause-by-layer`

`root-cause-by-layer` assumes the gradient is real. This skill is the gate that enforces that assumption. The dispatch order is strict:

```
observe failure
  ↓
reproducibility-first  ←— you are here
  ↓
(Branch A: real signal)  or  (Branch B: unambiguous log)
  ↓
root-cause-by-layer  →  loss-backprop-lens  →  fix
```

Skipping this skill means every downstream skill operates on possibly-noise. Cheap to apply (1–2 extra runs), expensive to skip (the wrong edit is more expensive than the reproduction).

## How to Apply — checklist

1. **Log the original observation.** Verbatim. Copy the full error / log line. Note the environment (branch, commit, machine, time).
2. **Pick the branch.** Go through the 3-criterion test for Branch B. If all three hold, document which contract the log violates and proceed to `root-cause-by-layer`. Otherwise go to Branch A.
3. **Branch A: reproduce.** Minimum 2 additional runs. Same environment. Record outcomes.
4. **Classify.** Transient / flaky / deterministic.
5. **Act accordingly.** Transient → do not edit. Flaky or deterministic → proceed with a real gradient.

## Common Rationalizations

| Excuse | Reality |
|---|---|
| "Reproduction takes time I don't have" | The wrong fix takes more time — your own + your reviewers' + production's. Cheapest path is reproduction. |
| "It's obvious what happened" | Branch B exists precisely for this — prove it. Obvious-to-you ≠ unambiguous-in-the-log. |
| "We reproduce after shipping" | "After shipping" is when production reproduces for you, at cost. |
| "Flake rate is low, ignore it" | Low flake rate is a claim requiring evidence. You have one sample. |
| "We'll add a retry and move on" | A retry is a gradient step — just on the wrong parameter. You updated `flaky_retries` instead of fixing the cause. |
| "I'll patch defensively just in case" | Defensive patches without a gradient pollute the parameter space with random directions. Over time: divergence. |

## Real-World Cue

Any commit message containing `fix: flaky X` **without** a linked reproduction (trace, log, second CI run) is a reproducibility-first violation. A proper commit message for a fix following this skill carries evidence: "reproduced 3/5 runs; root cause: race in Y.init".
