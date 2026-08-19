# ACS (Autonomous Compute Shell) — Big-Data value analysis

**Part of EXP-001.** Read-only investigation of CorvinOS `main` @ 6aa9a07. Tags: **CONFIRMED** =
read in code/ADR; **PLAUSIBLE** = inferred. This complements the CEL token-savings work: ACS is
CorvinOS's *other* answer to "too much context", so it belongs in the same experiment.

## Plain-language summary
When a task involves **too much data to fit in one AI conversation**, CorvinOS can split it up.
The **ACS** is a "manager + workers" system: a smart, more expensive _manager_ AI breaks the job
into small independent pieces; many cheap _worker_ AIs each handle one piece **in isolation** (each
sees only ~3 KB of context) and reach the real data through a database connection or files — not by
pasting the data into the prompt. The point: **no single AI ever has to hold the whole dataset**.

## 1. What ACS is (CONFIRMED)
- **ADR-0104** (status *proposed*). An **AWP runtime adapter**: implements the Agent Workflow
  Protocol (autonomy A0–A4, manager decision `DELEGATE|COMPLETE|FAIL`, budget envelope, validation
  rules) on CorvinOS's engine fleet (L22) + compliance envelope (L10/L16/L34/L35/L36).
- Runtime: `operator/bridges/shared/acs_runtime.py` (~3249 LoC).
  - **Manager loop** — a `claude -p` subprocess, default `claude-sonnet-5`, emits JSON decisions.
  - **Worker fan-out** — on `DELEGATE`, subtasks dispatch in parallel; each worker a fresh
    `claude -p`, default `claude-haiku-4-5` (cheap workers, strong manager).
  - **Budget envelope** (`acs_runtime.py:277-317`): `max_loops`, `max_total_tokens`,
    `max_wall_time`, `max_total_workers`=64, `max_workers_per_iteration`=6, `max_depth` — formal
    termination guarantee; a breach hard-aborts.
  - **Recursive delegation (A4)**: a worker can become a sub-manager; a shared `root_budget` checks
    the aggregate worker count across the whole recursion tree (a DoS fix).
- **Separate from L25 Compute** (`acs_runtime.py:15-17`): "L25 handles parameter sweeps; ACS handles
  agentic decision loops." (ADR-0104 Alt-1 rejected folding them together.)

## 2. Per-worker context isolation — the load-bearing big-data mechanism (CONFIRMED)
- Worker prompt (`_build_worker_prompt`, `acs_runtime.py:1625-1640`) gets ONLY: subtask id,
  instructions, output schema, success criteria, and `CONTEXT STATE` truncated to **3000 chars
  (~3 KB)**. Manager gets **8000 chars** + last-10 worker results + loss trajectory + (flag-gated)
  a CEL brief. Deliberate asymmetry: manager holds the whole-task view, workers stay isolated
  (ADR-0217, re-asserted under ADR-0279).
- **Where the big data actually lives (important nuance):** the 3 KB cap means the *bulk data is
  NOT in worker context*. Workers reach real data out-of-context via **DSI (Datasource Injection,
  ADR-0127)**: `RunContext.datasource_env` (`acs_runtime.py:385`, resolved `:1171-1197`) — a
  ClaudeCode worker runs its own live SQL; a Hermes worker gets a small pre-run snapshot. Artifacts
  flow via files in the run dir.
- **Accurate claim:** each worker gets an isolated *small* context and reaches big data via
  DB/files/snapshots — **not** by stuffing 10k rows into a prompt. The "a 10k-row table doesn't
  blow one context" benefit comes from **decomposition + external data references**, not a bigger
  window. (Exception: a pasted ≥10-row markdown table can itself be the payload — see §3.)

## 3. Invocation & routing (CONFIRMED)
Three paths:
1. **`acs_delegate` MCP tool** (`core/orchestration/.../mcp_server.py:776/1190`), accepts a
   `budget_override`, wrapped by a wall-clock watchdog.
2. **Big-data auto-routing** — `delegation_policy.worker_engine_target` (`delegation_policy.py:55-96`):
   `force_delegate → acs`; `is_big_data → acs` **in every mode incl. native**; else by operator's
   `worker_engine`. `is_big_data_task` (`:535-600`) = four affirmative shapes: (a) big-data
   vocabulary, (b) a pasted pipe/markdown table of **≥10 rows**, (c) a CSV/DB/SQL op PAIRED with a
   bulk verb or a volume, (d) a volume/count tied to a data noun in-clause — with a **code
   carve-out** so "refactor 2M lines of code" stays native.
3. Wiring: console `chat_runtime._worker_engine_target:1486`; bridges `_maybe_delegate_big_data`
   (flag `bridge_big_data_delegation`, ships dark).
- **Stays native:** ordinary questions, prose, and ALL normal coding (sequential, write-on-shared-
  files → structurally misfits the fan-out).

## 4. What ACS concretely brings for Big Data
| Value | Evidence | Tag |
|---|---|---|
| Parallelism (≤6/iter, ≤64 total) | `_dispatch_workers:1987` + budget | CONFIRMED |
| Per-worker context isolation (no single context holds the dataset) | 3 KB worker vs 8 KB manager | CONFIRMED |
| Reaching real data without loading it into context | DSI `:1171-1197` | CONFIRMED |
| Cheap-worker / strong-manager cost split | sonnet-5 mgr, haiku-4-5 workers | CONFIRMED |
| Dynamic tooling mid-loop (A3) — build the data tool from the problem | ADR-0104:227-249 | CONFIRMED (design) |
| Compliance envelope on data workers (L34 gate, metadata-only audit, at-rest encryption on snapshots) | `:1180-1196` | CONFIRMED |
| Out-of-LLM-loop **numeric** compute + data sharding | **L25 + ADR-0026 fabric** (`core/compute/…/shard.py`) — **NOT ACS** | CONFIRMED (separate subsystem) |

**Bottom line — "Big Data value" splits across two subsystems:**
- **ACS** = agentic decomposition + per-worker context isolation + parallel LLM fan-out + dynamic
  tooling → value when big-data work needs **reasoning/analysis** sharded so no context drowns.
- **L25 Compute + ADR-0026 fabric** = out-of-LLM-loop **sharded numeric** compute (parameter
  sweeps, hash/range/stratified/round-robin sharding, resource slots) → value for **deterministic
  bulk computation**.
They are deliberately separate. Analysis-shaped big-data prompts route to ACS; COMPUTE-shape is for L25.

## 5. Limits & trade-offs (CONFIRMED)
- **Quota:** every ACS run charges one `compute_units_per_day` (free tier **1/day**, fail-closed).
  Spending the daily unit on the wrong task buys the *worse* tool.
- **3 KB worker-state cap:** rich intermediate state can't flow worker→worker via context; it must
  go through files/DB/artifacts. This is the isolation's cost.
- **Native beats ACS** for coding and sequential/context-heavy/shared-file work.
- **Latency:** deep A4 runs are minutes-to-hours → background only, never inside an OS-turn.
- **Token cost:** A4 at max depth can be millions of tokens (bounded by `max_total_tokens`).
- ADR-0104 is still **proposed** (backfilled, never promoted) — treat production maturity cautiously.
- Does NOT give any worker a *bigger* window; no streaming/incremental ingestion (data must be a
  file or DSI source).

## 6. Relation to CEL (CONFIRMED)
CEL feeds **only the ACS manager** (iteration 0, flag `vibe_engineering`), **never the workers**
(`acs_runtime.py:50-52, 902-922`), and only after `strip_for_remote` (ADR-0279) makes it text-only.
So CEL improves *how the task is split*; the workers' own big-data strategy is the 3 KB isolation +
DSI. **Complementary, orthogonal — not an alternative context strategy for big data.**

## 7. Measurable hypotheses (feed EXP-002, benchmarkable)
1. **Correctness vs volume:** fixed question over 10/100/1k/10k/100k-row tables, native vs ACS.
   H: native accuracy degrades + context-overflow failures rise past some row count; ACS holds flat.
2. **Wall-clock speedup:** embarrassingly-parallel per-shard task at workers=1 vs 6. H: near-linear
   to the 6-cap, then flat.
3. **Cost efficiency:** same task as one sonnet native turn vs sonnet-manager+haiku-workers. H: ACS
   lowers $/correct once volume forces native re-reads.
4. **Isolation-cost breakpoint:** grow required cross-shard shared state; H: ACS quality falls
   sharply once shared state exceeds ~3 KB → the exact coupling boundary where native wins.
5. **Routing precision of `is_big_data_task`:** labeled corpus → confusion matrix; H: false-positive
   (wasted quota) + false-negative (big data left in an overflowing native turn) both low.

**Key files:** `acs_runtime.py` (isolation `:1625-1640`, manager+CEL `:895-948`, DSI `:1171-1197`,
budget `:277-317`); `delegation_policy.py` (routing `:55-96`, detection `:535-600`);
`adapter.py:7701/7984`; `chat_runtime.py:1486`; `mcp_server.py:776/1190`; `core/compute/` (L25 +
fabric); `license/compute_quota.py`. **ADRs:** 0104, 0202, 0203, 0217, 0127, 0275/0279, 0026, 0094, 0042/0043.
