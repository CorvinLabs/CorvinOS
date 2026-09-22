# Console — Initiatives board

Live status of running and finished initiative tasks. Sidebar: **Initiatives**,
directly below **Learnings** (`/app/initiatives`). ADR-2035.

## Data

One operator-authored file per tenant, resolved through `core.paths.tenant_home()`:

```
<CORVIN_HOME>/tenants/<tid>/global/initiatives.json
```

On this host the console runs with `CORVIN_HOME=/home/shumway/projects/CorvinOS/.corvin`
(see `systemctl --user show corvin-webui -p Environment`), not `~/.corvin`.

A missing file is an empty board — the page shows an empty state, never sample data.

### Schema (version 1)

```jsonc
{
  "version": 1,
  "initiatives": [{
    "id": "loop-b", "label": "Loop B", "title": "Phase 9 fixes",
    "description": "optional", "cadence": "optional free text",
    "start": "2026-09-22T00:00:00Z", "deadline": "2026-09-26T08:00:00Z",
    "blocked_by": {"initiative": "loop-b", "gate": "blocker"},   // optional
    "status_override": "optional — wins over the derived status of an ACTIVE run",
    "closed": {"at": "ISO-8601", "outcome": "completed|cancelled"},  // set by PUT …/close
    "tasks": [{"id": "p0-audit", "title": "…", "group": "P0 — 13 critical",
               "status": "pending|running|done|blocked", "progress": 40,
               "due": "ISO-8601", "note": "optional",
               "evidence": {"tests": ["tests/control_plane/test_x.py"],   // repo-relative pytest targets
                            "paths": ["/usr/bin/blender", "tools/gen.py"]}, // must exist
               "verification": {…}}],                                     // written by initiatives_verify
    "preconditions": [{"label": "…", "state": "ok|pending|fail", "detail": "optional"}],
    "checkpoints":   [{"at": "ISO-8601", "label": "Daily standup"}],
    "gates": [{"id": "blocker", "title": "Blocker Gate", "at": "ISO-8601",
               "decision": "pending|go|no_go",
               "criteria": [{"label": "…", "state": "ok|pending|fail",
                             "requires_tasks": ["p0-audit"]}],  // optional: state derived from tasks
               "on_go": "optional", "on_no_go": "optional"}]
  }]
}
```

### Derived on every read (never stored)

| Field | Rule |
|---|---|
| `time_progress_pct` | elapsed share of `start → deadline`, clamped 0–100 |
| `task_progress_pct` | mean task progress (`done` counts as 100) |
| `task_counts` | per status + `overdue` (due in the past, not done) |
| `blocked_by` | set while the referenced gate's decision is not `go` |
| `phase` | `finished` when `closed` is set or every task is done, else `active` |
| `outcome` / `finished_at` | from `closed`; for an all-done run `completed` at the latest task `completed_at` |
| `status` | finished → `done` / `cancelled`; active → `status_override` → `blocked` → `scheduled` (before start) → `at_risk` (overdue task or deadline passed) → `running` |
| `time_progress_pct` (finished) | frozen at `finished_at` |
| `duration_s` | finished: start → finished_at; active: start → now |
| `schedule_delta_s` | finished only: seconds before (+) / after (−) the deadline |
| `next_checkpoint` | earliest future checkpoint, open-task due date or pending gate |

## Evidence — keeping the facts current

Hand-typed status and percentages go stale. A task (or precondition) that
declares `evidence` is verified by `corvin_console.initiatives_verify`:

- runs the listed pytest targets (must resolve inside the repo; anything else
  counts as an error and is not run) in a **throwaway `CORVIN_HOME`** — never
  against the live install or its audit chain;
- checks the listed paths exist (existence only);
- merges `verification` into a fresh read of the file (concurrent edits
  survive) and writes one content-free `initiatives.verified` audit record.

Triggers: `corvin-initiatives-verify.timer` (every 30 min, units in
`core/console/systemd/`, installed under `~/.config/systemd/user/`) and the
**Verify now** button (`POST /v1/console/initiatives/verify`, detached, lock
file `.initiatives_verify.lock` prevents parallel runs).

For a task with a verification result the board derives:

| Field | Rule |
|---|---|
| `progress` | (passing tests + present paths) / (all tests + all paths) |
| `status` | `done` when everything passes; otherwise `running` once anything passes (operator `blocked` kept) |
| `completed_at` | when the evidence FIRST went green (`first_ok_at`) |
| `claim_conflict` | hand-marked `done`, evidence disagrees → shown as running with a red badge |
| `verification.stale` | result older than 2 h |

The console disables the status/progress controls of evidence-derived tasks.
A gate criterion with `requires_tasks` is `ok` when all named tasks are done,
`fail` once the gate time passed without that, `pending` otherwise.

Note: for a task whose evidence is a test suite, progress is the test pass
rate — "how much of what exists works", not "how much of the plan is built".

## Runs and history

Each initiative is a **run**. The page has two tabs:

- **Running** — active runs as full cards (task filter All / Open / Done).
- **Finished** — the history, newest first: outcome (Completed/Cancelled),
  finish time, duration, verdict against the deadline (early / on time / late),
  tasks done. Each row expands to the full card and can be reopened.

Finished runs stay in the file; nothing is deleted. Reopening removes only the
`closed` record. Overdue flags and countdowns are suppressed in finished runs.

## API (`corvin_console/routes/initiatives.py`)

| Method | Path | Auth | Effect |
|---|---|---|---|
| GET | `/v1/console/initiatives` | session | derived board + `server_time` + `revision` |
| PATCH | `/v1/console/initiatives/{iid}/tasks/{tid}` | session + CSRF | `{status?, progress?}`; audited `initiative.task.update` |
| PUT | `/v1/console/initiatives/{iid}/gates/{gid}` | session + CSRF | `{decision}`; audited `initiative.gate.<decision>` |
| POST | `/v1/console/initiatives/verify` | session + CSRF | start evidence verification in the background (202); audited `initiative.verify.start` |
| PUT | `/v1/console/initiatives/{iid}/close` | session + CSRF | `{outcome: "completed"\|"cancelled"\|null}` — close or reopen a run; audited `initiative.close.<outcome>` / `initiative.reopen` |

Tenant comes from the session only. Writes are atomic, mode `0o600`.

## Real-time

The page polls every 5 s (paused in background tabs) and ticks countdowns every
second against the server clock (`server_time` skew). Hand edits to the file
appear within one poll.

## Tests

- `core/console/tests/test_initiatives_route.py` — HTTP through the real router,
  evidence derivation, verifier (sandboxed home, repo-escape refused, audit)
- `web-next/tests/unit/initiatives-page.test.tsx` — page against MSW
