# Layer Forge — configuration generation for architectural layers (ADR-2222)

**Source:** `core/orchestration/layer_forge/` · **Entry points:** `scripts/layer_forge_cli.py`,
console routes `core/console/corvin_console/routes/layer_forge.py`
**Tests:** `tests/layer_forge/` (CLI subprocess E2E), `core/console/tests/test_layer_forge_routes_e2e.py` (HTTP E2E)

Layer Forge is the fourth Forge system (alongside Skill/Tool/Plugin Forge,
ADR-2217). It produces versioned *configuration* artifacts — Layer-Definitions
with their Quality-Gates and Enforcement-Rules — instead of executable code.

## Pipeline

```
PLAN      (optional) LLM drafts a manifest from {layer_id, intent}      -> no write, preview only
VALIDATE  schema + dependency DAG + version not taken           (writes nothing)
TEST      each quality gate = pytest subprocess, fail-closed     -> quality_gate_evaluated
ENFORCE   schema_validation + layer_boundaries (Mypy) + host-awareness, fail-closed -> enforcement_evaluated
REVIEW    adversarial LLM pass argues against acceptance; FLAGGED needs an
          explicit operator override to promote past 'accepted', never auto-rejects -> review_evaluated
CREATE    under the entry lock: audit, then write 'proposed'     -> definition_proposed
PROMOTE   under the entry lock: audit, then 'proposed'->'accepted' -> definition_transitioned
```

A run that fails VALIDATE/TEST/ENFORCE writes **no registry entry**, only one
`layer_forge.definition_rejected` record naming the `phase`. The caller may
always supply a hand-written manifest directly to CREATE, skipping PLAN
entirely — PLAN only exists to draft one from an intent description.

## Audit (ADR-2222 D6)

Every decision is written to the tenant's ONE hash chain
(`core.paths.tenant_audit_chain(tid)`) through `forge.security_events.audit_write_or_die`.
**Audit-first and fail-closed:** a state change happens only after its record
committed; a failed chain write raises `LayerForgeAuditError` and nothing changes
(console: HTTP 503).

| Event | Severity | Fields |
|---|---|---|
| `layer_forge.definition_proposed` | INFO | entry_id, version, target_layers, gate_count, rule_count, gates_skipped, actor, tenant_id |
| `layer_forge.definition_rejected` | WARNING | entry_id, version, phase, error_class, failing_gates, actor, tenant_id |
| `layer_forge.quality_gate_evaluated` | INFO | entry_id, version, gate_id, status, tenant_id |
| `layer_forge.enforcement_evaluated` | INFO | entry_id, version, rule_id, status, tenant_id |
| `layer_forge.definition_transitioned` | INFO | entry_id, version, from_status, to_status, actor, tenant_id |
| `layer_forge.review_evaluated` | INFO | entry_id, version, verdict, flags, prompt_version, tenant_id |
| `layer_forge.review_override_applied` | WARNING | entry_id, version, override_reason, overridden_flags, actor, tenant_id |
| `layer_forge.plan_generated` | INFO | layer_id, intent_len, intent_sha256, manifest_id, manifest_version, actor, tenant_id |
| `layer_forge.plan_failed` | WARNING | layer_id, intent_len, intent_sha256, error_class, actor, tenant_id |
| `layer_forge.canary_rollout_assigned` / `canary_rollback` | INFO / WARNING | see `audit.py::ALLOWED_FIELDS` |
| `layer_forge.optimizer_config_updated` | INFO | old_version, new_version, reason, signal, success_rate, total_overrides, tenant_id |
| `layer_forge.definition_outcome_feedback`, `gate_threshold_suggested` / `_applied` | INFO | see `audit.py::ALLOWED_FIELDS` |

Metadata only — never manifest bodies, gate output or exception messages. The PLAN events
carry a short hash and the length of the `intent`, never its text (user prose), and the
exception CLASS, never its message; any id a model wrote (`layer_id`, `manifest_*`) passes
`audit.ident()` and is recorded as `<invalid>` unless it is a short identifier. A
manifest that fails schema validation is recorded without `entry_id`/`version`
(an invalid id never reaches the chain). The field sets live in
`layer_forge/audit.py::ALLOWED_FIELDS` and are mirrored in `security_events.py`
(`EVENT_SEVERITY` + `_EVENT_ALLOWLIST`); `test_module_allowlist_matches_the_central_registry` pins them together, and
`tests/layer_forge/test_plan_phase_wiring.py` fails if the package emits an event name that is
not registered (`emit` RAISES for one). Until 2026-10-08 nine of the twelve events here, plus
`plan_generated`, `plan_failed` and `optimizer_config_updated`, were missing from the central
registry — so every successful PLAN ended as "audit failed" and the optimizer's own config change
was never recorded.
`actor` is `cli` or `console`.

## Concurrency (ADR-2222 D4)

Every registry read-modify-write (create, transition) and the audit record that
precedes it run inside `LayerPrimitive(entry_id).locked()` — a stdlib-only
`O_CREAT|O_EXCL` lock file per entry id. Registry files are replaced with
`atomic_write_json` (temp file + `os.replace`). The lock is not re-entrant.

## Storage

`<corvin_home>/tenants/<tid>/global/layer_forge/registry/<id>@<version>.json`
(locks in `.../layer_forge/locks/`). Versions are immutable; "latest" is by
semantic version. Lifecycle: `proposed → accepted → deployed → superseded`
(`accepted → proposed` allowed).

## Input confinement

Manifests arrive over HTTP, so: `id` must fully match `^[a-z0-9][a-z0-9._-]*$`
(also checked on every registry lookup — a URL id cannot reach a file path or
glob), `version` is semver, gate `test_path` must be repo-relative under
`tests/` with no `..` (re-checked after symlink resolution before pytest runs),
host-awareness paths must be repo-relative with no `..`. A gate whose pytest
exits 2–5 (interrupted, usage error, no tests collected) or whose interpreter
has no pytest is `ERROR`, never `PASS`.

## Host-awareness

A manifest may declare `source_tree`/`runtime` paths plus `cross_check`
(`sha256_match_or_fail` | `none`). Without a runtime install (`/opt/corvin`)
the runtime half is `SKIPPED` (`skipped_no_runtime_host`) — never a silent PASS.

## Console routes (session-scoped tenant; mutations need CSRF)

| Method | Path (under `/v1/console`) | Result |
|---|---|---|
| GET | `/layer-forge/definitions` | `{items, count}` |
| GET | `/layer-forge/definitions/{entry_id}[?version=]` | entry, 404 if unknown |
| POST | `/layer-forge/plan` `{layer_id, intent}` | 200 `{status: SUCCESS, manifest}` · 422 `{status: FAILED, error, phase}` · 503 audit failed — generates a manifest preview via LLM; nothing is persisted yet (ADR-2224/2225). The model call is bounded: `llm_plan.PLAN_TIMEOUT_S = 60` s, one retry |
| POST | `/layer-forge/definitions` (body = manifest) | 200 SUCCESS · 409 version exists · 422 rejected (`detail.phase`) · 503 audit failed |
| POST | `/layer-forge/definitions/{entry_id}/{version}/transition` `{to_status}` | 200 · 404 · 409 illegal transition · 503 |
| POST | `/layer-forge/gate-thresholds/analyze` `{gate_id, lookback_days}` | correlation suggestion, never auto-applied |
| POST | `/layer-forge/gate-thresholds/apply` `{gate_id, new_threshold, reason}` | operator-explicit apply, audited |
| GET | `/layer-forge/analytics[?since=&until=]` | decisions/confidence/flags/convergence metrics |

The REVIEW phase's model call is bounded too (`review.REVIEW_TIMEOUT_S = 45` s, one retry → worst case
`REVIEW_WORST_CASE_S = 90` s, then verdict `ERROR`, i.e. refused). It runs inside this request and inside
every layer of a bundle import, holding a worker and one import slot; the SDK default (10 min x 3 attempts)
let a hung call hold that for up to ~30 min. Neither call passes `temperature`: the installed SDK's
`messages.create()` takes none, and the PLAN call used to die on that before any network traffic.

Quality gates cannot be skipped from the console. Handlers are sync `def`
(the pipeline runs pytest subprocesses in FastAPI's threadpool, not on the
event loop).

## Console frontend

Reachable at `/app/forge?tab=layers` (`core/console/corvin_console/web-next/src/components/forge/LayersTab.tsx`) —
a sibling top-level tab in the Forge panel, not a Generator sub-tab (it
doesn't share the run/poll/phase engine protocol Skill/Tool/Plugin Forge use,
ADR-2217/ADR-0672; `plan()` is a single synchronous LLM call). Consolidated
from a standalone `/app/layer-forge` panel on 2026-10-06 — that URL now
redirects here. The "Forge a Layer" form (layer ID + intent) calls `POST
.../plan` for a manifest preview, then `POST .../definitions` to run it
through the real pipeline on confirm; nothing is created between those two
steps. The Definitions list/detail view below it is unchanged. The analytics
dashboard (`/app/layer-forge-analytics`) stayed a separate standalone panel
(own charts, no shared list+detail surface) — linked from the tab for
discoverability, since it has no sidebar entry of its own either. Transition
buttons in the detail view are still `disabled` stubs (see Not built).

## CLI

```bash
python scripts/layer_forge_cli.py [--tenant TID] create manifest.json [--skip-gates]
python scripts/layer_forge_cli.py [--tenant TID] get <id> [--version V]
python scripts/layer_forge_cli.py [--tenant TID] list
python scripts/layer_forge_cli.py [--tenant TID] promote <id> <version> <to_status>
```

`--tenant` defaults to `$CORVIN_TENANT_ID` or `_default`; the root follows
`CORVIN_HOME`. `--skip-gates` is recorded as `gates_skipped: true`. Exit codes:
0 success, 1 refused/failed, 2 usage error.

## Manifest shape

```json
{
  "id": "acme.audit-first-l34",
  "version": "0.1.0",
  "targets": [{"layer_id": "L34", "layer_name": "Data Flow Guard"}],
  "dependencies": [{"id": "base.rule", "type": "layer_definition"}],
  "quality_gates": [{"gate_id": "audit-first-check", "test_path": "tests/security/test_l34_audit_first.py"}],
  "enforcement_rules": [{"rule_id": "no-l34-to-l5", "type": "compile_time"}],
  "host_awareness": {
    "source_tree": {"paths": ["core/orchestration/layers/l34/enforce.py"]},
    "runtime": {"paths": ["core/orchestration/layers/l34/enforce.py"]},
    "cross_check": "sha256_match_or_fail"
  }
}
```

## Not built (named, not hidden)

- `enforcement_rules` are stored and counted; **schema_validation + layer_boundaries
  checkers are wired** (M1 2026-10-05); execution of the boundary rules themselves is deferred
  (phase 2, requires build artifact analysis).
- The Definitions detail view's transition buttons (Accept/Deploy/Supersede) are
  rendered `disabled` with a note pointing at the real transition endpoint — a
  human runs transitions via the CLI (`layer_forge_cli.py promote`) or a direct
  `POST .../transition` call today, not by clicking in the console.

## Review-prompt canary and the optimizer (Phase 3c/4a2)

Defects found and fixed 2026-10-08 (each has a regression test in `tests/layer_forge/`):

- `OptimizerEngine.rollback_canary` wrote `self._data` / called `self._persist()` on the ENGINE, which has
  neither; every rollback raised `AttributeError`, was swallowed and returned `False` — **a regressing canary
  was never rolled back**. It now calls `ReviewPromptVersions.set_current()` (versions stay immutable, only the
  pointer moves).
- `check_canary_regression` used `FeedbackPattern.is_significant` as "enough data", but that demands a success rate
  >= 70 %, so a canary that did badly (80 % -> 20 %) counted as "no significant data" and only mild drops were
  caught. It uses `has_enough_data` (>= 5 outcomes).
- `FeedbackPattern.is_significant` demanded >= 70 % for BOTH signals; an UNDERCAUTIOUS pattern is <= 40 % by
  construction, so the optimizer could relax a review but never tighten one. Significance is now per signal.
- `select_canary_version` read the literal tenant `_default` for everyone; it takes `tenant_id` / `storage_root`
  and the orchestrator passes its tenant.
- `EnforcementChecker.check_schema_validation` returned `ERROR` ("the check could not complete") for a detected
  dependency cycle or an unresolvable dependency; both are a definite `FAIL` of the manifest.
- `analytics` windows used `datetime.utcnow().timestamp()`, which treats the naive UTC value as local time: at UTC+2
  the window ended two hours in the past and a definition created a minute ago was outside it. `time.time()` now;
  an ISO string without a zone means UTC.
- `gate_outcome_correlation` ignored its `gate_id` argument and could never count a failure (an inner
  `status == "deployed"` inside `status == "deployed"`): the rate was always 1.0, the signal always "overcautious".
  It filters by gate and counts deployed vs rejected/superseded.
- The console panel's Create sent `{manifest}`; the route takes the manifest itself as the body, so every Create
  answered "validation failed: missing required field: id". The panel (and its vitest, which had pinned the wrong
  shape) now send the manifest.

**Review outcome (fixed 2026-10-10, ADR-2175 G6):** the analytics read `review_flagged` / `review_flags`, the fields the
orchestrator persists on the definition when a review is FLAGGED. They used to read a `_review_verdict` record that nothing
ever wrote, so the weekly decision, flag and gate-correlation figures were always zero in production. A failed quality gate
still refuses the definition rather than leaving a record to override, so the gate-threshold suggestion rests on flagged,
overridden definitions only. Learning events are stored under the label `layer_forge` (not a registered Skill, not `os.*`:
a run takes minutes, far beyond a `Skill.execute()` budget). The rollback audit event is written AFTER the
pointer moves (the module's "audit-first" rule would reverse that) — a design decision left open.

Tests: the REVIEW phase calls the Anthropic API, the one external boundary. `tests/layer_forge/conftest.py` stubs it
(autouse; `@pytest.mark.real_review` opts out) and the CLI wiring tests start their subprocess through a wrapper
that patches only that boundary — without it 19 tests were red on any machine without an API key.
