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

---

## Voice summary — always a human summary, always the profile language (2026-07-24)

The spoken voice note is now **always** an LLM summary rendered in the profile
language. Two long-standing behaviours that broke this were removed:

- **No verbatim short-circuit.** Both `adapter.py::build_voice_summary` (the
  `len(text) <= max_chars` branch) and `summarize.py` (the in-budget fast-path)
  used to speak a reply as-is, markdown-stripped, whenever it fit the ~400-char
  budget — in its source language, never humanised. That was the confirmed
  "reads the whole thing out loud, in English" symptom (short replies are the
  common case). Both are gone; all non-`<voice>` text flows through the
  summarizer, which keeps short input short ("never pad"). The only fast path
  left is the assistant's own `<voice>…</voice>` block (already a hand-written
  spoken summary).
- **Language is hard-pinned for every locale, de/en included.** `summarize.py`
  now emits an explicit `OUTPUT LANGUAGE` directive (`i18n.language_directive`)
  for **every** code, not just non-de/en; callers (`adapter.py`, `routes/voice.py`)
  always pass `--output-language` with the profile-resolved language. Previously
  de/en relied only on the base prompt's native prose, so an English answer for a
  German-pinned user drifted to English. `_resolve_voice_output_language` keeps
  the explicit profile `display_language` authoritative; text auto-detect is only
  the fallback for a profile with no pin.

**Degraded fallback is bounded, never full text.** When *no* LLM backend is
reachable (no Claude auth AND Hermes down/cold), `summarize.py` cannot summarise.
It used to return `naive_truncate` = the whole answer whitespace-collapsed —
which is exactly a verbatim readout. It now hard-caps that to `max_chars` via
`_cap_to_budget` (whole sentences up to the budget, at least one, hard-cut only a
single overrunning sentence). This is still a degraded result — it cannot
translate — but it is short and bounded.

**The last-resort generator no longer deletes warnings (2026-07-27).**
`summarize_smart.py` / `voice_summary_smart.py` is the template generator used
only when an install has no `summarize.py` at all. It works by CLASSIFYING the
response — work type, scope, risk, first sentence — and narrating from those
labels, and a label cannot carry an instruction. So an operational warning in the
middle of a response was simply not represented anywhere and vanished:

> in  `"Fixed memory leak in worker pool. CRITICAL: Workers must be restarted
>      after deployment. … it will cause a hang."`
> out `"Alright, deployment is no longer in our way. Spotted an issue and patched
>      it. Fixed memory leak in worker pool …"`

Voice is the surface where that is unrecoverable — there is nothing to scroll
back to. `ResponseAnalysis.critical_warnings` now carries such sentences
**verbatim** (post secret-stripping, max 3, max 220 chars each), and they are
spoken **second, right after the opening**: both truncations in this pipeline cut
from the end, so position is the guarantee. A response carrying a warning also
suppresses the celebratory opening — "Great news!" ahead of a DANGER notice works
against the warning — and is never classified `risk_level: trivial`.

Three related defects in the same module, fixed with it: the work-type classifier
matched substrings, so `"handlers"` matched `"handle"` and a refactor was reported
as a fix (now whole-word, and the EARLIEST mention wins rather than whichever
branch is written first); `polish_for_audio` defaulted to `lang="de"` while every
template in the module is English, emitting *"the REST Programmierschnittstelle
issue in the Kommandozeile"* (now defaults to `en`; the one production caller
always passed `lang` explicitly and is unaffected); and fragments lifted out of
the response lost their terminator to `re.split(r'[.!?]+')`, so TTS read them as
one unbroken clause.

**Unchanged, and still true:** this generator's scaffolding is English-only. With
`--lang de` a German warning is now carried verbatim, but the sentences around it
are English. That is the documented reason it was demoted to last-resort on
2026-07-24 and is not fixed here — the LLM path is primary.

**Budgets must fit the backend, not just the parent cap (VOICE-F8, 2026-07-25).**
The degraded path is only rare if the CLI backend actually gets a usable budget.
VOICE-F7 fixed a cap overflow by *shrinking* the child budgets (CLI 90 s → 45 s)
so CLI + Hermes fit inside a 120 s parent cap. Measured `claude -p` latency for a
real summary call (10.5 KB system prompt, haiku) is 23 s / 27 s / 75 s / >180 s
across five runs — median ≈ 50 s. The 45 s budget therefore lost most of the
time, and **23 of 23 field summaries in ~27 h degraded to near-verbatim** — the
exact behaviour this section says was removed. The fit-the-cap guard test stayed
green throughout, because summing under the cap is necessary but not sufficient.

Current budgets are derived bottom-up from that measurement, and the parent caps
were raised to fit them: main ladder CLI 90 s + Hermes 45 s = 135 s inside a
150 s cap (`adapter.py::build_voice_summary`, `routes/voice.py::
_TTS_SUMMARIZE_TIMEOUT_S`); annex ladder CLI 40 s + Hermes 35 s = 75 s inside
90 s. `summarize.py::_MEASURED_CLI_P50_S` records the measurement and
`test_summarize.py::test_cli_budget_covers_measured_latency` fails if a budget
drops to or below it. When touching these numbers, **re-measure first** — a
budget under the median silently disables a backend without failing anything.

**Keeping the backend warm.** Beyond the budgets, the bounded degraded path
should only fire for an active user on a COLD local model at the first voice
note after boot (a cold qwen3:8b load overruns the summary timeout). Two things prevent
that: (1) the L44 house-rules classifier runs on *every* task with `keep_alive`
30m and uses the *same* model the summary resolves to (`_resolve_default_model`),
so steady-state it is always resident; (2) the adapter fires a fire-and-forget
`summarize.py --prewarm` in a daemon thread at boot (`CORVIN_VOICE_PREWARM=0` to
opt out) to cover the boot gap before the first task. Both are fail-soft: a
Claude-CLI-only or cloud install has no Ollama, so prewarm is a no-op and the
CLI backend serves the summary.

The console `/voice/segment` "Read the full answer aloud" button is a deliberate
exception — it reads the raw answer verbatim by design and is a separate,
explicit user action, not the automatic voice note.

**Must NOT do:**
- READ paths (whitelist check, rate limit, profile lookup) MUST use
  `currentSettings()` (JS) or `_load_channel_settings()` (Python) —
  never the boot-time snapshot.

---

## Disconnecting / deleting a channel connection

`POST /bridges/{channel}/disconnect` drops a connection so the channel can be
set up again — previously a credential could only be created, never removed, so
moving a bridge to a different bot or revoking a leaked token was impossible
from the console.

| `mode` | Removes | Keeps |
|---|---|---|
| `disconnect` (default) | credentials + pairing state | whitelist, `pin`, rate limit, `chat_profiles`, `lang`, routing |
| `delete` | the whole `settings.json` | nothing (a `.bak` is written first) |

Both stop the daemon and set `enabled: false`. Gated like every mutation
(cookie + CSRF + re-auth) and audited as `bridge.connection.{disconnect,delete}`.

**Load-bearing details — each one is a way for "delete" to silently not delete:**

- **Stop the daemon first.** Daemons hot-reload `settings.json` and some
  re-persist credentials (WhatsApp's `saveCreds`), so cleaning the file under a
  live daemon can be undone a second later. The route stops before it writes.
- **Clean BOTH locations.** The zero-config setup endpoints write via
  `_resolve_bridges_dir()` (source/`_vendor`) while `_settings_path()` is the
  runtime path, and `_read_settings()` falls back runtime → legacy. Clearing one
  leaves a working credential behind.
- **`pin` is a preference, not a credential.** It matches `_SECRET_KEY_HINTS`
  (so it is masked on GET, correctly) but it is the operator's access PIN — it
  must survive a disconnect and must never count as proof of a connection. See
  `_PREFERENCE_SECRET_KEYS`.
- **Pairing state lives outside `settings.json`.** WhatsApp keeps linked-device
  credentials in `<channel>/auth/` (Baileys `useMultiFileAuthState`); the route
  archives that directory to `auth.bak.<stamp>` rather than leaving it, or the
  daemon re-attaches to the old account.

**`configured` means "has a usable credential", not "a settings file exists".**
`GET /bridges` derives it from `_channel_connected()`. The old file-existence
test reported a preferences-only file as configured — on a real install
`telegram` was exactly that — and after a disconnect every channel would still
have claimed to be set up. `has_settings` is reported separately so the UI can
distinguish "never configured" from "disconnected, preferences kept".

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
| outbox poller | `preCheck: client.isReady()` — no REST sends before the gateway is READY; files wait in the outbox | normal delivery |

`shared/js/outbox.js` additionally dedups send-failure log lines (same file +
same message logged once per 60 s instead of twice per second — the incident
produced 1000+ identical journal lines while waiting out an offline login).

### Delivery contract: return = delivered

`startOutboxPoller` unlinks the envelope whenever `sendFn` **returns normally**.
A `sendFn` that returns without having delivered therefore destroys the message.
Every not-delivered path MUST throw so the file stays queued.

This was violated in incident 2026-07-25: `sendDiscord` had
`if (!ch) { log('channel … not found'); return; }`. After a reboot the poller ran
~300 ms before the READY event (`preCheck` only checked `client.token`, which the
REST manager receives earlier), `channels.fetch()` hit an empty channel cache and
returned null, and a finished 1304-char reply was silently unlinked. Both halves
are fixed: the gate now waits for `isReady()`, and the null-channel path throws.

### Dead-letter path (opt-in)

Infinite retry is only correct for *transient* failures. Permanent ones
(non-snowflake `chat_id`, deleted channel) accumulated to **328 envelopes** by
2026-07-25; at ~200 ms per failed REST call a full poll pass took ~65 s, so every
real reply queued behind the poison backlog.

`startOutboxPoller` takes three optional parameters:

| Parameter | Meaning |
|---|---|
| `deadLetterDir` | Where to park undeliverable envelopes. **Unset → old behavior (retry forever), unchanged.** |
| `isPermanent(err)` | Channel-specific classifier; `true` retires on the *first* failure without retrying |
| `maxAttempts` (default 10) | Fallback budget for unclassified errors |

Discord sets `deadLetterDir: outbox/dead` and `maxAttempts: 20` (≈10 s of ticks).
`isPermanent` covers only errors that are permanent *by construction* — 50035
Invalid Form Body (non-snowflake `chat_id`), 50006 empty message, 40005 payload
too large. "Not reachable right now" codes (10003 Unknown Channel, 50001 Missing
Access, HTTP 403/404) are deliberately **excluded**: a Discord outage or a
briefly-removed bot produces those too, and retiring a finished reply on attempt
#1 would recreate the data loss this mechanism exists to prevent. They leave the
queue via the attempt budget instead. The envelope is moved byte-identical
(re-queue by moving it one level back up) next to a `<file>.reason.json` sidecar
recording reason, error, attempt count and timestamp.
`dead/` lives inside `outbox/` but is invisible to the poller and to
`pending_outbox`, both of which only match `*.json` files.

Discord additionally rejects a non-snowflake `chat_id` **locally** in
`sendDiscord`, before `channels.fetch()`, throwing a synthetic error carrying
code 50035 so the same permanent-error path retires it. The API verdict is
identical; skipping the round-trip keeps that traffic off Discord's
invalid-request budget, which is what gets a bot rate-limited at the edge.

### Stall detection: a live process is not a delivering process

Retry, dead-letter and the return-means-delivered contract all assume the send
eventually *settles*. One that never does defeats every one of them: on
2026-07-26 a `sendFn` call hung, `running` stayed `true`, and each following
interval returned at `if (running) return`. The Discord daemon delivered
nothing for 38 minutes — no ack, no `⏳ Noch dabei …` heartbeat, no final reply
— while the process stayed alive, the gateway socket stayed open, `/status`
answered `paired: true`, and **not one line was logged**. Neither the watchdog
(which checks service liveness) nor the operator could see it; the adapter kept
writing envelopes into an outbox nobody drained.

Three parameters, all with safe defaults:

| Parameter | Default | Meaning |
|---|---|---|
| `sendTimeoutMs` | 120 000 | Hard deadline per `sendFn` call. On expiry the poller throws `OUTBOX_SEND_TIMEOUT` and the envelope re-enters the normal retry path. `0` disables. |
| `stallWarnMs` | 300 000 | A tick running longer than this logs `outbox: tick stalled for Ns` (once per 60 s), instead of staying silent. |
| `stallResetMs` | 900 000 | Backstop: force-releases the `running` flag so a hang *outside* `sendFn` can no longer wedge the poller shut. `0` disables. |

The timeout is deliberately generous: the underlying send is not cancellable,
so a call that succeeds *after* the deadline can produce a duplicate when the
envelope is retried. 120 s is far beyond any healthy send, so only a genuine
hang trips it — and a rare duplicate beats a poller that silently drops every
subsequent reply. `OUTBOX_SEND_TIMEOUT` is a string code precisely so numeric
`isPermanent` classifiers can't mistake a hang for a permanent failure; a hang
is transient and rides the attempt budget.

The handle returned by `startOutboxPoller` exposes `stats()` →
`{running, stalled_s, idle_s}`. Discord publishes it as `poller_stalled_s` in
`/status` so an external watchdog has something actionable to poll —
`paired: true` plus an open socket is **not** evidence that anything is being
delivered.

### Tests must never write to the live outbox

`operator/bridges/shared/outbox/` is polled by the *running* daemons every
500 ms. The workflow `deliver`/`ask_human`/`answer` node types write there via
`_write_outbox` (`core/workflows/corvin_workflows/node_types.py`), and that path
was hardcoded to the repo directory — so every test run of those nodes handed
the live bridge a real send job. Cleaning up in `tearDown` does not help: the
daemon grabs the file first. 724 such envelopes, all addressed to the test
placeholder `chat_id: "owner-chat"`, were sitting in Discord's dead-letter dir
by 2026-07-26, each having cost a REST round-trip.

`_write_outbox` now honours **`ADAPTER_OUTBOX`**, the same override
`adapter.py` uses. The repo-root `conftest.py` points it at a tmpdir for every
test (autouse), and the affected suites also set it in `setUp` so the isolation
holds when a file is run directly, outside pytest.

Unit tests: `shared/js/test_net_probe.js`, `shared/js/test_outbox_poller.js`
(the latter pins the return-means-delivered contract, both dead-letter modes,
the send timeout and the stall detector), `discord/test_outbox_hardening.js`
(stall visibility in `/status` + the local snowflake guard).

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
- Don't `await` anything unbounded inside a `sendFn` — a call that never
  settles is the one failure the retry/dead-letter machinery cannot survive.
  Keep `sendTimeoutMs` on; setting it to `0` restores the wedge.
- Don't treat a healthy `/status` as proof of delivery — check
  `poller_stalled_s` and `pending_outbox` together. Both looked fine for the
  entire 38-minute outage.
- Don't let a test write to the live `outbox/` — set `ADAPTER_OUTBOX`. Post-hoc
  cleanup loses the race against a 500 ms poll tick.
