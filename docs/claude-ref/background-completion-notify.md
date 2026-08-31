# Background-Task Notifications and Supervision

How a background task reaches the user in Discord / WhatsApp / Telegram / Slack
/ Signal — at the END of the work ("I'll let you know when it's done"), WHILE it
runs, and how a run that stopped before finishing is carried through to
completion instead of being reported as a failure.

Three cooperating stores, all in `operator/bridges/shared/`, all polled by the
same two idempotent pollers (the adapter main loop and the `bg_monitor` systemd
timer):

| Store | Module | Answers |
|---|---|---|
| `CORVIN_HOME/pending_notifications/` | `completion_notify.py` | "the task finished, here is the result" |
| `CORVIN_HOME/task_progress/` | `task_progress.py` | "the task is still going, here is where it is" |
| `CORVIN_HOME/task_runs/` | `task_supervisor.py` | "the task stopped early — restart it until it is done, within budget" |

The last two ship dark behind `bridge_task_progress_updates` and
`bridge_task_supervision` (both default **off**; see below).

## The problem it solves

The bridge runs each OS turn as a one-shot `claude -p` subprocess that exits at
turn end. A Claude Code SDK background agent (`Agent` with
`run_in_background=True`) lives inside that process, so it cannot carry a result
across the per-turn boundary; a later `--resume` restores conversation history,
not a dead process's in-flight agent. Three "done" signal paths also each wrote
their envelope into a directory **no messenger daemon polls**:

- `notification_relay.py` wrote to `operator/voice/bridges/shared/outbox` (orphan) — fixed to `operator/bridges/shared/outbox`.
- `scheduler.py` workflow reports wrote to `bridges/<channel>/outbox` (orphan) — fixed to the shared outbox.
- The Task Engine only published completion to in-memory browser SSE.

## The mechanism — a durable, acknowledged queue

`operator/bridges/shared/completion_notify.py` is the backbone. Records live in
`CORVIN_HOME/pending_notifications/<id>.json` (routing PII lives here, NOT in the
task JSONL/audit log — GDPR-safe; `purge_user` honours Art. 17).

| Step | Call | Who |
|---|---|---|
| At task start | `register(task_id, channel, chat_id/to, sender, tenant_id, label)` | the producer, while the messenger context is still in hand |
| At completion | `mark_done(task_id, text, ok)` | the durable executor (task_id only — no PII) |
| Every poll tick | `deliver_ready(shared_outbox)` | the adapter main loop **and** the `bg_monitor` timer |

`deliver_ready` writes a correctly-routed envelope into the shared outbox
(`chat_id` for discord/telegram/slack/signal/email, `to` for whatsapp), then
**acknowledges** the record (marks it delivered). A per-record `O_EXCL` lock
makes the two independent pollers exactly-once — no double send. Delivered
records prune after `CN_DELIVERED_TTL`; abandoned pending records after
`CN_PENDING_MAX_AGE`.

Two pollers by design: the adapter delivers while the bridge polls; the
`bg_monitor` systemd timer delivers even when the adapter is idle/restarting.
Both are idempotent.

## Autonomous / system-initiated background tasks

The backbone is producer-agnostic — it does not matter whether a human (`/task`)
or the system itself starts the work. A full sweep of every autonomous executor
(timers, loops, queues, reapers, `create_task`, detached `Popen`) found:

| Executor | Detached past turn? | Messenger origin? | Notifies? |
|---|---|---|---|
| Scheduler (cron/one-shot: reminders + workflows) | yes | **yes** | **yes** — reminders via inbox re-injection, workflow reports via the shared outbox (Art. 50-marked) |
| `/task` + `bg_task_worker` + console TaskWorkerPool | yes | **yes** | **yes** — via `completion_notify` |
| ACS runs (all callers), `a2a_compute_engine` | no — awaited in-turn | — | replies in-turn |
| Gateway dispatcher | yes | no (peer/API) | HTTP webhook (`spec.webhook`) |
| A2A RemoteTriggerReceiver | no — synchronous | no (peer) | signed response to the peer |
| L25 Compute Worker | yes | **yes** (auto-injected) | **yes** — `WorkerClient.submit_run` attaches a `notify` origin from `CORVIN_CHANNEL_ID`; the worker registers at submit + `mark_done` at the terminal state |
| ACO healing / nerve fibers / boot_healer / integrity / telemetry / heartbeat / ping | yes (timers) | no | audit / console-badge / anonymous telemetry — a messenger origin here would be a **compliance violation** |
| L6 maintenance_loop / cve_surveillance / watchdog / audit-verify | varies | no | maintainer-CLI / syslog / stdout |

Conclusion: notify-on-completion is wired for **every autonomous executor that
has a real chat user** (scheduler + `/task`). The rest either answer a remote
peer, run synchronously in-turn, or are internal self-healing/telemetry with no
human recipient (and telemetry channels must stay anonymous). The one remaining
detached executors now notify when they have a chat user: the scheduler,
`/task`, and the **L25 Compute Worker** (`WorkerClient.submit_run` auto-attaches
a `notify` origin derived from the per-turn `CORVIN_CHANNEL_ID`; the worker
`register`s at submit and `mark_done`s at the terminal state — flat runs; the
pipeline/hac engine paths are a future extension). Runs from non-messenger
callers (console `web:sid`, CLI) inject no origin and stay poll-only.
Deployment requirement: the compute worker and the bridge poller MUST share
`CORVIN_HOME` (the worker's `_cmd_serve` pins it from `--corvin-home`), or the
completion record lands in a tree the poller never reads. Restart-safe: a run
resumed after a worker restart notifies via the recovery path
(`_recover_pending` → `_notify_compute_done`). The uid for GDPR erasure travels
as `CORVIN_ORIGIN_SENDER` on the engine spawn env.

## AI-content marking (single source of truth)

All three delivery paths (adapter reply, `completion_notify`, scheduler
workflow) stamp the EU AI Act Art. 50 §4 provenance block via one shared helper,
`provenance.build_provenance(channel, chat_id, persona)` — so the marking
contract cannot drift between them (`test_provenance.py` locks the shape).

## Delivery contract (all outbound messenger notifications)

- Directory: `operator/bridges/shared/outbox` — the ONLY dir the 7 JS daemons poll (`SHARED = resolve(__dirname,'..','shared')`). `ADAPTER_OUTBOX` overrides it (tests / single-dir deploys).
- Required field: `channel` (must equal the daemon's own channel).
- Routing key: `chat_id` for discord/telegram/slack/signal/email; `to` (JID) for whatsapp.

## Producers

### `/task <instruction>` (alias `/bg`) — the messenger-origin producer

Typed in any messenger. `adapter.process_one` handles it (after the auth/authz
gates, so only whitelisted users spawn work):

1. runs the L44 house-rules gate on the instruction (fail-closed);
2. `completion_notify.register(task_id, channel, chat_id, sender, tenant_id, label)` — captures the origin;
3. spawns `bg_task_worker.py` **detached** (`start_new_session=True`) so it OUTLIVES the turn's one-shot `claude -p` process;
4. ACKs immediately: "🛠️ Running in the background — I'll message you here when it's done."

`bg_task_worker.py` runs the instruction through the SAME fully-gated engine
path a normal turn uses (`adapter.call_claude_streaming` → budget / L34 / L35 /
CLAG / license gates — no compliance bypass), then calls
`completion_notify.mark_done(task_id, result, ok)`. The adapter main loop's
`deliver_ready` then pushes the result to the messenger. No separate worker-pool
daemon is required — the detached process IS the worker.

### `/task` safeguards & known limits

Hardened after adversarial review:
- **Bounded:** wall-clock deadline per task (`CORVIN_BG_TASK_TIMEOUT`, default 1800s) — the worker's watchdog SIGTERMs its own engine subprocess on timeout and reports "timed out", so a wedged turn can never run forever.
- **Rate-limited:** per-sender concurrency cap (`CORVIN_BG_TASK_MAX`, default 3) — `/task` past the cap is refused, preventing a fork-bomb.
- **No PII on argv:** the spec (instruction + routing ids) is passed via a `0600` temp file, not `argv` (which is world-readable in `/proc/<pid>/cmdline`); the worker unlinks it on read.
- **Gated:** runs after the whitelist/authz + license gates; L44 house-rules in the handler AND inside `call_claude_streaming` (L34/L35/CLAG/budget). Audit hash-chain stays intact (the worker inherits `CORVIN_HOME`, cross-process writes serialize on the flock).
- **Marked:** the completion envelope carries the Art. 50 §4 `provenance` block + `_final` flag, like every normal AI reply.

Remaining limits (documented, not closed): a running background turn is **not**
cancelable via `/cancel`/`/stop` from the messenger (the worker is a separate
process) — a cross-process cancel is future work; and background tasks deliver
**text only** (artifacts/files a bg turn produces are not mirrored).

**With `bridge_task_supervision` on, the wall-clock deadline stops being a
verdict.** `CORVIN_BG_TASK_TIMEOUT` still bounds ONE attempt, but the attempt is
recorded as *resumable* with its partial output and the supervisor starts the
next one — see the section below.

## Intermediate updates — `task_progress.py` (flag: `bridge_task_progress_updates`)

`completion_notify` is one-shot: one record, one delivery, at the end. A run that
takes hours therefore produced **nothing** until it finished, and nothing at all
if it never did — the worker even passed `on_status=None` explicitly ("no live
progress spam"). To a user, a multi-hour run was indistinguishable from one that
silently died.

`task_progress` is the same durable pattern applied to the middle of a run:

| Step | Call | Who |
|---|---|---|
| During the work | `emit(task_id, text, kind=…, force=…)` | `bg_task_worker`'s `on_status`, the supervisor, an orchestrator phase |
| Every poll tick | `deliver_progress(shared_outbox)` | the adapter main loop **and** the `bg_monitor` timer |

Routing is **inherited from the task's `completion_notify` record**, so there is
exactly one routing store and a progress update can never be routed differently
from its own completion. A task with no messenger origin (console, CLI) emits
nothing.

The envelope is a **normal message with a unique `msg_id`** — deliberately NOT
the daemon's `_progress` sticky. A sticky is edited in place, dropped once the
turn is finalized, and DELETED when the next real reply lands (see
`discord/daemon.js` `sendDiscord`): right for tool-call chatter inside one turn,
wrong for an out-of-band run whose updates must survive every intervening turn.
`_final` is not set — the completion is still to come. The Art. 50 §4
`provenance` block is stamped via the same shared `build_provenance` helper.

**Rate limiting is the design, not an afterthought** — a wedged loop emitting
every 100 ms would rate-limit the bot at Discord's edge and bury the user. Two
independent per-task bounds:

| Env | Default | Meaning |
|---|---|---|
| `TP_MIN_INTERVAL` | 120 s | Minimum between two DELIVERED updates. Emits inside the window **coalesce** into the pending record, so the user sees the LATEST state, never a backlog. |
| `TP_MAX_UPDATES` | 40 | Hard ceiling of delivered updates per task. Past it emits are counted and dropped; the completion still arrives. |
| `TP_STALE_UPDATE` | 1800 s | An update older than this is stale news and is dropped rather than replayed. |

`force=True` (used for state changes such as a heal/resume) bypasses the
interval but still respects the ceiling. Records carry routing PII, so
`purge_user` honours GDPR Art. 17 alongside `completion_notify.purge_user` and
`bg_monitor.purge_user` — all three are called by `corvin_erasure.py`.

## Supervision — `task_supervisor.py` (flag: `bridge_task_supervision`)

`/task` used to spawn ONE detached worker and hope. Everything that can end that
process ended the work permanently:

| What happened | What the user was told | What was true |
|---|---|---|
| `CORVIN_BG_TASK_TIMEOUT` fired (30 min) | "timed out and was stopped" | the work was mid-flight and had partial results |
| SIGKILL / OOM / reboot | "the background worker stopped without reporting a result" (the `CN_PENDING_REAP` path) | nothing was retried |
| the engine wedged without exiting | *nothing at all* | the record sat pending until `CN_PENDING_MAX_AGE` (7 days), holding a `/task` concurrency slot |

In every case the **instruction was already gone** — the `/task` spec file is
unlinked the moment the worker reads it — so nothing could resume the work even
in principle. Nothing owned "keep going until done".

The run record (`CORVIN_HOME/task_runs/<task_id>.json`) holds what a resume
needs: instruction, routing, profile, attempt history, and the budgets. It is
written by the adapter **before** the first worker starts, so a worker that dies
during start-up is still resumable.

A supervisor tick reconciles every run against reality:

| Observed | Action |
|---|---|
| worker alive, heartbeat fresh | leave it alone |
| worker gone (incl. a **zombie** — the adapter `Popen`s and never waits, and a zombie answers `kill(pid,0)`), or heartbeat stale past `SUP_HEARTBEAT_STALE` | SIGTERM if needed, then RESUME: spawn a fresh worker with a continuation prompt, and tell the user via `task_progress` |
| attempt or wall-clock budget spent | terminal: `mark_done(ok=False)` with an account of what was tried and the last partial output |
| the completion record is no longer `pending` | the work finished — retire the run record, never resurrect it |

**The bounds are the whole design** — an unbounded "restart until done" is a
fork bomb with extra steps:

| Env | Default | Meaning |
|---|---|---|
| `SUP_MAX_ATTEMPTS` | 5 | Total worker launches per run (the first counts as one). |
| `SUP_TOTAL_BUDGET` | 21600 (6 h) | Wall clock across ALL attempts — 5 wedged attempts must not add up to a day of silence. |
| `SUP_LAUNCH_GRACE` | 180 s | Grace after a launch before a worker may be declared dead (covers interpreter start-up + the heavy `import adapter`). Also what stops the tick between `register_run` and the adapter's own `Popen` from double-spawning. |
| `SUP_HEARTBEAT_STALE` | 600 s | Alive-but-wedged threshold. Must stay well above the worker's `CORVIN_BG_TASK_HEARTBEAT` (30 s). |
| `SUP_BACKOFF_BASE` / `SUP_BACKOFF_MAX` | 60 / 900 s | Doubling backoff, armed on BOTH the worker-reported failure and the supervisor's own relaunch — so a process that dies instantly on start-up cannot burn the attempt budget inside one grace period. |

A spawn is serialized by a per-run `O_EXCL` lock, so the adapter loop and the
`bg_monitor` timer can never double-launch. `supervise()` never raises: its
callers also carry the completion deliveries.

`completion_notify`'s dead-producer reap **skips a supervised run**
(`_supervised()`), or it would tell the user the task failed while the resume
that fixes it is already in flight — and mark the record ready, so the real
result would later be dropped by `mark_done`.

### Flag-off is the pre-feature path, exactly

With both flags off the adapter writes **no run record**, and the presence of
that record is the only switch every downstream component reads. The worker
takes its original branch (`on_status=None`, `mark_done` on failure), the
supervisor has nothing to supervise, and `completion_notify` reaps a dead
producer exactly as before. Both states are tested
(`test_bg_task_worker_supervised.py`, `test_task_supervisor.py`).

### Task Engine (secondary producer)

`task_worker_pool.py` calls `_notify_task_done(task_id, ok, summary)` →
`completion_notify.mark_done` at every terminal branch (completed / failed /
cancelled / error), summary = the task's real `result` stream event (≤1500 chars,
metadata fallback). No-op unless a producer registered that task_id, so
console-only web tasks are unchanged. This lets a future messenger→Task-Engine
enqueue notify too, but note the Task-Engine worker pool is not yet wired into
the bridge runtime (see [layer-22-task-engine-m2.md](layer-22-task-engine-m2.md));
`/task` above does not depend on it.

## bg_monitor role change

`bg_monitor.py` was a blind idle-timer that injected a synthetic "deliver
pending notifications" wakeup turn. Over one-shot `claude -p` it could not carry
a real result and mostly emitted spurious "All caught up." messages. `run_once()`
now does three things, in this order:

1. **Supervises** running tasks (`task_supervisor.supervise`) — healing first, so
   a budget-exhausted verdict or a resume notice reaches the user in THIS tick
   rather than 60 s later. This timer is the only thing that heals a stopped run
   while the adapter is idle or restarting, which is exactly when a long
   autonomous run is most likely to be stranded.
2. Flushes the durable **completion** queue to the outbox (backup poller).
3. Flushes the **progress** queue of still-running work.

It also carries `tenant_id` in the wakeup envelope (multi-tenant fix), and
injects the legacy idle wakeup **only** when `BGW_LEGACY_WAKEUP=1` (default OFF
— no more spam). Re-enable for an interactive/persistent-session deployment.

## Tests / proof

- `test_completion_notify.py` — register→done→deliver, exactly-once, per-channel routing, GDPR purge, prune.
- `test_completion_e2e.py` — full chain: completion → shared outbox → **real signal daemon `processOutboxPayload`** → `sendSignal` (send faked).
- `test_bg_task.py` — the `/task` producer: detached worker runs the (fake) engine → `mark_done` → delivered completion carries the real result; the `/task` handler registers the origin + spawns the detached worker + ACKs. **Reachability proof** for the supervisor: driven through the real `adapter.process_one` entry point, `/task` with the flags ON writes a run record carrying the instruction (the spec file is unlinked immediately, so this record is the ONLY thing that makes the work resumable), and with the flags OFF writes none.
- `test_bg_monitor.py` — delivery via `run_once`, no-spurious-wakeup-by-default, tenant capture.
- `test_scheduler.py::WorkflowOutboxTargetTests` — report lands in shared outbox, not the orphan per-channel dir.
- `test_notification_relay.py` — default outbox = the daemon-polled dir (orphan-path regression guard) + explicit chat_id honoured.
- `test_task_progress.py` — an update reaches the outbox the daemon polls, through `bg_monitor.run_once`; coalescing and the two rate ceilings; snowflake `chat_id` stays a string; exactly-once across two pollers; unroutable/stale records dropped rather than retried forever; GDPR purge.
- `test_task_supervisor.py` — a dead / zombie / heartbeat-stale worker is resumed with a continuation prompt; a healthy one is left alone; a finished one is never resurrected; both budgets end in an honest failure that says what was tried; backoff and the `O_EXCL` spawn lock; **a resume spawns a REAL OS process**; `bg_monitor.run_once` drives it; flag-off never resumes and `completion_notify` still reaps an unsupervised dead worker.
- `test_bg_task_worker_supervised.py` — the REAL `bg_task_worker.py` driven as a REAL subprocess (only the engine is stubbed): heartbeat, progress relay, timeout/crash recorded as resumable WITHOUT a premature failure message, the continuation prompt carrying the original goal, and flag-off reporting failure immediately as it always did.

All wired into `operator/bridges/run-all-tests.sh`.
