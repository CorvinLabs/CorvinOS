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
| KB projector (heal → export → apply → index, every 2 s) | `core/console/corvin_console/kb_projection.py` + `core/task_tracking/projection.py` |
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
| POST | `/import` | **retired → 410** (2026-10-03): Loop A/B/C were imported into the KB once (`I-02..I-04`) |
| POST | `/items/{id}/kb-transition` | CSRF; `{to, reason?, dod?}` — board move of a knowledge-base task; runs `kb task` (the KB state machine) and re-projects; **409** `{message, kb_refused}` with the KB's own refusal |
| GET | `/kb/status` | projector state: `ok`/`blocked`/`diverged`/`error`/`off`, `blocking`, `failing`, `drift_total` |
| POST | `/kb/sync` | CSRF; one forced projector tick (heal → export → apply) |

## Runs are linked, not copied

Chat turns, background/A2A/ACS/gateway/forge/compute runs stay in their own stores
(`task_sources.py`; types and privacy rule: console-initiatives.md § All tasks). The panel's **Activity** view lists them read-only; "Link"
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
  checkout is `resolve_adr_root()` (`CORVIN_ADR_ROOT` → sibling `Corvin-Knowledge` →
  submodule); both naming schemes (`ADR-NNNN-slug.md`, `NNNN-slug.md`) resolve.
  **When one number is carried by more than one file**, superseded/rejected
  siblings are ignored while a live one exists, and among live siblings the one
  that is NOT done decides — two live records that disagree read as open, never
  complete; the description names every file and the one the status was read
  from. **Items whose commits left the window** keep following their record:
  each run re-reads the record for every existing `git:<repo>#ADR-*` item not in
  the plan and patches title/status/description under the same ownership rule
  (reported as `refreshed`); without this an item froze at its last in-window
  value. Every such commit is linked as a run, and so is each agent session whose
  transcript ran a `git commit` carrying that commit's subject within the
  session's lifetime.
- **Operator edits win.** Insert-only via `import_items`; fields are patched only
  while no one but `sync:git` has updated, decided, deleted or restored the item
  (`service.synced_items(..., actor=)` → `foreign_edit`). After that only new run
  links are added. A deleted item is never re-created or linked. A sync that
  changes nothing writes nothing to the chain.
- The panel shows a **Running now** strip above the work views: every live run of
  every type (chat and bridge turns, background tasks, A2A, agent sessions …) —
  running, paused or queued, never a scheduled reminder — running first;
  "Open activity" opens Activity unfiltered (ADR-2081).

## Knowledge-base projection (ADR-2205)

Initiatives, epics and tasks are authored in **Corvin-Knowledge** (`kb/initiatives`,
`kb/epics`, `kb/tasks`, Markdown + frontmatter, one immutable `uid` each). The store
holds a **projection** of them — items with `external_ref = kb:<uid>`, `category: kb`,
label `kb`. One way only: nothing reads a status back into the KB.

- **Projector** `corvin_console/kb_projection.py`, started by BOTH hosts
  (`corvin_console.app`, `corvin_gateway.app`), tenant `CORVIN_KB_TENANT` (default
  `_default`), KB at `CORVIN_KB_REPO` or the sibling `Corvin-Knowledge` checkout; no KB →
  `state: off`. Every 2 s: KB changed (git HEAD or a file under `kb/{initiatives,epics,tasks}`)
  → `kb heal` (derivable fixes only, committed + audited by the KB) → `kb export` →
  `core/task_tracking/projection.apply` (upsert by `external_ref`, parent-first, actor
  `sync:kb`; unchanged items untouched). KB unchanged → `projection.diff` against the last
  export; any difference is **drift** (the store was written behind the KB's back) and is
  re-applied at once (`drift_healed`). Removed from the KB → `archived`, never deleted.
- **Fail-closed:** a KB whose `kb check` is red writes nothing but one
  `task_item.projection_blocked` record; the board shows a banner and the last
  consistent state.
- **Status mapping:** KB `open/in_progress/blocked/done/cancelled` → store
  `open/in_progress/blocked/complete/archived`; containers carry the KB's derived rollup.
- **Ownership:** `service.update`/`delete` refuse writes to `status`, `parent_id`, `kind`,
  `title`, `status_reason`, `category`, `labels` of a `kb:` item by any actor but `sync:kb`
  (`KbOwned` → **409** `kb_owned`). Priority, assignee, deadline stay local.
- **Board:** dragging a KB task calls `/kb-transition`; columns the KB state machine does
  not allow for that card are dimmed and refuse the drop (`encodings.ts::canMove`); KB
  containers never move. Blocking/cancelling asks for a reason, completing for a
  definition of done.
- **Audit:** per-item writes are `task_item.created/updated` (actor kind `sync`); plus
  `task_item.projection_applied`, `task_item.projection_blocked`, `task_item.kb_transition`
  (outcome `requested`, then `refused` if the KB said no — audit-first).
- **Git sync:** once a tenant has projected KB items, `task_tracking_git_sync` stops
  creating ADR tasks and archives the existing `git:<repo>#ADR-*` cards (reason names
  ADR-2205); a commit subject naming a KB task id (`T-0042`) is linked to that task.
- **Decisions:** `/items/{id}/decision` on a `kb:` item is refused (409 `kb_owned`) — a go
  decision sets status, which is the KB state machine's field; record a gate with `kb task`.
- **Writers of the store after 2026-10-03** (and nothing else): `sync:kb` (the projector), operator
  edits of local, non-`kb:` items and of the local fields of `kb:` items (priority, assignee,
  deadline), and `sync:git` (archives legacy ADR cards, links commits). Retired: `loop_a`'s board
  reporting + timer, the `initiatives.json` import, `corvin-initiatives-verify.timer`,
  `task_completion_registry.py` + its timer + `task_completion_verifier.py`. The 37 live
  `initiatives.json#…` items were archived with "moved into the knowledge base: <id>".
- **Periodic loop (ADR-2208, every `PERIODIC_S` = 600 s, never per tick):** `kb guidance run`
  (learning loop) → `kb sweep --create-tasks` (G6: a BUILT decision's `paths:`/`commits:` and done
  tasks' `done_evidence` — what vanished becomes a NEW task with `regresses:`, which turns the done
  card back to open on the board; the area is paused for agents) → the **SkillForge bridge**
  (`guidance_bridge`): each active guidance file becomes a `learned-experience` skill
  `assistant.kb_guidance_<class>` in the tenant registry (user scope), graded once with the capped
  bootstrap seed and acknowledged with `kb guidance ack`; a retired guidance file deletes its skill.
  `kb.py` stays the only writer of KB files. The result is in `/kb/status` → `periodic`.
- **Code tasks need G5 evidence:** a task whose epic implements a decision is `done` only through
  `kb task <id> done --evidence call_site=… --evidence e2e_test=… --evidence exit_code=0
  --evidence commit=<sha on origin/main> [--evidence repo=CorvinOS]`. A board drag to *done* sends
  no evidence, so for such a task it answers 409 with the KB's message — close it from the CLI.
- **Definition of done:** the planned `definition_of_done` is the acceptance criterion and is never
  overwritten; a `dod` sent with a board move to a task that already has one is stored as
  `completion_note`.
- **Reviews never reach the board:** `kb export` carries them in a separate `reviews` list. A
  `kind: review` item put every projector tick into `error` (the board froze from T-0041 to
  2026-10-04).
- **Red means inconsistent, not unfinished:** `kb check`'s G1–G4 findings (no concept, no plan, no
  closed review) are warnings — they never block the projection.
- E2E: `core/console/tests/test_kb_projection_e2e.py` (real routes, real `kb.py`, fixture KB repo;
  `KbPeriodicLoopE2E` drives `periodic()` — drift → regression task on the board, guidance → minted skill).

## The `initiatives.json` cutover (historical)

The importer (`task_tracking_import.py`) and the evidence verifier timer were retired on
2026-10-03 (ADR-2205): `initiatives.json` was imported into the knowledge base (Loop A/B/C
are `I-02..I-04`), `POST …/import` answers **410**, and the 37 board items it had created
were archived "moved into the knowledge base: <id>". Work items are authored only with
`kb new` and moved only with `kb task` / a board drag (`/kb-transition`).

## Hardening (adversarial review 2026-10-03)

- **Projector states:** `ok` · `blocked` (red KB — the last good projection stays; ONE
  `projection_blocked` record per KB state, not one per tick) · `held` (work-item `*.md` files
  edited but not committed — export reads the working tree, so the projector keeps the last good
  projection, still repairs drift against it, and refuses board moves with the reason; the banner
  names the files; a red KB is reported as `blocked` first) · `diverged` · `error` · `off`.
- **Restore:** a soft-deleted `kb:` item is restored even when it was deleted without a cascade
  id (a raw write); `task_item.restored` is recorded only when a row actually changed.
- **Labels** are deduplicated and capped at 12 exactly like the store keeps them, so a KB file
  with duplicate or many labels converges instead of being re-written every tick.
- **`kb index`** runs after every applied export — the Knowledge Graph panel never goes stale.
- **Transitions:** audited `requested` → `applied` | `refused` | `error`; options are passed as
  `--reason=…`, so a reason that starts with `-` stays a reason; the free text reaches only the
  task file (the KB audit log and commit message carry its hash). A move from a tenant the KB
  is not projected into is refused.
- **Subprocesses** (`kb.py`, the panel's `git`) get a minimal environment — the console's API
  keys are never handed to code read from the KB checkout — and `git` runs with hooks and
  fsmonitor switched off. **Trust boundary:** the projector executes `scripts/kb.py` FROM the
  KB checkout; whoever can push to Corvin-Knowledge `main` can run code as the console user.
  The panel's pull is `--ff-only` under the KB's own `kb/.lock`.
- **Rollback** (`snapshots.rollback_to_version`) refuses a `kb:` item; the unmounted, unauthed
  `core/task_tracking/routes.py` that exposed it was deleted.
- **Stopping:** `kb_projection.stop(tenant)` ends the thread (tests, shutdown).

## Panel

**Runs next to the items (ADR-2081).** Every work view also shows the runs of
the install — all active ones plus those finished in the last 24 h, all types
except commits and scheduled reminders (`WORK_RUN_TYPES`; a reminder is an
appointment, permanently "scheduled", not a run — both stay in Activity),
matched by the search box — in its own form,
never mixed into the items (a run has no priority, deadline or rollup, and must
not be draggable): **Tree** — a "Runs" block ABOVE the item tree, one group per
type + channel (`Chat · discord`, `A2A · inbound`), groups with active runs
expanded; **Board** — dashed, non-draggable cards in the column of the same
meaning (queued → Open, running/paused → In progress, failed/stale → Blocked,
done/cancelled → Complete), active runs on top, finished below the items, 8 +
8 per column; **Timeline** — a separate 24 h lane with its own axis and legend
(minutes-long runs are invisible on the items' axis), every active run plus
the latest 40 finished; **Table** — a second body under the item rows. The
views fetch the latest 1000 finished runs; when that page ends inside the
24 h window the views say so ("older runs of the last 24 h are listed in
Activity") instead of claiming the whole window. Tree groups follow the data
(a group opens while it has active runs); only the operator's own toggles are
remembered. Each
run can be linked to an item; a run's `steps` (subagents / worker turns)
expand in place. Mappings: `pages/tasks/run-encodings.ts` (unit-tested).

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
