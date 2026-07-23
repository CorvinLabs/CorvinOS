# ADR-0214: Tiered Delegation Engine (TDE)

Implementation of the Tiered Delegation Engine from ADR-0214, hardened by the
2026-07-23 adversarial review (rounds 1–2, live-LLM E2E driven).

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
| `tde_audit.py` | Hash-chained `tde.*` events via bridges audit; allowlisted scalar details only (content-free) |
| `slash_command_parser.py` | `/use-engine <name>` (same-line or newline task), `/engine-auto`, `/debug-engine` |
| `streaming_executor.py` | Phase 3 partial: L34-filtered streaming into a LOCAL executor (remote streaming IPC not built yet) |

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
`tde.step_delegated`, `tde.loss_recorded`, `tde.plan_executed`.
Details are allowlisted scalars (engine, confidence, reason codes, counts,
durations). Task text, statement values and snapshots never enter the chain.
Sandbox for tests: `FORGE_ROOT`.

## Testing

- Unit (no LLM): `tests/test_tde_phase1_detector.py`, `…_phase1_gate.py`,
  `…_phase2_integration.py`, `…_phase2_executor.py` — 64 tests.
- Live E2E (REAL LM calls, opt-in): `CLAUDE_LIVE_E2E=1 pytest tests/test_tde_e2e_live.py -q`
  — real classification, real per-step delegation, audit-chain verification.

## Status (truthful)

- Phase 1 + 2: implemented and E2E-proven (real LLM classification, real
  delegation, real loss measurement, verified audit chain).
- Auto-routing in the live console remains ADR-0114 (ACS vs direct); TDE in the
  console is EXPLICIT opt-in via slash command (ADR-0214 requires canary before
  auto-routing goes live).
- Phase 3 open: A2A remote IPC, remote streaming path, detector marketplace
  plugins (CLS tier gating), loss-profile persistence across sessions.

See ADR-0214 for the full design rationale.
