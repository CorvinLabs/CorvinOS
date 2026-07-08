# Background-Completion Notifications (notify-on-done)

How a background task that finishes AFTER the originating turn ended reaches the
user in Discord / WhatsApp / Telegram / Slack / Signal. This is the mechanism
behind "I'll let you know when it's done."

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

Remaining limits (documented, not closed): a running background turn is **not** cancelable via `/cancel`/`/stop` from the messenger (the worker is a separate process; the wall-clock deadline is the only stop) — a cross-process cancel is future work; and background tasks deliver **text only** (artifacts/files a bg turn produces are not mirrored).

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
a real result and mostly emitted spurious "All caught up." messages. It now:

- **Primarily** flushes the durable completion queue to the outbox (backup poller).
- Carries `tenant_id` in the wakeup envelope (multi-tenant fix).
- Injects the legacy idle wakeup **only** when `BGW_LEGACY_WAKEUP=1` (default OFF — no more spam). Re-enable for an interactive/persistent-session deployment.

## Tests / proof

- `test_completion_notify.py` — register→done→deliver, exactly-once, per-channel routing, GDPR purge, prune.
- `test_completion_e2e.py` — full chain: completion → shared outbox → **real signal daemon `processOutboxPayload`** → `sendSignal` (send faked).
- `test_bg_task.py` — the `/task` producer: detached worker runs the (fake) engine → `mark_done` → delivered completion carries the real result; the `/task` handler registers the origin + spawns the detached worker + ACKs.
- `test_bg_monitor.py` — delivery via `run_once`, no-spurious-wakeup-by-default, tenant capture.
- `test_scheduler.py::WorkflowOutboxTargetTests` — report lands in shared outbox, not the orphan per-channel dir.
- `test_notification_relay.py` — default outbox = the daemon-polled dir (orphan-path regression guard) + explicit chat_id honoured.

All wired into `operator/bridges/run-all-tests.sh`.
