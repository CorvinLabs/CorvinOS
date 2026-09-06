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
