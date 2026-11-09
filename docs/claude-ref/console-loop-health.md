# Console loop health on a saturated host (2026-11-06)

The console (one uvicorn event loop, in either shipped host: `corvinos-serve` →
`corvin_console.standalone:create_app`, or `corvin-webui.service` → `corvin_gateway.app`,
which mounts the console) must keep answering HTTP even when the host is overloaded. Two
incidents on a 4-vCPU Windows VM with EDR/management agents showed how it stopped doing so:
the port stayed LISTENING, ~200 sockets sat in CLOSE_WAIT, the browser got "connection refused".

**Both hosts wire `loop_health` (2026-11-07).** Until then only `standalone._lifespan` did.
The live Linux install runs the gateway host, so it had anyio's 10 s worker lifetime, no
pre-warm, no stall log and no saturation signal — and its systemd watchdog
(`corvin-watchdog.service`, written by `scripts/setup.sh`) restarted it ten times in one
hour because `/v1/console/healthz` took 5–7 s on three probes 20 s apart while every request
was still served. `gateway._lifespan` now calls `install_early()` first and
`await install_serving(app.state)` right before it yields, exactly like standalone;
`core/console/tests/test_loop_health.py` pins both call sites.

## What starved the loop

| Cause | Fix | Where |
|---|---|---|
| Thread creation costs 0.2–24 s on that host; anyio's idle workers exit after 10 s, so nearly every `run_in_threadpool` started a thread ON the loop | idle anyio workers are never retired (any finite `MAX_IDLE_TIME` lets a quiet night empty the pool; the limiter still bounds it), 8 anyio workers + 8 default-executor threads are pre-started on distinct threads after boot, a console-created default executor is sized to 32 | `corvin_console/loop_health.py` (`tune_worker_threads`, `prewarm`); `install_early` first in `standalone._lifespan`, watchdog + pre-warm via `install_serving` right before it yields |
| Console ran at BELOW_NORMAL priority (inherited from its launcher; measured on the live install) | ABOVE_NORMAL on Windows, best-effort (never HIGH/REALTIME; child processes such as the `claude` CLI do not inherit it) | `loop_health.raise_process_priority` |
| `os_turn.*` chain audits written synchronously from the chat stream | awaited `asyncio.to_thread`; a cancel landing on the await lets the write finish and pairs an already-written `os_turn.started` with `os_turn.completed(-1)`; the CLI-spawn site, the final completion, the TDE path and all cancel handlers stay synchronous | `chat_runtime._os_audit_off_loop` |
| Anomaly scan read the whole 112 MB chain every 5 min | `max_scan_bytes` = 24 MiB | `core/learning/anomaly_monitor.py` |
| Boot healer `rglob` over every session file | bounded `os.scandir` walk, depth 3 | `aco/boot_healer._walk_workdirs` |
| Per-request licence reload (verify + monotonic-clock write + `grace period elapsed` log/audit every 5 s) | at most every 30 s from the request path; a changed/new/removed `session.key` or `global/license.key` goes through at once; a skipped request still waits for a reload in flight; `/license/apply` is unaffected; expiry/grace/revocation are re-checked every 30 s instead of 5 s | `auth._maybe_reload_license` |
| **KPI collector re-parsed the WHOLE chain every 15 s** — `audit_metrics.aggregate(since=None)` walked all 500 MB / 1.5 M records AND ran a full `verify_chain`; each pass took 7–10 s of GIL-heavy JSON parsing in an executor thread, the loop stalled for the whole pass, `/healthz` took 7–10 s every ~26 s (Linux, 2026-11-07; the `kill -USR2` dump caught `collector_daemon._collect_tenant_sync → render → aggregate → _project → _iter_events` mid-walk) | the all-time snapshot is INCREMENTAL: a per-chain cursor (inode + head fingerprint + byte offset + snapshot + open-run tracker) projects only the bytes appended since the last pass, whole lines only (a half-written record waits for the writer), and starts over on a replaced/truncated chain; the intact gauge comes from `verify_chain_incremental` (same verdict, proven prefix re-hashed, ADR-0640 R4); every chain walk yields the GIL every `PACE_EVERY_LINES` lines. The one-time first pass still parses the whole chain once per process | `corvin_gateway/audit_metrics.py` (`_aggregate_all_time`, `_ChainCursor`, `PACE_EVERY_LINES`) |
| Integrity scan read the whole chain to look at its last 500 lines (`read_text().splitlines()[-500:]`: 1–2 GB transient heap + a GIL-holding decode/split at boot and every 300 s) | reads the tail from the end, ≤ 16 MiB | `aco/integrity_monitor._tail_lines` |
| Restart hung in "Waiting for connections to close" for the unit's TimeoutStopSec (90 s) and ended in SIGKILL: uvicorn waits for every open connection and a chat WebSocket is open as long as a tab is; the lifespan's shutdown (live-turn finalisers, turn-recovery timestamp) never ran | `--timeout-graceful-shutdown 15` in every writer of the corvin-webui command | `ops/launcher/service_entry.py::_webui_command`, `corvinOS/installer/core.py`; pinned by `tests/test_webui_command_graceful_shutdown.py` |
| Plugin health poll ran ON the loop: `health.HealthCollector._loop` called `poll_once()` inline — every plugin's `health_check` under a 2 s deadline (a thread the caller waits on) plus the audit emit — 2+ s per poll | `await asyncio.to_thread(self.poll_once)`; still awaited, order unchanged | `core/plugins/corvin_plugins/health.py`; `core/plugins/tests/test_health_collector.py::LoopStaysFreeTests` |
| Every audit event re-executed `bridges/shared/paths.py`: `audit_path()` → `_load_tenant_audit_chain()` → `exec_module` on EVERY call (and `_load_corvin_home()` the same) — a module import per chain write, 2+ s on the loop under GIL contention | the sibling module is executed once per process (`_paths_module()`); its functions read `CORVIN_HOME`/`CORVIN_TENANT_ID` at call time, so nothing observable changes | `corvin_operator/bridges/shared/audit.py`; `tests/security/test_audit_paths_loader_cached.py` |
| Chat WebSocket refused BEFORE `accept()`: the handshake answered HTTP 403, the browser saw close 1006, never 4401/4404, and chat-registry.ts reconnected every 30 s forever ("reconnecting" for 1.5 h on a chat the 50-per-tenant cap had removed) | accept, then close with 4401 (no session) / 4404 (no such chat); the frontend's existing branches show the message and stop | `routes/chat.py::chat_stream`; `core/console/tests/test_chat_ws_close_codes.py` |

## Saturation signal

`loop_health.start_watchdog` starts ONE daemon thread, after the boot and the pre-warm
(so neither is reported): every 5 s it schedules a callback on the loop and measures how
long the loop takes to run it; every 30 s it times a thread start+join. A lag over 2 s
writes the loop thread's stack (code locations only, no locals, no request data) to
`<CORVIN_HOME>/logs/loop-stall.log` (at most once a minute; rotated to `.1` at 2 MiB).

`loop_health.host_saturated()` is true for 120 s after a loop lag > 2 s, or after 3 of
the last 4 thread-start probes took > 1 s (the incident host's MEDIAN was 0.16–0.3 s, so
only a sustained, severe state counts). Only OPTIONAL work asks it:

- KB projector tick: at most every 30 s instead of 2 s (`kb_projection._loop`)
- starting a NEW SkillForge fork (`corvin_operator/skill_creator/autonomous.py`) — the
  canary verdicts in the same pass, incl. the `gates_failed` rollback, always run

Audit, tripwires, the boot healer's integrity scan and the learning brake (anomaly
monitor, ADR-2304 freezes) never ask it. Under pytest `install_early` / `install_serving`
do nothing (the functions are tested directly).

`install_gc_pause_log` (part of `install_early`) logs every cyclic-GC pass over 0.5 s with
its generation and duration: a full collection on a multi-GB heap stops every thread and
no thread stack can name it. On 2026-11-07 it stayed silent while the stalls were measured,
which ruled GC out and left the chain walk.

## External watchdog (systemd, Linux)

`corvin-watchdog.service` probes `/v1/console/healthz` every 20 s and restarts
`corvin-webui.service` after 3 consecutive failures. **Slow is not dead:** the probe is
`curl -fs --connect-timeout 5 -m 30` — a console that is not listening fails in 5 s, a
listening one gets 30 s to answer. It was `-m 5`: on 2026-11-07 the loop stalled 2–7 s
roughly every 20 s (healthz 6.7 s, then 6 ms), three probes in a row hit a stall, and the
restart dropped every chat WebSocket and cost ~2 min of boot at 100 % CPU and 2 GB RSS —
after which the next three probes could fail again. Ten restarts between 12:34 and 13:34,
each logged `attempt 1`, because a single healthy probe in between resets the attempt
counter. The frontend showed this as a constant "reconnecting". Only ~90 s without any
answer restarts the console now. `scripts/setup.sh` is the source of the unit; the test
above pins the probe line.

## Supervisor

`<CORVIN_HOME>/bin/corvin-ensure-console.ps1` (generated by `update.ps1`) never starts a
second console while a process listens on the port; after 10 minutes without an HTTP
answer from the same listener it stops it and starts one — but only if that listener's
command line is a CorvinOS console (`corvin_console`/`corvinos-serve`/`corvin_gateway`);
any other program on the port is logged once and left alone. See
[windows-installation-guide.md](../windows-installation-guide.md#autostart-always-on-mode).

## Diagnosing the next stall

1. `kill -USR2 <console pid>` — `loop_health.install_thread_dump_signal` registers a
   `faulthandler` dump: EVERY thread's stack (code locations only) goes to stderr, i.e.
   `journalctl --user -u corvin-webui`. It works from the signal handler in C, so it answers
   while a busy thread holds the GIL or the loop is stalled. Prefer it over py-spy on Linux:
   `py-spy dump` needs ptrace, and `kernel.yama.ptrace_scope=1` (Ubuntu default) denies a
   non-ancestor process without sudo. POSIX only (no `SIGUSR2`/`faulthandler.register` on
   Windows). Never send USR2 to a console built before 2026-11-07: without the handler the
   default action terminates the process — the boot log line `loop health: kill -USR2 <pid>
   dumps every thread's stack` says the handler is in place.
2. `uv tool run py-spy dump --pid <console pid>` (Windows, or with ptrace access) — the loop
   thread must not sit in `Thread.start`, `write_event` or `last_chain_records`.
3. `<CORVIN_HOME>/logs/loop-stall.log` — where the loop was when it stopped.
4. Host side: time `threading.Thread(...).start(); join()` in a loop; > 1 ms median points
   at kernel contention (EDR/inspection hooks), which no console change can remove.
5. `journalctl --user -u corvin-watchdog` — if it says `console unhealthy for 3 probes`
   while `corvin-webui` never logged a crash, the console was slow, not down: find the stall
   (1–3) instead of tuning the watchdog further.

## Long-lived streams end themselves (T-0380)

Uvicorn keeps a graceful shutdown waiting for every open response, and the application is not told that a shutdown started. A stream that
lives for minutes therefore holds every restart for its full `--timeout-graceful-shutdown` (15 s) and is then cancelled, which the journal
shows as a traceback and "Cancel 1 running task". Every SSE route lives at most ~10 s and ends with an `end` event; `EventSource`
reconnects at once (`retry: 1000`). The settings stream (`routes/settings_stream.py`, `_MAX_S = 10`) does the same as the project explorer's
activity stream; the client (`hooks/use-settings-stream.ts`) re-reads every settings query on each reconnect, so a change made while the
stream was down is not lost. Measured live 2026-11-09 with an open stream: the restart took 14 s instead of 25 s and the journal had no
traceback. A new SSE route must not exceed this lifetime. Test: `tests/e2e/test_settings_stream_e2e.py`.
