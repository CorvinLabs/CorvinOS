# Vibe Engineering — Implementation Plan

**Realizes:** IDEA-0001, CONCEPT-0004 (discipline), CONCEPT-0005 (license gate),
ADR-0275 (surface), ADR-0276 (license gate), ADR-0277 (ContextStage contract).
**Status:** Draft — revised after adversarial review R1 (2026-08-07).

## HONEST PREMISE (corrected after review)
The CEL is **built but NOT wired into any live turn**. Verified: `TaskEngine`
(Phase 5.5, `operator/task_analysis/engine.py`) is imported only by `scripts/*`,
`operator/orchestration/tde/*`, and `orchestration/{decision_cache,initial_analysis}`
— grep of the live turn path (`operator/bridges/adapter.py`, `core/console/.../
chat_runtime.py` + routes) for `TaskEngine|enrich_task|RichTaskBrief|MemoryLookup|
cel_memory` returns ZERO hits. The live path imports `initial_analysis.py`, which only
builds a *prompt*, never the CEL. ADR-0269 (the CEL foundation) is itself `proposed`.
**Therefore Vibe Engineering must WIRE the CEL into the live turn first** — it is not
"surface an already-running pipeline," it is "wire it, then make it observable/metered."
This is Phase P-1 below and is a prerequisite for P0/P1 having any call site (avoids the
"registered-but-never-called / dead plugin type" failure class — CLAUDE.md e2e-wiring).

## Guiding invariants (non-negotiable)
- **I1 — CE builds the brief; the gates inspect the spawn.** CE runs BEFORE
  L34/L44/L35, never admits a spawn the gates would deny (ADR-0275). NB: vacuous until
  P-1 wires the CEL into a real spawn path — it guards nothing before then.
- **I2 — degrade never fails.** Over-budget / CE-unavailable → plain context, a full
  working turn, NEVER an error (ADR-0276). This is the OPPOSITE of the ACS gate, which
  blocks the run — see P0.
- **I3 — fail-closed on the meter = deny ENRICHMENT (not deny service).** Quota
  subsystem down → skip CE, still serve the turn on plain context. Never fail-open into
  unmetered CE.
- **I4 — ContextStages are a NEW plugin type** (transform-shape, not a self-registering
  capability backend) that needs its OWN grade gate — NOT free reuse of SkillForge (see
  P3). Still on the existing registry (no second registry), but honestly net-new wiring.
- **I5 — ships dark.** Every phase behind `spec.features.vibe_engineering` (+ sub-flags),
  default off. The license meter is a MONETIZATION gate, not a compliance mechanism, so
  flag-gating it is fine (and it must NOT be documented into compliance-baseline.md).

## Phase P-1 — Wire the CEL into the live turn (PREREQUISITE, was missing)
Give the live console/bridge turn an actual CEL pass, behind the flag:
- Add a single orchestration entry `operator/context_engineering/pipeline.py::
  build_brief(task, tenant, session) -> (brief, trace)` that runs ALL stages
  (memory → graph → skill) in one place — the true "run all / run none" boundary
  (fixes C1: today memory is built in `memory_lookup.enrich_task` but graph+skill are
  added later in `task_analysis/engine.py:236-266`; there is no single chokepoint).
- Call it from `chat_runtime.py`'s turn path (and later adapter.py) when the flag is on,
  feeding the brief into the existing prompt assembly.
- Import strategy: reuse the `sys.path.insert(operator/)` + file-path importlib dance
  the CEL/ACS gate already use (project memory "operator/ stdlib-Shadow-Falle") — the
  new module must not rely on the `__init__.py`-only bootstrap (fixes M2).
Tests: flag-on → a live turn produces a brief + trace; flag-off → unchanged prompt path,
zero CEL calls (e2e-wiring proof, both states).

## Phase P0 — License gate (backend; operator priority)
- `operator/license/limits.py`: add `context_engineering_units_per_day` = 10 to
  FREE_TIER (verify the exact FREE_TIER dict shape + how `feature` keys are read).
- `operator/context_engineering/license_gate.py::enforce_ce_quota(tenant_id) -> bool`.
  Borrow ONLY the counting + fail-closed-on-import mechanics from
  `acs_engine_adapter._enforce_acs_compute_quota`; the caller semantics are NEW and
  opposite: **True = enrich, False = degrade to plain context and STILL RUN** (never a
  block/deny dict). (fixes H2.)
  **MUST call `load_license_from_env()` FIRST** (like the ACS gate does): the console/
  bridge/scheduler process does not auto-load the license, so without it `_ACTIVE_LICENSE`
  is None and `get_limit()` falls back to FREE_TIER — capping a PAID tenant at 10/day.
  (fixes R2 M-B.)
- **Separate pool (fixes H1):** `increment_and_check(...)` keys its count by DATE inside
  `counter_file` and uses `feature` to pick the limit; `channel`/`chat_key` are
  audit-only. So separation needs BOTH `feature="context_engineering_units_per_day"`
  AND a distinct `counter_file="context_engineering_quota.json"` — NOT "own channel key".
- **One unit per turn (fixes H3):** the counter is NOT idempotent (it always
  increments). Call `enforce_ce_quota` EXACTLY ONCE, at P-1's `build_brief` boundary —
  never per-stage — so a turn charges exactly one unit.
- Wiring: `build_brief` calls the gate first; on False, return a plain brief (raw task +
  minimal system context) and skip all stages.
Tests: (a) FREE 11th turn/day → plain brief, zero stage calls, one unit charged;
(b) quota subsystem raises → plain brief (fail-closed, still serves); (c) paid tier not
gated; (d) CE spend does NOT decrement `compute_quota.json` (separate counter_file).

## Phase P1 — Observability MVP (read-only)
1. **CEL trace schema** (`context_engineering/trace.py`): per-turn {stage, status,
   tokens_in, tokens_out, confidence_tier, sources[], duration_ms, degraded?,
   schema_version}. Emitted by `build_brief` (one place). Persisted per (tenant,
   session) under the session workdir; NOT a chat artifact. Overhead: emit is
   append-only + off the critical path (measure; budget < a few ms/turn) (addresses G1b).
2. **Backend routes** (`routes/vibe_engineering.py`): `GET /vibe/pipeline/recent?n=`,
   `GET /vibe/pipeline/{turn_id}` — read-isolated by the authenticated `rec.tenant_id`
   (never cross-tenant, ADR-0007) (addresses G1 isolation).
   **Talent (fixes H5, M3):** wire `routes/talent.py` to `talent_score` — but that reads
   predictions/feedback/*.jsonl produced by the CEL measurement hooks, which only exist
   once P-1 runs live turns. So: ship an explicit EMPTY STATE ("no context-engineered
   turns yet") until data accrues; do NOT present zeros as insight. Retire or route
   through the dead Flask `context_engineering/api_server.py` so telemetry has ONE source.
3. **Frontend** (`web-next/src/pages/vibe-engineering.tsx` + a NEW `NavGroup`
   "Vibe Engineering" in `components/layout.tsx` that groups the ALREADY-EXISTING
   `/app/talent` entry + a new pipeline page — NOT "a tab under Dashboard"; the nav is a
   flat NavGroup[] with no Dashboard parent, and Talent is already in nav) (fixes M1).
   `npm run build`.
Tests: route unit tests (trace shape, empty state, tenant isolation); Playwright smoke
(nav group renders, stage expands, empty-state shows). Flag-off → nav entry hidden, routes
not mounted.

## Phase P2 — Configurability
Per-stage budget dials + presets (Phase-4d attention budget backend). Writing a config
is paid; Free can view. Files: `context_engineering/config.py`, `PUT /vibe/pipeline/
config`. Mid-turn flag/config changes take effect NEXT turn only (in-flight turns keep
their brief) (addresses G1a/d). Tests: config persists; off-budget still degrades.

## Phase P3 — Pluggability
`ContextStage` contract (ADR-0277) `run(brief_in) -> (brief_out, telemetry)` — a NEW
plugin type (not a capability backend), with a NEW grade gate (the registry has a
trust/origin ladder but NO grading; SkillForge grading is skills-only — do not claim
reuse) (fixes H4). Re-express first-party stages as ContextStages (dogfood → proves the
type is actually invoked, not dead). Context Sankey (token in/out per stage). Tests: a
stage cannot admit a gate-denied spawn (I1 assertion); ungraded stage stays out of the
default pipeline; the new type has a live invocation (e2e-wiring proof).

## Phase P4 — Marketplace
Share/import pipelines + community stages on the existing marketplace surface.

## Sequencing
**P-1 (wire live) → P0 (meter) → P1 (observe) → P2 (configure) → P3 (plug) → P4.**
P-1 is the hard prerequisite: without it P0's gate and P1's trace have no call site.

## Test strategy
Backend pytest per phase; every invariant I1-I5 a named assertion. Frontend `npm run
build` + one Playwright per surface. e2e-wiring proof for P-1 (CEL reached from a real
turn) and P3 (the new plugin type is actually invoked). Docs updated same commit.

## Open decisions (resolve before P2)
- Separate CE pool via own counter_file (recommended, per H1). · Public name.
- Paid-tier CE limits (business ADR). · Viewing traces never costs a unit (recommended).
