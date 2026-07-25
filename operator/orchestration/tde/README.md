# ADR-0214: Tiered Delegation Engine (TDE)

Implementation of the Tiered Delegation Engine from ADR-0214, hardened by the
2026-07-23 adversarial review (rounds 1–4, live-LLM E2E driven — round 4 used
TDE itself, via real delegation, to review parts of its own codebase).

## Architecture

```
send(task)
  ├─ SlashCommandParser (/use-engine, /engine-auto, /debug-engine — same-line + newline forms)
  ├─ L34 prescan (engine-agnostic, fail-closed — overrides CANNOT bypass it)
  ├─ InitialAnalysis (ADR-0210 Phase 1 — REAL one-shot LM call, analysis_runner.py)
  ├─ Precedence: L34-block > user override > trivial-gate > detector
  ├─ RobustEngineDetector (5-signal ensemble, softmax, <30% confidence → claude_code)
  ├─ EngineRegistry.execute()
  │   ├─ tiered_delegation → AdaptiveDelegationExecutor
  │   │     ├─ plan validation + symmetric can_parallelize batching (ADR-0210 ParallelExecutor)
  │   │     ├─ three gates per step: L34 (fail-closed) → budget (hard) → loss (soft)
  │   │     ├─ delegation via WorkerIPC (Subprocess = real one-shot LLM per step)
  │   │     └─ loss learning: exploration (forced measurement) → semantic judge → proxy
  │   ├─ claude_code → ClaudeCodeLocalEngine (sequential, full context)
  │   └─ acs → AcsEngineBridge (real ACSRuntime via run_acs_workflow)
  └─ tde.* audit events (hash-chained, CONTENT-FREE) + LossProfileTracker
```

## Components

| Module | Role |
|---|---|
| `analysis_runner.py` | REAL ADR-0210 Phase-1 LM call (claude CLI, `SITE_INITIAL_ANALYSIS`, Haiku default) |
| `robust_engine_detector.py` | 5-signal ensemble; task-type vocabulary matches the analysis prompt; uncertainty fallback |
| `l34_delegation_gate.py` | Fail-closed gate: name+content heuristics (secrets/PII regexes), classifier errors → RESTRICTED; `prescan()`, `filter_plan()` (secrets ALWAYS redacted), `sanitize_snapshot()` |
| `loss_profile_tracker.py` | In-session learning; per-engine keying; decay-weighted estimates; proxy entries down-weighted (×0.25); `evidence_for()` separates learned values from defaults |
| `adaptive_delegation_executor.py` | Parallel batches; three-gate decision; bounded exploration (side-effect-free steps only, forced measurement); budget charging; `tde.*` audit |
| `loss_judge.py` | Semantic loss via `SITE_DELEGATE_OUTPUT_JUDGE` (lexical Jaccard reports ~80% between equivalent outputs — judge is the only usable signal) |
| `worker_ipc.py` | `MockWorkerIPC` (tests) / `SubprocessWorkerIPC` (REAL delegation: one tool-less LLM one-shot per step, neutral cwd, fenced-JSON parsing) / `A2AWorkerIPC` (Phase 3, raises) |
| `tde_engine.py` | Real engines for the registry (TDE / claude_code-local / ACS bridge — no fake-success placeholders) |
| `send_integration.py` | L22 hookpoint: parse → prescan → precedence → detect → execute → record REAL outcome |
| `tde_audit.py` | Hash-chained `tde.*` events via bridges audit; allowlisted scalar details only (content-free); every event carries `tde_run_id`/`step_num` correlation |
| `slash_command_parser.py` | `/use-engine <name>` (same-line or newline task), `/engine-auto`, `/debug-engine` |
| `streaming_executor.py` | Phase 3 partial: L34-filtered streaming into a LOCAL executor (remote streaming IPC not built yet). Round 4: values are chunked below the L34 content-scan ceiling so streaming actually succeeds for its designed use case (>1GB data) instead of always failing closed; unsafe chunks are skipped (not a full-stream abort); emits `tde.streaming_step_executed` |
| `decision_gate.py` | ADR-0222 pure verdict logic: per-band `evaluate_band` / overall `evaluate_tde_verdict`. TDE must beat BOTH the direct turn and the F5 tier baseline at held quality. Honesty invariant: a win only counts on `data_source="measured"` with `n_measured ≥ min_samples_per_band` (30); assumption-sourced wins stay predictions |
| `tde_measurement.py` | ADR-0222 k=5 measurement-week sampler: `RealTdeOrchestrator` runs the {direct, F5-tier} baselines and judges both against direct, `MeasurementRecorder` persists samples to `measurement.jsonl`, `aggregate_measured_evidence` rolls them into `BandEvidence`. Default OFF (`TDE_MEASUREMENT_ENABLED=1`); fail-closed — a missing token count or an unavailable judge drops the sample rather than booking a fabricated number |

## Surfaces

- **Web console:** per-turn engine badge (`engine` stream event → `Engine: …` on the
  assistant bubble; ACS/TDE/Hermes/Claude Code). Explicit TDE turn via
  `/use-engine tiered_delegation <task>` (chat_runtime `_stream_tde_turn`,
  pre-spawn gates classify it as delegation, ADR-0213 transcript sync applies).
- **Bridges (Discord/Telegram/WhatsApp):** context bar shows `[⚙ ACS: <primitive>]`
  when the turn ran with a non-DIRECT ACS-X directive. TDE does not execute on
  bridges yet (honest boundary — no fake display).
- **Wheel installs:** `operator/orchestration` is vendored (hatch_build `_VENDOR_MAP`).

## Learning loop (honest semantics)

1. No evidence (< `MIN_SAMPLES` effective samples for `(step.action, model, engine=TDE)`):
   side-effect-free steps delegate with a FORCED shadow measurement (exploration);
   mutating steps stay local. This breaks the 10%-default vs 5%-threshold deadlock.
2. Shadow measurement: local re-run (side-effect-free actions only) + semantic judge
   (0–100 equivalence). Judge unavailable → discounted lexical fallback.
3. With evidence: delegate iff learned loss ≤ 5% (`QUALITY_THRESHOLD`); learned
   loss from OTHER engines never unlocks TDE delegation.
4. Whole-task outcomes are recorded as proxy entries (down-weighted) from real
   success/failure — never fabricated values.

## Audit events (hash-chained, content-free)

`tde.engine_selected`, `tde.l34_blocked`, `tde.delegation_decision`,
`tde.step_delegated`, `tde.step_executed_local` (audit-graph endpoint —
symmetric counterpart to `step_delegated` for genuinely-local steps),
`tde.loss_recorded`, `tde.plan_executed`, `tde.streaming_step_executed`
(round 4).
Details are allowlisted scalars (engine, confidence, reason codes, counts,
durations). Task text, statement values and snapshots never enter the chain.
Sandbox for tests: `FORGE_ROOT`.

Every event above also carries `tde_run_id` (the `tde-<epoch>-<hex>` id
`chat_runtime._stream_tde_turn` generates per turn, threaded down through
`SendIntegration` → `TieredDelegationEngine` → `AdaptiveDelegationExecutor`)
and, for per-step events, `step_num` (the plan's 1-based step number). This
is the correlation key the audit-graph endpoint below uses to reconstruct
one turn's delegation tree from the shared chain.

## Audit-graph endpoint (ADR-0214, `_build_tde_audit_graph`)

`GET /compute/tde/{run_id}/graph` (`core/console/corvin_console/routes/compute.py`)
reconstructs one TDE turn's real delegation tree from the hash-chained
`tde.*` events carrying that `run_id` in `tde_run_id` — modeled directly on
the existing L25/ACS graph endpoints (`_build_l25_graph` / `_build_acs_graph`
in the same file), but built entirely from the audit chain instead of
per-run artifact files (TDE writes no manifest/iteration files of its own).

Payload shape (`{nodes, edges, meta}`, vis.js-compatible):

- **`task_root`** (level 0) — the turn itself: `run_id`, `n_events`, `wall_time_s`.
- **`l34_prescan_block`** (level 1, only if `tde.l34_blocked{scope=prescan}`
  fired) — red, `reason_code`.
- **`mgr_1`** (level 1, from `tde.engine_selected`) — engine choice: `engine`,
  `confidence` (AWP-style green/yellow/orange/red), `override`, `trivial`,
  `task_type`, `complexity`.
- **`step_*`** decision nodes (level 2, one per `tde.delegation_decision`,
  chained sequentially like the ACS iteration chain) — `step_num`,
  `step_action`, `delegate`, `l34_blocked` (true + red when a matching
  `tde.l34_blocked{scope=step}` exists for that `step_num`), `reason_code`.
- **`w_*`** worker nodes (level 3, one per matched `tde.step_delegated` /
  `tde.step_executed_local`, merged with `tde.loss_recorded` by `step_num`
  when present) — `engine` (`tiered_delegation` or `local`), `success`,
  `duration_ms`, `loss_pct`, `measured`. Color follows the AWP confidence
  heatmap when a loss is known, else plain success/fail.
- **`completion`** (level 4, from `tde.plan_executed`) — `step_count`,
  `delegated_count`, `local_count`.
- **`meta`** — `run_id`, `n_events`, `n_steps`, `n_delegated`, `n_local`,
  `wall_time_s`, `engine`, `confidence`, `loss_min`/`loss_max`/`loss_curve`
  (same shape as the ACS graph), and the GDPR Art. 30/32 integrity fields:
  **`chain_verified`** (bool) and **`chain_problems`** (list, truncated to 20).

Chain verification reuses `forge.security_events.verify_chain` over the
whole audit file, then scopes the result to the **line range this run's own
events span** (`chain_problems` filtered to `lo <= line <= hi`) rather than
failing every run whenever any other segment of a long-lived shared chain is
broken — a tamper anywhere inside that range (including a downstream
`broken_chain` pointer mismatch caused by tampering the record just before
it) flips `chain_verified` to `false` for this run only.

A view of the graph emits `tde.audit_graph_viewed` (via
`console_audit.action_performed`, mirroring `compute.acs_graph_viewed`).

Tenant-scoping caveat: TDE audit events land wherever
`operator/bridges/shared/audit.py::audit_path()` resolves (the
scope-independent `corvin_home()/global/forge/audit.jsonl` workspace root —
see `tde_audit.py`'s module docstring), not the tenant-scoped path the rest
of `compute.py` uses. The endpoint reads from that same location for
read/write consistency; it does not itself add tenant isolation for the TDE
chain (a pre-existing, separately-tracked gap).

No frontend yet — this is the backend contract; the graph viewer UI is a
separate follow-up once this payload shape is settled.

## Testing

- Unit (no LLM): `tests/test_tde_phase1_detector.py`, `…_phase1_gate.py`,
  `…_phase2_integration.py`, `…_phase2_executor.py`, `…_round2_hardening.py`,
  `…_streaming_executor.py`, `…_proc_holder.py` — 154 tests. Genuinely
  offline: `AdaptiveDelegationExecutor.use_semantic_judge` now defaults to
  `False` (round 4 — the prior `True` default silently spawned a real
  `claude` CLI subprocess, ~10s each, from several "no LLM calls" tests;
  `TieredDelegationEngine`, the real production caller, always sets this
  explicitly and is unaffected).
- Live E2E (REAL LM calls, opt-in): `CLAUDE_LIVE_E2E=1 pytest tests/test_tde_e2e_live.py -q`
  — real classification, real per-step delegation, audit-chain verification.
- Audit-graph endpoint: `core/console/tests/test_tde_audit_graph.py` — builder
  shape (normal turn, loss-curve, L34-block coloring) + full route
  round-trips against a real hash-chained `audit.jsonl` fixture, including
  the broken-chain case (tampered record inside the run's line range flips
  `chain_verified` to `false`) and a same-file cross-run isolation check
  (tampering run B must not flip run A's `chain_verified`).

## Nervous System integration (ADR-0177)

`TdeDelegationFiber` (`core/console/corvin_console/aco/nerve_builtins.py`)
reads the `tde.*` audit-chain tail and reports delegation failure-rate and
learned quality-loss as `NerveSignal`s on every healer cycle — always an OK
summary signal (counts, not content), plus HIGH/MEDIUM signals when the
failure rate or measured loss is elevated. Round 4 also fixed a structural
bug found while wiring this in: `from operator.bridges.shared.X import Y`
can **never** resolve (the stdlib `operator` module always wins over the
repo's `operator/` namespace-package candidate, regardless of sys.path
order) — this had silently defeated `AuditChainFiber`, `ComplianceFiber`
(nerve_builtins.py) and two checks in `integrity_monitor.py` since inception;
all four now use the same working pattern already used throughout this
package (repo-relative `sys.path` insert + bare import of the leaf module).

## Status (truthful)

- Phase 1 + 2: implemented and E2E-proven (real LLM classification, real
  delegation, real loss measurement, verified audit chain). Round 4 additionally
  used TDE's own delegation path to review two of its own findings (see
  ADR-0214 addendum) — a real dogfood run, not a simulated one.
- Auto-routing in the live console remains ADR-0114 (ACS vs direct); TDE in the
  console is EXPLICIT opt-in via slash command (ADR-0214 requires canary before
  auto-routing goes live).
- Phase 3 open: A2A remote IPC, detector marketplace plugins (CLS tier
  gating), loss-profile persistence across sessions. Streaming (round 4) is
  now chunked and unit-tested, but still local-only — no streaming-capable
  WorkerIPC exists yet.
- Round-4 follow-up (RESOLVED): a client disconnect while per-step
  delegated/local worker subprocesses are in flight inside
  `AdaptiveDelegationExecutor.execute()`'s parallel batches now kills every
  subprocess that batch started, not just whichever task happened to raise.
  `execute()` allocates one `worker_ipc.ProcHolder` per concurrently-scheduled
  task in a batch (delegated: threaded through `WorkerIPCInterface.send_delegation`'s
  new `proc_holder` kwarg; local: threaded through the injected
  `step_executor_fn` when it declares a `proc_holder` parameter, detected via
  signature inspection so arbitrary embedder-supplied executors — e.g.
  Hermes's genuinely-local one — aren't required to accept it). A
  `CancelledError` raised out of `asyncio.gather()` (the whole `execute()`
  coroutine being cancelled) kills every holder in that batch before
  re-raising. Same pattern as the InitialAnalysis one-shot fix
  (`chat_runtime._stream_tde_turn`'s `_analysis_holder`). Covered by
  `tests/test_tde_proc_holder.py::TestParallelBatchProcHolderTracking`
  (normal batch completion + real mid-batch cancellation killing both
  sibling subprocesses).

See ADR-0214 for the full design rationale.
