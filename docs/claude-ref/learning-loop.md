# ACP Learning Loop — closed end-to-end (ADR-0613)

**Status:** live since 2026-09-06 · **ADR:** ADR-0613 (Corvin-ADR) · depends on ADR-0314, ADR-0532, ADR-0549, ADR-0555

Until 2026-09-06 the ADR-0314 "learning loop" was a **log, not a loop**: Skills
were booted and unit-tested, but the L5/L10 entry points had zero production
callers, no task outcome was ever recorded, the optimizer modules had no
importer outside tests, the learning store bypassed the audit chain, and the
Learning Dashboard answered with hard-coded mock data. This page is the
source of truth for what runs now.

## The loop

```
real turn ──► delegation_policy.resolve_delegation_route()   (every turn, every surface)
                 │  stays native ─► _acp_shadow_route(engine=native)
                 └─ delegation-worthy ─► resolve_worker_engine() ─► _acp_shadow_route(engine=<chosen>)
                        bundled decision STANDS; os.delegation_router runs in SHADOW (advisory)
                        • skill_executed  → core audit chain ("skill.executed")
                        • SKILL_EXECUTED  → learning EventStore (audit-first, "learning.skill_executed")
                          output: {engine, confidence, bundled_engine, shadow: true,
                                   confidence_threshold, learned_config_version}

task ends ──► TaskManager.record_event(task.completed | task.failed)
                 └─► core.learning.outcome_sink.emit_task_outcome()
                        • OUTCOME event (task_id, status, exit_code, duration_ms, engine, task_type)
                        • tenant = the task's OWN metadata (create_task(tenant_id=…)), never env

operator ──► POST /v1/console/learning/feedback   {task_id, outcome_quality, would_repeat, reason}
                 • FEEDBACK event (closed enums only; the free text never enters a chain)
                 • FeedbackInterpreter → ConfigHypothesis[]   (deterministic rules, ADR-0549)
                 • SkillAdapter.run_optimizer_epoch(hyp, recent_outcomes(tenant, 10))
                        50-epoch baseline → hypothesis phase → accept iff Δsuccess ≥ MDE (0.05)
                        accepted → new config VERSION (snapshot persisted) + CONFIG_UPDATED event
                                   + console audit "skill_config_updated:hypothesis_accepted"

next turn ──► DelegationRouterSkill.execute() reads load_skill_config(tenant)
                 confidence < learned confidence_threshold ⇒ advice escalated one engine tier
                 (default 0.70 ⇒ a tenant that never gave feedback routes exactly as before)
```

## Components

| Piece | File | Contract |
|---|---|---|
| L5 shadow call sites | `operator/bridges/shared/delegation_policy.py::_acp_shadow_route`, called from `resolve_delegation_route` (turn stays native — the majority of turns) and `resolve_worker_engine` (delegation-worthy turn — engine chosen) | exactly ONE record per turn; runs AFTER the bundled rule + extension-point hook; never changes the answer; degrades to "no record" on any failure; skips un-booted processes without creating a phantom registry |
| Outcome sink | `core/learning/outcome_sink.py` | `emit_task_outcome()` / `recent_outcomes()`; content-free; fail-soft; tenant from task metadata only |
| Task chokepoint | `core/console/corvin_core/task_manager.py::TaskManager.record_event` | emits on `task.completed` / `task.failed`; `create_task(tenant_id=…)` at both console creation sites |
| Audit-first store | `core/learning/event_store.py::EventStore.write_event` | core chain record (`learning.<event_type>`, content-free) FIRST via `event_persistence.core_audit_event`; no chain commit ⇒ no disk record (RuntimeError); disk record carries `audit_ref` |
| Config adapter | `core/skills/os_skills/skill_adapter.py` | under `<CORVIN_HOME>/tenants/<t>/skills/os_delegation_router_config.json`; versions persisted WITH config snapshots; epoch persisted; `rollback()` works after restart; `load_skill_config()` is the read side (mtime-cached, never writes) |
| Router consumption | `core/skills/os_skills_phase1.py::DelegationRouterSkill.execute` | optional `tenant_id` input → learned `confidence_threshold`; `shadow`/`bundled_engine` echoed |
| Console API | `core/console/corvin_console/routes/method_discovery_api.py` | see table below; ALL real, ALL audited; CSRF on mutations; tenant from `SessionRecord` |
| Panel | `web-next/src/panels/LearningDashboard.tsx` | reads `data.patterns` envelope from `routes/learning.py` (ADR-0548); flag `learning_enabled` (registered, in `GATED_FLAGS`, in the whitelist template) |

## Console endpoints (`/v1/console/learning/…`)

| Method · path | Answer | Audit |
|---|---|---|
| `GET config-versions?skill_id=` | real version history; `[]` until a hypothesis is accepted | — |
| `POST feedback` | `{status: recorded, hypotheses[…accepted, optimizer_reason], recent_outcomes, current_config, current_version}` | console `learning.feedback_received:<quality>` + chain `learning.feedback` (+ `skill_config_updated` on accept) |
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

### 9D / meta loop (`nine_d_loss.py`, `meta_optimizer.py`, `watchdog.py`)

`NineD_LossOptimizer` drives the real `DivergenceWatchdog` (checkpoint →
`apply_gradients` → `validate_state` → `restore_checkpoint` on divergence);
checkpoints persist under `<CORVIN_HOME>/tenants/<t>/learning/meta_checkpoints/`.
Tier 2 loops learn with the meta loop's `α_infra`/`damping_infra` (no hardcoded
0.01/0.95). `MetaOptimizer.compute_loss` reads both key conventions
(`core_loss`/`prev_core_loss` and `loss_delta_core`); a non-finite delta scores
1.0 (never a silent 0). Every hyperparameter change — gradient step, feedback
step, `set_state`/rollback — is committed to the core chain FIRST as
`learning.hyperparameter_changed {changes{param{old,new}}, reason}` and is NOT
applied when the chain write fails; `set_state` validates NaN/Inf/bounds.
The 9D optimizer has no production caller yet (it is exercised by tests only).

### Live experiment collector (`live_experiment_collector.py`)

Records ONLY measured values: learning counts / recent success rate / outcome
loss from the `EventStore`, process `rusage` + host load average, audit chain
size, event counts of the last hour, and per-signal "seen at all" health —
under `<CORVIN_HOME>/tenants/<t>/experiments/live_measurements/`. A source that
cannot be read is recorded as `None` and named in `sources`; nothing is ever
simulated (the pre-2026-09-07 collector wrote `random.gauss` values labelled as
measurements).

## Audit events (ADR-0537 attribution)

| Event | Chain | Emitted by |
|---|---|---|
| `skill.executed` | core | `CoreAuditBackend` on every registry execution (incl. shadow) |
| `learning.skill_executed` / `learning.outcome` / `learning.feedback` / `learning.config_updated` / `learning.preference` | core (content-free: event_id, event_type, skill_id, skill_version, lom) | `EventStore.write_event` (audit-first) |
| `action_performed` — `learning.feedback_received:*`, `skill_config_updated:hypothesis_accepted`, `skill_config_updated:rollback`, `learning.config_rollback`, `learning.preference_confirmed` | console tenant chain | `method_discovery_api.py` |

## What is learned from — and what is only audited

`SkillMetadata.learn` (default `True`) says whether a Skill's executions feed the
learning store. Every execution is audited regardless (`skill.executed`).
`os.capabilities`, `os.headless_mode` and `os.plugin_health_monitoring` declare
`learn=False`: they are deterministic flag/manifest lookups the SPA triggers
continuously (346 executions in ten minutes of polling were observed) and no
optimizer consumes them. The manifest route additionally caches the resolved
flags per tenant for 5 s (`routes/capabilities.py::_read_flags`), invalidated
by `POST /features/toggle`, so an operator decision is never stale.

## Registry hardening (2026-09-07 adversarial review F-K2/F-K3/F-K5/F-K6/F-K8)

| Mechanism | Where | Rule |
|---|---|---|
| LoM required AND resolvable | `SkillsRegistry.execute(..., lom=)` | `lom="<file>:<function>"` (or `<file>:<function>:L<line>`) is mandatory; missing OR unresolvable → audited `skill.executed` with `status=error`, the Skill does NOT run. `lom_hash` = SHA-256 of the named function's source segment (`ast`), so it survives line drift; for the `:L<line>` form the FUNCTION is resolved first and the line must fall inside it (decorators included) — before 2026-09-07 that form hashed the line without ever looking the function up, so a fabricated name still "bound", and a blank line produced the constant `sha256("")` (R3-B1). A blank line, a non-`.py` target, anything outside the repo root and anything under `.corvin/`, `.venv/`, `site-packages/`, `node_modules/` or `.git/` are all refused, and a source file > 2 MB is not parsed. Resolution is memoised on `(lom, path, mtime, size)` — `execute()` resolves twice per call and re-parsing cost up to 80 ms each time. Production call sites: `capabilities.py:_read_flags_uncached`, `slash_commands.py:_plugin_builder_enabled`, `vibe_engineering.py:get_pipeline`, `bootstrap.py:start_health_monitoring`, `delegation_policy.py:_acp_shadow_route`. |
| Decision in the chain | `SkillExecutionResult.to_audit_event` → `decision_summary()` | the core writer drops any `output` key; the chain now carries an allowlisted `decision` (engine, enabled, mode, confidence, shadow/bundled_engine, `flag_count`/`flags_on`/`flags_hash`) — never free text. Field sets are registered as positive allowlists (`SKILL_AUDIT_ALLOWLISTS`). |
| Compliance tier | `SkillMetadata.tier` (`compliance` / `core` / `installed`) | `os.capabilities` is `compliance`: `unregister()` / `disable_skill()` raise `SkillDisableRefused`, the 3-failure auto-disable is refused — every refusal is audited as `skill.disable.refused`. |
| Lost-update guard | `SkillAdapter._locked()` | `fcntl.flock` on `<config>.lock` + RELOAD inside the lock around `run_optimizer_epoch` / `rollback`; two concurrent feedback requests advance the epoch by two, never one. |
| Namespace gate | `skill_forge.registry.SkillRegistry(caller_persona=…)` | create/update/delete/grade/promote are gated on `forge.policy` namespaces when a persona is attached (MCP server hands its turn persona over; console routes act as `assistant` → `assistant.*` only, 422 otherwise). Fail-closed when the policy cannot be loaded. |
| Grade cap | `SkillRegistry.grade(..., organic=False)` | non-organic grades (self-award, auto, bootstrap) are clamped to `AUTO_GRADE_CAP_MAX = 0.3` and audited `capped`; only a caller vouching for a real run passes `organic=True` (MCP `skill_grade` with a `run_id`; `skill_inject.grade_from_user_followup` — an outcome grade backed by the previous turn's `run_id` and the operator's own follow-up). The post-turn auto-grade stays non-organic. |
| Ungraded injection | `skill_context.resolve_inject_ungraded` | conjunctive: `CORVIN_DELEGATE_INJECT_SKILLS_UNGRADED=1` alone never widens; `=0` always narrows. |

## Invariants (must NOT be weakened)

* The bundled routing answer is never altered by the shadow path. Promoting the
  Skill's advice to a real override goes through the `engine.engine_selection`
  extension point and its `permitted_engines` bound — never through `_acp_shadow_route`.
* The core chain admits only the PROCESS tenant (ADR-0007). A learning event
  for another tenant is refused by the writer, detected by the read-back, and
  **not written to disk** — counted in `EventEmitter.write_failures`. Tests
  that write for tenant X run as tenant X (`CORVIN_TENANT_ID`).
* `core/paths/tenant.py::corvin_home()` honours `CORVIN_HOME` (then a
  repo-local `.corvin`, then `~/.corvin`) — the same order as
  `operator/bridges/shared/paths.py`. Nothing in learning/skills may hard-wire `~/.corvin`.
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

* The L10 entry point `adapt_context_l10` (`os.context_adapter`) is still not
  called from a production path — only L5 is shadow-wired (ADR-0613 scope).
* `DelegationRouterSkill` advises in model tiers (`claude-haiku-4 / sonnet-4 /
  opus-5`), not in `delegation_policy` engine ids (`native / acs / tde`); the
  shadow record carries both so agreement is measurable, but the advice cannot
  be promoted to a real override without a vocabulary mapping (follow-up ADR).
* Learning events for a session tenant that is not the process tenant are
  refused fail-closed (see invariants) — one process per tenant is the
  supported multi-tenant deployment for learning.
