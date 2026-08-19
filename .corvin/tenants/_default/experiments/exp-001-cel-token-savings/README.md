# EXP-001 — Does CEL save tokens? (tenant experiment record)

> ⭐ **SINGLE SOURCE OF TRUTH for experiments.** This tenant folder
> (`~/.corvin/tenants/_default/experiments/`) is the authoritative record of every
> CorvinOS experiment. All results, notebooks, metrics-as-loss specs, and evidence
> live here. Anything elsewhere (repo docs, publications) is a derived copy. Tracked
> in git on the **`experiments`** branch (force-added past `.corvin`'s gitignore).

**Tenant:** `_default` · **Status:** active (pilot complete, 2026-08-19)

This folder is the **canonical, tenant-scoped record** of the CEL token-savings experiment —
continuously documented from the start, and structured so its metrics can later drive **LDD
training of CEL** (metrics-as-loss).

## Contents
| File | What it is |
|---|---|
| `lab-notebook.md` | The running scientific log (Goal→Method→Did→Observations→Conclusions), plain-language, append-only, 13 dated entries. **Start here.** |
| `metrics-as-loss.json` | Machine-readable loss spec: the metric vector, CEL's controllable parameters (θ), nuisances to hold fixed, the data contract for LDD. |
| `acs-big-data-analysis.md` | Deep read-only analysis of ACS (Autonomous Compute Shell) & its Big-Data value — the *other* answer to "too much context" (manager/worker fan-out, per-worker isolation, DSI). |
| `data/` | Evidence snapshot: pilot + cache-stable reports, raw records, the diverse suite, the **memory-grounded suite (v4)** and its **memory fixtures** (`data/memory-fixtures/bench-cel-*.md`). |

## Current headline (honest)
- CEL does **not** cut raw tokens (it injects context) — pilot overall cost ≈ equal, single-turn +1.9 %.
- The proven win is a **cache-class cost** win (cache-stable relocation, ADR-0395): −55.8 % cost/run.
- Multi-turn shows a **modest** CEL advantage (cost-per-correct −3.5 %) but from too few tasks to trust.
- New finding surfaced by the quality dimensions: CEL makes answers **~27 % longer** (a cost driver).

## How this feeds LDD training later
`metrics-as-loss.json` defines a **loss vector** (cost-per-correct, quality-error, quality-stdev,
tokens-to-correct, output regulariser). CEL's tunable parameters (injection threshold, pruning,
rerank-k, brief cap, stages, cache-stable) are θ; the harness emits one loss sample per rep. Metrics
stay **advisory** until they pass the define-metric calibration gate (n≥5, MAE≤0.15), then may gate
a training step. See the notebook §4.

## Reproduce
```
cd CorvinOS/benchmark/token-savings
./run_benchmark.sh --arms cel --tasks tasks/suite-v3-diverse.json --n 10 --model claude-haiku-4-5-20251001
```
Do **not** change Settings during a run — the harness owns the flags and restores them; a mid-run
toggle is auto-detected and those reps are dropped (`tainted`).

## Documented by
The reusable skill `assistant.scientific_experiment_documentation` (SkillForge) — scientific rigor,
layperson-readable, keeps this notebook append-only.
