# Delegation Routing — how the OS picks the right execution mechanism (ADR-0203)

This is the **single conceptual reference** for every "which tool runs this
task?" decision in CorvinOS. It exists because the answer used to live in
three places with three vocabularies (console triage, bridge ACS-X, ATO
hints) — and they could disagree.

## 1. The mechanism inventory

| # | Mechanism | Right for | Entry points | Metered? |
|---|---|---|---|---|
| 1 | **Direct OS-turn** (Claude Code `-p`, session workspace, built-in Task-tool sub-delegation) | default; ALL coding; anything sequential/context-heavy | every surface | no |
| 2 | **ACS delegation_loop** (manager/worker fan-out, `acs_runtime.py`) | **big-data-shaped tasks** (explicit volumes, million-row/GB-scale corpora — `_is_big_data_task()`), plus the explicit `/delegate` override; pre-ADR-0217 it was the default for ALL fan-out shapes | `/delegate` / big-data auto-route / `acs_delegate` MCP / AWP `engine: delegation_loop` | **yes** — 1 compute unit/run |
| 3 | **AWP DAG workflows** (`corvin_workflows`, deterministic node graph; "dynamic workflows" = generated/exported specs, awpkg) | fixed repeatable pipelines, branching, human-in-the-loop pauses | `workflow_run` MCP / console routes / CLI / scheduler | **yes** — compute units |
| 4 | **Loop / recurring** (`scheduler.py` cron+reminders; `/loop`-style self-paced iteration inside a turn) | time-based recurrence, monitoring, retry-until-green | scheduler; LOOP directive → model iterates | per-fire = normal turn |
| 5 | **Goal system** (`goal.py`, `<session_goal>` block, `/goal`) | persistent multi-session objectives | `/goal` (bridges); GOAL directive | no |
| 6 | **L25 Compute** (deterministic data processing, DSI datasources) | statistics, charts, CSV/dataset transforms, ML | COMPUTE directive → `compute_run`; console compute routes | **yes** — compute units |
| 7 | **Normal delegation** (`corvin_delegate` MCP: `delegate_claude_code/codex/opencode/hermes/copilot`) | one bounded call to a *named* engine | model-chosen tool; DELEGATE directive | no (deliberate, LIC-DELEGATE-MCP-COMPUTE-01) |
| 8 | **Background tasks** (`/task`·`/bg` bridges; console TaskManager) | long-running detached jobs with completion notify | explicit user command / CCC `/create task` | task-count quotas |
| 9 | **TDE — Tiered Delegation Engine** (ADR-0214, `operator/orchestration/tde/`): one InitialAnalysis LM call → parallel step batches → per-step three-gate delegation (L34 fail-closed → budget → learned loss) to subprocess one-shot workers | **off unless selected** — runs only while the operator has picked `worker_engine: tde` in Settings → Worker Engine | ADR-0114 delegated branch (auto, `_worker_engine_target`, `tde` mode only); console `/use-engine tiered_delegation <task>` (also `tde` mode only); `SendIntegration` for embedders | **yes** — shared agentic-compute pool (ADR-0216), charged at the `TieredDelegationEngine.execute` chokepoint |

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
path before any model sees the task. Two things decide it, in this order:

1. the console web-chat triage (`chat_runtime._should_delegate`) — is this task
   delegation-worthy at all?
2. the **operator's worker-engine selection** (`spec.web_chat.worker_engine`,
   Console → Settings → Worker Engine) — where a delegation-worthy task
   actually runs. The rule is
   `delegation_policy.worker_engine_target(mode, force_delegate, is_big_data,
   tde_available, quota_ok)`:

> **Reach (2026-07-27, ADR-0255).** The **Console web-chat** calls the full
> rule. The **messenger bridges** now have two paths, both dark by default:
>
> - `bridge_big_data_delegation` (`adapter.py::_maybe_delegate_big_data`,
>   unchanged since 2026-07-26): big-data-shaped messages only, no
>   `/delegate`, no triage heuristic, no mode-awareness.
> - `bridge_worker_engine_parity` (`adapter.py::_maybe_delegate_worker`,
>   ADR-0255): the FULL rule — the operator's `worker_engine` mode, an
>   explicit `/delegate`, and the SAME triage heuristic the console runs
>   (`chat_runtime.should_delegate_bundled`, imported directly — a bridge
>   turn and a console turn given identical input classify identically).
>   Falls back to the unchanged `bridge_big_data_delegation` path while off,
>   so an existing install that has not opted in sees no behavior change.
>   TDE on a bridge is gated by the separate opt-in flag `bridge_tde_execution`
>   (TDE_ROBUST_USABLE_PLAN Step 4, default OFF): with it OFF, `_worker_engine_target`
>   reports `tde_available=False`, so `mode="tde"` degrades to the direct turn on a
>   bridge exactly as before (ADR-0221 P3/P4 frozen default). With it ON (a
>   single-operator measured test), the bridge probes TDE for real via the SAME
>   `_tde_available`/`_tde_quota_peek_ok` the console uses and runs it through
>   `_run_tde_delegation` (engine-agnostic core, `SendIntegration.select_engine_and_execute`),
>   degrading to native on ANY failure or an exhausted pool (self-healing), and — when
>   `TDE_MEASUREMENT_ENABLED=1` — measures each real run in a daemon thread against the
>   tool-less baselines into `measurement.jsonl` (feeds `corvin tde gate`). The flag alone
>   unlocks only TDE, not ACS parity. The **remote-trigger** path still has no Tier-1
>   delegation at all.
>
> Both flags classify big-data with the SAME `delegation_policy.is_big_data_task`
> (moved out of `chat_runtime.py` on 2026-07-26 — while it lived there the
> bridges could not have routed big data even in principle).

| mode | delegation-worthy task lands on |
|---|---|
| `native` (**DEFAULT**) | the direct OS-turn — except a big-data shape, which still fans out to ACS |
| `acs` | ACS delegation_loop |
| `tde` | TDE, while it is available and the pool has headroom; otherwise the direct OS-turn |

An explicit `/delegate` and a **structured-data** shape (`_is_big_data_task()`)
route to ACS in **every** mode; a stock (`native`) install performs no other
auto-delegation. Since 2026-07-28 that shape is spelled out affirmatively —
see § 2a — and an ordinary request, prose, or a coding task no longer reaches it.
Every degrade ends at the direct OS-turn, never at a different delegation
engine: an unavailable TDE or an exhausted pool must not silently swap the
operator's selection. **Since 2026-08-07 (TDE_ROBUST_USABLE_PLAN Step 1) the
degrade also covers IN-FLIGHT failures**, not just the pre-dispatch
availability/pool check: once a turn has entered `_stream_tde_turn`, a missing
orchestration module (TOCTOU), an analysis/worker-IPC exception, or a mid-run
shared-pool exhaustion no longer surfaces an error to the user — it emits an
internal `_tde_degraded` sentinel (swallowed by the caller, never streamed) and
falls through to the native OS-turn, which owns the single os-span close +
`web.turn.completed` with the real rc (so the ADR-0171 engine-span is never
sealed with the wrong status and there is no duplicate turn-completion). The
user sees a short `notice/tde_fallback` (or the shared-pool notice when the
cause was quota) and then the native answer. A TDE run that completed but whose
steps failed (`ok=False`) is NOT degraded — it already spent the run, so its
honest error result stands rather than paying for a native re-run. (The ACS branch keeps its own hardened ADR-0201 ladder
for the cases that do reach it.)
(ADR-0214's RobustEngineDetector — TDE vs ACS vs claude_code — remains the
embedder-facing selector in `SendIntegration`; the console's Tier-1 choice is
the deterministic function above, not the softmax detector. TDE turns stay
gated as delegation by the pre-spawn gates. Which mechanism actually ran a turn is now visible in the
chat UI via the per-turn `engine` badge — for `tiered_delegation` turns with
step data (`tdeProgress`, `total_steps > 0`) this is the rich **TDE inline
badge** (`TdeInlineBadge` in `chat.tsx`, ADR-0214/0216) rather than the
generic "Engine: X" line; see § 8 below — and on bridges via the
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

## 2a. What counts as a structured-data (auto-ACS) shape

`delegation_policy._is_big_data_task()` is the ONE auto-delegation a `native`
install performs, and every ACS run charges one `compute_units_per_day` — so
the rule is affirmative and narrow (maintainer decision 2026-07-28). Four
shapes, in the order they are checked:

| # | Shape | Fires on | Does NOT fire on |
|---|---|---|---|
| 1 | **Big-data vocabulary** | "Big Data", "Data Lake", "Data Warehouse", "riesige Datenmengen/Datasets/Logfiles" | — (self-describing) |
| 2 | **Tabular paste** | a pipe/markdown table of ≥ `_TABLE_MIN_ROWS` (10) content rows — the table IS the mass data | a 3-row table inside an ordinary question; the bare word "Tabelle" ("erstelle eine Tabelle" stays native) |
| 3 | **Structured source + bulk work** | a CSV/TSV/Parquet/JSONL/XLSX/spreadsheet file **or** a database/SQL operation (`_DATA_FILE_RE` / `_DB_RE`), PAIRED with a bulk data verb (`_DATA_WORK_VERB_RE`: analysieren, aggregieren, gruppieren, joinen, importieren, bereinigen, deduplizieren, parsen, abfragen, …) **or** a volume | naming a source without doing data work — "Wie verbinde ich mich mit MySQL?", "Erkläre mir SQL", "Fasse die Datenbank-Migration zusammen" |
| 4 | **Volume + data noun** (legacy) | a GB/TB/PB volume or a big count (millions, grouped ≥1e6, `500k`) tied to a data noun in the SAME clause | hardware volumes ("128 GB Arbeitsspeicher", "2 TB SSD" — `_HW_NOUN_RE`) and **code** clauses ("2 Millionen Zeilen Code", "3 TB Codebase-History" — `_CODE_NOUN_RE`) |

The pairing in rule 3 is load-bearing: *naming* a database is not *doing*
database work, and without it a smalltalk question that mentions SQL would burn
a compute unit. The code carve-out in rule 4 is what finally makes
"Coding never routes into the ACS fan-out" (§ 6) true for big-count coding
prompts — "Zeilen" is a data noun, which is exactly how they used to slip
through rule 1c, which sits ABOVE the coding gate.

Everything else — ordinary conversation, prose, normal coding requests — is
False, and False means the turn runs natively and costs no quota. That
asymmetry is deliberate: a wrong positive spends the operator's daily pool on
the wrong mechanism, a wrong negative only runs the turn on the documented
degrade floor.

The ReDoS bounds survive the additions: `_BIG_DATA_MAX_SCAN` (2000) still caps
the task-description scan, the table row count gets its own larger
`_TABLE_MAX_SCAN` (200 000) because table rows are payload rather than
description, and every new regex is anchored with no variable-length run
followed by a window.

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
1c. BIG-DATA shape (`_is_big_data_task()`, ADR-0217) → delegated branch → ACS
    (the "ACS only for big data" mapping is affirmative: a big-data task
    delegates even when the COMPUTE blueprint would have said DIRECT).
    Carve-outs keep their cheaper mechanism: recurrence/goal shapes
    (a DAILY 500-GB scan is still a scheduler task) and a NAMED worker
    engine (direct delegate_* path)
2.  RECURRING/PERSISTENT/DATA     → scheduler / goal / L25 compute — NEVER ACS
    LOOP·GOAL·COMPUTE at ANY real signal (≥0.50 render floor, review F1:
    "stündlich"/"täglich" weigh 0.60-0.65 and must still not burn quota)
    DELEGATE only when a real ENGINE is NAMED (review F2: bare "delegiere" /
    "mit Hermes" the parcel carrier must not steer off the fan-out)
3.  FAN-OUT shape                → delegated branch (console) / Workflow tool
    (multi-source/multi-perspective/per-item with substantive shape).
    WITHIN the delegated branch the worker-engine setting decides: big-data
    shape → ACS delegation_loop in every mode; everything else → the selected
    engine (`native` = direct OS-turn, the default)
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
| ACS fan-out | ✓ (triage/`/delegate`) | full triage/`/delegate`/mode behind `bridge_worker_engine_parity` (dark, ADR-0255); big-data-only fallback behind `bridge_big_data_delegation` (dark) while parity is off | ✗ | ✓ `acs_delegate` |
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
| TDE run (only in `worker_engine: tde`, auto-routed or `/use-engine tiered_delegation`) | **yes** — charged at the `TieredDelegationEngine.execute` chokepoint. **The L34-forced-`claude_code` fallback is metered too** (`ClaudeCodeLocalEngine.execute` charges the same pool when it runs the real local executor — closes the 2026-07-24 review bypass where a CONFIDENTIAL token in the prompt forced claude_code and ran unmetered). Only real-compute configs are metered (real IPC or the default claude-CLI local executor), stub/mock test configs are not; invalid plans refund the unit. The auto-route additionally peeks the pool WITHOUT charging (`_tde_quota_peek_ok`) and steers an exhausted pool into the ACS branch's ADR-0201 degrade ladder |
| ACS quota fallback (single direct turn) | **no** (un-metered `run_delegate`) — but bounded by `_FALLBACK_MAX_PER_DAY`=50/tenant/day (race-safe LIC-1 lock, review D3), an elevated-but-fixed `BUDGET_FALLBACK_MAX_S` wall-clock ceiling (review F6/F7; caller `budget_override` threads through route AND chokepoint, F8/D1), and max `_ACS_FB_MAX_CONCURRENT`=2 concurrent fallback turns per tenant on the console route (typed 429 beyond, review D4) |
| `workflow_run` / compute routes | **yes** |
| Scheduler fires / background tasks | normal-turn cost / task-count quotas |

Note: `delegate_*` single calls are capped at the 600 s interactive
`BUDGET_MAX_S` — only the quota fallback may request the higher ceiling, and
only via `run_delegate(budget_ceiling_s=…)`, never from the MCP tool surface
(review F7).

## 6. Invariants (must NOT be weakened)

- Explicit user commands beat every classifier (Tier-1 override).
- LOOP/GOAL/COMPUTE/DELEGATE shapes never route into the ACS fan-out —
  with exactly ONE exception since ADR-0217: a big-data-shaped COMPUTE task
  (rule 1c) delegates to ACS; LOOP/GOAL and named-engine DELEGATE shapes
  keep their mechanism even when big-data-shaped.
- Coding never routes into the ACS fan-out (ADR-0202) — `/delegate` remains
  the escape hatch.
- Tier 2 stays **fail-open and advisory**: classifier failure → no
  directive → the turn still runs. Tier 1 failure modes stay fail-safe
  toward the DIRECT path (a mis-routed direct turn is recoverable; a
  mis-routed fan-out burns quota).
- The triage path never spawns a subprocess (heuristic stage only — the
  Haiku fallback is reserved for the bridge adapter's Tier-2 injection).
- The worker engine is the operator's choice, and `native` is the default: a
  stock install auto-delegates NOTHING except a big-data shape. TDE is never
  entered — not by the auto-route and not by `/use-engine tiered_delegation`,
  which answers with a "TDE is switched off" hint instead — unless
  `worker_engine: tde` is selected. `/delegate` remains the explicit ACS
  override; `/use-engine claude_code` is the explicit sequential override
  (`_force_direct`) and hard-suppresses delegation — kept in lockstep with the
  pre-spawn gate's `_will_delegate` so the L34/L35 compliance row always
  matches the engine that actually spawns. The engine choice
  (`_worker_engine_target` → `delegation_policy.worker_engine_target`) stays
  pure + deterministic — no subprocess, no LM call, unit-tested as a matrix,
  and the TDE availability probes never run outside `tde` mode.
- Every degrade ends at the direct OS-turn. An unavailable engine or an
  exhausted pool must never re-route into a *different* delegation engine than
  the one the operator selected.
- `_tde_available()` requires BOTH the TDE module set (source tree or the
  wheel-vendored `_vendor/operator/orchestration`, wired via
  `_operator_bootstrap._OPERATOR_SUBTREES`) AND a resolvable `claude` CLI — a
  Hermes-only / no-API-key install reports TDE unavailable and delegates via
  ACS (which pins a local worker model) rather than failing every turn.
- Big-data detection (`_is_big_data_task()`, bounded/non-backtracking — a
  scan-capped multi-regex + Python clause-proximity test, NOT one mega-regex)
  ties volumes to a DATA noun in either
  order, so "3 GB RAM" / "128 GB Arbeitsspeicher" (hardware) never route to
  the ACS fan-out; grouped ("1.000.000") and suffixed ("500k") counts match.
  Since 2026-07-28 it additionally requires a STRUCTURED-DATA shape (§ 2a) —
  CSV/spreadsheet file, database/SQL work, or a real tabular paste — and
  carves out code clauses. Do not widen it back to "any volume + any data
  noun", and do not make a bare source mention (the word "Datenbank", "SQL",
  or "Tabelle") sufficient on its own: both regressions put ordinary chat
  turns on the metered fan-out.
- A chat turn surfaces at most `_MAX_TURN_ARTIFACTS` (20) artifact chips, and
  runtime bookkeeping trees (`_SESSION_INTERNAL_DIRS` — `acs/`, `tasks/`,
  `tde/`, `voice/`) are never chat artifacts at all. Filtering happens BEFORE
  the cap so internal state cannot consume a real output file's budget, and
  truncation emits an `artifacts_truncated` notice rather than dropping files
  silently. See § 7a.
- The TDE plan is untrusted LM output: `initial_analysis.from_dict` REJECTS a
  plan over `MAX_PLAN_STEPS` (64) and clamps per-step / total token estimates,
  and the executor bounds real subprocess fan-out per batch
  (`MAX_BATCH_CONCURRENCY`) — one charged pool unit cannot self-authorize an
  unbounded number of concurrent `claude` processes.
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

## 6a. TDE worker capability (design characteristic, ADR-0217)

TDE steps — delegated and local — run as **tool-less single-turn one-shots**
(`claude -p --max-turns 1 --disallowedTools "*"`) over the InitialAnalysis
statement, by ADR-0214 design. ACS workers, by contrast, run with full tools
and up to `--max-turns 20`. This is deliberate and safe for what each engine
now handles:

- **Coding tasks never reach TDE** — rule 4 routes every coding-shaped task to
  the DIRECT Claude Code OS-turn (full tools, real Task-tool sub-delegation,
  cwd/repo context). So a "fix the bug in foo.py" is unaffected by TDE's
  tool-less workers (verified: `_should_delegate` returns False for coding).
- **TDE handles parallel analysis/reasoning/synthesis** over the full context
  it is given, where tool-less full-context one-shots are the right shape.
- **Tasks that genuinely need live tools AND aren't coding AND aren't big
  data** (e.g. "fetch from these 3 live APIs and compare") are the narrow band
  where ACS's tool-capable workers would do more. Per the maintainer's ADR-0217
  scope ("ACS only for big data") these currently route to TDE; if this band
  proves material, the fix is a routing carve-out, not a change to TDE workers.

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

## 7a. Delegation bookkeeping is not a chat artifact (2026-07-28)

Every model-chosen `delegate_*` MCP call writes a WDAT run record under
`<session>/acs/runs/<run_id>/{manifest,result}.json`
(`corvin_delegate.delegation._write_wdat_run`) so the run shows up in the
Agentic-Compute panel. That tree lives inside the session workdir, and the
direct OS-turn's artifact scan diffs the WHOLE workdir
(`after_files - _before_files`).

In the console chat `web:ISGd-xIvqn` (2026-07-27) one turn made 72 such calls,
so the scan emitted **144 artifact chips** — 72× `manifest.json`, 72×
`result.json` — which is what the operator saw as "hundertmal dasselbe". Note
what it was NOT: that turn ran `native` with `will_delegate: false`
(`chat_debug.jsonl`), so no ACS fan-out was involved. The repeated chips were
*named* after ACS runs, which is why the symptom read as an ACS problem.

The ACS delegation branch already filtered these (`_ACS_SKIP_DIRS` /
`_ACS_SKIP_ROOT_FILES`) — but only relative to its OWN `run_dir`, so the direct
turn, which is the path that actually ran, had no filter at all. The scan now
lives in `chat_runtime._scan_turn_artifacts()` (extracted so it is testable
without a subprocess) and applies `_is_session_internal()` plus the
`_MAX_TURN_ARTIFACTS` cap. Regression tests:
`core/console/tests/test_turn_artifact_scan.py`.

## 8. TDE inline badge (chat UI, ADR-0214/0216)

For an assistant turn where `engine === "tiered_delegation"` AND the turn
carries step data (`tdeProgress` present, `total_steps > 0`), the per-turn
chat-bubble badge (`MessageBubble` in `chat.tsx`) branches from the generic
"Engine: X" line to a richer `TdeInlineBadge`. This is a pure display
concern — it does not change routing, gating, or metering, only what the
already-computed `TdeProgress` payload (`chat-registry.ts`) renders as:

| Field | Rendered as | Source |
|---|---|---|
| `completed_steps` / `total_steps` | `Steps: X/Y` | `TieredDelegationEngine.execute` step loop |
| `delegated_count` / `local_count` | `Delegated: N · Local: M` | per-step three-gate delegation outcome |
| `l34_forced` | `L34 gate: forced` / `L34 gate: clear` | L34 fail-closed pre-step gate |
| `latency_delta_pct` | `Latency Δ: ±N% (measured)` — omitted if `null` | real wall-clock measurement in `_summarize()`, never estimated |
| `token_savings_pct` / `token_usage_instrumented` | **always** `Token savings: not instrumented` unless `token_usage_instrumented === true` | ADR-0215 honesty contract: no real token-usage instrumentation exists, so this field is structurally always `null` today — the badge must never render it as `0%` or any number, which would misrepresent an unmeasured quantity as a measured saving |
| `task_type` / `complexity` | `<task_type> · <complexity>` — omitted if both absent | turn's `InitialAnalysisRequest.classification` |
| `quota_used_today` / `quota_limit` | `Quota: N/limit today` — the **whole chip is omitted** when `quota_limit === null` (unlimited tier); never fabricated as `N/0` or `N/∞` | ADR-0216 shared agentic-compute pool chokepoint (`_enforce_tde_compute_quota`) |

A **"View graph →"** link sets the chat page's `auditTab` state to
`"tde-graph"` and opens the Audit panel (`auditOpen = true`); the existing
`TdeAuditGraphPanel` then resolves the same session's latest TDE turn on its
own (it already scans `messages` for `tdeRunId`/`tdeProgress` — no run id is
threaded through the click handler).

**Fallback (unchanged):** the generic "Engine: X" line renders for every
non-TDE engine, and for the zero-step edge case (`total_steps === 0` — the
`engine_progress` event never fires for a TDE run with no steps), so a TDE
turn is never left without an engine attribution line.
