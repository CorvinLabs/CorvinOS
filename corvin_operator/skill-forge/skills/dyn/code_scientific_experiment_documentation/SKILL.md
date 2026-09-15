---
name: code_scientific_experiment_documentation
description: Document an experiment continuously from the start with scientific rigor but layperson-readable prose, in an append-only tenant lab notebook, and structure its metrics as a reusable LDD loss vector so the system under test can later be trained on them.
---

# Scientific experiment documentation (rigorous, but readable by a layperson)

Use this whenever a task is really an **experiment** — a measurable question with an honest
yes/no, not a feature build. It produces a durable, tenant-scoped record that a non-expert can
follow and that a later LDD training loop can consume as loss.

## Where it lives
- Tenant-scoped: `<corvin_home>/tenants/<tenant>/experiments/exp-NNN-<slug>/`.
- Files: `lab-notebook.md` (the running log), `metrics-as-loss.json` (machine-readable loss spec),
  `README.md` (index), `data/` (evidence snapshot: raw records + reports + task suite).

## Two audiences, one document
Write so a domain expert trusts it AND a layperson understands it:
- Open every notebook with a **plain-language summary** — no jargon, one short section anyone reads.
- Define each technical term in _italics_ the first time it appears ("a _token_ is the unit an AI
  charges by, roughly a word-piece").
- Prefer a concrete number over an adjective ("−55.8 % cost", not "much cheaper").

## Structure of every entry (non-negotiable)
**Goal -> Method -> What we did -> Observations -> Conclusions.** Keep failed attempts and dead
ends — they are evidence. Date every entry. The notebook is **append-only**: never rewrite a prior
entry; correct it only with a new dated entry that says what changed and why.

## Scientific honesty (the point of the whole thing)
- State a **falsifiable hypothesis** (H0/H1) up front; report what the data shows, not what was hoped.
- Separate **CONFIRMED** from **PLAUSIBLE**; confidence level is part of the result.
- Never sell a saving the quality gate did not clear. Report the honest null result plainly.
- Name every **confounder** you removed and every **nuisance** you held fixed.
- Prefer a real measurement (live run, real prices) over an estimate; never hard-code a baseline.

## Measurement rules that keep numbers real
- Measure the **sum / the true quantity**, never one convenient backend field (a single field once
  captured 0.003 % of the real value and passed green).
- Drive the **real path**, proven by a call-site test — not a mock.
- Detect interference: record the **actually-resolved** parameter per sample; if it disagrees with
  the intended label, mark the sample tainted and DROP it (measure reality, not the label).
- Variance is a **result**, not noise: stratify tasks along the axes the effect should vary on, and
  size the sample by tasks (generalization) not reps (which saturate).

## Structuring metrics as an LDD loss (so the system can be trained later)
In `metrics-as-loss.json` record:
- **theta (controllable parameters)** of the system under test — what training would tune.
- **Nuisances** to hold fixed or randomise (model, cache regime, runtime state, confounder flags, seed).
- A **loss VECTOR**, not one averaged scalar, whenever components genuinely trade off (e.g.
  cost vs quality). Each component: formula, direction (minimize), unit, weight, status, which theta
  it depends on. Add regularisers (e.g. penalise verbosity).
- A **data contract**: which raw/aggregate files are the loss samples and the exclusion rule.
- **Calibration precondition**: a new metric is advisory-only (informs search direction) until it
  passes the define-metric gate (n>=5 predicted-vs-observed pairs, MAE<=0.15); only then may it gate.

## Closing an entry
End with observations that update the hypothesis and a short "open threads / next steps" list, so
the next session (or a future agent) can resume without re-deriving the state.
