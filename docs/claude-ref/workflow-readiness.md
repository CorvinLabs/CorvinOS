# Workflow readiness gate

A workflow draft is **ready** only when a measured report says so. A confirmation word in the
design chat ("ok", "passt", "go ahead") no longer moves a draft to `ready`. The decision record is
ADR-2275 in Corvin-Knowledge; the plan is PLAN-0962.

Diagram: `docs/diagrams/workflow-readiness-flow.svg`. Creating a draft from a server-side
template: [workflow-templates.md](workflow-templates.md).

## The report

`core/workflows/corvin_workflows/readiness.py::check_readiness(yaml_text, *, acceptance, repairs,
max_repairs=3, wall_limit_s=10)` is pure (no network, no channel, no file written). It returns a
`ReadinessReport`: `ready`, `synthetic` (always `True`), `signals`, `run_state`, `elapsed_ms`, and the
per-node `nodes` outcomes of the synthetic run.

| Signal | `pass` when | Otherwise |
|---|---|---|
| `validator` | `validate()` (R1-R11) holds | `fail`, names the rule id; no run is started |
| `reachability` | the synthetic run completed and every node ran or was skipped by a route / `fail_branch` | `fail`, names the node (or `timeout`) |
| `acceptance` | the stored acceptance case holds on the synthetic run | `unmeasured` with no case stored; `fail` when it does not hold or is malformed |
| `external_exposure` | never fails: `info` with the number of stubbed engine and channel/human nodes | - |
| `repairs` | designer repairs of this draft are at or below the ceiling (default 3) | `fail` above it |

`ready` is true only when no signal is `fail` or `unmeasured`. A line that cannot be measured is never
`pass`.

## The synthetic run

A real `DAGRunner` pass with a deterministic `StubEngine`, in a worker thread joined with `wall_limit_s`
(on timeout the run is cancelled and `reachability` fails with `timeout`). `deliver`, `answer`,
`ask_human`, `approval` and `peer_agent` (nothing is sent to a peer; a pinned endpoint must be paired; see [workflow-peer-agent.md](workflow-peer-agent.md)) are replaced for this run through `DAGRunner(executor_overrides=...)`: their
config was already validated by R10, they record instead of writing an outbox envelope, and `ask_human` /
`approval` answer instead of pausing. The global node registry is never touched. `code` nodes keep their
own sandbox (deterministic by contract); every other node gets the stub's canned or generic reply.

It proves structure and the acceptance case. It does **not** prove a real run: prompts, credentials, rate
limits and the channels are not exercised. The UI and the API say so (`synthetic: true`, `notice`).

## Acceptance case

Stored under `meta["acceptance"]` (`PUT /workflows/{wid}/acceptance`, 64 KiB cap, schema-checked by
`readiness.validate_acceptance`):

```json
{"inputs": {"topic": "x"},
 "stub_responses": {"<agent or node id>": {"...": "..."}},
 "expect": {"state": "complete",
            "nodes_reached": ["<node id>"],
            "final_contains": {"<node.field.path>": "<substring>"}}}
```

`stub_responses` keyed by an agent name is that agent's canned reply; keyed by an `ask_human` / `approval`
node id it picks that node's answer (`{"approved": false}` rejects). `{"__raise__": true}` makes an engine
call fail on purpose, to exercise a `fail_branch` (a failed node counts as expected only then).
`expect` is required and non-empty; without a stored case the `acceptance` line is `unmeasured`.

## Routes (`routes/workflow_readiness.py`)

| Route | Behaviour |
|---|---|
| `GET /v1/console/workflows/{wid}/readiness` | computes and returns the report plus `phase`, `acceptance`, `notice`; audits `workflow.readiness_checked` |
| `PUT /v1/console/workflows/{wid}/acceptance` | stores the case (CSRF; 400 schema, 413 over 64 KiB) |
| `POST /v1/console/workflows/{wid}/ready-override` | `{reason}` 1-300 chars; sets phase `ready`; audits `workflow.ready_overridden` with `reason_len` only; 409 when the draft does not validate |

The tenant comes only from the session record. At most four checks run at once (429 beyond that).

## Where the gate bites

- Design chat (`routes/workflows.py::_design_turn`): on a confirmation in `detailing`, the report is
  computed on the YAML as it stands after that turn. Passing moves the phase to `ready`; otherwise the phase
  stays and the server appends one line naming the first failing or unmeasured signal. An error while
  measuring is a refusal.
- Repairs: a generated draft the validator rejects is counted (`meta["repairs"]`, code `yaml_regenerated`,
  audit `workflow.design_repaired`). The ceiling is `spec.workflows.max_design_repairs` in the tenant YAML
  (default 3). The codes `field_added` and `node_replaced` are reserved: nothing emits them yet.
- Run route: `POST /workflows/{wid}/runs` with `dry_run: true` is the **Synthetic check**. It runs the same
  report and streams its per-node results as `node_started` / `node_completed` / `node_failed` /
  `node_skipped`, ending with `run_completed` carrying `synthetic: true`, `ready` and `signals`. The old
  schema-only walk is gone. The run record keeps `dry_run: true`.
- Editor: the inspector has a **Ready** tab (signals, "Check now", acceptance editor, "Mark ready anyway").

Existing workflows keep their stored phase; the gate applies on the next transition.

## Audit

`workflow.readiness_checked` (INFO: `ready`, `failing`, `unmeasured`, `elapsed_ms`, `synthetic`),
`workflow.design_repaired` (INFO: `code`, `count`), `workflow.ready_overridden` (WARNING: `reason_len`,
`failing`). Scalars only: no YAML text, no user text, no node ids. Registered in `EVENT_SEVERITY` and
`_EVENT_ALLOWLIST`.

## Tests

`core/workflows/tests/test_readiness.py` (unit, incl. timeout and no-socket),
`core/console/tests/test_workflow_readiness_routes.py` (HTTP, cross-tenant, chain verifies),
`core/console/tests/test_workflow_ready_gate.py` (design WebSocket gate, repairs, synthetic check),
`web-next/tests/unit/readiness-panel.test.tsx`, `web-next/tests/e2e/workflow-readiness.spec.ts`.
