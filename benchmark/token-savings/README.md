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
- **Input-token capture must be verified.** A smoke run showed the console worker usage
  reporting `input_tokens=0` (output only). CEL's *main* cost is a **bigger input prompt**, so
  an output-only measurement hides that cost and can **overstate** savings. The runner prints a
  loud `WARNING` and sets `input_captured:false` in the report when this happens — **do not
  claim a saving from an output-only run.** Fixing worker input-token capture (or counting the
  assembled prompt's tokens directly) is a prerequisite for a valid headline number. Note this
  gap affects the *existing* dashboard too (`chat_runtime.py:3727` defaults input to 0).
- **It costs real tokens to run.** Opt-in, scale `--n` and the task suite deliberately.
- **Quality checks are fact-presence** (safe, no code execution). Coding-task savings with a
  "did the produced tests pass" gate need the isolated verification harness (see the
  self-learning concept, ADR-0373) and are a later addition.

## Extending the suite

Add tasks to `tasks/suite-v1.json` (fixed input + an objective `check`), or create
`suite-v2.json` and bump `suite_version`. Never store LLM output in the suite — only the
fixed prompt and the check.
