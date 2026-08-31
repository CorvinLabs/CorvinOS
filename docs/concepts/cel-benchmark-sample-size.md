# CEL Token Benchmark — how many tasks for a valid sample?

**Date:** 2026-08-19 · Companion to `cel-token-savings-paper-concept.md`.
**Question (shumway):** "Do 20 tasks suffice, or do we need 50 / 100? And it's really about *variance*."

The short answer: **20 is a pilot. The paper's headline needs ~50 diverse tasks; a per-category
("where does CEL help") result needs ~100.** Reasoning below — and the key move is that *variance
is the result we measure, not noise we average away*.

---

## 1. Two different "sample sizes" — don't conflate them

| Knob | What it is | What it controls | Diminishing returns |
|---|---|---|---|
| **T — number of tasks** | distinct pieces of work (factorial, budget-chain, extraction …) | how well the effect **generalizes** to *the kinds of work a user does* | none until T is large — this is the scarce resource |
| **n — reps per task per arm** | same task re-run | **measurement noise** within a task (LLM sampling, cache warm/cold jitter) | strong: past **n≈10–15** the marginal variance cut is tiny |

**The trap:** running 3 tasks × n=200 looks like "600 samples" but it is *3 samples* of the thing
we care about (task-to-task generalization). Reps cannot buy generalization. **More tasks can.**

## 2. The two-level variance model (why tasks dominate)

Each task `t` has a true CEL effect `δ_t` (e.g. −55 % cost). We observe it with rep-noise. The
standard error of the *population* mean effect is:

```
SE(mean δ)  ≈  sqrt(  σ²_between / T   +   σ²_within / (T · n)  )
                        └── task-to-task ──┘   └── rep noise ──┘
```

- `σ²_within` is divided by `T·n` → cheap to shrink with a few reps.
- `σ²_between` is divided by **T only** → the floor on our confidence. Once `n≥~10`, the first
  term dominates and **only adding tasks tightens the estimate.**

And here `σ²_between` is **large on purpose**: CEL should help memory-heavy, multi-turn, context-
reusing tasks and do little (or cost more) on stateless one-liners. High between-task variance is
exactly the signal — so we need enough tasks to (a) pin the mean through that spread and (b)
resolve *which* strata drive it.

## 3. Power analysis for the headline claim

Paired design (each task run off & on), one-sided Wilcoxon signed-rank, α = 0.05, 80 % power.
`d = mean_effect / σ_between` (standardised by the **between-task** SD — the generalization unit):

| True effect size `d` | Tasks `T` needed | Interpretation |
|---|---|---|
| 0.8 (large, consistent win) | **~15** | if CEL clearly helps nearly every task |
| 0.5 (medium) | **~34** | realistic if the win is solid but heterogeneous |
| 0.3 (small net, high spread) | **~85** | if wins and losses nearly cancel on average |

We *expect* medium-to-small on the between-task scale (heterogeneous effect), so the honest
headline target is **T ≈ 35–50**, rising toward **~85–100** if the net effect turns out small.

## 4. Variance is the deliverable → stratify (this is the real reason for 100)

The paper's most valuable result is not one average number — it's **the heterogeneity map**:
*CEL saves X% on multi-turn state-recall, ~0% on stateless one-liners, costs Y% on trivial Q&A.*
To make any single stratum statistically legible you need enough tasks **inside** it:

- `K ≈ 10` archetypes (the `strata.category` axis of `suite-v3-diverse.json`).
- **5–6 tasks/stratum → ~50 tasks:** solid overall + a *coarse* per-stratum signal.
- **8–10 tasks/stratum → ~80–100 tasks:** each stratum individually significant → the publishable
  heterogeneity table.
- **2 tasks/stratum (i.e. T=20):** cannot resolve any stratum; collapses the whole story into one
  noisy mean. **Pilot only.**

The axes that generate the between-task variance (design the strata to span them):
`category` (coding/math/logic/extraction/data/format/refactor/planning/qa/state-recall) ×
`turns` (1 vs N) × `cross_turn_reuse` (does turn N use turn 1?) × `length` (short vs long).
`suite-v3-diverse.json` already tags every task on these axes.

## 5. Recommended plan (pilot → power → full run)

Don't guess `T` — **measure `σ_between` in a pilot, then compute the exact `T`** (honest, cheap):

1. **Pilot:** `suite-v3-diverse.json` (18 tasks, spans 10 strata, 4 multi-turn), `n = 10`, arms
   = CEL-off vs on, confounder-free. → get the mean effect **and `σ_between`**.
2. **Power:** plug `σ_between` into §3's formula for the real `T` at 80 % power.
3. **Headline run:** grow the suite to **~50 tasks (5–6/stratum)**, `n = 12`. Report overall
   (bootstrap 95 % CI + Wilcoxon) **and** per-stratum.
4. **Full/publishable:** if any stratum's CI is still wide, extend that stratum toward **~100
   tasks (8–10/stratum)**. Only strata that need it — don't uniformly inflate.

**Reps:** keep `n = 10–15`. Beyond that, spend the token budget on **more tasks**, not more reps.

## 6. Cost / time (Haiku OS-model on `_default`, confounder-free)

| Run | tasks × arms × n × ~turns | ≈ turns | ≈ time | ≈ cost |
|---|---|---|---|---|
| Pilot | 18 × 2 × 10 × ~1.4 | ~500 | ~15–25 min | ~$5–10 |
| Headline (T=50) | 50 × 2 × 12 × ~1.4 | ~1 700 | ~45–70 min | ~$20–40 |
| Full (T=100) | 100 × 2 × 10 × ~1.4 | ~2 800 | ~1.5–2.5 h | ~$40–80 |

(Cheap because Haiku + cache-stable = mostly cache-read. Opus worker-model would be ~5×.)

## 7. Bottom line

- **20 tasks → pilot** (variance estimate + sanity), never the paper's evidence.
- **~50 tasks → valid headline** + coarse "where it helps".
- **~100 tasks → publishable heterogeneity table** (each stratum significant).
- Add **tasks** for generalization; cap **reps** at ~10–15. Let the pilot's measured
  `σ_between` set the final `T` — that's the difference between "we picked 50" and "50 gives us
  80 % power for the effect we actually observed."
