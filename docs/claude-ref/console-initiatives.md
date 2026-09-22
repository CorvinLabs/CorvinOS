# Console — Initiatives board

Live status of running and finished initiative tasks. Sidebar: **Tasks**,
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

## All tasks — every type, one list

The page also lists **every task, run or job on the install**, marked by type,
split into **Running** and **Finished**, filterable by type chips. Source:
`corvin_console/task_sources.py`, served by `GET /v1/console/initiatives/tasks`.
Each source is read where its subsystem writes it — nothing is copied.

| Type | Store (under the tenant home unless noted) |
|---|---|
| Initiative | `global/initiatives.json` (this board) |
| Chat | `sessions/<chat>/tasks/<id>.json` — web, CLI, Discord, Telegram turns |
| Background task | `sessions/voice/<bridge>/bgtask*/tasks/` — background `/task` runs |
| ACS | `global/acs/runs/*/manifest.json` + session run dirs without an index entry |
| Workflow | `workflows/<wid>/runs/*.meta.json` (marketplace plugin) + `workflow_runs/*.json` (paused AWP) |
| Flow | `global/flows/runs/*.manifest.jsonl` |
| Gateway run | `global/gateway/runs/run_*.json` |
| Forge tool | `global/forge/runs/` (tenant) + host-level forge run dirs — host-level ones for `_default` only |
| Compute | `compute/runs/`, `compute/pipelines/`, `compute/hac/`, `global/compute/jobs/` |
| Scheduled | `voice/schedule.json` |
| Skill creator | in memory of the console process |

**One status vocabulary:** active = queued · running · paused · scheduled;
finished = done · failed · cancelled; **stale** = claims to be active but
shows no sign of life (no end record, no heartbeat, no worker) for > 2 h. A
stale record is counted apart ("Running (11 · 80 stale)") and carries its
reason; it is never shown as running.

**Privacy:** a bridge chat task's instruction is another person's message —
bridge tasks are titled by channel and persona only; web/CLI turns (the
operator's own) show a preview. Scheduled reminders show their schedule, not
their text.

**Cost:** files are re-parsed only when (mtime, size) changed; the aggregate
is cached 2 s; the route is sync (threadpool) so the scan never blocks the
event loop. Measured on this install: 5 140 records, cold 260 ms, warm
~116 ms, cached 5 ms.

A source that is empty for a reason says so under the table (e.g. the
workflow plugin does not load on this build).

## Evidence — keeping the facts current

Hand-typed status and percentages go stale. A task (or precondition) that
declares `evidence` is verified by `corvin_console.initiatives_verify`:

- runs the listed pytest targets (must resolve inside the repo; anything else
  counts as an error and is not run) in a **throwaway `CORVIN_HOME`** — never
  against the live install or its audit chain;
- checks the listed paths exist (existence only);
- merges `verification` into a fresh read of the file (concurrent edits
  survive) and writes one content-free `initiatives.verified` audit record.

Triggers: `corvin-initiatives-verify.timer` ticks every 5 min and runs
`--if-changed`: it verifies only when the repo state (HEAD, working-tree
status and diff, untracked files), an evidence spec or an evidence path
changed since the last run — or that run is older than 30 min. Units in
`core/console/systemd/`, installed under `~/.config/systemd/user/`. Plus the
**Verify now** button (`POST /v1/console/initiatives/verify`, detached, lock
file `.initiatives_verify.lock` prevents parallel runs).

For a task with a verification result the board derives:

| Field | Rule |
|---|---|
| `progress` | (passing tests + present paths) / (all tests + all paths) |
| `status` | `done` when everything passes; otherwise `running` once anything passes (operator `blocked` kept) |
| `completed_at` | when the evidence FIRST went green (`first_ok_at`) |
| `claim_conflict` | hand-marked `done`, evidence disagrees → shown as running with a red badge |
| `verification.stale` | result older than 40 min (the timer is then not running) |

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
| GET | `/v1/console/initiatives/tasks?types=&finished_limit=&finished_offset=` | session | every task type, normalised (see "All tasks") |
| PATCH | `/v1/console/initiatives/{iid}/tasks/{tid}` | session + CSRF | `{status?, progress?}`; audited `initiative.task.update` |
| PUT | `/v1/console/initiatives/{iid}/gates/{gid}` | session + CSRF | `{decision}`; audited `initiative.gate.<decision>` |
| POST | `/v1/console/initiatives/verify` | session + CSRF | start evidence verification in the background (202); audited `initiative.verify.start` |
| PUT | `/v1/console/initiatives/{iid}/close` | session + CSRF | `{outcome: "completed"\|"cancelled"\|null}` — close or reopen a run; audited `initiative.close.<outcome>` / `initiative.reopen` |

Tenant comes from the session only. Writes are atomic, mode `0o600`.

## Real-time — what "up to date" means, per source

| Change | Reaches the open page | Mechanism |
|---|---|---|
| Time (countdowns, elapsed %) | every second, to the unit shown (minutes above one day) | client tick against server time |
| Hand edit of the file | next poll, ≤ 5 s | poll |
| Change from another session/operator | next poll, ≤ 5 s | poll |
| Tab was in the background | a fetch is issued on return (measured 6–7 ms) | `refetchOnWindowFocus: "always"` (polling pauses while hidden) |
| Code / test / file change in the repo | ≤ 5 min + test runtime (~30 s); immediately via **Verify now** | timer `--if-changed` |

**On open** the page always fetches (`refetchOnMount: "always"`,
`staleTime: 0`) — numbers cached from an earlier visit are never shown as
current — and asks the server to re-check the evidence if the repo changed
since the last run (`POST /initiatives/verify?if_changed=true`; a no-op
without an audit record when nothing changed).

**Robustness.** Each poll has an 8 s budget (`POLL_TIMEOUT_MS`): a request
the server does not answer is aborted instead of blocking every poll queued
behind it. A failed poll is retried twice after 1 s; while polls keep failing
the last good data stays on screen and the indicator reads "Reconnecting…
showing data from N s ago" — the full error card appears only when there has
never been data. A 404 (route absent on this build) is not retried.

**Server stalls (fixed, ADR-2036).** The console process used to stall for
seconds every heal cycle because the ACO integrity monitor re-parsed the
187 MB audit chain up to 25x per cycle; it now verifies incrementally (same
verdict). This panel's latency depended on that more than on anything in the
panel itself.

All of that assumes the console answers promptly. When the last successful
update is older than 15 s the live indicator turns amber ("Delayed · last
update N s ago") instead of implying the numbers are current. After
**Verify now** the bar shows "Verifying evidence…" from the click until a
result newer than the click arrives.

Proven by `web-next/tests/e2e/initiatives-live.spec.ts` against the live
console, one page never reloaded
(`npx playwright test -c playwright.live.config.ts`); it reverts every change.

## Tests

- `core/console/tests/test_initiatives_route.py` — HTTP through the real router,
  evidence derivation, verifier (sandboxed home, repo-escape refused, audit)
- `web-next/tests/unit/initiatives-page.test.tsx` — page against MSW
