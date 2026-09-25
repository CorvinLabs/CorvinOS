# Task-Tracking SSOT + the Tasks panel

Work items — initiatives, epics, stories, tasks, subtasks, issues, proposals —
live in ONE per-tenant store. The console's **Tasks** panel (`/app/initiatives`,
sidebar below Learnings) renders it. ADR-2051 (model), amended by **ADR-2056**
(storage, kinds, console surface, cutover) and **ADR-2060** (host activity:
agent sessions, commits, ADR-derived items kept current by a sync). Diagram:
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
| Host activity readers (Claude Code sessions, git) | `core/console/corvin_console/host_activity.py` |
| Git/ADR sync writer (CLI, timer every 5 min) | `core/console/corvin_console/task_tracking_git_sync.py`, `systemd/corvin-task-tracking-sync.{service,timer}` |
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
| GET | `/items[?include_deleted=true]` | items + rollups + evidence + live runs (`live_runs`, `running_runs`, `live_run_titles`) + `summary` + `import_available` |
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

## Host activity: agent sessions, commits, ADR-derived items (ADR-2060)

Until 2026-09-24 nothing wrote to the store after the one-time import, so the
panel showed a frozen plan while the real work — and the Claude Code sessions
doing it — was invisible. Two run sources and one writer close that:

- **`agent`** (Activity type "Agent session") — interactive Claude Code sessions
  on this host. Live: `<claude_home>/sessions/<pid>.json` (pid checked against
  `/proc/<pid>/stat` start time; `busy` → running, `idle` → paused "waiting for
  input"). Finished: transcripts under `<claude_home>/projects/` touched in the
  last 7 days. `claude_home` = `CLAUDE_CONFIG_DIR` or `~/.claude`. **Only
  `entrypoint: cli`** — `sdk-cli` transcripts are CorvinOS's own bridge/ACS
  workers and carry other people's messages (the `chat` source covers them);
  sessions with a cwd under `CORVIN_HOME` are excluded too. Titled by the
  session's `ai-title`, never by prompt text.
- **`commit`** — non-merge commits of the console's own checkout, last 7 days;
  id `commit:<repo>:<sha12>` (fixed length — it is stored in run links).
- Both are **host-level**: shown to tenant `_default` only; any other tenant
  gets an empty source with a note.
- **Sync writer** `python -m corvin_console.task_tracking_git_sync [--dry-run]`
  (timer `corvin-task-tracking-sync.timer`, every 5 min, actor `sync:git`):
  one initiative per repository (`git:<repo>`), one task per decision record
  referenced in a commit subject in the window (`git:<repo>#ADR-NNNN`, title
  from the record's heading, `category: adr`). **Status follows the record's
  frontmatter status** (accepted/implemented/… → complete,
  rejected/superseded/… → archived, else or no file → in progress). The ADR
  checkout is `resolve_adr_root()` (`CORVIN_ADR_ROOT` → sibling `Corvin-ADR` →
  submodule); both naming schemes (`ADR-NNNN-slug.md`, `NNNN-slug.md`) resolve.
  Every such commit is linked as a run, and so is each agent session whose
  transcript ran a `git commit` carrying that commit's subject within the
  session's lifetime.
- **Operator edits win.** Insert-only via `import_items`; fields are patched only
  while no one but `sync:git` has updated, decided, deleted or restored the item
  (`service.synced_items(..., actor=)` → `foreign_edit`). After that only new run
  links are added. A deleted item is never re-created or linked. A sync that
  changes nothing writes nothing to the chain.
- The panel shows a **Running now** strip above the work views (live agent
  sessions; "Open activity" filters Activity to them).

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
drawing), **Table** (sortable), **Graph** (the live DAG, below), **Activity** (runs). View, filters and the
selected item are in the URL. The detail drawer edits fields with the
optimistic `version` and shows decision, evidence, children, dependencies,
linked runs and history.

**Graph view** (`pages/tasks/graph-view.tsx`, layout in `graph-layout.ts`,
React Flow, lazy-loaded) — **one graph per initiative / loop**: how it was
broken down, projected onto a graph. A picker lists every top-level container
(plus "Items without an initiative" when loose top-level work exists) with its
progress, done/total, in-progress count and a pulsing "N running" when a linked
run works on it right now; it opens on the one with running work, else the most
recently touched, and remembers the choice per browser. The graph is a tidy
tree top → bottom: the initiative on top, each breakdown level below, parents
centred over their parts in stored order (`sort_key`). A node with 3+ parts
groups its LEAF parts by type (`category`, else `kind`): each group of 2+ is a
framed, roughly square grid labelled "6 tasks", "4 preconditions",
"92 decision records", with ONE breakdown edge onto the frame; parts with their
own breakdown (a gate with its criteria) stay subtrees. Dependencies are arrows
across (dashed "waits for" while the prerequisite is open); prerequisites from
outside the initiative sit in a column to its left, marked "External". A line
above the graph states the shape: "broken down into 15 items over 2 levels:
6 tasks · 4 preconditions · 3 criteria · 1 checkpoint · 1 gate".
Positions depend on **structure only** (ids, parents, dependencies,
`sort_key`), so the 5 s poll recolours nodes and never moves them; the view
refits only when the structure changes. "Where we are" per node
(`nodeState`): **running** (a linked run is running now — beats done; the node
pulses, stopped by reduced motion) → blocked → waiting → in progress → ready
(open, nothing left to wait for; a pending gate reads "Decision pending") →
done; state chips count each over work items and zoom to it, "Where we are
now" zooms to running work. Theme tokens in this console are HSL
components (`--card: 0 0% 100%`) — use `hsl(var(--card))`, never
`var(--card)` as a colour; only `--viz-*` are hex.

The list endpoint carries, per item and derived on every read, `live_runs`
(linked runs still active), `running_runs` (of those, running now) and up to
three `live_run_titles`, resolved against `task_sources` (routes/task_tracking.py
`_attach_live_runs`, `service.run_links`).

**Live data.** Every query of the panel (list, detail drawer, Activity) uses
`LIVE_QUERY` (`pages/tasks/live.ts`): 5 s poll, immediate refetch on tab focus
and on mount, `staleTime: 0` — overriding the console-wide
`refetchOnWindowFocus: false`, which the drawer once inherited. Rollups,
overdue flags, KPIs and evidence are derived server-side on every read; linked
runs resolve through `task_sources` (2 s aggregate cache). A line under the
title says "Live · updated Ns ago"; after 15 s without a successful poll it
turns to "Updates delayed", and a failing poll shows "Connection lost —
showing data from …" (the data stays, never presented as live). Drawer fields
own their draft only while focused, so a poll never overwrites typing.
`tests/e2e/tasks-live.spec.ts` proves it against the running console: a change
from a second session reaches tree row, KPI, board column, table, drawer fields
and history without a reload.

Colours: status marks use `--viz-status-progress/-blocked/-complete`
(`src/index.css`, validated light vs `#ffffff` and dark vs `#0e1320` with the
dataviz validator, all pairs); `open` is a hollow mark. Priority is an ordinal
amber ramp keyed on the tier (`--viz-tier-*`). Status is never colour alone.
