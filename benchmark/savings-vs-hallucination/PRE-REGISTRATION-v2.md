# Pre-registration — Run 2 (written BEFORE the run)

**Date:** 2026-09-04 (before any v2 measurement)
**Why this file exists:** honesty gate. Predictions are locked here so the result cannot be
retrofitted. A null result is a valid outcome; matching these numbers is not the goal —
measuring correctly is.

## What "correct benchmark" means here (the thing I iterate on)
The instrument is correct when ALL of these hold, verified independently of the outcome:
1. **Detector validity:** abstention/hallucination classifier agrees with a human read on a
   held-out calibration set of ≥ 20 real answers (target ≥ 0.9 agreement), both languages.
2. **Token isolation:** marginal context tokens = `input_total(arm) − input_total(none)` is
   positive, stable across reps, and tracks the designed context size (full ≫ pruned).
3. **No taint:** the resolved arm config per sample matches its label; tainted samples dropped.
4. **Cache separation:** cost reported per regime (cold = cache-creation, warm = cache-read),
   never a cache-averaged single number presented as "the" cost.
5. **Guardrail on the right axis:** gates on `halluc_rate_trap`, not overall.
6. **Reproducibility:** re-running the aggregation on the raw file yields identical numbers.

## Locked predictions (falsifiable)
| # | Quantity | Predicted | Falsified if |
|---|----------|-----------|--------------|
| P1 | marginal tokens full vs pruned | full − pruned ≥ +1000 tok | delta < +500 |
| P2 | correctness answerable, full | 0.85–1.00 | < 0.7 |
| P3 | correctness answerable, pruned | 0.05–0.25 | > 0.4 |
| P4 | hallucination answerable, pruned | 0.00–0.15 | > 0.3 |
| P5 | abstain rate answerable, pruned | ≥ 0.80 | < 0.6 |
| P6 | trap hallucination, full | 0.10–0.30 | (direction test — see P7) |
| P7 | trap hallucination full > pruned | full − pruned ≥ +0.08 | delta ≤ 0 (authority effect absent/reversed) |

## Core claim under test
The real trade-off is **cheap ⇄ honestly-ignorant**, NOT cheap ⇄ hallucinating. Pruning
saves tokens and lowers correctness while the model stays honest (abstains). Hallucination
concentrates at the FULL, authoritatively-framed end (P7). If P7 is falsified (delta ≤ 0),
the authority-effect from Run 1 was noise — a clean, publishable null.

## Decision rule
- P1–P5 are the "instrument works + expected large effects" checks. If any is falsified,
  investigate the instrument BEFORE interpreting P6/P7.
- P7 is the scientific question. Report its result as-is, significance-tested (Mann-Whitney
  or a proportion test on trap events), with n and CI. No spin either way.
