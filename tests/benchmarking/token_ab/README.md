# Token A/B benchmark harness

Reproducible A/B benchmark for the token savings of routing and cascade variants against a fixed baseline.
Decisions: ADR-2306 (per-class non-inferiority gate) and ADR-2307 (this benchmark as the pre-gate).
Concepts: CONCEPT-0121 (error budget), CONCEPT-0122 (benchmark design). Stdlib only.
Overview of the benchmarking tests: `../BENCHMARKING_GUIDE.md`.

## What is pinned

- corpus: `corpus/MANIFEST.json` (SHA-256 per profile; `load_corpus` refuses a mismatch). `python3 gen_corpus.py --profile smoke --check`.
- models: `config/models.json` (concrete ids only; aliases and `latest` are refused before a live run)
- seed: one `--seed` for arm order, simulation and bootstrap
- decision parameters and arms: `config/arms.json` (`delta`, `alpha`, `bootstrap`, `min_stratum_tasks`; control arm `baseline_retest`)
- every run writes `manifest.json` with the hashes of harness, configuration and corpus

## Design in one paragraph

Every task runs on every arm, `reps` times, arm order shuffled per (task, rep). Success comes from a deterministic oracle
(exact answer or hidden unit tests), never an LLM judge. Cost is read from the CLI envelope: `input`, `output`,
`cache_read`, `cache_write`, `cost_usd`. The decision is a paired cluster bootstrap over tasks: ADOPT only if the lower one-sided
bound of the success difference is at least minus `delta` AND the savings interval is above zero, plus a Holm correction over the
per-category non-inferiority tests, a worst-class guard, and the rule that a category with fewer than `min_stratum_tasks` tasks is
exploratory and never adopts. The control arm tells you whether `delta` is below the baseline's own noise.

## Run

```bash
pytest tests/benchmarking/token_ab -q                      # self-tests: corpus hashes, graders, sandbox, statistics, replay, live guards
python3 tests/benchmarking/token_ab/ab_bench.py run --runner fake --profile smoke --seed 1 --run-id sim-1   # simulation, SIMULATION banner
python3 tests/benchmarking/token_ab/ab_bench.py analyze --run-dir tests/benchmarking/token_ab/results/sim-1
CLAUDE_LIVE_E2E=1 pytest tests/benchmarking/token_ab/test_token_ab_live.py -q -s   # real calls, Haiku only, capped (see the file)
CLAUDE_LIVE_PILOT=1 pytest tests/benchmarking/token_ab/test_token_ab_pilot_live.py -q -s   # Opus/Sonnet/Haiku pilot, ~0.7 USD, capped at 0.90
```

Measured price of one call of ~6.1k context tokens (2026-10-27, CLI `total_cost_usd`): Opus 5.5 0.0440 USD, Sonnet 5.5 0.0220,
Haiku 5.5 about 0.0015. Plan costs from these, not from rate-card factors: a full run (240 tasks, 3 repetitions, 3 direct arms)
is about 720 calls per arm, i.e. roughly 32 USD for Opus alone before the other arms.

A live run of the full arm set (`config/arms.json`) costs real tokens (runs = tasks x reps x arms; calls = one per run, up to
three for the cascade). It needs `--max-calls`, `--allow-code-exec` and bubblewrap. Preregister `delta` per class first
(CONCEPT-0121 section 4).

## Honest limits

- The `fake` runner draws from `config/sim.json`: a test of the machinery, never a measurement.
- The corpus is synthetic and not the real traffic; its categories are not the `step.action` classes of the loss profile.
- Absolute token counts include the CLI's own context; compare ratios, not absolutes.
- No arm yet for the real delegation path (TDE): the planner call does not report tokens (see `corvin_operator/orchestration/tde/README.md`).
- The bwrap sandbox needs `bwrap` and `/usr/bin/python3`; without them code tasks are graded without isolation in simulation and refused live.
