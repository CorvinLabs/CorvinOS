# Adapter runtime reference

Detailed behaviour of adapter-level resilience and configuration mechanisms.
CLAUDE.md summarises; this file has the full contract.

---

## Hot-reload convention for bridge settings

Settings changes under `operator/bridges/<channel>/settings.json` take
effect **immediately** — no restart. Adapter re-reads per inbox message;
daemons re-read on mtime change.

| What hot-reloads | Where |
|---|---|
| `whitelist`, `pin`, `rate_limit_per_hour`, `local_announce_inbound` | every daemon |
| `chat_profiles` (all fields), `voice_summary_mode`, `progress_updates` | adapter |
| `enabled_chats`, `debug_chats` | WhatsApp / Discord daemon |

**Needs restart:** tokens (`telegram_token`, `discord_token`, etc.), HTTP ports,
structural daemon code changes. After structural changes, note:
"Needs `bridge.sh restart`."

**Must NOT do:**
- READ paths (whitelist check, rate limit, profile lookup) MUST use
  `currentSettings()` (JS) or `_load_channel_settings()` (Python) —
  never the boot-time snapshot.

---

## Inbox dispatch model — turn pool vs. side-channel pool (load-bearing)

Inbound items are read by the poll loop (`INBOX.glob("*.json")`, name-sorted) and
submitted via `submit_inbox_item()`. Two execution pools:

- **Turn pool** `_executor` — `ThreadPoolExecutor(max_workers=MAX_PARALLEL)`,
  default `ADAPTER_MAX_PARALLEL=4`. Normal turns run here, **behind the per-chat
  lock** (`_chat_lock_for(route)`), so messages in one chat stay ordered while
  different chats run in parallel.
- **Side-channel pool** `_sidechannel_executor` — separate
  `ThreadPoolExecutor(max_workers=max(2, MAX_PARALLEL))`. Envelopes flagged by
  `_peek_side_channel()` (`_cancel` from `/stop`/`/cancel`, `_btw`, `_signal`
  from `/sig`, `_observer`) run here **without** the per-chat lock.

The side-channel pool is **separate by design**: a `/stop` must not only bypass
the per-chat lock but also the bounded turn queue — otherwise, when all
`MAX_PARALLEL` turn slots are busy, the `_cancel` would queue behind the very
turn it is trying to abort and the task would run to completion ("chat keeps
going autonomously"). The dedicated pool guarantees `/stop`/`/btw`/`/sig` get a
worker immediately, independent of turn load. Side-channel envelopes also bypass
the stale-message check and the license gate (always acted on).

The stale-message check (default TTL 1h, `ADAPTER_MSG_STALE_TTL_MS`) no longer
drops silently: the user gets a one-line outbox notice ("your message from Nh
ago arrived while I was unavailable — please resend") plus the existing
`bridge.message_dropped_stale` audit event. A silent drop read as "the bot
ignored me" (2026-07-08 incident: a re-injected recovered turn vanished
without a trace).

**Must NOT do:** route side-channel envelopes through `_executor` (re-introduces
the starvation bug) · hold the per-chat lock while dispatching a `_cancel` ·
size the side-channel pool from a shared budget that turns can exhaust.

### In-flight dedup (`_in_flight`) — duplicate-submit protection

`submit_inbox_item()` records `msg_id → (submit-ts, runner-Future)` in
`_in_flight`; the poll loop's re-submission of a file already in flight is a
no-op. The periodic cleanup (`_cleanup_in_flight`, every
`ADAPTER_CLEANUP_INTERVAL`) drops an entry only when it is older than
`ADAPTER_IN_FLIGHT_TTL` (default 1 h) **and** its Future reports done (or was
never attached — failed submit). Entries whose runner is still executing are
**never** dropped, regardless of age.

Why (incident 2026-07-10): the old wall-clock-only TTL dropped the entry of a
still-running >1 h turn; the next poll tick re-submitted the same inbox file
and a duplicate runner queued behind the per-chat lock. At turn end the
original moved the file to `processed/` — the duplicate then crashed with
`FileNotFoundError` ("runner error … No such file"), and in the worse timing
window it would have **re-executed the whole instruction** (same class as the
2026-07-09 double-execution incident). E2E: `test_adapter_in_flight.py`
(red→green verified against the pre-fix code).

**Must NOT do:** reintroduce a wall-clock-only TTL drop for live runners ·
key the dedup on anything but `msg_id` (inbox filename stem).

## Stream-idle watchdog

`ADAPTER_STREAM_IDLE_TIMEOUT` (default 300 s): SIGTERM + session reset + one retry on silence.
`ADAPTER_HEARTBEAT_INTERVAL` (default 90 s): "⏳ Noch dabei …" status during silence.
Set to `0` to disable either. Tests override via env at re-import time.

### Tool-call awareness (`ADAPTER_TOOL_IDLE_TIMEOUT`)

The idle clock (`last_event`) advances only on stream events. But claude's
stream-json protocol emits **no events while a tool/MCP call executes** — a
`tool_result` (`user`) message normalises to nothing in
`ClaudeCodeEngine._normalise_all`. So the silent gap between a `tool_call` event
and the next assistant/result event equals the tool's wall-time. For the
`orchestrator` persona, `delegate_*` calls routinely run for minutes, which the
old short watchdog mistook for a hang and SIGTERM'd mid-flight.

Fix: the loop tracks `last_event_type`. When the most recent event was a
`tool_call`, the watchdog applies `ADAPTER_TOOL_IDLE_TIMEOUT` (default 1800 s;
`0` disables the tool backstop) instead of `ADAPTER_STREAM_IDLE_TIMEOUT`. This
keeps the short hang-detection for the "awaiting tokens" state while letting a
healthy long-running tool/delegation finish, with a finite backstop against a
genuinely stuck tool. Applied identically across the Claude, OpenCode, and
Hermes engine paths. The cancellation message/log distinguishes
`awaiting tokens` from `awaiting tool result`.

E2E coverage: `test_adapter_stream_idle.py` —
`test_tool_call_in_flight_survives_short_idle` (4 s silent tool gap survives a
2 s token-idle) and `test_tool_backstop_kills_genuinely_hung_tool` (a
never-returning tool still dies at the backstop).

### Sticky progress messages + finalize guard (all channels)

`adapter.py`'s `_emit_status()` (`~L9319`) writes `_progress: true` outbox
envelopes while a turn is running (tool-call status lines), and the
heartbeat thread writes `_heartbeat: true` envelopes (`~L3921`) if nothing
else has fired yet. Both carry the turn's `msg_id` so a daemon can
correlate them with the eventual real-reply envelope.

Every bridge daemon (`operator/bridges/<channel>/daemon.js`, or
`handler.js` for Signal/Teams) applies the same two-part mechanism instead
of relaying each envelope as a brand-new message:

1. **Sticky edit-in-place** — the first `_progress`/`_heartbeat` envelope
   for a chat sends one message/activity and remembers a platform ref
   (message object, `message_id`, `ts`, Signal send `timestamp`, or Bot
   Framework `activityId`); every subsequent one **edits that same
   message** instead of sending a new one (Discord `Message.edit()`,
   Telegram `editMessageText`, Slack `chat.update`, WhatsApp/Baileys
   `sendMessage({..., edit: key})`, Signal `edit_timestamp` on `/v2/send`,
   Teams `TurnContext.updateActivity()`). When the real reply is ready,
   the sticky message is deleted/remote-deleted first so the chat shows a
   clean final answer.
2. **Finalize guard** — the shared outbox dir is processed in alphabetical
   order, so `{msg_id}_00.json` (the real reply) can sort **before**
   `{msg_id}_hb.json` / `{msg_id}_sNN.json` (heartbeat/progress). Once a
   daemon has delivered the real reply for a `msg_id`, it marks that
   `msg_id` finalized (60 s TTL) and silently drops any further
   `_progress`/`_heartbeat` file for the same `msg_id` — otherwise a late
   status line could land in the chat *after* the answer, reading as the
   agent talking to itself.

Both pieces of bookkeeping (the sticky-ref map and the finalized-TTL map)
are the same primitive across every daemon:
`operator/bridges/shared/js/sticky_progress.js` (`makeStickyProgress()`).
Each daemon supplies its own platform I/O (edit/send/delete); the module
itself does none. Unit tests: `shared/js/test_sticky_progress.js`. Per-daemon
wiring is covered by `<channel>/test_sticky_progress_wiring.js` (structural,
for the daemons that construct a live client at require-time) or exercised
directly against `handler.js` (Signal, Teams — `test_signal_daemon.js`,
`test_teams_e2e.js`).

**Must NOT do:** drop the finalize-guard check before the edit/send
dispatch · let a daemon fall back to "one new message per heartbeat"
instead of sticky-editing · let the finalized-TTL map grow unbounded.

---

## Transient HTTP-error reset (adapter-self-heal)

The adapter retries once when the engine surfaces a transient API failure
(HTTP 400/408/429/500/502/503/504/529 or the symbolic tokens `rate_limited`,
`overloaded_error`, `internal_server_error`, `service_unavailable`,
`request_too_large`). Classifier: `model_selector.is_transient_http_error()`.

Connection-level failures are transient too (added after incident
2026-07-10, where a local network outage killed a running turn with zero
retries): `unable to connect`, `connection refused/reset/timed out`,
`connection error`, `getaddrinfo`, `enotfound`, `eai_again`, `econnrefused`,
`econnreset`, `etimedout`, `enetunreach`, `network is unreachable`,
`name or service not known`. These never reached the API, so they retry
**with the session preserved** (they are deliberately NOT in
`_SESSION_CORRUPTING_TOKENS`). A short blip heals on the single retry; a
long outage surfaces the error to the user after the retry fails.

Known trade-off (same exposure as the pre-existing 429/5xx policy): the
retry re-runs the whole prompt, so tools already executed before a
mid-turn connection loss can run twice. Bounding that would require
retrying only when the failure precedes the first tool_call event —
backlog, not done here.

**Session wipe vs. retain — critical distinction:**

| Error type | Session wiped? | Reason |
|---|---|---|
| `400` / `api_error_status` | **Yes** | `--continue` session likely broken |
| Stream idle timeout | **Yes** | subprocess hung; fresh start needed |
| "session" in error text | **Yes** | explicit corruption signal |
| `429` / `5xx` / rate-limit tokens | **No** | pure API transient, local state intact |

`is_session_corrupting_http_error()` (in `model_selector`) governs the
wipe decision. **429 and 5xx errors retry with the session preserved** so
the conversation context is not lost on transient API pressure.

429 / `retry-after: N` triggers a `parse_retry_after_seconds()` sleep (default 8 s,
clamped [5, 120]) BEFORE the retry so a rate-limited retry is not burned
immediately. Single retry budget — if the second attempt also fails, the error
surfaces to the user.

Idle/session-corruption resets require `has_session` (a hang on a fresh subproc
tends to hang again). HTTP-transients retry whether or not a session existed,
since the upstream is unhappy, not the local state.

`ClaudeCodeEngine` drains stderr in a daemon-thread (`_STDERR_TAIL_CHARS = 4096`).
Naked HTTP-status errors (`error == "400"`) and short symbolic tokens get the
last 500 stderr chars appended via `_enrich_naked_error`, so the journal entry
is actionable instead of just a status code. The drain thread also prevents
the stderr pipe buffer from filling and stalling the CLI subprocess.

**Must NOT do:**
- Don't fold idle-timeout into `is_transient_http_error` — the `has_session`
  guard differs; an idle-hang on a fresh subproc should NOT retry.
- Don't wipe session state on 429 / 5xx — that silently destroys conversation
  context. Only 400 / `api_error_status` / idle / session-keyword warrant a wipe.
- Don't add 5xx codes you can't actually observe to `_TRANSIENT_HTTP_CODES`;
  every entry should be backed by either a production log or an E2E test
  case (see `test_adapter_http_reset.py`).
- Don't let `stderr_tail()` write to the audit chain — observability is
  best-effort, never load-bearing.

---

## Per-chat profiles (layer 1)

Default without `chat_profiles`: max-open (`--dangerously-skip-permissions`, all tools).
`chat_profiles` is the **opt-in list of exceptions** for individual chats to be more restrictive.
`permission_mode` values: `default`, `plan`, `acceptEdits`, `bypassPermissions`.

**Must NOT do:** A `"default"` key inside `chat_profiles` restricts EVERY chat —
almost always a mistake.

---

## Notification relay (layer 3)

If `<repo>/.corvinOS/voice/relay.json` has `enabled: true`, Notification/SessionStart
hooks from the desktop are forwarded to your phone via the configured bridge.
Bridge must be running; no additional setup needed (hook registered in `hooks/hooks.json`).

---

## Voice-Mode TTS API-Key lookup

Canonical location: `~/.config/corvin-voice/.env` (mode 0600).
Accepts `OPENAI_API_KEY` or `OPENAI_APIKEY`. Lookup order:
canonical → service.env → repo walk-up → `$PWD/.env` → `$HOME/.env`.

**Must NOT do:** Don't add a candidate that walks across project boundaries.

---

## Persona-Rework v0.9 — uniform open pattern

All bundle personas use `permission_mode: bypassPermissions`. Differentiation by role
(description, mcp_servers, forge_enabled, tool_namespace, working_dir).
The structural sandbox-boundary is **Layer 10 path-gate**, not permission_mode.

**Must NOT do:**
- Don't reintroduce per-persona `disallowed_tools` for defense-in-depth on
  Bash/Edit/Write — path-gate enforces.
- Don't add new personas without `permission_mode: bypassPermissions`.

---

## `/settings` — single-message config-state dump

`/settings` (aliases `/einstellungen`, `/config`) renders full chat+system configuration.
Implementation: `operator/bridges/shared/settings_view.py` (pure-Python, best-effort).
Three blocks: WORKING/PFADE, SESSION, SYSTEM.

**Must NOT do:** Don't add sub-commands. Don't pull in PyYAML/Pydantic from the
bridge process. Don't write to audit chain from this aggregator. Every block
degrades to `—` on exception — never fail-loud.

---

## Boot: stale-task reaper (ADR-0080)

On adapter boot, before the main loop starts, the adapter finalizes any task
left in `running` or `pending` state by a previous adapter process that was
SIGKILL'd or crashed.

```
glob: tenants/*/sessions/**/tasks
  → TaskManager(_tasks_dir).reap_stale_running()
  → each orphan gets record_event("task.failed", exit_code=-1, reason="orphaned_on_restart")
```

The glob covers **all** session directories regardless of bridge type (Discord,
Telegram, WhatsApp, web, CLI). The reaper is called once per boot, before any
new task can be created, so there is no TOCTOU race on the status transition.

**Must NOT do:**
- Don't call `reap_stale_running()` during normal operation — it is a boot-only
  sweep and calling it concurrently with active workers would cause double
  terminal events.

---

## Shutdown: chain continuity anchor (ADR-0135 M2)

On clean shutdown the adapter writes `chain_anchor.json` alongside `audit.jsonl`
so the next boot can detect chain truncation or replay:

- **atexit handler** fires on normal exit (return from `main()`) and on
  `KeyboardInterrupt` / `sys.exit()` after `SystemExit` unwinds the stack.
- **SIGTERM handler (graceful drain, 2026-07-09)** does NOT `sys.exit()`. It
  only sets `_shutdown_event`; the main loop sees the flag within one
  `POLL_INTERVAL`, stops accepting new inbox items, and **drains in-flight
  runs** for up to `ADAPTER_DRAIN_TIMEOUT` (default 90s). If all runs finish it
  returns 0 (atexit writes the anchor); if the budget is exhausted it SIGTERMs
  the remaining engine process groups, writes the anchor manually, and
  `os._exit(0)`. The old handler called `sys.exit(0)` directly, which joined
  the non-daemon executor workers still streaming a `claude` run — the process
  hung until systemd's `TimeoutStopSec` SIGKILLed the whole cgroup, crashing
  every active session with `exit_code=143`. The unit now sets
  `TimeoutStopSec=120` (> the 90s drain budget) and `KillMode=mixed`. The
  handler still does NOT call `write_chain_anchor()` before the drain —
  `_write_lock` is non-reentrant and may be held on the main thread.

Path resolution (tenant-aware, mirrors `self_test.py`):

```
VOICE_AUDIT_PATH env  →  use directly
else: CORVIN_HOME / tenants / <current_tenant()> / global / forge / audit.jsonl
anchor = audit.jsonl.parent / chain_anchor.json
```

Verification happens in two complementary steps:

1. **Self-test** (`_check_chain_anchor()`) calls `verify_chain_anchor(..., emit=False)` —
   pure diagnostic, no audit events (CLAUDE.md "no side-effects in checks" rule;
   required for healthcheck idempotency).
2. **Boot-only** call in the adapter's boot sequence (after self-test) calls
   `verify_chain_anchor(..., emit=True)` — this emits `audit.chain_continuity_break`
   CRITICAL when a breach is confirmed (ADR-0135, GDPR Art. 32). Only the "failed"
   status emits; "ok" and "absent" are silent (already surfaced in CheckResult).

**Must NOT do:**
- Don't call `write_chain_anchor()` from inside `_sigterm_handler` — deadlock
  risk if `_write_lock` is held on the main thread.
- Don't pass `emit=True` from the self-test check — that pollutes the audit
  chain on every `bridge.sh doctor` or Docker HEALTHCHECK invocation.
- Don't remove the boot-only `verify_chain_anchor(emit=True)` call — it is
  the only path where `audit.chain_continuity_break` CRITICAL is emitted.
- Don't add a separate SIGKILL handler — SIGKILL is unblockable; the anchor is
  absent (WARNING at next boot, not CRITICAL).

---

---

## Auto-update — tag-based release tracking

Runs on `bridge.sh up/restart/fg` and `SessionStart` hook. **Tag-only strategy**
(`v*` semver tags). Skip conditions (any one): `.corvin/no-auto-update` marker,
`autoupdate: false` in config.json, dirty tree, fetch fail, no tags, HEAD already
on latest tag, HEAD has commits past latest tag (dev tree).

**Must NOT do:**
- Don't switch to branch fast-forward — tag-only is the explicit contract.
- Don't add `--force` or auto-stash — dirty-tree skip is the safety guard for
  uncommitted work.
- Don't drop the "HEAD has commits past latest tag" check — protects dev trees.
- Don't run `npm install`/`pip install` from the autoupdate hook.
- Don't move `maybe_autoupdate` to `cmd_doctor`/`cmd_status` (read-only paths).

---

## Bridge-daemon network-outage resilience (Discord)

A local uplink outage (DNS dead — e.g. hotspot drop, incident 2026-07-10)
produces the same surface symptoms as a Discord-side failure, but requires
the **opposite** policy: connection-level errors never reached Discord, so
they consume no IDENTIFY/rate budget and may be retried fast, while HTTP/API
errors keep the conservative ladder (a stale Cloudflare 503 once caused a
14-restart storm that locked the bot token at the edge).

Shared classifier: `shared/js/net_probe.js` —
`isNetworkError(msg)` (syscall-level signatures: `getaddrinfo`, `ENOTFOUND`,
`EAI_AGAIN`, `ETIMEDOUT`, `ECONNREFUSED`, `ECONNRESET`, `ENETUNREACH`, …) and
`networkUp()` (DNS probe of `discord.com`, 3 s timeout, injectable resolver
for tests). Consumers in `discord/daemon.js`:

| Mechanism | Behavior when uplink is DOWN | Behavior when uplink is UP |
|---|---|---|
| `loginWithBackoff` | connection-shaped error **and** probe confirms offline → probe every 15 s, retry login immediately on recovery; ladder counter NOT advanced | ALL failures take the 60 s→5 m→15 m→30 m→60 m ladder — including connection-shaped ones (an `ECONNRESET` from a Cloudflare edge ban is remote-caused and may have consumed an IDENTIFY; the error signature alone cannot distinguish local from remote, the probe is the gate) |
| stuck-reconnect detector (3 strikes/60 s) | strikes reset, no exit — discord.js's own resume loop keeps running and resumes without a fresh IDENTIFY | 3 strikes without resume → exit 2 for a systemd restart |
| zombie watchdog (3×60 s) | strikes frozen (offline ≠ silent half-connect) | not-READY accumulates strikes → exit 2 |
| outbox poller | `preCheck: client.token != null` — no REST sends before login; files wait in the outbox | normal delivery |

`shared/js/outbox.js` additionally dedups send-failure log lines (same file +
same message logged once per 60 s instead of twice per second — the incident
produced 1000+ identical journal lines while waiting out an offline login).

Unit tests: `shared/js/test_net_probe.js`, `shared/js/test_outbox_poller.js`.

**Must NOT do:**
- Don't take the fast login path on error signature alone — the probe must
  CONFIRM the uplink is down, or a Discord-side `ECONNRESET` bypasses the
  IDENTIFY-budget ladder with an unbounded 15 s retry loop.
- Don't let confirmed-local login failures advance the API backoff ladder —
  the daemon goes blind for minutes after the network returns.
- Don't exit on reconnect-strikes while `networkUp()` is false — a restart
  trades a resumable gateway session for a blind login loop.
- Don't classify HTTP/API failures (rate limit, 5xx, `TOKEN_INVALID`) as
  network errors — the IDENTIFY-budget protection depends on the split.
- Don't remove the outbox `preCheck` — pre-login REST sends always throw and
  spam the journal at tick frequency.
