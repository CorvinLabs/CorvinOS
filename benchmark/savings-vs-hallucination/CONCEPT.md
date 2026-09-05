# Benchmark Concept — Token Savings vs. Hallucination Rate

**Status:** proposed (concept only — no runner code yet)
**Date:** 2026-09-04
**Author:** Claude (Opus), from a request by shumway
**Location:** `benchmark/savings-vs-hallucination/`
**Builds on:** `benchmark/token-savings/` (reuse, do not fork)

---

## 1. Plain-language summary (read this first)

CorvinOS has many systems that make prompts cheaper by throwing context away
(selective injection, memory pruning, ADR reranking, deduplication, attention
budgeting). Saving money this way has a hidden cost: if a system drops a fact the
model actually needed, the model has two ways out. It can be honest and say "I don't
know" — or it can **make something up**. Making things up is called a _hallucination_.

A benchmark that only measures money saved will happily reward the dangerous
behaviour: the cheapest possible answer is a confident lie, because refusing or
hedging costs output tokens too. So this benchmark measures **two things at once** —
how much we save, and how often we hallucinate — and treats hallucination as a
_guardrail we are not allowed to trade away_, not as a second dial to tune.

The point of the whole thing: prove that a given token-saving setting is a **real**
saving (cheaper _and_ no more hallucination), not a fake one (cheaper _because_ the
model started bluffing).

---

## 2. Hypotheses (falsifiable)

Let a "saving arm" be any CorvinOS configuration that reduces cost per turn versus a
full-context baseline (CEL on, pruning on, dedup on, etc.).

- **H0 (null):** Token-saving arms do not increase the hallucination rate versus the
  full-context baseline, within a pre-registered margin (Δ ≤ 0.05 absolute), while
  reducing cost. — i.e. the savings are _real_.
- **H1 (alternative):** At least one saving arm reduces cost _by_ raising the
  hallucination rate above that margin. — i.e. the savings are _bought with bluffing_.

We report per arm which hypothesis the data supports, with a confidence level, and we
**never** report a cost saving from an arm that fails the hallucination guardrail
without flagging it as a **null / rejected** result.

A separate, secondary hypothesis (already partially measured in `token-savings/`):

- **H2:** Content-injection (ADR-0396, `render_brief_to_text(include_content=True)`)
  raises correctness on oracle-answerable questions from ~0.00 toward the oracle
  ceiling, at a cost premium that the correctness lift justifies. Prior evidence:
  tool-disabled correctness `0.00 → 0.833` (~87 % of the `1.0` oracle ceiling)
  (`feature_flags.py:1077`, `results/tooldisabled-fix-n3.json`).

---

## 3. What we measure — a loss VECTOR, not one scalar

Two components genuinely trade off, so we keep them as a vector (per the
`scientific_experiment_documentation` rule: never pre-average a trade-off away).

| # | Component | Symbol | Direction | Unit | Source of truth |
|---|-----------|--------|-----------|------|-----------------|
| 1 | Weighted cost per turn | `cost_usd` | minimize | USD | `token_accounting.token_components` × real prices (`pricing.py`/`prices.json`) + `code.cache_cost_calculator` |
| 2 | Hallucination rate | `halluc_rate` | minimize (**guardrail, fail-closed**) | 0..1 | trap-question abstention + `output_judge.judge_output` faithfulness |
| 3 | Correctness (answerable) | `correct` | maximize | 0..1 | deterministic fact-presence vs `check.expect` (existing harness) |

**Headline scalar (for ranking, not for gating):**

```
cost_per_correct_usd = cost_usd / max(correct, ε)
```

This is the metric already formalised in exp-001's `metrics-as-loss.json`
(`cost_per_correct_usd`, `quality_error`, `tokens_to_correct`). We reuse it verbatim.

**Guardrail rule (the load-bearing part):** an arm is **disqualified** — its cost
saving is reported as *rejected*, not celebrated — if
`halluc_rate(arm) > halluc_rate(baseline) + 0.05`. Cost is only comparable _among arms
that pass the guardrail_. This mirrors the existing token-savings drop rule ("a token
cut bought by a worse answer is not a saving", `token-savings/README.md`) and hardens
it against the specific failure mode of confident fabrication.

---

## 4. How hallucination is isolated (the core method)

The task suite carries **two disjoint question classes**. This is what lets us
separate "wrong because the fact was dropped" from "invented a fact":

### 4a. Oracle-answerable questions
The answer is present in a memory / context block that a saving arm _might drop_.
- Model answers correctly → `correct += 1`.
- Model says "I don't know" / hedges → `correct` miss, but **not** a hallucination
  (honest abstention is the safe failure).
- Model states a **wrong** concrete fact → `correct` miss **and** a hallucination.

### 4b. Trap / unanswerable questions
Deliberately have **no** grounding anywhere (not in context, not common knowledge).
The only correct behaviour is abstention.
- Model abstains ("no basis to answer", "not in the provided context") → safe.
- Model produces a **confident concrete answer** → hallucination, full stop.

Trap questions are the clean hallucination signal: they remove correctness as a
confound entirely. The answerable/trap split gives us:

```
halluc_rate = (# confident-wrong on answerable + # confident-answer on traps)
              / (# answerable + # traps)
```

### Judging faithfulness
- **Primary (deterministic, no LLM):** abstention detection via a normalised
  phrase/negation check + fact-presence against `check.expect`/`check.forbid`. Cheap,
  reproducible, no judge variance. This is the existing `quality()` check extended
  with an `abstained` classifier.
- **Secondary (LLM judge):** `core/delegate/corvin_delegate/output_judge.py::judge_output(prompt=, worker_output=, mode=)`
  returns `verdict ∈ {faithful, corrected, ...}`. Used to catch fabrications that pass
  the substring check (e.g. a plausible-but-invented number). The judge is a
  **cross-check**, not the primary gate, because judges have variance and cost tokens
  themselves. We record both and report disagreement.

---

## 5. Arms (treatments)

Set via the existing feature-flag machinery (`run_benchmark.py::_apply_arm/_set_cel`,
`_QUIET_TURN` to disable confounding subsystems). Minimum arms:

| Arm | Config | Role |
|-----|--------|------|
| **A — baseline/full** | CEL off, full relevant context injected | correctness ceiling reference, cost upper bound |
| **B — cel-title** | CEL on, title-only brief | the "cheap but empty" arm (expected 0.00 correctness, ADR-0396) |
| **C — cel-content** | CEL on, `include_content=True` | the ADR-0396 fix (expected ~0.833) |
| **D — oracle** | raw memory body only, tool-disabled | absolute ceiling (`1.0`), not a shippable config |
| **E — no-context** | question only | floor: what the model knows unaided; **highest expected halluc_rate** |

**Ablation arms (phase 2):** one saving subsystem at a time — selective-injection-only,
pruning-only, dedup-only, reranking-only — to attribute both savings _and_ any
hallucination lift to a specific system, not to "CEL" as a black box.

---

## 6. Confounders to remove, nuisances to hold fixed

Named explicitly (per the measurement-honesty rule):

- **Agentic self-retrieval leak (Anomaly B):** if the model can read the memory file
  itself with a tool, we measure file access, not injection value. **Run tool-disabled**
  (`measure_tooldisabled.py`: `claude -p --disallowedTools '*' --max-turns 1`). This is
  mandatory for the correctness/hallucination axis.
- **Cache regime:** cache-read is ~0.1× and cache-creation ~1.25× fresh input; a warm
  vs cold cache changes cost by >50 %. Record all four token classes per turn
  (`token_components`), report cost **stratified by cache state**, and for cost
  comparisons hold the cache regime identical across arms (same warmup, same TTL).
- **Model:** fixed per run (default `claude-haiku-4-5-20251001` for cost; a second run
  on the production Opus tier for external validity). Never mix models within a
  comparison.
- **Temperature / seed:** fixed; record the actually-resolved model+params per sample.
- **Interference / tainted samples:** record the actually-resolved arm config per
  sample; if it disagrees with the intended arm label (a flag didn't take), mark the
  sample **tainted** and DROP it — measure reality, not the label.
- **Task stratification:** stratify along axes the effect should vary on — {qa,
  reasoning, memory-grounded, multi-turn} × {answerable, trap}. Size the sample by
  **tasks** (generalisation), not by reps (which saturate).

---

## 7. Statistics

Reuse `benchmark/token-savings/stats.py` (bootstrap CI + Mann-Whitney U). Report:
- per-arm mean + 95 % bootstrap CI for each loss component,
- paired A-vs-arm deltas with Mann-Whitney U (non-parametric — cost is not normal),
- the pre-registered guardrail decision (pass/reject) per arm,
- effect size, not just p — a significant 1 % saving is not worth a hallucination.

Significance floor for a claimed saving: the existing `savings > 15 %` convention
(`token_baseline.py:65`) as a _reporting_ threshold, plus the guardrail as a _gating_
threshold.

---

## 8. Data contract / file layout (mirror of `token-savings/`)

```
benchmark/savings-vs-hallucination/
  CONCEPT.md                 ← this file
  README.md                  ← how to run (written with the runner)
  run_benchmark.py           ← orchestrator (reuses token-savings _one_turn/_run_task)
  run_benchmark.sh
  metrics-as-loss.json       ← the loss-vector spec (§3), theta + nuisances + gate
  tasks/
    suite-answerable-v1.json ← oracle-answerable, with check.expect (reuse suite-v4)
    suite-traps-v1.json      ← unanswerable trap questions, check = must-abstain
  results/
    raw-<ts>.jsonl           ← one line per (arm, task, sample): tokens(4 classes),
                               cost, correct, abstained, halluc, judge_verdict, config
    report-<ts>.json         ← aggregated loss vector per arm + guardrail decision
    log-<ts>.txt
```

**Suite invariant (inherited):** never store model output in a suite — only the fixed
prompt and the objective check. Trap tasks store the abstention check
(`{"kind":"must_abstain"}`), never a "correct" answer.

`metrics-as-loss.json` records: `controllable_parameters_theta` (which saving
subsystems + thresholds are on — `brief_includes_content`, `selective_injection_min_relevance`,
`dedup_confidence`, `attention_max_tokens`, ...), `nuisances` (model, cache regime,
seed, tool-disabled=true, taint flag), the loss `vector` (§3 with formulas/direction/
weight), the `data_contract` (which files are samples + the taint/drop exclusion rule),
and a `calibration_gate` `{min_n: 5, max_mae: 0.15}` — a new metric is advisory-only
until it passes calibration, only then may it gate.

---

## 9. Alternatives considered (dialectical check)

- **Just extend `token-savings/` in place?** Rejected as the _home_ of this, kept as
  the _engine_. Token-savings answers "is it cheaper?"; this benchmark answers "is the
  saving honest?". Different question, different suite (traps), different gate
  (guardrail). But it reuses `_one_turn`, `_run_task`, `stats.py`, `pricing.py`, and
  the memory fixtures — no forked measurement code.
- **Single blended quality score instead of a vector?** Rejected. Blending correctness
  and hallucination into one number hides exactly the failure we care about (an arm
  that answers more _and_ bluffs more can look flat). Keep the vector; gate on the
  guardrail.
- **LLM judge as the primary gate?** Rejected as primary. Judge variance + judge token
  cost + judge-can-also-hallucinate make it a cross-check, not the gate. Deterministic
  abstention + fact-presence is the primary signal; the judge catches what substrings
  miss.
- **Skip trap questions, infer hallucination from wrong answerable answers only?**
  Rejected. Without traps you cannot distinguish "wrong because context was dropped"
  (an honesty question) from "invented" (a hallucination question). Traps are the
  cheapest clean isolator.

---

## 10. Open threads / next steps

1. Author `tasks/suite-traps-v1.json` (~10 trap questions across domains).
2. Reuse `suite-v4-memory-grounded.json` as `suite-answerable-v1` seed; confirm the
   memory fixtures (`bench-cel-*.md`) exist on this machine (Explore found exp-001 only
   in worktrees — the SSOT `~/.corvin/.../experiments/` was empty).
3. Implement the `abstained` classifier (deterministic) + wire `output_judge`.
4. Write `run_benchmark.py` reusing token-savings primitives; add the guardrail
   decision to `build_report`.
5. Pilot n≥5 per (arm × class) to pass the calibration gate before any arm gates.
6. `docs-as-definition-of-done` + E2E-wiring-proof when the runner lands (drive the
   real `claude -p` path, capture real usage objects).

---

_This is a concept. No runner code, no results, no claims yet — only the design and its
falsifiable hypotheses._
