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

Metadata only — never manifest bodies, gate output or exception messages. A
manifest that fails schema validation is recorded without `entry_id`/`version`
(an invalid id never reaches the chain). The field sets live in
`layer_forge/audit.py::ALLOWED_FIELDS` and are mirrored in `security_events.py`
(`EVENT_SEVERITY` + `_EVENT_ALLOWLIST`); `test_module_allowlist_matches_the_central_registry` pins them together.
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
| POST | `/layer-forge/plan` `{layer_id, intent}` | 200 `{status: SUCCESS, manifest}` · 422 `{status: FAILED, error, phase}` · 503 audit failed — generates a manifest preview via LLM; nothing is persisted yet (ADR-2224/2225) |
| POST | `/layer-forge/definitions` (body = manifest) | 200 SUCCESS · 409 version exists · 422 rejected (`detail.phase`) · 503 audit failed |
| POST | `/layer-forge/definitions/{entry_id}/{version}/transition` `{to_status}` | 200 · 404 · 409 illegal transition · 503 |
| POST | `/layer-forge/gate-thresholds/analyze` `{gate_id, lookback_days}` | correlation suggestion, never auto-applied |
| POST | `/layer-forge/gate-thresholds/apply` `{gate_id, new_threshold, reason}` | operator-explicit apply, audited |
| GET | `/layer-forge/analytics[?since=&until=]` | decisions/confidence/flags/convergence metrics |

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
