# Delegation Routing — how the OS picks the right execution mechanism (ADR-0203)

This is the **single conceptual reference** for every "which tool runs this
task?" decision in CorvinOS. It exists because the answer used to live in
three places with three vocabularies (console triage, bridge ACS-X, ATO
hints) — and they could disagree.

## 1. The mechanism inventory

| # | Mechanism | Right for | Entry points | Metered? |
|---|---|---|---|---|
| 1 | **Direct OS-turn** (Claude Code `-p`, session workspace, built-in Task-tool sub-delegation) | default; ALL coding; anything sequential/context-heavy | every surface | no |
| 2 | **ACS delegation_loop** (manager/worker fan-out, `acs_runtime.py`) | N *independent* subtasks: multi-source research, comparisons, multi-perspective review, per-item bulk | console triage / `/delegate` / `acs_delegate` MCP / AWP `engine: delegation_loop` | **yes** — 1 compute unit/run |
| 3 | **AWP DAG workflows** (`corvin_workflows`, deterministic node graph; "dynamic workflows" = generated/exported specs, awpkg) | fixed repeatable pipelines, branching, human-in-the-loop pauses | `workflow_run` MCP / console routes / CLI / scheduler | **yes** — compute units |
| 4 | **Loop / recurring** (`scheduler.py` cron+reminders; `/loop`-style self-paced iteration inside a turn) | time-based recurrence, monitoring, retry-until-green | scheduler; LOOP directive → model iterates | per-fire = normal turn |
| 5 | **Goal system** (`goal.py`, `<session_goal>` block, `/goal`) | persistent multi-session objectives | `/goal` (bridges); GOAL directive | no |
| 6 | **L25 Compute** (deterministic data processing, DSI datasources) | statistics, charts, CSV/dataset transforms, ML | COMPUTE directive → `compute_run`; console compute routes | **yes** — compute units |
| 7 | **Normal delegation** (`corvin_delegate` MCP: `delegate_claude_code/codex/opencode/hermes/copilot`) | one bounded call to a *named* engine | model-chosen tool; DELEGATE directive | no (deliberate, LIC-DELEGATE-MCP-COMPUTE-01) |
| 8 | **Background tasks** (`/task`·`/bg` bridges; console TaskManager) | long-running detached jobs with completion notify | explicit user command / CCC `/create task` | task-count quotas |
| 9 | **TDE — Tiered Delegation Engine** (ADR-0214, `operator/orchestration/tde/`): one InitialAnalysis LM call → parallel step batches → per-step three-gate delegation (L34 fail-closed → budget → learned loss) to subprocess one-shot workers | parallelizable coding/analysis plans where full-context steps can fan out safely | EXPLICIT opt-in only: console `/use-engine tiered_delegation <task>`; `SendIntegration` for embedders; live E2E suite | no (helper-model one-shots) |

**Remote instances (A2A, L38)** are not a ladder mechanism: like mechanism 7
they are a model-chosen tool (`a2a_send` MCP, persona flag
`orchestration_enabled`), never forced by Tier-1/Tier-2 routing. Since
2026-07-20 the model can address a paired instance by its **connection name**
(the label set in Agent Hub — e.g. "delegiere das an Papa Laptop"):
`a2a_send` resolves name → endpoint via `RemoteEndpointRegistry.resolve()`
(unique match required, ambiguity errors out); `a2a_list_endpoints` lists the
names. What the remote side will actually execute is governed by ITS origin
rights (Observer/Executor, persona, tool opt-ins) — see
[layer-38-a2a-network.md](layer-38-a2a-network.md) § Per-connection rights.

## 2. Two-tier routing model

The load-bearing structural insight: there are TWO kinds of routing decision,
and they must not be conflated.

**Tier 1 — authoritative runtime routing.** The runtime *forces* an execution
path before any model sees the task. Today exactly one Tier-1 decision
exists: the console web-chat triage (`chat_runtime._should_delegate`) that
either spawns the ACS fan-out or the direct OS-turn. (ADR-0214's
RobustEngineDetector — TDE vs ACS vs claude_code — is implemented and
E2E-proven in `SendIntegration`, but does NOT drive console Tier-1 routing
yet: per ADR-0214 that requires a canary; the console runs TDE only on the
explicit `/use-engine tiered_delegation` command, gated as delegation by the
pre-spawn gates. Which mechanism actually ran a turn is now visible in the
chat UI via the per-turn `engine` badge, and on bridges via the
`[⚙ ACS: <primitive>]` context-bar segment.) Explicit user commands
(`/delegate`, `/task`, `/goal`, schedule requests) are Tier-1 overrides:
the user has already chosen the mechanism; classifiers never override them.

**Tier 2 — advisory primitive directive.** The runtime *classifies* the task
(ACS-X, `acs_classify.py`: GOAL · LOOP · WORKFLOW · COMPUTE · DELEGATE ·
DIRECT) and injects an `<acs_directive>` into the OS-turn system prompt. The
**model** then picks the concrete tool (Workflow tool, `compute_run`,
`delegate_*`, `/loop`-style iteration, setting a goal). Tier 2 never spawns
anything itself — it steers the turn that Tier 1 already chose.

Both tiers run on **both** chat surfaces since ADR-0203: the bridges always
had Tier 2 (adapter.py `<acs_directive>` injection); the console got it in
`chat_runtime._acs_directive_block` (same shared classifier, heuristic stage
only, fail-open). ATO (`ato_classify.py`, ADR-0165) stays a third, purely
observational layer: audit hints, no routing.

## 3. The priority ladder (the actual decision order)

Applied at Tier 1 (console triage) and encoded in Tier 2's primitive set.
First match wins:

```
1.  EXPLICIT USER COMMAND        → that mechanism, always
    /delegate → ACS · /task → background · /goal → goal · "schedule …" → scheduler
1b. EXPLICIT WORKER/FAN-OUT DEMAND → ACS, before the classifier + coding gates
    ("mehreren Workern", "3 workers", "fan-out", "parallele Recherchen") — the
    user literally named workers; a product-noun collision or an incidental
    coding token must not hijack it (review F2/F3/F4 + D6(a)).
    A BARE parallel adverb ("parallel"/"gleichzeitig") is NOT enough here
    (review D6): it is too weak to force the quota-burning fan-out and falls
    through to rule 2/3 — "überwache … parallel" is monitoring (LOOP),
    "prüfe alle 10 Minuten parallel" is a scheduler task
2.  RECURRING/PERSISTENT/DATA     → scheduler / goal / L25 compute — NEVER ACS
    LOOP·GOAL·COMPUTE at ANY real signal (≥0.50 render floor, review F1:
    "stündlich"/"täglich" weigh 0.60-0.65 and must still not burn quota)
    DELEGATE only when a real ENGINE is NAMED (review F2: bare "delegiere" /
    "mit Hermes" the parcel carrier must not steer off the fan-out)
3.  FAN-OUT shape                → ACS delegation_loop (console) / Workflow tool
    (multi-source/multi-perspective/per-item with substantive shape)
4.  CODING shape                 → direct OS-turn + built-in Task tool — NEVER ACS
    (sequential, context-heavy, workspace-bound; incl. crash/freeze; ADR-0202)
5.  REMAINING SUBSTANTIVE         → ACS (console legacy: strong verbs, long/multi-step)
6.  EVERYTHING ELSE               → direct OS-turn
```

Rule 1b sits ABOVE the classifier + coding gates (review F2/F3/F4): an
explicit worker/fan-out demand is unambiguous and must win over a noun
collision or an incidental coding token. The discriminator is signal
STRENGTH, not a confidence threshold (review D6 refutation): the earlier fix
suppressed 1b on a 0.90 confidence gate, which let the whole 0.60–0.85 LOOP
band slip through on a bare adverb — an ordinary monitoring verb ("überwache"/
"beobachte"/"watch"/"monitor" = 0.85) plus "parallel" fired 1b and burned a
compute unit. Only an EXPLICIT worker/fan-out phrase (`_EXPLICIT_WORKER_RE`:
a count/quantifier + "worker(s)", "fan-out", or "parallele <plural work-noun>")
reaches rule 1b now; a bare "parallel"/"gleichzeitig" adverb — and a bare
"worker" noun ("celery worker crashes") — defers to rule 2 (blueprint → DIRECT)
and rule 3 (which demands a substantive multi-source shape). DELEGATE is
irrelevant here now — 1b no longer inspects the blueprint at all.
Rule 2 sits ABOVE fan-out because each of those shapes has a *cheaper,
structurally correct* mechanism, and mis-routing them into ACS burns the
daily compute unit on the wrong tool. Rule 4 (coding) sits BELOW fan-out so
a genuinely fan-out-shaped task that mentions code tokens still fans out.

Implementation: rule 1b is `_EXPLICIT_WORKER_RE` (strong signal only); rule 2 reuses the
**shared ACS-X heuristic** from the console triage (`_acs_x_blueprint`,
LOOP/GOAL/COMPUTE at ≥0.50, DELEGATE needs `_NAMED_ENGINE_RE`, fail-open with
a one-time import-failure warning); rules 3–5 are the console regex tables
(`_TRIAGE_FANOUT_RE`, `_TRIAGE_CODING_RE`, strong/weak verbs). One classifier
vocabulary, two consumers. The `acs_classify` `jede…`-recurrence signal spans
a bounded window (`[^.!?]{0,30}`) so a per-item fan-out
("für jeden … in Minuten") is not mis-read as LOOP. Two console-side
supplements (review D6/D7): `chat_runtime._recurrence_supplement` upgrades
the German "alle N Minuten/Stunden/Tage" form (unknown to the shared table)
to LOOP 0.90 for both Tier-1 routing and the Tier-2 directive, and the
`mehrere[nrm]?` flexion covers the dative "aus mehreren Quellen" in
`_TRIAGE_MULTI_RE`/`_TRIAGE_FANOUT_RE`.

## 4. Surface-capability matrix

Not every mechanism exists on every surface — a directive must never assume
a capability the surface lacks.

| Mechanism | Console web-chat | Messenger bridges | Voice | MCP (model-chosen) |
|---|---|---|---|---|
| Direct OS-turn | ✓ | ✓ | ✓ (via bridge) | — |
| ACS fan-out | ✓ (triage/`/delegate`) | ✗ (no fan-out path) | ✗ | ✓ `acs_delegate` |
| DAG workflows | ✓ (routes) | via MCP tools in-turn | via MCP | ✓ `workflow_run` |
| Scheduler | via MCP/in-turn | ✓ (native, 30 s tick) | ✓ | — |
| Goal | in-turn advisory | ✓ `/goal` | ✓ | — |
| L25 Compute | ✓ (routes + MCP) | via MCP | via MCP | ✓ |
| `delegate_*` | ✓ (MCP merged into turn) | ✓ | ✓ | ✓ |
| Background `/task` | CCC `/create task` | ✓ `/task`·`/bg` | ✓ | — |
| Worker personas (hermes-/copilot-worker) | n/a | WORKFLOW+DELEGATE directives suppressed (ADR-0160 M4a) | n/a | n/a |

## 5. Metering map (why the ladder ordering is also a cost policy)

| Path | Charges `compute_units_per_day`? |
|---|---|
| Direct OS-turn (incl. its Task-tool subagents) | **no** |
| `delegate_*` single calls | **no** (maintainer decision) |
| ACS fan-out (any entry) | **yes** — web-chat charges at `chat_runtime` (direct-`ACSRuntime` path), everything else at the `run_acs_workflow` chokepoint; quota exhausted → single-turn fallback (ADR-0201) |
| ACS quota fallback (single direct turn) | **no** (un-metered `run_delegate`) — but bounded by `_FALLBACK_MAX_PER_DAY`=50/tenant/day (race-safe LIC-1 lock, review D3), an elevated-but-fixed `BUDGET_FALLBACK_MAX_S` wall-clock ceiling (review F6/F7; caller `budget_override` threads through route AND chokepoint, F8/D1), and max `_ACS_FB_MAX_CONCURRENT`=2 concurrent fallback turns per tenant on the console route (typed 429 beyond, review D4) |
| `workflow_run` / compute routes | **yes** |
| Scheduler fires / background tasks | normal-turn cost / task-count quotas |

Note: `delegate_*` single calls are capped at the 600 s interactive
`BUDGET_MAX_S` — only the quota fallback may request the higher ceiling, and
only via `run_delegate(budget_ceiling_s=…)`, never from the MCP tool surface
(review F7).

## 6. Invariants (must NOT be weakened)

- Explicit user commands beat every classifier (Tier-1 override).
- LOOP/GOAL/COMPUTE/DELEGATE shapes never route into the ACS fan-out.
- Coding never routes into the ACS fan-out (ADR-0202) — `/delegate` remains
  the escape hatch.
- Tier 2 stays **fail-open and advisory**: classifier failure → no
  directive → the turn still runs. Tier 1 failure modes stay fail-safe
  toward the DIRECT path (a mis-routed direct turn is recoverable; a
  mis-routed fan-out burns quota).
- The triage path never spawns a subprocess (heuristic stage only — the
  Haiku fallback is reserved for the bridge adapter's Tier-2 injection).
- Quota exhaustion degrades (ADR-0201), never hard-fails — but the degraded
  path is itself bounded (`_FALLBACK_MAX_PER_DAY`, `budget_ceiling_s`,
  per-tenant fallback concurrency) so it cannot become an unbounded
  un-metered surface (review F6/F7/D3/D4).
- EVERY "ACS → direct turn" fallback branch re-runs the L34/L35 pre-spawn
  gate against the engine that will ACTUALLY spawn (`_os_engine`),
  fail-closed — not just the quota-exhausted branch (review D2). The initial
  gate classified the turn as engine "acs"; without the re-gate,
  CONFIDENTIAL data could bypass the residency policy on the runtime-
  unavailable / dir-uncreatable branches.
- The WORKFLOW directive is suppressed on the console direct turn (review
  F9): that turn is un-metered, and "use the Workflow tool" would route it
  back into quota-charging compute — a contradiction. Bridges (no fan-out
  path of their own) keep the WORKFLOW directive.

## 7. Known gaps (documented, not hidden)

- The bridge adapter's Tier-2 uses the two-stage classifier (with Haiku
  fallback); the console uses heuristic-only. Ambiguous console tasks may
  get no directive where a bridge turn would.
- `_TRIAGE_FANOUT_RE`/`_TRIAGE_CODING_RE` (rules 6–7) live in
  `chat_runtime.py`, not yet in `acs_classify.py` — the WORKFLOW primitive's
  signal table and the console fan-out table are similar but not identical.
  Target state: fold both into one shared table.
- `delegation_loop` exists in three forms (standalone `ACSRuntime`, AWP
  engine, DAG node in `routes/workflows.py`) — the DAG node reimplements
  the manager semantics instead of reusing `acs_runtime._manager_loop`.
- ATO (`ato_classify.py`) overlaps ACS-X vocabulary but stays advisory-only;
  candidates for merging into the shared table.
