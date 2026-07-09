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

---

## Transient HTTP-error reset (adapter-self-heal)

The adapter retries once when the engine surfaces a transient API failure
(HTTP 400/408/429/500/502/503/504/529 or the symbolic tokens `rate_limited`,
`overloaded_error`, `internal_server_error`, `service_unavailable`,
`request_too_large`). Classifier: `model_selector.is_transient_http_error()`.

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
- **SIGTERM handler** calls only `sys.exit(0)` (raises `SystemExit`, releases
  any held `_write_lock`, then atexit fires).  It does NOT call
  `write_chain_anchor()` directly — `_write_lock` is non-reentrant and may
  be held on the main thread.

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
