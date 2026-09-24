# Task-Tracking SSOT + the Tasks panel

Work items — initiatives, epics, stories, tasks, subtasks, issues, proposals —
live in ONE per-tenant store. The console's **Tasks** panel (`/app/initiatives`,
sidebar below Learnings) renders it. ADR-2051 (model), amended by **ADR-2056**
(storage, kinds, console surface, cutover). Diagram:
[`docs/diagrams/task-tracking-ssot-flow.svg`](../diagrams/task-tracking-ssot-flow.svg).

## Where things live

| Piece | Path |
|---|---|
| Store (SQLite, WAL, `0o600`, dir `0o700`) | `<CORVIN_HOME>/tenants/<tid>/global/task_tracking/tasks.db` |
| Schema + connection + per-tenant writer lock | `core/task_tracking/store.py` |
| Vocabularies + request models (Pydantic v2) | `core/task_tracking/models.py` |
| Reads (derived rollups) + audited mutations | `core/task_tracking/service.py` |
| Console routes `/v1/console/task-tracking/*` | `core/console/corvin_console/routes/task_tracking.py` |
| `initiatives.json` importer (CLI + route) | `core/console/corvin_console/task_tracking_import.py` |
| Panel | `web-next/src/pages/tasks/` (encodings in `encodings.ts`) |
| API client | `web-next/src/lib/api/task-tracking.ts` |

On this host the console runs with `CORVIN_HOME=/home/shumway/projects/CorvinOS/.corvin`.

## Model

`kind` and where it may sit (`models.PARENT_RULES`, mirrored in `encodings.ts`):

| kind | allowed parent |
|---|---|
| initiative | none |
| epic | initiative |
| story | initiative, epic |
| task | none, initiative, epic, story, issue, proposal |
| issue / proposal | none, initiative, epic, story |
| subtask | epic, story, task, issue, proposal |

`status` ∈ open · in_progress · blocked · complete · archived. `priority` ∈
critical · high · medium · low. `approval_state` ∈ none · suggested · pending ·
approved · rejected (gates: pending → Go = approved / No-go = rejected).
`category` is a free token (`gate`, `precondition`, `checkpoint`, `criterion`, …).
A create/PATCH may only set `approval_state` to none · suggested · pending;
**approved / rejected only through `POST …/decision`** (event
`decision_recorded`), and a `gate`'s status follows its decision (Go →
complete, No-go → blocked, Reset → open). `checkpoint` is a date marker: never
overdue and not in the KPI counts. Dependencies accept `depends_on` · `related`
· `duplicates` (model "A blocks B" as "B depends_on A").
Cycles (parent and `depends_on`) are refused. Soft delete cascades; restore
brings back exactly the cascade (`cascading_delete_id`) and refuses while the
parent is still deleted or when the hierarchy rules no longer allow it.

Derived on every read, never stored: `overdue` (a bare-date deadline means the
end of that day, UTC), `waiting_on` (open `depends_on` targets), `child_ids`,
`rollup` = {progress = mean of non-archived leaves, descendants, counts by
status, overdue}, and the KPI `summary` (counts only work kinds — task, subtask,
issue, proposal; initiatives/epics/stories are containers).

## Audit-first

Every mutation is one SQLite transaction: apply → append a `task_item.*`
record to `tenant_audit_chain(tid)` → insert the local reference row → COMMIT.
A chain write that fails rolls everything back and the route answers **503**.
Chain records carry ids, enums and changed field NAMES only — never a title,
description, assignee or run reference (`_EVENT_ALLOWLIST` enforces it). The
local `events` table keeps the full delta + the chain hash for the History
section; it is a reference copy, not the source of truth (the 200 MB chain is
never scanned per request).

Events: `task_item.created · updated · deleted · restored · decision_recorded ·
dependency_added · dependency_removed · run_linked · run_unlinked · imported ·
rolled_back`.

**A chain record is permanent, a row is not.** If a transaction rolls back
after it already appended records (a later chain write failed mid-import, the
commit failed), `_txn` appends `task_item.rolled_back {item_id: first, count,
error_class}` so the chain never claims a change the store does not hold.
The prefix is not `task.` because `task.spawn_*` already names runtime spawns.

## API (`/v1/console/task-tracking`, tenant = session)

| Method | Path | Notes |
|---|---|---|
| GET | `/items[?include_deleted=true]` | items + rollups + evidence + `summary` + `import_available` |
| GET | `/summary` | KPI counts |
| GET | `/items/{id}` | item, ancestors, children, depends_on, required_by, runs (resolved), history |
| POST | `/items` | CSRF; 201 |
| PATCH | `/items/{id}` | CSRF; body carries the `version` it read → **409** `{message, current}` on mismatch; `null` clears a nullable field |
| POST | `/items/{id}/delete` · `/restore` | CSRF |
| POST | `/items/{id}/decision` | `{decision: pending\|approved\|rejected, version}` |
| POST / DELETE | `/items/{id}/dependencies[/{dep}]` | cycle-checked |
| POST / DELETE | `/items/{id}/runs` | `run_type` ∈ task_sources types except `initiative` |
| POST | `/import` | idempotent `initiatives.json` import |

## Runs are linked, not copied

Chat turns, background/ACS/gateway/forge/compute runs stay in their own stores
(`task_sources.py`). The panel's **Activity** view lists them read-only; "Link"
attaches one to a work item (`runs` table). Bridge chat text never enters the
store.

## The `initiatives.json` cutover

`python -m corvin_console.task_tracking_import [--tenant _default] [--apply]` —
dry run by default. Items under a branch the operator deleted in the store are
skipped, never re-created; a completion date the file does not state stays
empty (never the import time). Insert-only, idempotent on `external_ref =
initiatives.json#<iid>[/task|group|precondition|checkpoint|gate/…]`; the whole
batch is validated before the first chain write. Mapping: initiative →
initiative, task group → epic, task → task (status/progress as the board derived
them), precondition / checkpoint / gate → task with that `category`, gate
criterion → subtask, `blocked_by` → dependency on the gate.

After the import the file is **frozen for authoring**: `PATCH …/tasks/{tid}`,
`PUT …/gates/{gid}`, `PUT …/close` answer **410**. It is not deleted. The
evidence verifier (`initiatives_verify.py`, timer every 5 min) still writes its
`verification` results into it; the task route attaches them to items by
`external_ref` (derived, never stored) and flags `claim_conflict` when an item
is complete but its evidence does not pass. "Verify evidence" in the panel
triggers a run.

## Panel

Views: **Tree** (default; hierarchy with rollup bars, collapse, filter keeps the
path to a hit), **Board** (status columns, work items only, drag to move),
**Timeline** (start→deadline bars on one shared axis, gates/checkpoints as
diamonds, now-line, hover tooltip; below two dated items it says so instead of
drawing), **Table** (sortable), **Activity** (runs). View, filters and the
selected item are in the URL. The detail drawer edits fields with the
optimistic `version` and shows decision, evidence, children, dependencies,
linked runs and history.

Colours: status marks use `--viz-status-progress/-blocked/-complete`
(`src/index.css`, validated light vs `#ffffff` and dark vs `#0e1320` with the
dataviz validator, all pairs); `open` is a hollow mark. Priority is an ordinal
amber ramp keyed on the tier (`--viz-tier-*`). Status is never colour alone.
