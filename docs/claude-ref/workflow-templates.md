# Workflow templates — one server-side set

**Source:** `core/console/corvin_console/routes/workflows.py` (`_TEMPLATES`, `list_workflow_templates`,
`create_workflow`); UI `web-next/src/pages/workflows.tsx` (Templates tab, `CreateWorkflowDialog`),
client `web-next/src/lib/api/workflows.ts` (`listWorkflowTemplates`, `createWorkflow`).
**Tests:** `tests/sandbox/test_gap_forge_wf_compute.py::test_G4_templates_are_served_and_create_a_workflow`.

There is ONE template set: `_TEMPLATES` in `routes/workflows.py` (`daily-digest`, `inbox-monitor`,
`data-pipeline`, `approval-loop`). The design chat offers them by keyword match
(`_match_template`, WS `accept_template`), and the Templates tab lists the same set.

| Method | Path (under `/v1/console`) | Result |
|---|---|---|
| GET | `/workflow-templates` | `{count, templates: [{id, title, description, steps, node_count}]}` — session required |
| POST | `/workflows` `{id, title?, description?, template: "<key>"}` | creates the workflow from the template YAML with `workflow.name` set to `id`; 404 unknown template; 400 when `yaml` is sent too; 402/409/503 as for any create |

The route is `/workflow-templates`, not `/workflows/templates`, so it can never collide with a
workflow id. A create from a template is audited as `workflow.created` with
`trigger=template=<key>`; the YAML is validated like an uploaded one. The workflow starts in phase
`discovering`, like every new workflow.

UI: "Use" on a card opens the create dialog prefilled with the template's title and id, shows its
steps, and creates from the template; Create opens the editor with the template's nodes.

Until 2026-10-24 the tab rendered five hard-coded cards (`daily_briefing`, `alert_routing`, …)
whose ids the server did not know; "Use" opened an empty dialog and wrote two `sessionStorage` keys
nothing read.

The four templates are `agent`/`delegation_loop` graphs, i.e. every run of one spawns the engine.

## Node field `model` (ADR-2308)

`agent`, `fan_out` and `route` nodes in `mode: classify` accept an optional `model:` — `haiku`, `sonnet`,
`opus` or a registry model
id. It is checked when the document is validated (an unknown value is rejected with
`model_unknown`; the console answers HTTP 400 on save), not first when the node runs. Omit it and the node keeps the model the
engine always used. A `route` in `mode: condition` never calls a model; a `model:` on it is rejected.
The console's node inspector edits the field, and the readiness report counts model nodes without it
(info only). An operator pin on the console's `worker_model` slot outranks the field. Full
order, scope and audit event: [layer-engines.md](layer-engines.md) § Per-node model for workflow nodes.
