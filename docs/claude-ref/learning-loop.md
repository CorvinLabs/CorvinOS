# ACP Learning Loop (ADR-0613) and the learning-system register (ADR-2281)

**Status:** as the register says (below) — the writer side of the loop is state A (event
store, outcome sink, feedback gate); the delegation router reads the learned config only in a
cleared Phase 2, so the router loop is state B · **ADRs:** ADR-0613, ADR-2281 (Corvin-Knowledge)
· depends on ADR-0314, ADR-0532, ADR-0549, ADR-0555, ADR-2092

Until 2026-09-06 the ADR-0314 "learning loop" was a **log, not a loop**: Skills
were booted and unit-tested, but the L5/L10 entry points had zero production
callers, no task outcome was ever recorded, the optimizer modules had no
importer outside tests, the learning store bypassed the audit chain, and the
Learning Dashboard answered with hard-coded mock data. This page describes the
mechanisms; **which system is live is stated only by the register**.

## The register (ADR-2281) — single statement of state

`core/learning/learning_systems.yaml` lists every learning system with its production
callers (`path:line`, re-read when recorded), its tests and its state: **A** live and read ·
**B** live but shadow / no reader yet, with an `intake` naming when its output may act ·
**D** dead, retired by the named decision (ADR-2282) until its last module is deleted. Nothing
here, in CLAUDE.md or elsewhere may call a system "wired", "live" or "complete" unless its
register entry is A. Three CI checks hold it (all in `run-all-tests.sh`):

| Check | File | Fails on |
|---|---|---|
| Register | `tests/learning/test_learning_systems_register.py` | a caller file/line that does not exist, a missing test file, an A without caller, a B without `intake`, a D without a resolving ADR id or with every module gone, an unclassified unreachable `core/vibe_engineering` module, and a row of the table below that differs from the register |
| Reachability ratchet | `scripts/learning_reachability.py` + `scripts/learning_reachability_baseline.json`, `tests/learning/test_learning_reachability_ratchet.py` | a NEW unreachable module in the learning scopes (same graph as `zero_caller_sweep`), and a STALE baseline entry (module reachable again or deleted). Shrink-only |
| Call-site pins | `tests/learning/test_learning_call_site_pins.py` | L06 injection gate / follow-up grade and L10 distill scheduling / injection through `adapter.process_one`; L14 `emit_*_confidence` through `POST /v1/console/forge-bundles/import`. Each was shown red with its call site cut |

<!-- register-table:start (kept equal to core/learning/learning_systems.yaml by the register test) -->
| Id | System | State |
|---|---|---|
| L01 | Learning host and Data Hub daemon | A |
| L02 | Anomaly monitor (learning host, every 300 s) | A |
| L03 | Event store and outcome sink | A |
| L04 | Feedback gate (throttle, reality check, trust weight) | A |
| L05 | Delegation-router config loop (os.delegation_router learned config) | B |
| L06 | Skill grades, injection gate, promotion | A |
| L07 | CEL turn grades | B |
| L08 | Model selector learner (descriptive only) | B |
| L09 | Savings meter (Free-tier cost.optimize allowance) | A |
| L10 | User model distillation (L28, opt-in per chat) | A |
| L11 | Shadow OS skills (workflow optimizer, flow guard, security orchestrator, context adapter) | B |
| L12 | Routing ledger and rollback judge | B |
| L13 | Flow-guard learning route | B |
| L14 | Layer Forge learning integration (emit_*_confidence / *_outcome) | A |
| L15 | KB learning (kb guidance run, guidance skills) | A |
| L16 | Compute search strategies (grid, random, bayes) and HAC attribution | A |
| L17 | Learning and maturity panels (report health, not learning) | A |
| L18 | Video learning feedback API (ratings, per-variant statistics, advice) | A |
| L19 | Autonomous Forge (autopilot off by default) | B |
| H03 | Plugin-declared learning loops (manifest discovery) | B |
| R19–R19 | Brain hub leftover kept by ADR-2282: `subsystems/notification_daemon.py` (still imported by the unreachable `core/skill_forge/skill_forge_v2.py`); R01–R18 were removed with their deleted modules | D |
<!-- register-table:end -->

Findings of 2026-10-25: **L10** (now A) — both adapter call sites ran, but
`user_model.distill()` queried `conversation_recall.recall('')`, an empty FTS5 phrase that
matched nothing, so no model was ever distilled. `recall()` now answers an empty query that
names a chat with that chat's newest turns (oldest first; without a chat it still returns
nothing), and the pin `test_l10_a_real_turn_yields_a_distilled_model` passes through
`adapter.process_one`. **L08** (B) — the selector learner's only input is the 0.85 / 0.2 "turn ended without an error" proxy and the
selection never reads it. The 19 unreachable `core/vibe_engineering` modules are classified
not-learning in the register (`classified_not_learning`), none is retired by ADR-2281.

## The loop

```
real turn ──► delegation_policy.route_and_record()   (EXACTLY once per OS turn, bridge AND console)
                 │  reached via resolve_delegation_route (turn stays native) or
                 │  resolve_worker_engine (delegation-worthy), or directly for a turn
                 │  the triage never saw (bridge flags off / bare /delegate; console
                 │  delegation off / throttled / /use-engine claude_code)
                 ├─ bundled rule decides; os.delegation_router ADVISES (confirm | de-escalate to native)
                 ├─ Phase gate (monitoring/dual_write.effective_phase): shadow unless
                 │  CORVIN_ACP_PHASE=phase2_dual_write AND Skill booted AND no rollback AND
                 │  readiness ready for the surface; phase2_real is always refused
                 ├─ dual_write only: clamp (native-only de-escalation, never over /delegate);
                 │  a Skill-changed route is served only if "routing.phase2_decision" COMMITTED
                 └─ one content-free row in <tenant>/learning/routing/routing_ledger.jsonl
                        • skill.executed → core audit chain, decision {engine, bundled_engine, shadow, confidence}

turn ends ──► delegation_policy.record_turn_outcome()   (bridge process_one / console _os_emit_completed)
                 • outcome row joined by turn_id: engine that SERVED + ok + latency
                 • a Skill-served turn runs the rollback judge (≥100 samples/side, >2 % drop ⇒ trip)

task ends ──► TaskManager.record_event(task.completed | task.failed)
                 └─► core.learning.outcome_sink.emit_task_outcome()
                        • OUTCOME event (task_id, status, exit_code, duration_ms, engine, task_type)
                        • tenant = the task's OWN metadata (create_task(tenant_id=…)), never env

operator ──► POST /v1/console/learning/feedback   {task_id, outcome_quality, would_repeat, reason}
                 • GDPR Art. 6 consent gate FIRST: scope "learning_feedback", deny-by-default;
                   no consent ⇒ 403, audited as console.action_denied
                   (action learning.feedback, reason consent_required); nothing else runs
                 • FEEDBACK event (closed enums only; the free text never enters a chain),
                   written SYNCHRONOUSLY through the ADR-0534 trust gate (throttle → reality check
                   → trust weight; EventEmitter.write_sync). Rejected ⇒ 422
                   {error: feedback_rejected, reason, audit_ref}, console action_denied
                   (reason feedback_rejected:<reason>); not recorded, optimizer never runs.
                   Not committed ⇒ 503 learning_event_not_recorded. Until 2026-10-25 the event was
                   only queued: a gate rejection was lost on the worker AFTER "recorded" was
                   answered and the optimizer still ran on the hypotheses.
                 • FeedbackInterpreter → ConfigHypothesis[]   (deterministic rules, ADR-0549)
                 • SkillAdapter.run_optimizer_epoch(hyp, recent_outcomes(tenant, 10))
                        50-epoch baseline → hypothesis phase → accept iff Δsuccess ≥ MDE (0.05)
                        accepted → new config VERSION (snapshot persisted) + CONFIG_UPDATED event
                                   + console audit "skill_config_updated:hypothesis_accepted"

next turn ──► DelegationRouterSkill.execute() reads load_skill_config(tenant)
                 a LEARNED confidence_threshold (only once a version exists) is the acceptance
                 bar for de-escalation advice in Phase 2; default 0.75. The Skill never
                 escalates — the one-tier escalation it used to apply was removed (ADR-2092).
```

## Components

| Piece | File | Contract |
|---|---|---|
| L5 routing call site | `corvin_operator/bridges/shared/delegation_policy.py::route_and_record` (Skill via `_consult_router`), reached from `resolve_delegation_route` / `resolve_worker_engine` when the caller passes the turn (`sink`/`turn_id`) — a call without it is a pure query (`record=False`: same answer, no row, no audit, no Skill run in shadow) | exactly ONE decision per turn per surface; runs AFTER the bundled rule + extension-point hook; degrades to the bundled engine on any failure; skips un-booted processes. The bridge boots the registry itself since 2026-09-28 (`adapter._boot_acp_skills`, ADR-2092 G0) — before that every bridge turn was skipped |
| L5 routing ledger + gates | `core/skills/os_skills/monitoring/` — `routing_ledger` (decision/outcome rows, closed vocabulary, ADR-0297 `has_sensitive` gate on every string), `correctness_tracker` (observable success, skill- vs bundled-routed), `rollback_detector` (persisted trip, audited reset), `readiness` (≥500 decisions, join ≥0.95, per-complexity proxy evidence), `dual_write` (phase gate + clamp) | see ADR-2092; Phase 2 activation is refused and audited once per process per reason (`routing.phase2_refused`) until every gate holds |
| Outcome sink | `core/learning/outcome_sink.py` | `emit_task_outcome()` / `recent_outcomes()`; content-free; fail-soft; tenant from task metadata only |
| Task chokepoint | `core/console/corvin_core/task_manager.py::TaskManager.record_event` | emits on `task.completed` / `task.failed`; `create_task(tenant_id=…)` at both console creation sites |
| Audit-first store | `core/learning/event_store.py::EventStore.write_event` | core chain record (`learning.<event_type>`, content-free) FIRST via `event_persistence.core_audit_event`; no chain commit ⇒ no disk record (RuntimeError); disk record carries `audit_ref` |
| Config adapter | `core/skills/os_skills/skill_adapter.py` | under `<CORVIN_HOME>/tenants/<t>/skills/os_delegation_router_config.json`; versions persisted WITH config snapshots; epoch persisted; `rollback()` works after restart and records `active_version` (the version whose config is live — NOT the last list entry: a rollback restores an older config and keeps the newer versions, and reporting `versions[-1]` named the wrong version in the audit trail and the routing ledger); `load_skill_config()` is the read side (mtime-cached, never writes; files written before `active_version` existed fall back to the last entry) |
| Router consumption | `core/skills/os_skills_phase1.py::DelegationRouterSkill.execute` | optional `tenant_id` input → learned `confidence_threshold`; `shadow`/`bundled_engine` echoed |
| Console API | `core/console/corvin_console/routes/method_discovery_api.py` | see table below; ALL real, ALL audited; CSRF on mutations; tenant from `SessionRecord` |
| Panels | Learning Loops view (`routes/learning_analytics.py`), maturity (`routes/api_vibe_maturity.py`) | register L17 (A): they report the loops' health, they do not learn. The former `web-next/src/panels/LearningDashboard.tsx` no longer exists (checked 2026-10-25), and no web-next file reads the `learning_enabled` flag |

## Console endpoints (`/v1/console/learning/…`)

| Method · path | Answer | Audit |
|---|---|---|
| `GET config-versions?skill_id=` | real version history; `[]` until a hypothesis is accepted | — |
| `POST feedback` | requires the per-user consent scope `learning_feedback` (deny-by-default; missing ⇒ 403). The feedback must pass the ADR-0534 gate (needs a `skill_executed` of the skill in the last 24 h; else 422 `feedback_rejected`). Then `{status: recorded, hypotheses[…accepted, optimizer_reason], recent_outcomes, current_config, current_version}` | denial: console `action_denied` (action `learning.feedback`, reason `consent_required`; a 503 with reason `consent_unverifiable` when the consent store cannot answer). Else console `learning.feedback_received:<quality>` + chain `learning.feedback` (+ `skill_config_updated` on accept) |
| `POST config/rollback?to_version=` | real rollback; **404** on unknown version | console `learning.config_rollback` + chain `learning.config_updated` |
| `GET preferences` | derived from recorded OUTCOME events per task_type; `{}` without outcomes | — |
| `POST preferences/confirm?task_type=` | PREFERENCE event | console `learning.preference_confirmed` |
| `GET health` | `operational` only when the subsystem imports AND an emitter is booted | — |

Only `os.delegation_router` is tunable (`TUNABLE_SKILLS`); any other `skill_id` is a 400.

### Operator interface (`routes/learning.py`, `routes/learning_metrics.py`) — real data or an honest error code (2026-09-07)

Until 2026-09-07 these routes answered with constants (`alpha_core=0.1`,
`convergence_percent=87.5`, `[]`), `override`/`rollback` reported `"success"`
while changing nothing, `learning_metrics.py` was mounted under a doubled
prefix (`/v1/console/v1/console/learning/metrics`) and its WebSocket took the
tenant from a query parameter with no authentication (adversarial review
F-L2/F-L3/F-L4). Everything below is computed from the tenant-BOUND
`event_store.EventStore` and the core chain — never a placeholder.

| Method · path | Answer | Notes |
|---|---|---|
| `GET learning/status` | `{event_counts{per type}, recent_outcomes{window:50,total,successes,success_rate}, outcome_loss (1−success_rate), last_outcome_at, last_feedback_at, last_config_update_at, status}` | `status` ∈ `no_data` (no events) · `collecting` (outcomes, no config change yet) · `learning` (≥1 `config_updated`) |
| `GET learning/metrics?window=1h\|6h\|24h` | 12 equal buckets `{timestamp, outcomes, successes, success_rate, feedback, skill_executions, config_updates}` + `sample_count` | 400 on any other window |
| `GET learning/checkpoint` | the REAL rollback points: `SkillAdapter.get_version_history()` per tunable Skill (`checkpoint_id` = `version_id`) | `[]` until a hypothesis is accepted; 503 when `core.skills` is absent |
| `GET learning/audit?limit=` | the tenant's `learning.*` records from the core hash chain (`audit_ref`, type, ts, skill_id, lom, hash, prev_hash) | content-free by construction; 503 when the writer is not resolvable |
| `POST learning/override` · `POST learning/rollback/{id}` | **501** — there is no live meta loop to override; the real, audited change path is `POST learning/config/rollback?to_version=` | CSRF-gated like every POST |
| `GET learning/metrics/current` · `GET learning/metrics/history` | the same `learning_status()` / `learning_series()` as above (one computation, three surfaces) | single prefix `/v1/console/learning/metrics` |
| `POST learning/metrics/export {format: json\|csv, window}` | the tenant's learning events in the window, INLINE (`Content-Disposition: attachment`, `X-Rows-Exported`) — content-free rows (`event_id, event_type, skill_id, timestamp, audit_ref, lom`) | no download token; GDPR Art. 20 |
| `WS learning/metrics/stream` | pushes `{type: metrics, data: <status>}` immediately and every 10 s; `ping`→`pong` | authenticates the `corvin_console_sid` cookie via `auth.load_session`; **1008** without a live session; tenant = the session's tenant, no `tenant_id` parameter |

Free text is never persisted: `feedback_text` (ratings) and `reason` (grades)
are accepted and reduced to `has_text`/`text_length` (`has_reason`/
`reason_length`) before the event is written (`operator_feedback.build_rating_event`,
`LearningIntegration.grade_pattern` — the latter now writes through the
audit-first `event_store.EventStore`, not the unchained TreeOfThoughts JSONL).
`EventStore(tenant_home, tenant_id=…)` is tenant-bound: an event carrying any
other tenant is refused before the chain write.

`EventStore.query_events(..., newest_first=False)` selects from the OLDEST end
by default (date files ascending, write order within a file). A consumer that
wants "the last N samples" — e.g. `consistency_checker.FeedbackConsistencyValidator`
— MUST pass `newest_first=True`, which walks the date files descending, reverses
each file's lines and returns newest → oldest; the default returned the oldest N,
so a "recent" window computed from it never advanced (2026-09-07, R3-B2). One
unreadable line never costs more than itself: a malformed JSON line AND an
unknown `event_type` enum value (a newer schema, corruption) are logged and
skipped per line — the unknown enum used to raise `ValueError` out of
`query_events` and discard every later event, so a single record made consumers
see an empty history (R3-B3).

### Core-chain allowlists (`event_persistence._LEARNING_EVENT_ALLOWLISTS`)

The core writer's metadata floor is default-deny for detail keys (F-A4). Every
learning chain record type (`learning.<event_type>`, `learning.retention`,
`learning.erasure`, `learning.hyperparameter_changed`, the hybrid-context and
erasure-cascade events) registers its positive key set at writer resolution,
exactly like `skill_registry_phase1` does for `skill.*`. Without it the
`audit_ref` the commit check reads back is scrubbed and every learning write
fails closed. Adding a detail key to a learning record means adding it there.

### Deleted: 9D / meta loop and the "staged but unwired" modules

`nine_d_loss`, `meta_optimizer`, `watchdog`, `unified_loss`, `weight_smoother`,
`weight_updater`, `alert_policy`, `alert_dispatcher`, `geo_validator`,
`feedback_validator`, `outcome_validator`, `metrics_exporter`, `export_import`,
`divergence_detector`, `consistency_checker` and `gradient_backprop` no longer
exist (deleted with the unwired scaffolding on 2026-10-11, T-0125; checked
2026-10-25). The security mitigations once attributed to them (feedback HMAC,
gradient clipping, tiered damping, stale-feedback TTL, oscillation detection,
export HMAC, alert nonce, geo validation) are therefore NOT guarantees of this
install, implemented or otherwise. Which learning systems are live is the
register's A entries, nothing else.

### Live experiment collector (`live_experiment_collector.py`) — retired

Retired by ADR-2282 and deleted on 2026-10-25 (T-0271).

`core/learning/live_experiment_collector.py` and its `corvin-live-collector` console
script were deleted. Its last data was from 2026-09-06, its user unit was disabled, and its
only reader (`live_collection_dashboard`) was already gone. The maturity panel measures on
demand instead (`maturity_live.build_measurement`, served by `routes/api_vibe_maturity.py`).
A disabled `corvin-live-collector.service` user unit outside the repo may still exist on a
host; it now points at a removed entry point and can be deleted by the operator.

## Console surface — bounded, CSRF-gated, honest

* **`SkillAdapter._locked` is BOUNDED** (`LOCK_EX | LOCK_NB` + `LOCK_TIMEOUT_SECONDS`
  = 2.0s, then `SkillConfigLockBusy`). `POST learning/feedback` and
  `POST learning/config/rollback` map it to **503 `lock_busy`** and audit the
  refusal. Both handlers are `async def`, so a plain blocking `flock` did not
  stall one request — it stalled the whole console event loop (an anonymous
  `GET /v1/console/version` timed out alongside it). Never reintroduce a
  blocking `LOCK_EX` here; `core/console/tests/test_route_lock_nonblocking_registries.py`
  covers it.
* **`POST /v1/console/api/learning/subscribe` and `/unsubscribe` require CSRF.**
  They read only query params, so with `content-type: text/plain` they stayed
  CORS-*simple* requests and a browser sent them cross-site with the console
  cookie. The subscriber store is capped (`MAX_SUBSCRIBERS_PER_TENANT`, 503 at
  the cap) and `prune_stale_subscribers` runs on every registration — it
  previously had no caller at all.
* **`GET /api/learning/skills/{name}` is computed from real events**
  (accuracy from OUTCOME, latency from `latency_ms`/`duration_ms`, confidence
  from CONFIDENCE, satisfaction from FEEDBACK `quality_rating`, usage from
  SKILL_EXECUTED). A dimension with no events is `null` = *not recorded*, never
  0. `GET /api/learning/user/{id}` returns `available: false` + a reason:
  ADR-0314 events carry NO user dimension (GDPR Art. 5), so per-user metrics
  cannot be computed — the endpoint says so instead of returning nulls under a
  docstring promising metrics. `/summary`'s `cached` flag is the real cache
  state (it was hard-coded `true`).
* **The CSV export neutralises spreadsheet formulas** (`csv_safe` in
  `routes/learning_metrics.py`): a cell beginning `=`, `+`, `-`, `@`, TAB or CR
  is prefixed with `'`. `skill_id`, `lom` and `audit_ref` are free-form strings
  from the event store and are executed by Excel/Sheets otherwise. The JSON
  export is byte-exact and untouched.

## `query_events` — limit always windows from the NEWEST end

`EventStore.query_events(limit=N)` returns the **N most recent** matching events;
`offset=K` skips the K most recent; `newest_first` controls only the ORDER of the
returned list (default `False` = chronological, so `[-1]` is the newest). There
is no way to ask for the oldest N, deliberately: the old default gave callers
the oldest N, and `recent_outcomes` — the optimizer's sole ground truth — was
therefore frozen on outcomes #4991–#5000 of all time once a tenant passed 5000
of them.

## Audit events (ADR-0537 attribution)

| Event | Chain | Emitted by |
|---|---|---|
| `skill.executed` | core | `CoreAuditBackend` on every registry execution (incl. shadow) |
| `learning.skill_executed` / `learning.outcome` / `learning.feedback` / `learning.config_updated` / `learning.preference` | core (content-free: event_id, event_type, skill_id, skill_version, lom) | `EventStore.write_event` (audit-first) |
| `action_performed` — `learning.feedback_received:*`, `skill_config_updated:hypothesis_accepted`, `skill_config_updated:rollback`, `learning.config_rollback`, `learning.preference_confirmed` | console tenant chain | `method_discovery_api.py` |
| `skill.feedback` | core (metadata only: data_class / destination_engine identifiers, closed `result` / `feedback_type` / `rule_effect`, the DENY rule's real `confidence_before`, `event_id`, `audit_ref`; never `reasoning`) | console `POST /flow/feedback` → flow-guard `LearningIntegration.record_feedback`, audit-first via the STRICT `CoreAuditBackend` (ADR-2283) |
| `skill.approval.requested` / `.granted` / `.denied` / `.revoked` | core (metadata only: ids, metric name, confidence, closed codes, `operator_ref` = sha256 prefix, `audit_ref`; never a reason text or raw operator id) | L5 k=2 `OperatorApprovalGate` via the gateway's STRICT `CoreAuditBackend` (ADR-2283) |

**Strict core-chain backend (ADR-2283).** `CoreAuditBackend(tenant_id, strict=True)`
(`core/skills/skill_registry_phase1.py`) is the fail-closed dict backend for learning paths
whose contract is "no decision without its record": it raises `AuditNotCommitted` when the
writer is absent, raises, or reports the record did not commit, and returns the record's
`audit_ref`. The default (`strict=False`, the Skills registry) is unchanged: log, count,
continue. It dots the event type (`skill_approval_granted` → `skill.approval.granted`);
register the dotted name. Live users: the gateway's approval gate
(`corvin_gateway/app.py::_init_approval_gate`, `/v1/approvals/...`; a non-committing write
answers 500 and the approval stays pending) — E2E
`tests/learning/test_adr_2283_approval_gate_audit_e2e.py`; and the console flow-guard
learning route (`routes/flow_guard.py`): `POST /flow/feedback` writes its record BEFORE the
route's own FlowGuard policy changes (503 `audit_not_committed` and no change when it does not
commit), may only strengthen — `rejected` / `pii_leak_detected` add or raise DENY, `error` is
neutral, `success` / `approved` / unknown answer 422 — discards the body's `reasoning`, and
`GET /flow/audit` returns no free text. That FlowGuard is separate from the L34 production
gate — E2E `tests/learning/test_adr_2283_flow_guard_feedback_e2e.py`.

## What is learned from — and what is only audited

`SkillMetadata.learn` (default `True`) says whether a Skill's executions feed the
learning store. Every execution is audited regardless (`skill.executed`).
`os.capabilities`, `os.headless_mode` and `os.plugin_health_monitoring` declare
`learn=False`: they are deterministic flag/manifest lookups the SPA triggers
continuously (346 executions in ten minutes of polling were observed) and no
optimizer consumes them. The manifest route additionally caches the resolved
flags per tenant for 5 s (`routes/capabilities.py::_read_flags`), invalidated
by `POST /features/toggle`, so an operator decision is never stale.

## Honest labels: selector learner and CEL grades (ADR-2281 §5, 2026-10-25)

* **Model-selector learner = a descriptive statistic.** Its per-turn input is a binary
  proxy (`model_selector_shadow.py`: `quality_score = 0.85` when the turn ended without an
  error, `0.2` when it failed) plus operator ratings, and nothing in the model selection
  reads it. The Models → Learning tab says so ("What the learner has recorded, per tier",
  `web-next/src/pages/models/tabs/learning.tsx`; pinned by `tests/unit/learning-tab.test.tsx`).
* **CEL turn grades: no console surface renders them.** The native console path records
  `success=True` for every completed turn (`chat_runtime.py`, behind `outcome_feedback_loop`),
  so such a grade means "turn completed", not "the stage helped". Measured 2026-10-25: the
  only reader is `GET /v1/console/vibe-engineering/grades` (`routes/vibe_engineering.py`);
  its frontend hook `useStageGrades` (`web-next/src/adapters/vibe.ts`) has no consumer, so
  no panel shows a CEL grade and there is no label to change. A panel that starts rendering
  them must label an automatic native-path grade "turn completed".
* **Live proof (2026-10-25, `scripts/console-deploy.sh --marker 'the model selection does not
  read it'`):** the host served `assets/index-DNo90ACa.js`; across the 96 built chunks the
  marker is in exactly one (positive control) and none contains `HotfixPanel`,
  `LearningDashboard`, `SkillLearningDashboard`, `learning-loops-dashboard` or
  `skill-learning-dashboard`, the pages ADR-2282 retired. The running console process still
  mounts the retired backend routes until its next `safe-restart.sh`.

## Feedback trust gate (ADR-0534, accepted + built 2026-10-10)

Feedback is untrusted input. `EventStore.write_event` runs `core/learning/feedback_gate.admit()`
on every `EventType.FEEDBACK` event BEFORE the audit-first write; a refusal raises
`FeedbackRejected(reason, audit_ref)` and nothing is stored (routes answer **422**
`{error: feedback_rejected, reason, audit_ref}`).

| Layer | Module | Rule | Refusal event |
|---|---|---|---|
| 1 Throttle | `feedback_throttle.py` | ≤ 5 decisions / 60 s per subject, counted over `<tenant>/learning/feedback_gate/decisions.jsonl` (content-free; rejected attempts count) — never an in-process deque, the console builds a handler per request | `learning.feedback_throttled` |
| 2 Reality | `reality_check.py` | the skill ran (a `skill_executed` learning event of THIS tenant) within 24 h before the signal (+5 s skew); an optional `claimed_output_hash` must equal `execution_output_hash()` of one of those runs | `learning.feedback_rejected` (`subject_not_verifiable`, `no_corresponding_execution`) · `learning.feedback_reality_mismatch` |
| 3 Trust | `adversarial_detector.py` | `trust_weight = max(0.1, 1 − rejected/total)` over the last 20 decisions, stamped into `signal.trust_weight`; < 0.3 → alert | `learning.adversarial_feedback_detected` (ERROR) |

Accepted → `learning.feedback_accepted` (with `trust_weight`). Consumers multiply by
`adversarial_detector.weight_of(signal)` (1.0 for signals stored before the gate):
operator-rating stats (weighted mean), the workflow optimizer's Beta update, and the router
threshold step of `POST /feedback/skill` — that route moves a live config without storing a
FEEDBACK event, so it calls `admit()` itself and passes `trust_weight` to
`loop_closure.apply_outcome_to_skill`.

* `tool:<id>` ratings are refused (`subject_not_verifiable`): a tool has no execution record here.
* `grade_pattern` checks the node exists and passes `subject_verified=True` (replaces Layer 2
  only); an unknown pattern is a 404. The console's per-request `LearningIntegration` has an
  in-memory pattern store, so over HTTP every pattern is unknown today.
* `POST /learning/feedback/submit` writes a real gated FEEDBACK event (it used to answer
  `"queued"` and store nothing).
* No override secret. Change `RateLimitConfig` in code review instead.
* **Producers of rated skills must write the execution.** A rating whose skill never wrote a
  `skill_executed` event is refused, so every surface that offers ratings owes the store that event.
  The Video Producer (`os.video_producer`) is not a registry Skill and runs in a plugin that cannot
  import `core.learning`, so the host does it: `create_video_job` starts a watcher thread
  (`video_producer_api._record_job_execution`) that waits for the job's END STATE and writes one
  `skill_executed` event (ids, status, counts — never the task text) through `EventStore.write_event`.
  Written at the end, not the start: a job that never ran must not make a rating valid. A job ending
  `error` or `cancelled` counts (it ran). Consequences, stated: a video older than 24 h cannot be
  rated (the routes say so: "more than 24 hours ago"), a job the console restart interrupted records
  nothing, and a rating on a running job is refused ("once it has been produced"). Proof:
  `core/console/tests/test_video_producer_process_e2e.py` (real child process → event → rating 200),
  `test_video_producer_review_2026_10_10_e2e.py::JobExecutionIsRecordedForTheFeedbackGate`.

Proof: `tests/learning/test_feedback_trust_gate_e2e.py`,
`tests/e2e/test_stream4_skill_feedback_console_e2e.py`.

## Registry hardening (2026-09-07 adversarial review F-K2/F-K3/F-K5/F-K6/F-K8)

| Mechanism | Where | Rule |
|---|---|---|
| LoM required AND resolvable | `SkillsRegistry.execute(..., lom=)` | `lom="<file>:<function>"` (or `<file>:<function>:L<line>`) is mandatory; missing OR unresolvable → audited `skill.executed` with `status=error`, the Skill does NOT run. `lom_hash` = SHA-256 of the named function's source segment (`ast`), so it survives line drift; for the `:L<line>` form the FUNCTION is resolved first and the line must fall inside it (decorators included) — before 2026-09-07 that form hashed the line without ever looking the function up, so a fabricated name still "bound", and a blank line produced the constant `sha256("")` (R3-B1). A blank line, a non-`.py` target, anything outside the repo root and anything under `.corvin/`, `.claude/`, `.venv/`, `site-packages/`, `node_modules/` or `.git/` are all refused — the exclusion set names every root the RUNNING SYSTEM can rewrite, and `.claude/` was missing from it until 2026-09-07 (round-4 review, F8) while `.claude/worktrees/` held 17 full `.py`-bearing copies of the repo inside the repo root, so a LoM naming one bound and produced a normal-looking source hash for source that is not the shipped source, and a source file > 2 MB is not parsed. Resolution is memoised on `(lom, path, mtime, size)` — `execute()` resolves twice per call and re-parsing cost up to 80 ms each time. Production call sites: `capabilities.py:_read_flags_uncached`, `slash_commands.py:_plugin_builder_enabled`, `vibe_engineering.py:get_pipeline`, `bootstrap.py:start_health_monitoring`, `delegation_policy.py:_consult_router`, `console/app.py:headless_enabled`. A call site that forgets `lom=` is REFUSED and the refusal is SILENT at the call site — it just reads as "the feature is off" — which is how `headless_api_mode` shipped dead. `tests/integration/test_phase1_k2_k5_call_sites.py::TestEveryProductionCallSitePassesALoM` is an AST fence over `core/`, `operator/` and `ops/` that fails on the next one. |
| Decision in the chain | `SkillExecutionResult.to_audit_event` → `decision_summary()` | the core writer drops any `output` key; the chain now carries an allowlisted `decision` (engine, enabled, mode, confidence, shadow/bundled_engine, `flag_count`/`flags_on`/`flags_hash`) — never free text. Field sets are registered as positive allowlists (`SKILL_AUDIT_ALLOWLISTS`). |
| Compliance tier | `SkillMetadata.tier` (`compliance` / `core` / `installed`) | `os.capabilities` is `compliance`: `unregister()` / `disable_skill()` raise `SkillDisableRefused`, the 3-failure auto-disable is refused — every refusal is audited as `skill.disable.refused`. |
| Lost-update guard | `SkillAdapter._locked()` | `fcntl.flock` on `<config>.lock` + RELOAD inside the lock around `run_optimizer_epoch` / `rollback`; two concurrent feedback requests advance the epoch by two, never one. |
| Namespace gate | `skill_forge.registry.SkillRegistry(caller_persona=…)` | create/update/delete/grade/promote are gated on `forge.policy` namespaces when a persona is attached (MCP server hands its turn persona over; console routes act as `assistant` → `assistant.*` only, 422 otherwise). Fail-closed when the policy cannot be loaded. |
| Grade cap | `SkillRegistry.grade(..., organic=False)` | non-organic grades (self-award, auto, bootstrap) are clamped to `AUTO_GRADE_CAP_MAX = 0.3` and audited `capped`; only a caller vouching for a real run passes `organic=True` (MCP `skill_grade` with a `run_id`; `skill_inject.grade_from_user_followup` — an outcome grade backed by the previous turn's `run_id` and the operator's own follow-up). The post-turn auto-grade stays non-organic. |
| Ungraded injection | `skill_context.resolve_inject_ungraded` | conjunctive: `CORVIN_DELEGATE_INJECT_SKILLS_UNGRADED=1` alone never widens; `=0` always narrows. |

## Invariants (must NOT be weakened)

* The bundled routing answer is never altered in shadow. In a CLEARED Phase 2 the
  Skill may only de-escalate to `native` (the ADR-0251 D2 bound hooks already obey),
  never over an explicit `/delegate`, and only after `routing.phase2_decision`
  committed to the chain (ADR-2092 G3/G4).
* The core chain admits only the PROCESS tenant (ADR-0007). A learning event
  for another tenant is refused by the writer, detected by the read-back, and
  **not written to disk** — counted in `EventEmitter.write_failures`. Tests
  that write for tenant X run as tenant X (`CORVIN_TENANT_ID`).
* `core/paths/tenant.py::corvin_home()` honours `CORVIN_HOME` (then a
  repo-local `.corvin`, then `~/.corvin`) — the same order as
  `corvin_operator/bridges/shared/paths.py`. Nothing in learning/skills may hard-wire `~/.corvin`.
* The free-text feedback `reason` is used by the interpreter's keyword rules
  and is never persisted or chained.
* `SkillConfig.apply_delta` clamps to `[0, 1]` and rejects unknown params;
  `ConfigHypothesis.delta` is bounded to `[-0.20, 0.20]` — one feedback can
  move a parameter by at most 0.20, and every accepted change is reversible.

## Verification

```bash
# unit + E2E (real HTTP boundary, real store, real chain in a sandbox)
.venv/bin/python -m pytest core/learning/tests/test_event_store_audit_first.py \
    core/learning/tests/test_outcome_sink.py core/skills/tests/test_skill_adapter_persistence.py \
    tests/test_delegation_policy_acp_shadow.py -q
core/console/.venv/bin/python -m pytest core/console/tests/test_learning_loop_routes_e2e.py \
    core/console/tests/test_task_manager_outcome_sink.py -q

# LIVE with a REAL LLM turn (real claude CLI; costs credits)
CLAUDE_LIVE_E2E=1 core/console/.venv/bin/python -m pytest core/console/tests/test_learning_loop_live_e2e.py -q -s
# → [live] loop closed: shadow=1 outcome=1 hypotheses=1

# live service (after a restart of corvin-webui.service)
curl -s -c c -b c http://127.0.0.1:8765/v1/console/auth/local-login >/dev/null
curl -s -b c http://127.0.0.1:8765/v1/console/learning/health
grep -c '"learning.skill_executed"' "$CORVIN_HOME/audit.jsonl"
```

## Known limits (stated, not hidden)

* The L10 entry point `adapt_context_l10` (`os.context_adapter`) runs in shadow
  since 2026-09-26 (register L11, state B): the CEL stage `l10_adapter` calls it once per turn from
  `pipeline.build_context`, audits `skill.executed` + `context.adapted`
  (`adaptation_type=l10_shadow`) and never changes the served brief. The Skill
  receives content-free inputs only (no task text), so its advice carries almost
  no signal yet. It runs only in a process where `boot_skills` booted an audited
  registry for the turn tenant (gateway/console, and the bridge adapter via
  `adapter._boot_acp_skills`); a process without one records
  `skipped: skills_not_booted` rather than an unaudited execution. **A
  hand-authored `spec.context_engineering.pipeline` REPLACES the defaults**, so it
  must list `- stage: l10_adapter` itself or the stage never runs for that tenant
  (the live install had 0 `context.adapted` records for exactly this reason until
  2026-10-11); `resolve_pipeline` logs a one-time warning when it is missing.
* **Unwired learning scaffolding was deleted (2026-10-11, T-0125).** 117 of the 184 modules
  in `core/learning/` (about 39 000 lines, e.g. `unified_loss_optimizer`, `feedback_batcher`,
  `phase2b_*`, `nine_d_loss`, variants such as `confidence_scorer_a3`, `meta_optimizer_a4`,
  `phase7_orchestrator_bridge` and the ADR-0393 classifier family) had no path from any production root and were removed together with the
  tests that exercised only them. What remains: `EventStore` / `outcome_sink` (events, register
  L03, A), `stream4_skill_feedback` -> `loop_closure.apply_outcome_to_skill` -> `SkillAdapter`
  (the one feedback path that writes a config, register L05, B: its reader is the gated Phase 2),
  `model_selection_*` (descriptive statistics, L08, B) and the dashboards (L17, A). There
  is no automatic optimizer: nothing reads a learned router threshold outside the gated Phase 2
  path, so an optimizer for it would close no loop. Method note: a plain importer graph MISSES
  names re-exported by `core/learning/__init__.py` (`from core.learning import LearningIntegration`
  is a production import of `integration`); resolve re-exports before calling anything dead.
* **Second clean-up round in `core/skills` (2026-10-11, T-0140).** 66 more modules (about 15 000
  lines: the marketplace installer/core/skill integration, A/B-testing framework, batch approval,
  config applier/tuner, the `graders/`, `orchestration/` and `feedback/` packages, the
  `context_filter*` and `knowledge_graph` skills, older copies inside `flow_guard/`,
  `workflow_optimizer_skill/` and `security_orchestrator/`) and the tests that exercised only them
  were removed. NOT removed, on purpose: anything an ACCEPTED ADR names by file or by its own
  directory (decided, not yet wired: e.g. `skill_validator`/`compiler` for ADR-0533, `executor`,
  `health`), the Data Hub learning group (PLAN-0948), the Video Producer, and everything those
  import. Method note: a package's `__init__` runs when ANY child is imported, so a live module
  also makes its parents' eager imports live; the first draft of this round missed that and
  would have broken four live packages (caught by a dangling-import check before any commit).
* **Third round, by decision (ADR-2282, 2026-10-25).** The never-wired learning code that
  accepted decisions still described was retired together with those decisions, group by
  group (T-0264..T-0272), each named with its live successor: L5 K3–K5
  (`quality_gate`/`conflict_resolver`/`rollback_guard`/`utils`), the LDD in-task loop and
  `base.LearningLoop` (DoD learning moves to `weight_optimizer`), `loss_signals` (successor
  `anomaly_monitor`), `outcome_sink_a2` (successor `outcome_sink.emit_task_outcome`),
  `completion_detectors` + `core/notification` (successor `completion_notify` + the
  workflows outbox), `token_accounting`/`token_metrics_aggregator`/`vibe_metrics_api`
  (successor `engine_span.usage_split`), the legacy `core/console/routes/` tree, the six
  flow-guard learning modules (`threshold_tuner` lowered an L34 threshold and
  `exception_system` forced ALLOW — both contradicted ADR-2049; L34 itself is unchanged),
  `workflow_optimizer_skill/confidence_calculator.py`, and the CEL stages
  `selective_injection`/`memory_pruning`/`adr_reranking` (in no shipped pipeline; their
  "embedding" was a salted `hash()`; `context_priority` is the live stage), 11
  `core/marketplace/*` learning/distribution modules (successors `corvin_plugins/trust.py`,
  the manifest origin axis, `state.py`, `routes/marketplace.py`; `custom_repositories.py` and
  `auth/` stay), and the Flask `routes/learning_loops.py` with `learning_loop_manifest` /
  `learning_loop_audit_integration` / `learning-loops-dashboard.tsx` (successor
  `learning_analytics.py` + `loop_discovery.py`, ADR-0908), the live experiment collector
  (successor `maturity_live.build_measurement`) and the Stream-2 optimizer route with its eight
  `*_stream2.py` modules and `components/learning/` (successor `stream4_skill_feedback`, same
  feedback gate), and the Brain hub (`hub`/`brain`/`brain_startup`/`config`/`cli` and
  `core/orchestration/subsystems/`, except `notification_daemon.py`, which the unreachable
  `core/skill_forge/skill_forge_v2.py` still imports), plus the parallel skill optimizers
  (`core/skills/{skill_learning_loop,skill_optimizer}`, `core/learning/skill_optimizer`,
  `core/engine/{meta_skills,skill_learning_loop}`, `skill_learning_routes` +
  `skill-learning-dashboard.tsx` — successor: SkillForge grading/canary/promotion),
  `auto_grading` (it would let a skill promote itself past the 0.3 auto-grade cap),
  `operator_fingerprint`/`adaptive_strategy`/`confidence_optimizer`/`tool_ranking(_cache)`
  and `core/vibe/` (successor `anomaly_monitor`). Registered audit
  event types of retired emitters stay in `EVENT_SEVERITY`/`_EVENT_ALLOWLIST` (old chain
  records may carry them).
* **The one config-writing feedback path (register L05) is race-tested across processes.** `SkillAdapter.apply_config_delta`
  is a read-modify-write of `<skill>_config.json` shared by the console and the bridge;
  `tests/skills/test_skill_adapter_cross_process_race.py` runs 4 real processes x 6 deltas and
  requires 24 unique gapless versions and the exact end value (with the `flock` removed only 6-7
  of 24 survive). An HTTP-level concurrency test cannot show this: requests share one event loop
  and the handler has no `await` between read and write. `EventEmitter` accepts an event only if
  it precedes `stop()`'s sentinel (one lifecycle lock; `tests/unit/test_event_emitter_races.py`).
* **Phase 2 readiness has a structural dead end, and says so.** The Skill may only
  de-escalate TO `native`, so a surface where the bundled rule never delegates has
  nothing to change. `monitoring/readiness.py` reports that as
  `no_delegation_subject` (with `delegating_decisions == 0`) as soon as 30 decisions
  exist, instead of `insufficient_samples`, because more samples cannot help.
  `skill_never_disagrees` now means the Skill HAD delegations to question and never
  disagreed. On 2026-10-11 both live surfaces were in the first state (console 131
  decisions, bridge 560, 0 delegating); `console_soak_incomplete` now carries the
  bridge's real counts instead of a hard 0. Unblocking Phase 2 therefore needs
  delegating traffic (an operator decision on `web_chat.worker_engine`), not time.
  Measured again 2026-10-25 (T-0280, read-only on the live tenant, recorded in the
  register entries L05/L12): both surfaces now delegate and neither is ready. Console:
  365 decisions, join 0.923, 36 delegating, 0 disagreements → `insufficient_samples`
  (and the join rate is below 0.95). Bridge: 962 decisions, join 0.989, 26 delegating,
  1 disagreement → `console_soak_incomplete`. No Phase-2 decision is proposed.
* `DelegationRouterSkill` advises in model tiers (`claude-haiku-4 / sonnet-4 /
  opus-5`), not in `delegation_policy` engine ids (`native / acs / tde`); the
  shadow record carries both so agreement is measurable, but the advice cannot
  be promoted to a real override without a vocabulary mapping (follow-up ADR).
* Learning events for a session tenant that is not the process tenant are
  refused fail-closed (see invariants) — one process per tenant is the
  supported multi-tenant deployment for learning.

## Operator skill feedback route (Stream 4)

`POST /v1/console/feedback/skill` (mounted from `routes/stream4_skill_feedback.py`) takes a
rating in `-2..2` for `os.workflow_optimizer` / `os.security_orchestrator` / `os.flow_guard`.
The rating's sign becomes the OUTCOME signal (`>0` yes, `<0` no, `0` other). The route chains a
`skill_feedback` event FIRST (signal only — no free text); if the chain does not commit it
answers 500 and stores nothing. It then appends the structured record (ids, rating, category —
never the free-text reason) to `<tenant>/learning/skill_feedback/events.jsonl`
(`core/skills/feedback/history_store.py`). `GET …/history?skill_id=` and `GET …/status` read that
file only; `status` reports `total_received` and `last_feedback_timestamp` and nothing unmeasured.
Loop closure (`core/skills/feedback/loop_closure.py::apply_outcome_to_skill`): for a skill in
`CONFIG_CONSUMERS` (today only `os.delegation_router`, whose `_learned_threshold` reads the
config back through `load_skill_config`) the outcome moves `confidence_threshold` by +/-0.05 via
`SkillAdapter.apply_config_delta` (audit-first, versioned, rollback-able). The response carries
`config_applied` / `config_version`; a rating of 0 ("other") and a threshold already at its bound
change nothing; for every other skill the feedback is audited and stored only, and the message
says the skill reads no learned config yet. A config write that cannot be audited fails and the
feedback stays stored.
Proof: `tests/e2e/test_stream4_skill_feedback_console_e2e.py`.

**Where an operator rates (2026-10-13).** Until then no reachable console surface posted here — the
four `pages/skill-feedback/*` pages and `components/learning/FeedbackModal.tsx` (retired by ADR-2282) were mounted nowhere
and called endpoints that do not exist — so the tenant had 0 feedback in two weeks. The Learning Loops
detail view now shows "Rate recent decisions" for the four skills above
(`components/learning-loop-rating.tsx`): `GET /v1/console/feedback/skill/recent?skill_id=` lists the
latest `skill_executed` records (event id, time, and the decision only when it is a short identifier —
never input, output or reasoning text), and Correct/Wrong posts `rating ±1, category accuracy` with
the event id as `subject_id`. Proof: `core/console/tests/test_learning_anomalies_e2e.py`.

## DoD weight loop (ADR-2283 decision 3, ADR-2284 decision 1, 2026-10-25)

Learned Definition-of-Done weights persist per tenant in
`<tenant_home>/global/forge/dod_weights.json` (`weight_persistence.weights_path`, resolved through
`core.paths.tenant.tenant_home` — `CORVIN_HOME` honoured, never `Path.home()`; atomic write, mode
0600). The file holds the weights, their per-weight confidence and the sample count; all three are
loaded back, so the confidence gate survives a restart.
Proof: `tests/learning/test_adr_2283_dod_weights_tenant_path.py`.

The loop (`core/console/corvin_console/routes/dod_verifier_dashboard.py`):

1. `POST /v1/console/api/dod/verify` scores with `DoD_WeightOptimizer.recommend_next_weights(task_type)`
   (injected into `DoD_VerifierSkill` as `weights_for`): per weight the learned value only when its
   confidence is > 0.5 (ADR-0723), else the default; then clamped to [0.05, 0.6] and normalised to
   sum 1. The response says `weights_source: default|learned`. It records a `SKILL_EXECUTED` and an
   `OUTCOME` learning event, both carrying `task_type` — the first is the execution the ADR-0534
   reality check needs (without it every DoD rating was refused, and the route answered 500).
2. `POST /v1/console/api/dod/feedback {task_id, check_name, feedback}` looks up the newest verification
   of `task_id` (none → 422), requires a learnable task type (`cli_command`, `api_endpoint`,
   `lib_function`, `plugin`, `skill`, `feature` — anything else → 422; it is never folded into another
   type's bucket) and a live check key (else 422), then writes FEEDBACK through
   `EventStore.write_event` (the ADR-0534 gate: throttled → 429, refused → 422).
3. The verdict maps to a signed delta (`accurate` −0.20 → the check's weight up, `inaccurate` +0.20 →
   down, `not_applicable` +0.10 → down by half a step; the optimizer caps one rating at 0.05) and
   `observe_feedback` runs on the tenant's restored state. The change is chained FIRST as
   `skill_config_updated` (`param_name weights.<task_type>`, `task_type`, `old_weights`, `new_weights`,
   `confidence` — numbers only, never the `note`); a change that cannot be chained is not persisted (500).
   Then the tenant file is written.

Each rating raises the confidence of one weight by 0.1, so a weight is first applied after its sixth
accepted rating. Feedback traffic is near zero today: the loop is wired but mostly idle.
Proof: `tests/learning/test_adr_2284_dod_weight_loop_e2e.py` (verify → 6 ratings → verify over HTTP;
defaults until the gate, one chained record per change, the gate's throttle refuses a burst).

## Learning anomalies (ADR-2044 Phase 3, 2026-10-13)

`core/learning/anomaly_monitor.py` measures its conditions over data the loop already stores;
`LearningHost.tick` runs it every 300 s (`ANOMALY_CHECK_S`), edge-triggered and audit-first: an
anomaly is recorded once when it starts (`audit_sink.emit`, WARNING) and marked active only after
the record committed; it is forgotten when the condition clears.

| Event | Condition | Source |
|---|---|---|
| `skill.high_rejection_rate` | > 20 % of ≥ 10 gate decisions on one subject refused in 24 h | `<tenant>/learning/feedback_gate/decisions.jsonl` |
| `skill.config_oscillation` | > 3 config versions of one skill in 1 h | `<tenant>/skills/*_config.json` |
| `skill.confidence_regression` | mean `output.confidence` of the last 24 h ≥ 0.10 below the 6 days before (≥ 20 each) | `skill_executed` learning events |
| `skill.convergence_stall` (ADR-2284) | newest ≥ 20 (≤ 200) OUTCOME events of a skill in four equal windows: loss `1 − success_rate` > 0.20 in every window, max − min < 0.02 | OUTCOME learning events (`signal.success`, else `signal.passed`) |
| `skill.latency_regression` (ADR-2284) | p95 of `latency_ms` over the last 24 h > 1.5 × the p95 of the 6 days before (≥ 20 each side, nearest-rank p95) | `skill.executed` records of the tenant CORE chain (legacy `skill_executed` too; newest ≤ 50 000 read, needle-filtered) — the learning store's `SKILL_EXECUTED` signal carries `execution_time_ms`, not `latency_ms`, so the chain is the stored source |

Every condition's event is registered statically in `EVENT_SEVERITY` / `_EVENT_ALLOWLIST` for the
ADR-2284 ones (metadata only: skill id, counts, ratios) and at runtime through
`audit_sink.register_events(anomaly_monitor.EVENTS)` for all.

Consumer: the host's persisted state (`anomalies`), returned by `GET /v1/console/api/learning/daemon`
and shown in the "Anomalies" card of the Learning Loops view (an ADR-2284 event shows its event name
there; the card has no dedicated wording for it yet). Nothing acts on them automatically.
Proof: `tests/learning/test_anomaly_monitor.py`, `core/console/tests/test_learning_anomalies_e2e.py`,
`tests/learning/test_adr_2284_anomaly_conditions.py` (seeded store → one learning-host tick → one
chained record → the daemon route; no second record on further ticks).

## Data Hub learning loop (PLAN-0948, ADR-2255, 2026-10-12)

A second, separate loop - not the L5 router loop above. Its subject is a **Data Hub SKILL artifact**: created through
`POST /v1/console/datahub/create` it is registered in SkillForge as `assistant.datahub_<slug>` with
`meta.data_sources = ["datahub:<artifact_id>"]` (`datahub_unified/register.py`).

| Stage | Where | What |
|---|---|---|
| Produce | `skill_inject.collect_active_skills` / `grade_from_user_followup` | a content-free line in `<tenant>/learning/daemon/inbox-YYYYMMDD.jsonl` (`core/background/learning_inbox.py`; closed fields; producers never raise) |
| Host | `core/background/learning_host.py`, started by `corvin_gateway.app`, `corvin_console.app`, `corvin_console.standalone` | drains the inbox from a persisted cursor, checks watched sources by hash every 300 s, runs the regeneration batch, snapshots `state.json`; 2 s -> 30 s idle back-off; a failing tick is counted, not fatal |
| Learn | `learning_daemon.py` | per-source weights (unseen source = 0.5), feedback consensus, causal edges; consensus mean < 0 over >= 3 events, or a changed source, queues the skill; 24 h cooldown, a request during cooldown is deferred not dropped |
| Act | `datahub_unified/regenerate.py` | re-read the source (secret -> refused), regenerate via `UnifiedCreator`, `learning.regeneration` `applying` on the chain FIRST, `update_body(quota_exempt)` (grades + meta kept), `finished` |
| See | `os.learning_daemon` (registry Skill, read-only), `GET /v1/console/api/learning/daemon` | persisted summary: weights, queue, regenerations, decisions |

**Hardened 2026-10-12 (adversarial review, 44 findings across Epic I-11).** The skill id is
`assistant.datahub_<artifact_id>` - never derived from the artifact NAME (two similar names shared one
skill, and the name, possibly personal, became an id in the permanent chain); delete removes only the
skill whose `meta.artifact_id` matches. Regeneration re-checks the stored source path on every read (a
symlink or a path that no longer resolves to itself, or one inside another tenant's home, is refused)
and records EVERY terminal outcome (`completed`, `refused`, `failed`, `partial`, `no_artifact`); a
failed artifact write rolls the registry body back. The inbox keeps a read offset PER day file, so a
line finished in yesterday's file after the reader moved on is not lost. One host per tenant across
processes: an exclusive lease on `daemon/host.lock`; a second process stands by and takes over when
the holder exits. A failed or refused regeneration is retried (1 h / 24 h), not counted as done. A
state file that cannot be restored is recorded (`learning.state_refused`) and kept aside as
`state.json.refused-<ts>`. State of deleted artifacts is evicted (`learning.sources_evicted`) - but
only when the artifact listing could be READ (an unreadable listing evicts nothing). All three host
lifespans stop the host on shutdown.

**Single-tenant per process, by construction.** The core audit writer refuses a record for any tenant
other than the process tenant (ADR-0562), so a learning host can only audit - and therefore only run -
for `CORVIN_TENANT_ID`. Producers drop events for any other tenant instead of letting an unread inbox
grow.

**Limits, stated.** Operator *ratings* of these skills do not feed the daemon (the ADR-0534 gate admits a rating only for a subject
with a recorded execution; prompt skills record none) - the live feedback source is the operator's follow-up in the next turn.
The generator is the template `UnifiedCreator`: regenerating from the same data rebuilds the same text. Since 2026-10-24 a SKILL
body carries a **data profile** (`DataHubSkill.profile()`: rows, per-column fill, min/max/mean/median/sum, distinct counts) computed
from ALL ingested rows - before that the body held only the row count and the operator's description, so a worker given the skill
learned nothing about the data. Default is aggregates only; `include_values: true` on `POST /datahub/create` (kept in the artifact's
`source`, so regeneration honours it) adds the most frequent labels of low-cardinality columns, never when the ADR-0661 scan flagged
PII or injection text. The scan is narrow (IBAN, SSN, e-mail+phone pair): a clean scan is NOT proof of "no personal data", which is
why values are opt-in. ACS workers receive such a skill as text with `dynamic_skills.enabled` (delegation-routing.md section 10);
proof: `tests/e2e/test_datahub_skill_reaches_acs_worker.py` (live part opt-in via `CORVIN_LIVE_LLM=1`); decision: ADR-2279 D6. `os.data_hub` /
`os.skill_tool_creator` (the 12-phase generator) stay optional and out of scope.

**Must NOT do:** put user text, a path or a skill body into an inbox line or a `learning.regeneration` record · let a producer raise into a
turn · regenerate before the `applying` record committed · spend the operator's authoring quota on a daemon rewrite ·
start the host from a request path (it is started once per host process).
