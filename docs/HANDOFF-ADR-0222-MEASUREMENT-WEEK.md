# ADR-0222 Measurement Week — Status and Operator Guide

**Last updated:** 2026-07-25 (k=5)
**Status:** Sampler BUILT and wired. Measurement week not yet run — no measured evidence exists.

This was a build-plan handoff through k=4. It now documents what actually EXISTS,
because the plan and the code diverged in ways that mattered (see *Corrections* at
the end — three claims in the previous version of this file were false).

---

## What the sampler does

The decision gate (`decision_gate.py`) answers one question per task band: *does
per-step TDE net-save tokens at held quality?* It may only answer on MEASURED data
(`data_source="measured"`, `n_measured ≥ min_samples_per_band`, currently 30).
Producing that data is what the measurement week is for.

For a sampled turn, three arms run on the SAME task:

| Arm | What it is | Role |
|---|---|---|
| **TDE** | The per-step decomposition the user's turn already ran | The candidate |
| **direct** | Whole task, ONE turn, the user's own model (`whole_task_direct_baseline`) | The REFERENCE — loss 0 by definition, its token count is the denominator of every savings figure |
| **tier** (F5) | Whole task, ONE turn, tier-resolved model (`whole_task_tier_baseline`) | The simplest alternative TDE must beat |

Both baselines then get judged against direct by the F1-upgraded semantic judge
(`loss_judge.judge_loss_sync`, model via `CORVIN_TDE_JUDGE_MODEL`). The result is
one `MeasurementSample` appended to `measurement.jsonl`.

### Sequential, not parallel

The arms run one after another. Concurrent arms would contend for the same
CLI/rate-limit budget and put contention noise into the very token and latency
numbers being measured. For the same reason the sampler starts only after the
turn's ADR-0213 context-sync has completed — that sync is itself an awaited
`claude -p --continue` on the expensive user model.

### Off the response path

The sampler is a detached task started at the very END of `_stream_tde_turn`,
after the answer is streamed, after it is persisted, and after the context-sync.
The user never waits for it. Trade-off accepted: a process shutdown mid-measurement
loses that sample; measurement-week collection is best-effort by design.

It is unreachable on the cancellation path, so a disconnected client never pays
for two baseline turns.

---

## Operator guide

### Environment variables

| Variable | Default | Effect |
|---|---|---|
| `TDE_MEASUREMENT_ENABLED` | unset (OFF) | Must be exactly `"1"`. Anything else — including `true`, `yes`, `TRUE` — stays off. |
| `TDE_MEASUREMENT_SAMPLE_RATE` | `1.0` | Fraction of eligible turns to sample, `0.0`–`1.0`. Unparseable or out-of-range reads as `0.0` (a typo must not silently triple every turn's cost). |
| `TDE_MEASUREMENT_PERSIST_OUTPUTS` | unset (OFF) | `"1"` writes raw model output text into `measurement.jsonl`. Debug only — see *Data* below. |
| `CORVIN_TDE_JUDGE_MODEL` | site default (Haiku) | The judge. Set this to a STRONG model for the measurement week: a Haiku judge scoring Haiku-vs-Haiku is blind to the real quality drop (ADR-0222 F1). |
| `CORVIN_TDE_REFERENCE_MODEL` | unset | ADR-0222 F1 stronger shadow reference (pre-existing, unchanged by k=5). |
| `CORVIN_HOME` | `~/.corvin` | `measurement.jsonl` lives in `<CORVIN_HOME>/measurement-week/`. |

### Cost

A sampled turn runs the whole task THREE times plus two judge calls — budget ~3x
the tokens of an unmeasured turn. `TDE_MEASUREMENT_SAMPLE_RATE` thins this.

### Quota — read this before enabling

The two baseline turns are **NOT charged** against the shared daily
agentic-compute pool (`compute_units_per_day`, shared by TDE/ACS/compute). Only
the user's own TDE turn is charged, through the normal chokepoint.

This is deliberate — charging the diagnostic arms would end a free-tier
measurement week after ~3 sampled turns — but it means the flag lets an instance
spend un-metered compute. **It is a MAINTAINER-ONLY switch.** Do not enable it on
an instance whose compute budget is supposed to be capped by the pool.

### Data

`measurement.jsonl` is written OUTSIDE the hash-chained audit log. By default it
carries no model output text — only tokens, losses, and output *lengths* (GDPR
Art. 5(1)(c) data minimisation; the gate reads tokens and losses only). The task
prompt itself is never persisted. `TDE_MEASUREMENT_PERSIST_OUTPUTS=1` opts into
full text for locally debugging a suspicious judge score.

### "The week is running but no samples appear"

Every refusal path logs its reason. In order of likelihood:

1. **Partially instrumented run** — the most common. `summary["total_tokens"]`
   sums only the steps that returned a usage block, so on a run where some steps
   reported usage and others did not it UNDER-counts what TDE spent, biasing
   savings in TDE's favour. The sampler requires
   `instrumented_step_count == step_count` and logs the skip with both numbers.
2. **Baseline arm failed** — CLI missing, non-zero exit, unparseable envelope.
3. **Judge returned no verdict** — judge stack unavailable or its answer
   unparseable. Never substituted with a number.
4. **Turn was not successful** (`ok` false) — failed turns are not sampled.
5. **Sampling rate** — see above; an out-of-range value samples nothing.

---

## The honesty invariant (why the code refuses so much)

A sample that reaches the gate flips a routing default. Every field that could
carry a fabricated number is rejected rather than defaulted:

- **Token counts must be > 0**, not merely non-negative. A zero is never a real
  measurement of a turn that produced output — and `(direct - 0) / direct` reads as
  **100% savings**, i.e. the strongest possible pro-TDE evidence produced by an
  *absent* measurement. `direct_tokens = 0` additionally divides by zero in the gate.
- **A judge verdict of `None` drops the sample.** Substituting `0.0` or the lexical
  fallback would book a fabricated quality score — the exact defect ADR-0222 F1 closed.
- **`task_band` is checked against a real tuple.** The `Literal` annotation is not
  enforced at runtime; an unknown band would create a phantom evidence group that
  never reaches `min_samples_per_band` and quietly starves the verdict.
- **`load_from_log()` REPLACES the buffer.** It used to append, so a second call
  double-counted every sample — and `n_measured` is the gate's sample-size guard.
- **Band-name translation is explicit.** The classifier emits
  `simple | moderate | complex`; the gate's bands are `trivial | moderate | complex`.
  `simple` maps to `trivial`. Unmapped, every simple task landed in `moderate`,
  leaving `trivial` permanently at `n_measured=0` while diluting `moderate`.
  Unrecognised labels fall to `moderate` (the middle band, so they can bias neither
  direction) and are logged.

Losing a data point costs one turn of evidence. Accepting a fabricated one corrupts
a verdict that ADR-0220 then builds on.

---

## Known limitations

- **Tier routing only pays off on the chat band.** `resolve_model_for_workload`
  currently routes only high-confidence CHAT down to the fast tier; `code` and
  `uncertain` resolve back to `user_model`, so the tier baseline EQUALS the direct
  turn there (net savings 0). The gate reads that honestly rather than faking a
  cheaper baseline. Extending the tier map is the follow-up if the chat band shows
  the play has legs.
- **No per-turn confidence is threaded through the TDE path**, so the tier baseline
  is resolved with `confidence=None` — the conservative reading.
- **Output-shape confound.** The TDE arm's answer is `"\n\n".join(step outputs)`
  while the baselines return a single answer. The judge scores substance, not
  formatting, but a systematic shape difference between arms is a real confound to
  keep in mind when reading the first results.
- **Judge threads are not cancellable.** `judge_loss_sync` runs via
  `asyncio.to_thread`; on the orchestrator's overall timeout the thread finishes on
  its own (bounded by the judge's own 60s subprocess timeout).
- **One measurement at a time** (`_MEASUREMENT_MAX_CONCURRENT = 1`). Excess
  concurrent turns are not sampled, and the skip is logged.

---

## Code map

| Piece | Where |
|---|---|
| `RealTdeOrchestrator` (runs + judges the arms) | `operator/orchestration/tde/tde_measurement.py` |
| `MeasurementSample`, `MeasurementRecorder`, `aggregate_measured_evidence` | same file |
| `classify_band` + `_COMPLEXITY_TO_BAND` | same file |
| `MockTdeOrchestrator` | same file — **test double, no production caller** |
| `whole_task_direct_baseline`, `whole_task_tier_baseline`, shared `_whole_task_single_turn` | `operator/orchestration/tde/tde_engine.py` |
| Gate verdicts (`evaluate_band`, `evaluate_tde_verdict`) | `operator/orchestration/tde/decision_gate.py` |
| Hook: coverage gate, ctx capture, detached spawn | `core/console/corvin_console/chat_runtime.py` (`_stream_tde_turn`, `_measurement_should_sample`, `_run_tde_measurement`, `_spawn_tde_measurement`) |
| Wiring declarations | `operator/orchestration/tde/WIRING.yaml` |

Both baselines share `_whole_task_single_turn` deliberately: they must differ in
EXACTLY ONE variable, the model. Two code paths would let a prompt or parser
difference masquerade as a model-tier difference in the gate's evidence.

### Tests

| File | Covers |
|---|---|
| `tests/test_tde_measurement_k5_real_orchestration.py` | Real orchestration, every fail-closed refusal path, band mapping, redaction round-trip, log idempotency |
| `tests/test_tde_measurement_k5_hook.py` | Sampling gate, detached spawn/concurrency/logging, and structural invariants of the turn wiring |
| `tests/test_tde_measurement.py`, `test_tde_measurement_recorder.py` | Sample/aggregation units |
| `tests/test_tde_decision_gate.py`, `test_tde_decision_gate_measured.py` | Gate verdicts + honesty invariant |

The hook tests assert on `inspect.getsource` because the properties at stake are
*placement* properties (measurement after the result yields, coverage gate before
the context build) that no unit-level call can observe.

---

## What's next

1. **Run the week.** Set `TDE_MEASUREMENT_ENABLED=1` and a strong
   `CORVIN_TDE_JUDGE_MODEL` on a maintainer instance. Collect ≥30 samples per band.
2. **Read the verdict.** `MeasurementRecorder.get_aggregated_evidence()` →
   `evaluate_tde_verdict()`. `amplifier_survives` can now legitimately turn true —
   or falsify the premise.
3. **Only then** wire the verdict into routing (`decision_gate` is deliberately
   `deferred` in `WIRING.yaml` until real evidence exists). ADR-0220 stays BLOCKED
   until this resolves.

---

## Corrections to the previous version of this file

Recorded because they were load-bearing and would otherwise be inherited again:

- **"All 17 tests green. Production-ready for k=4 integration."** — Those tests
  passed only because `tests/conftest.py` deleted the real stdlib `operator` module
  from `sys.modules` to make `operator.orchestration.tde…` imports resolve. That
  hack also made `operator/` a package for any process rooted at the repo, which
  crash-looped `corvin-webui.service` (~80 systemd restarts; `asyncio` needs
  `operator.eq`). Fixed in `5187bd4` by deleting `operator/__init__.py`, reverting
  the conftest hack, and moving to the repo's existing `tde.X` import convention.
  Without the hack, 10 of those tests failed on collection.
- **"k=4 Phase 2 wiring complete."** — The k=4 hook could never have run: its
  import path did not exist, and `orchestrate_measurement` called `time.time()`
  without importing `time`. Both were invisible because the hook wrapped everything
  in `except (ImportError, Exception): pass`.
- **"Use `analysis.classification.task_type`" for band classification.** — Wrong
  field (that is the task TYPE, not its complexity) and wrong vocabulary. k=5 uses
  `summary["complexity"]` through `_COMPLEXITY_TO_BAND`.
- The old sketch also read `result["usage"]["total_tokens"]`, a key this result does
  not have; the real figure is `summary["total_tokens"]` (ADR-0219 R1), and it needs
  the instrumentation-coverage check described above.
