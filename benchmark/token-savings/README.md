# Token-Savings A/B Benchmark (evidence-based, reproducible)

**Claim under test:** "CorvinOS's context engineering (CEL) saves tokens." This benchmark
**measures** it instead of estimating it — the old dashboard baseline was a fabricated
constant (`1800 × multiplier`), so its "savings %" was never a real number. Here every
figure comes from real runs.

Concept + methodology: [`docs/concepts/token-savings-benchmark-concept.md`](../../docs/concepts/token-savings-benchmark-concept.md).

## What it does

- **Arm A (baseline):** each task run with CEL **off** (`vibe_engineering=false`).
- **Arm B (vibe):** same task, CEL **on**.
- Runs each task `n` times per arm through the **real** console turn path
  (`chat_runtime.stream_turn`) and captures the **worker's real token usage** — never an
  estimate, never `chars/4`.
- Scores every answer with an **objective fact-presence check**, and **drops** any pair
  where B answered *worse* than A. A token cut bought by a worse answer is not a saving.
- Reports **savings = (median A − median B) / median A**, with a **bootstrap 95% CI** and a
  **Mann-Whitney-U** significance test. A single number is never shown without its CI.

## Run it

```bash
# 0. validate the wiring first — no LLM calls, no numbers produced:
./run_benchmark.sh --dry-run

# 1. real benchmark (spends real tokens — start small):
./run_benchmark.sh --n 20 --tenant _bench_tokensave
```

Outputs:
- `results/raw-<run-id>.jsonl` — one line per run (task, arm, tokens_in/out, quality). The
  **evidence trail**; recompute anything from it yourself.
- `results/report-<run-id>.json` — the per-task-type + overall savings with CIs.

## How to read the result

- **`SIGNIFICANT` + a CI above 0** → you may honestly say "we measured X% ± Y% saved
  (95% CI, n=N, suite v1, model …)".
- **`no significant saving measured`** → **do not claim a saving.** The honest answer is
  "no significant difference at this n / on these tasks."
- Per-task-type rows show where CEL helps and where it doesn't — savings vary a lot by task.

## Honest limits (read these)

- **Reproducible statistically, not bit-for-bit.** LLM output is non-deterministic; you
  reproduce the same *distribution within the CI*, given the same suite version + model + `n`,
  not identical token counts. The report records model id, suite version and `n` so a re-run
  is comparable.
- **CEL can cost more per turn** (it injects context → bigger prompts). Its value, if any,
  is at the **task** level (fewer turns / less rework), which is the unit measured here. The
  benchmark can therefore **legitimately show that CEL does not save** on some/all tasks —
  that is the point of measuring instead of asserting.
- **Token capture is now correct (4 classes).** Early smoke runs showed `input_tokens=2` while
  `cache_read=24433` + `cache_creation=38060` were ignored — the console reads only `input_tokens`
  and undercounts real input by ~99.99%. This benchmark now sums all four classes via
  `core/learning/token_accounting.py` (fresh + cache-creation + cache-read + output). The
  **existing dashboard is still wrong** and is a *separate, larger* fix: the default native turn
  path records **nothing** (the recorder is Hermes-only, `chat_runtime.py:3723`), the store API
  can't receive cache fields, and its "subsystem" split (`50/100/25`) is fabricated.
- **RAW COUNT ≠ COST — and CEL likely uses MORE raw tokens.** Measured correctly, CEL injects
  context, so its **raw token count is usually higher**, not lower (a smoke run: A=62896 vs
  B=63263, −0.6%). Cache-read costs ~0.1×, cache-creation ~1.25×, output ~5× — so CEL's *cost*
  story lives in the cache economics, not the raw count. The report prints per-arm components;
  apply your model's real prices to them for a cost figure. **The honest headline is probably
  not "fewer tokens"** — it is cost-per-task or fewer-turns, if anything.
- **suite-v1 measures CEL's worst case (single-turn, cold).** Every task pays `cache_creation`
  and never amortizes it across a session. CEL's best case (warm reuse over a multi-turn task)
  needs multi-turn tasks — not yet in the suite.
- **It costs real tokens to run.** Opt-in, scale `--n` and the task suite deliberately.
- **Quality checks are fact-presence** (safe, no code execution). Coding-task savings with a
  "did the produced tests pass" gate need the isolated verification harness (see the
  self-learning concept, ADR-0373) and are a later addition.

## Extending the suite

Add tasks to `tasks/suite-v1.json` (fixed input + an objective `check`), or create
`suite-v2.json` and bump `suite_version`. Never store LLM output in the suite — only the
fixed prompt and the check.
