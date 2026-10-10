# Adapter runtime reference

Detailed behaviour of adapter-level resilience and configuration mechanisms.
CLAUDE.md summarises; this file has the full contract.

---

## Hot-reload convention for bridge settings

Settings changes under `<corvin_home>/bridges/<channel>/settings.json` (ADR-0008
§8.3; the legacy in-repo `corvin_operator/bridges/<channel>/settings.json` only
while no canonical file exists) take effect **immediately** — no restart.
Adapter re-reads per inbox message; daemons re-read on mtime change.

**One resolver: `paths.resolve_bridge_settings_file(channel)`** — the file the
channel's daemon reads, in the daemon's order (canonical if it exists, else
legacy; `ADAPTER_BRIDGES_DIR` overrides both for tests). The adapter
(`_load_channel_settings`, `_sender_is_operator`), `roles.py`, `disclosure.py`
and `phase3_cli.py` all call it, and so does the console's chat-settings editor
(`routes/chat_settings.py`, which read AND wrote the legacy file — it now
re-reads the file under its lock before merging a PATCH, `_update_channel`,
and writes it 0o600 from the first byte because it holds the bridge token). The resolver
is mirrored byte-identically in `forge/forge/paths.py` and `cowork/lib/paths.py`,
because `import paths` resolves to a different file per process — the forge copy
in the console, where the route answered 500 until the mirror existed. Until 2026-09-27 those four composed the
legacy path by hand while the daemon read the canonical file: the adapter's
whitelist re-check saw no whitelist (fail-open, "no whitelist configured" on
every message), `roles.is_intrinsic_owner` treated the empty whitelist as
DEV-mode and made EVERY sender an owner, and every `chat_profiles` entry and
`stream_idle_timeout_seconds` were ignored. `test_bridge_settings_ssot.py`
reproduces that layout and forbids hand-built settings paths in
`bridges/shared`.

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
reachable (no Claude auth, or a local-only tenant — `CORVIN_TTS_LOCAL_ONLY` or an
egress policy that denies `api.anthropic.com`, ADR-2091), `summarize.py` cannot
summarise.
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
so CLI + the (since removed) local Hermes stage fit inside a 120 s parent cap. Measured `claude -p` latency for a
real summary call (10.5 KB system prompt, haiku) is 23 s / 27 s / 75 s / >180 s
across five runs — median ≈ 50 s. The 45 s budget therefore lost most of the
time, and **23 of 23 field summaries in ~27 h degraded to near-verbatim** — the
exact behaviour this section says was removed. The fit-the-cap guard test stayed
green throughout, because summing under the cap is necessary but not sufficient.

Current budgets are derived bottom-up from that measurement, and the parent caps
were raised to fit them: main summary CLI 90 s inside a 150 s cap
(`adapter.py::build_voice_summary`, `routes/voice.py::_TTS_SUMMARIZE_TIMEOUT_S`);
annex CLI 40 s inside 90 s. (The Hermes stage that used to share these caps was
removed by ADR-2091; the ladder is now `cli` → `structural`.) `summarize.py::_MEASURED_CLI_P50_S` records the measurement and
`test_summarize.py::test_cli_budget_covers_measured_latency` fails if a budget
drops to or below it. When touching these numbers, **re-measure first** — a
budget under the median silently disables a backend without failing anything.

**No local model to keep warm (ADR-2091).** The summary ladder is `cli` →
`structural`; the local Hermes/Ollama stage, its boot-time `summarize.py
--prewarm` and `CORVIN_VOICE_PREWARM` were removed. `--prewarm` is still accepted
and prints `prewarm-skipped`. A local-only tenant goes straight to the bounded
structural fallback — the `cli` backend has no egress check of its own, so it is
never spawned there.

The console `/voice/segment` "Read the full answer aloud" button is a deliberate
exception — it reads the raw answer verbatim by design and is a separate,
explicit user action, not the automatic voice note.

**Must NOT do:**
- READ paths (whitelist check, rate limit, profile lookup) MUST use
  `currentSettings()` (JS) or `_load_channel_settings()` (Python) —
  never the boot-time snapshot.
- Never compose `<dir>/<channel>/settings.json` by hand in Python — call
  `paths.resolve_bridge_settings_file()`.

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
genuinely stuck tool. Applied identically across the Claude and OpenCode
engine paths (the Hermes path was removed by ADR-2091). The cancellation message/log distinguishes
`awaiting tokens` from `awaiting tool result`.

E2E coverage: `test_adapter_stream_idle.py` —
`test_tool_call_in_flight_survives_short_idle` (4 s silent tool gap survives a
2 s token-idle) and `test_tool_backstop_kills_genuinely_hung_tool` (a
never-returning tool still dies at the backstop).

### Open background children (ADR-2236)

A claude process that has started `Bash run_in_background`, a `Monitor` or a background `Agent`
stays alive until they end and emits one `result` per wake-up, so the first `result` is not the end
of the turn — **process EOF with no open child is** (`bg_scope.ScopeTracker`, fed from the CLI's
`task_*` / `background_tasks_changed` events). Consequences in this loop:

- **No idle kill while a child is open.** The CLI is legitimately silent; `ADAPTER_STREAM_IDLE_TIMEOUT`
  is replaced by the child's own cap `CORVIN_BG_CHILD_MAX` (default 7200 s, measured from the oldest
  open child; a warning status goes out at 80 %). Before this, a quiet child was killed after 300 s
  and — on an existing session — the reset+retry **re-ran the prompt, starting the child a second
  time**.
- **Delay-by-one delivery** when `process_one` installs an interim sink (`bg_interim_sink`): a result
  that arrives while a child is open is sent at once as an *interim* message (normal envelope, own
  `msg_id`, Art. 50 provenance, no `_final`, file name `<msg_id>_-NNN.json` so it sorts before the
  final `_00`; first answer carries "⏳ N background task(s) still running"). A result that arrives
  with no child open is held as the final candidate — a newer result demotes it to interim, EOF
  promotes it to the one `_final` message. The session ledger records interim and final in order.
  Callers without a sink keep the legacy rule (the last non-empty result is the return value).
- **Caps end a runaway scope honestly.** Past `CORVIN_BG_CHILD_MAX` (default **30 min** for an
  interactive turn, **2 h** for a detached `/task` worker; the oldest open child, and twice that for the
  whole scope so a chain of sequential children cannot run for days), or more than
  `CORVIN_BG_WAKEUP_MAX` (default 25, one billed model turn per wake-up) wake-ups while a child is open,
  the process group is stopped and the final message says what was cut (kinds and count, never a
  description). The cap is checked when the stream is quiet **and after every event**. A cap that is 0,
  negative, NaN, infinite or garbage is rejected and the default applies (it would disable the only
  bound); a usable one is clamped (1 s…24 h, 1…1000 wake-ups). Not an error: no retry, `task.completed`
  is recorded.
- **Never re-run what already started children.** An error after background work started (or after an
  interim message went out) is NOT retried — every retry branch (transient HTTP, idle+session reset,
  context overflow) re-sends the whole prompt, which started the child a second time and delivered the
  first answer twice. The user gets "Claude API call failed … background work that was already started
  (…) has been ended; the request was not run again" plus the last update.
- **A kill is not a finish.** The CLI exits only after its children end, so EOF with a child still open
  means a kill, a crash or an operator stop: the closing message says "the Claude process ended while N
  background task(s) were still running … the result is incomplete" (audit `bgscope.cancelled`,
  `bgscope.completed end_reason=process_died`; an operator `/cancel` keeps its silent contract). If the
  model's last wake-up result is empty, a deterministic closing line ("✅ Background work finished: 1 task
  done.") stands in — never the first answer a second time.
- **An operator `/cancel` is recognised from the exit code, not from a flag that arrives too late.** Measured
  on the real CLI (2.1.294): with a background child it catches SIGTERM, ends the child itself and exits
  **143** (128+SIGTERM), not −15 — so `_STOPPED_EXIT_CODES` is {−15, −9, 143, 137}. `_cancel_chat` also stamps
  the request flag BEFORE signalling (the turn thread wakes the instant the process is gone). An error from
  the CLI's own result event (`API Error: 529`) is not mistaken for a stop: the engine SIGTERMs the process
  itself afterwards, but that error carries the raw result event, the engine-made "exited without result" does
  not. A clean exit (rc 0) with a child whose end was never announced is simply done. The detached `/task`
  worker catches SIGTERM and stops its claude process too (it lives in its own session; before, the
  supervisor's SIGTERM killed only the worker and the resumed attempt could start a duplicate).
- **Restarting the services must not kill running work.** `systemctl --user restart corvin-webui` (or the
  bridge adapter) ends the unit's whole control group: every claude turn in it dies mid-task and the user
  gets no closing message and no voice summary (measured 2026-10-08: five console restarts in one day; the boot
  reaper marked the then-running tasks `orphaned_on_restart` in the same second). Use
  `scripts/safe-restart.sh [--wait S] UNIT...` — it waits until no `claude -p` turn runs in the unit's
  cgroup (idle on two polls), then restarts, and never kills a running turn (exit 3 if still busy). Run it
  detached (`systemd-run --user …`) when the caller lives inside the unit it restarts.
- **Interim text is model output**: it passes the same post-spawn output sentinel as the final answer.
  The console stops a scope with SIGTERM and only SIGKILLs after a grace — measured on the real CLI,
  SIGKILL leaves its background children orphaned, SIGTERM does not.
- **Known limits (stated, not hidden).**
  - A scope occupies a bridge worker (`MAX_PARALLEL=4`) and serialises its chat until the last child ends,
    bounded by `CORVIN_BG_CHILD_MAX`. The old loop did the same but cut a quiet child after 300 s; that is
    why the interactive default is 30 min, not 2 h. Long background work belongs in `/task`. Releasing the
    worker after the first result (a detached continuation) is the follow-up.
  - While a child is open the idle watchdog is off, so a hung wake-up API call is bounded only by the cap.
  - `completion_notify.send_interim` writes straight to the outbox; with `proactive_communication` ON the
    FINAL completion additionally passes the governed gate and an interim does not.
- **Status line per child.** `bg_scope.status_line()` turns every child transition into one line
  ("⏳ Background shell command started: … — 1 running", "✅ … finished — all background work is done",
  "❌ … failed (exit 3)") sent through `on_status(..., tool_name="_bgchild")` — the sticky progress message,
  edited in place, so a chatty scope does not flood the chat. The alive heartbeat reads "⏳ N background
  tasks running · 14m 3s" while children are open. `bg_scope_observer(cb)` lets a caller (the detached
  `/task` worker) watch the tracker.
- `task.completed` is recorded only after the stream ended. `TaskManager.record_event` additionally
  defers a completion that reports `children_open > 0` (`task.completion_deferred`, status stays RUNNING,
  no learning outcome) — a backstop for a FUTURE caller: today's callers record completion after the
  scope is closed, so none can trip it, and a capped scope must complete.

Console (`chat_runtime`): the same tracker; interim results carry `interim: true` +
`pending_children`, exactly one result carries `final: true` and is the text that is spoken and
pinned as the voice key; `bg_status` events report the open count and, per child (last 20), `{id, kind, state, age_s, label}`
— `label` is the ADR-2236 D9 scrubbed description (`bg_scope.safe_description`: one line, ≤80 chars,
mentions / e-mail / secret / token shapes replaced); the raw description, a background agent's prompt
and `output_file` contents never leave the server. The web client keeps the list in the chat registry
(`bgChildren`, cleared when the turn ends) and renders it as the **background activity strip** above the
composer (`components/chat/BackgroundActivity.tsx`: icon by kind — shell / monitor / agent / workflow —,
label, a clock that runs on client-side from the reported age, a pulse for running and ✓/✗ for ended
children; running first, last 3 ended kept; collapsible; read-only and outside the composer, so it never
takes focus or blocks typing). Tests: `core/console/tests/test_bg_scope_console_e2e.py`,
`web-next/tests/unit/chat-bg-activity.test.tsx`, `web-next/tests/e2e/chat-bg-activity.spec.ts`.

E2E: `test_bg_scope_completion.py` (bridge, via `process_one`),
`core/console/tests/test_bg_scope_console_e2e.py` (console, via `stream_turn`),
`test_bg_scope_watchdog_repro.py` (the two defects as regression guards).

### Sticky progress messages + finalize guard (all channels)

`adapter.py`'s `_emit_status()` (`~L9319`) writes `_progress: true` outbox
envelopes while a turn is running (tool-call status lines), and the
heartbeat thread writes `_heartbeat: true` envelopes (`~L3921`) if nothing
else has fired yet. Both carry the turn's `msg_id` so a daemon can
correlate them with the eventual real-reply envelope.

Every bridge daemon (`corvin_operator/bridges/<channel>/daemon.js`, or
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
`corvin_operator/bridges/shared/js/sticky_progress.js` (`makeStickyProgress()`).
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

## Inbox / archive hygiene (adversarial hardening 2026-09-07)

Findings F-B3 / F-B5 / F-B8 / F-B9 / F-B12 of the 2026-09-07 bridge review.
All of it is structural — no flag, no env kill-switch.

| Mechanism | Where | Contract |
|---|---|---|
| **Atomic inbox envelopes** | every daemon → `shared/js/inbox_write.js::writeInboxAtomic` | `<id>.json.tmp` (0600) + `rename()`. The adapter's 1 Hz `inbox/*.json` poll can never read a half-written envelope. |
| **Poison quarantine** | `adapter.py::_quarantine_poison` (from `process_one`) | An unparsable / non-object envelope is MOVED to `processed/poison/` (0700), never unlinked — it is a user's message. Audit `bridge.inbox_poison_quarantined` `{file, bytes, reason}`; content never enters the chain. |
| **`processed/` retention** | `adapter.py::_sweep_processed` on the cleanup tick (`ADAPTER_CLEANUP_INTERVAL`, 300 s) | Regular files directly under `processed/` older than the window are deleted (GDPR Art. 5(1)(e) — the archive held 264 MB / 8 k envelopes of personal data with no purpose). Window: env `ADAPTER_PROCESSED_RETENTION_DAYS` → shared `settings.json` `processed_retention_days` → **30**. `0`/negative disables. `poison/` and sub-directories are never swept. Audit `bridge.processed_swept` `{removed, bytes, retention_days}` — counts only. |
| **System-prompt temp files** | `adapter.py::_sweep_sysprompt_tmp` on the same tick | `call_claude()` unlinks its `.corvin-sysprompt-*.txt` in `finally`; a SIGKILL/OOM between `mkstemp` and that `finally` used to leave the file (memory, recall, vault hints) behind forever. Any such file older than 1 h under the sessions root is swept. |
| **Voice-summary hand-off** | `build_voice_summary` → `summarize.py --stdin-json` | The user's question and the answer travel to `summarize.py` as a JSON envelope `{"text", "task"}` on **stdin**; `summarize.py` hands the prompt to `claude -p` on stdin and the system prompt via `--append-system-prompt-file` (0600 temp). Nothing user-authored is ever an argv value (`/proc/<pid>/cmdline` is world-readable; also removes the E2BIG ceiling). `--task <text>` no longer exists. |
| **Mid-turn heartbeat markers** | `mid_turn_heartbeat.default_state_dir()` = `<corvin_home>/bridges/mid_turn_heartbeats/` | Previously written into the repo tree (`corvin_operator/bridges/shared/`, un-ignored, world-readable, raw chat id in the filename). Now 0700 dir / 0600 files, filename carries a sha256 fingerprint of the session key; the raw ids stay in the marker body only. |
| **L34 gate on a nameless engine** | `adapter.py::_check_compliance_or_fail` | An engine without `name` cannot be matched against the locality matrix → **refused** (`[compliance] Spawn rejected … fail-closed`). It used to fail-open. |
| **L44 low-confidence allow is audited** | `adapter.py::_check_house_rules_or_fail` | `house_rules.py` writes `house_rules.escalated` for a `clear_low_confidence` verdict and the adapter then allows the turn; the override is now recorded as `house_rules.allowed_after_lowconf` (WARNING; rule id, reason code, confidence, fingerprinted chat key) so the chain never claims "blocked" for a request that ran. |

### Round 2 (2026-09-07) — prompt head, legacy fallback, dispatcher peeks

| Finding | Mechanism | Where | Contract |
|---|---|---|---|
| **R2-E1** | **Prompt-head sentinel** | `agents/claude_code.py::guard_prompt_head` (single shared helper) — called at EVERY `claude -p` spawn site: `adapter.py` `_build_claude_args` + `_call_claude_streaming_via_engine`, `corvin_console/task_worker_pool.py::_worker_stdin_payload`, `corvin_console/chat_runtime.py` (turn stdin + ADR-0213 context-sync note), `corvin_console/routes/assistant.py` (added R3-C1), `../orchestration/tde/worker_ipc.py` (added R3), `../voice/scripts/summarize.py::_run_claude_print` | The CLI expands a user message whose **first byte is `/`** into a slash command / skill on EVERY transport — positional-after-`--` and the stdin `stream-json` user message alike. Proven: a chat instruction `/pwn` executed `.claude/commands/pwn.md` from the persona workdir under `bypassPermissions`; `/cost` returned the operator's subscription usage with `num_turns == 0` (the CLI answered, the model never ran). Every site now prepends ONE fixed, non-slash sentinel line (`User input:\n`), **unconditionally** — independent of whether a CEL brief / observer block / volatile prefix happens to be present, because those are conditional and this must not be. The user's text follows verbatim, so `/pwn` reaches the model as literal text. Fail-closed: `_worker_stdin_payload` raises rather than build an unguarded payload, and `summarize.py` raises `OSError` (degrading to the no-LLM structural fallback) if the helper is not importable. The guard is idempotent. |
| **R2-E2** | **Legacy `call_claude()` prompt off argv** | `adapter.py::call_claude` → `_build_claude_args(..., prompt_via_stdin=True, spawn_prompt_out=…)` | The legacy image/document fallback (reached from the engine-streaming `except` branch) still built argv WITH the prompt — world-readable via `/proc/<pid>/cmdline` for the process lifetime, plus the ~128 KiB E2BIG ceiling. The prompt now travels on **stdin** (`communicate(input=…)`, plain text — no `--input-format`, so the whole of stdin is the user message). `spawn_prompt_out` hands the caller the exact sentinel-guarded text the builder produced, so the two can never drift. |
| **R2-B4** | **Total dispatcher peeks** | `adapter.py::_peek_envelope` (used by `_route_key` + `_peek_side_channel`) | The peeks caught only `(OSError, JSONDecodeError)`. A non-object envelope (`AttributeError` on `.get`) or non-UTF-8 bytes (`UnicodeDecodeError`) raised out of `submit_inbox_item` **before the future was attached**: the whole poll tick died and the msg_id stayed pinned in `_in_flight` for `IN_FLIGHT_TTL` (3600 s), so the message could never be retried. `_peek_envelope` returns `None` for anything that is not a JSON object in valid UTF-8; the runner then quarantines it through the normal poison path. |

Regression tests: `shared/test_adapter_inbox_hygiene.py`, `shared/test_mid_turn_heartbeat.py`,
`shared/test_adapter_compliance_gate.py`, `shared/test_adapter_house_rules_binary.py`,
`shared/test_adapter_voice_summarizer_choice.py`, `../voice/scripts/test_summarize.py`;
`shared/test_adapter_prompt_head.py` (R2-E1/E2 — argv builder + a recording `claude`
stand-in that `call_claude()` really execs), `email/test_imap_state.js` (R2-B3);
`run-all-tests.sh` now registers the email suites (`test_inbound_auth.js`,
`test_imap_state.js`, `test_disclosure_ordering.js`) plus the two adapter suites above —
they existed but were never wired into a full pass, so the DMARC/DKIM gate that decides
whether a `From` address may act as the owner had **no** coverage in CI;
live: `shared/test_adapter_live_llm_e2e.py` (`CLAUDE_LIVE_E2E=1`, real `claude -p` haiku turn
through `process_one`, asserts outbox reply + processed move + hash-chained turn events) and
`core/console/tests/test_task_worker_pool_argv.py` (`CLAUDE_LIVE_E2E=1`, the REAL `/task`
worker pool driving the REAL CLI with `/cost` and with a scratch `.claude/commands/pwn.md`).

### Round 3 (2026-09-07) — `@<path>` expansion, the missed spawn site

| Finding | Mechanism | Where | Contract |
|---|---|---|---|
| **R3-C2** | **`@<path>` neutraliser** | `agents/claude_code.py::neutralise_at_references`, applied inside `guard_prompt_head` — so every present and future call site inherits it | The byte-0 sentinel guarded **byte 0 only**. The CLI ALSO expands `@<path>` into that file's **content** *anywhere* in the message, client-side, before the model runs. It is not a tool call, so no tool policy, permission mode, `--add-dir` or sandbox restricts it: proven with all tools disallowed, through both the `/task` worker's `stream-json` transport and the plain-stdin adapter transport, `num_turns 1`, `permission_denials []`. Reachable from **every** channel carrying user text — Discord / Telegram / WhatsApp / e-mail bodies, console `/task`, console chat, voice summary — so one message exfiltrated `~/.corvin/audit.jsonl`, `~/.config/corvin-voice/secrets.json`, `.env`, any readable file. **Measured trigger:** only a *token-start* `@` expands (start-of-string or whitespace before it); `x@canary.txt` and `(`/`<`/`"`/`,`/`:`/`=`/`[`/`/`/`-` before the `@` do not — which is why e-mail addresses were never a vector. **Fix:** insert one zero-width **U+2060 WORD JOINER immediately BEFORE** every `@` whose preceding character is not an e-mail local-part char `[A-Za-z0-9._%+-]`. Nothing is deleted or rewritten — dropping the joiners restores the input byte-for-byte — and the guard stays idempotent. **Placement matters:** U+200B *before* the `@` does NOT stop the expansion (a zero-width space reads as a token boundary, a word joiner does not); a joiner *inside* the token (`@`+joiner) does stop it but made the model call the message "obfuscated … prompt-injection attempt" and refuse an ordinary echo. **Trade-off:** the rule is deliberately wider than the measured trigger, so `@handle`, `<@1234>` Discord mentions and a pasted `@property` decorator each gain one invisible joiner in front of the `@`; e-mail addresses are untouched. Byte-0 `!` (client-side shell — `!cat secret.txt` ran with the Bash tool disallowed) and `#` (memory-add) are covered by the existing sentinel, verified inert behind it. |
| **R3-C1** | **The missed spawn site** | `corvin_console/routes/assistant.py` | It imported neither the sentinel nor the neutraliser and passed the prompt as a **positional argv element** with no `--`; with the route's defaults (`context={}`, `history=[]`) that element *was* the operator's message, so `--version` was parsed as a CLI flag and `/pwn` expanded a project command from the spawn cwd. The prompt now goes through `guard_prompt_head` and travels on **stdin** (`subprocess.run(..., input=…)`), fail-closed on `ImportError` exactly like `task_worker_pool.py`. `corvin_operator/orchestration/tde/worker_ipc.py::_run_worker` was guarded in the same pass. |
| **R3-C1b** | **Spawn-site ledger** | `core/console/tests/test_claude_spawn_site_ledger.py` | The root cause was a *discovery* gap: nothing enumerated the `claude -p` spawn sites. The test now walks `core/`, `operator/` and `scripts/` for modules that spawn the CLI with `-p` and holds them against a ledger — `_MUST_GUARD` (must reach `guard_prompt_head`) and `_PENDING` (same finding class, other surfaces, each with a reason). A **new** spawn site, or a removed ledger entry, fails the test. |

Regression tests: `shared/test_adapter_prompt_head.py` (neutraliser contract + idempotence +
e-mail preservation), `core/console/tests/test_assistant_route_prompt_guard.py` (real router,
real HTTP request, real exec of a recording `claude` on PATH),
`core/console/tests/test_claude_spawn_site_ledger.py`;
live: `core/console/tests/test_task_worker_pool_argv.py::test_live_at_path_reference_is_not_expanded_into_file_content`
(`CLAUDE_LIVE_E2E=1`) drives the REAL worker pool + REAL CLI with `@/etc/hostname` and
asserts the machine's host name is absent from the answer — this is what pins the
neutralisation against a future CLI change, not the doc.

### Round 4 (2026-09-07) — the ledger's `_PENDING` backlog is closed

| Finding | Mechanism | Where | Contract |
|---|---|---|---|
| **R4-C1** | **26 unguarded spawn sites** | the 12 bridge helper models in `corvin_operator/bridges/shared/` (`router`, `acs_classify`, `acs_gate_chain`, `acs_runtime`, `house_rules`, `output_sentinel`, `user_style`, `user_model`, `memory_bridge`, `ulo_compliance`, `dialectic`, `compute_narrator`) plus `context_engineering/stages/llm_synthesis.py`, `compute/fabric/oracle/oracle.py`, `console/browser/agent.py`, `console/routes/workflows.py` (3 spawns), `delegate/{output_judge,prompt_safety}.py`, `workflows/engines_claude.py`, `tde/{analysis_runner,loss_judge,tde_engine}.py`, `skill_creator/llm_client.py`, `voice/hooks/artifact_register.py`, `voice/scripts/engine_canary.py`, `scripts/run_spotify_workflow_demo.py` | Round 3's ledger listed these as `_PENDING`; the first twelve are fed the message body of a public Discord / Telegram / WhatsApp / e-mail turn **verbatim**, so `@/etc/hostname` in one chat message was a file read on every one of them. Each site now guards the **WHOLE payload** it hands to the CLI — never a substring — because the template and the attacker-influenced text are interleaved by the time the payload exists, and the sentinel additionally keeps a `-`-leading payload from being parsed as a CLI flag at the ~14 sites that pass the prompt **positionally**. Reply contracts are untouched: the guard edits only the prompt, and every one of these parses its verdict out of **stdout**. |
| **R4-C2** | **One fail-closed import surface** | `corvin_operator/bridges/shared/prompt_guard.py` | Copying a defensive `try: import … except: _guard = None` block into thirty modules is thirty chances to forget the `is None` branch and spawn unguarded. The shim imports `agents.claude_code.guard_prompt_head` once and **raises `PromptGuardUnavailable`** from its own `guard_prompt_head()` when that fails — there is no code path that returns the caller's text, so callers need no `None` check. Sites outside `corvin_operator/bridges/shared/` bootstrap the shim by walking up to the repo root; their fallback is a **raising stub**, never a pass-through. The three round-1..3 sites keep their inline `is None` refusal and are listed in the ledger's `_INLINE_NONE_CHECK`; `chat_runtime.py` / `summarize.py` import the helper directly with no fallback at all (`_DIRECT_HARD_IMPORT`). |
| **R4-C3** | **L44 stays fail-closed** | `house_rules.py::_house_rules_classify_chunk_once` | A raising guard inside a compliance gate must not become a silent allow **or** a pointless retry storm. The guard failure is converted to `_HouseRulesClassifierError("guard_missing")` — the module's own error contract — and `guard_missing` joins `spawn_missing`/`auth_missing` as a **non-transient** cause, so the retry wrapper breaks immediately and the gate escalates. |
| **R4-C4** | **Ledger categories** | `core/console/tests/test_claude_spawn_site_ledger.py` | `_PENDING` is now **empty and asserted empty** — anything landing there is an open finding, not a blessed exemption. A new `_NO_CLI_TEXT` category holds the two files the discovery regex finds that hand **no text at all** to the CLI (`compute/fabric/config.py` — an argv *template* dataclass default; `bridges/shared/engines/system_prompt_injector.py` — `claude -p` appears only in docstrings). `test_guarded_sites_fail_closed_when_the_helper_is_missing` now covers **every** `_MUST_GUARD` entry, not three of them. |

Regression tests: `corvin_operator/bridges/shared/test_spawn_prompt_guard.py` — three of the most
exposed sites (`router.route`, `acs_classify.classify`, `house_rules._house_rules_classifier`)
driven through their own entry points against a **recording `claude` stand-in the module
actually execs**, asserting on what the CLI RECEIVED (argv + stdin), plus the shim's
refusal contract and L44's `guard_missing` mapping;
`core/console/tests/test_claude_spawn_site_ledger.py` (7 tests, `_PENDING` empty);
live: `test_spawn_prompt_guard.py::test_live_at_reference_does_not_leak_the_host_name_through_memory_bridge`
(`CLAUDE_LIVE_E2E=1`) drives the REAL CLI through `memory_bridge._run_haiku` with
`@/etc/hostname` between markers and asserts this machine's host name is absent — the
unguarded control run on the same prompt answered "als Dateireferenz aufgelöst ergibt er
`shumway`".

### Round 4, second pass (2026-09-07) — the guard moves INSIDE the engine

Round 4's ledger was green while three HIGH-severity routes were live and unguarded. The
defect was in the ledger's **discovery**, not in the neutraliser: `_DASH_P` required a
quoted literal `-p` argv element, and every module that spawns through
`ClaudeCodeEngine.spawn()` writes none — `_build_args` appends `-p` itself. Eight modules
were therefore structurally invisible, including `adapter.py`, `a2a_worker.py`,
`gateway/dispatcher.py` and `delegate/delegation.py`.

| Finding | Mechanism | Where | Contract |
|---|---|---|---|
| **R4b-F1** | **`/btw` mid-stream injection reached the CLI raw** `[CRITICAL]` | `agents/claude_code.py::ClaudeCodeEngine.inject` ← `adapter.py::inject_btw` ← `eci/dispatcher.py::dispatch_btw` ← `daemon.js` (`discord:781`, `telegram:313`, `slack:317`, `whatsapp:1281`, `teams:244`) | `inject()` writes a **second** user message into the live `claude` process and called no guard — a surface the round-1..3 model ("a spawn site builds an argv or a first stdin message") does not contain. The CLI applies its client-side expansions to **every** user message: measured live over `--input-format stream-json` with `--disallowedTools "*"`, a line whose content was `/r4probe` ran a project slash command and one containing `@cC.txt` inlined that file. Any chat user typing `/btw look at @/etc/hostname` (or `@~/.corvin/audit.jsonl`, `@.env`) read that file, inside a turn that runs `--dangerously-skip-permissions`. `inject()` now neutralises before framing. The **buffered** `/btw` transport was safe by accident: it lands in `--append-system-prompt`, which is **not** scanned for `@`. |
| **R4b-F2** | **Gateway tenant Run route** `[HIGH]` | `core/gateway/corvin_gateway/dispatcher.py:381` → `:846` (`engine.spawn(prompt, env=env)`), route `app.py::submit_run` | `POST /v1/tenants/{tid}/runs` handed raw `spec.input` to the engine with `permission_mode is None` → `--dangerously-skip-permissions` and the prompt as the last positional argv element. On that transport a byte-0 `!cmd` is **local shell execution** (proven: `!echo R4BANG_OK_MARKER` ran with every tool disallowed, `permission_denials []`). Behind the tenant JWT — which does not remove it: a *tenant* is not the *operator* (ADR-0007). |
| **R4b-F3** | **A2A worker, plus an ordering trap** `[HIGH]` | `corvin_operator/bridges/shared/a2a_worker.py:658` → `:778`, `_CONTROL_CHARS` at `:147` | The A2A framing block neutralises byte 0 (the payload starts `<a2a_instruction`) but nothing touched `@`, so a remote peer read arbitrary local files through an instruction body that passed every existing A2A defence. The guard is **not** a drop-in at this call site: `sanitize_instruction` strips U+2060 (`0x2060, # WORD JOINER — MED-04 fix`), so a guard applied *before* it silently re-arms the `@`. Correct order is `sanitize_instruction → frame_instruction → engine.spawn`, which the engine-level guard makes automatic. An ORDERING HAZARD comment now sits at `_CONTROL_CHARS` and `test_sanitize_instruction_would_strip_the_joiner_if_applied_first` pins it. |
| **R4b-F4** | **Ledger discovery rebuilt** | `core/console/tests/test_claude_spawn_site_ledger.py` | Discovery now recognises **both** shapes — hand-built argv (`"-p"` + a claude-binary reference) and engine-mediated (`ClaudeCodeEngine`/`claude_code` + a `.spawn(`/`.inject(` call) — and scans the **whole repo** minus `_SKIP_DIRS` instead of `core`/`operator`/`scripts` (which had left three `benchmark/` spawns unclassified). Categories: `_MUST_GUARD` (builds the payload itself → must call the helper; `adapter.py` added, since its own guard calls are load-bearing for the legacy raw-stdin `/btw` path), `_ENGINE_GUARDED` (guarded by the engine; asserted to build no argv of its own), `_NO_CLI_TEXT`, `_OFFLINE_FIXTURE_HARNESS` (operator-run `benchmark/` measurement scripts, asserted to live under `benchmark/` — deliberately unguarded because a sentinel line would corrupt the token counts they measure), `_PENDING` (empty, asserted empty). `adapter.py`'s raising import stub is a third fail-closed shape (`_RAISING_STUB`). |
| **R4b-F5** | **`delegate_*` MCP tools** `[MEDIUM]` | `core/delegate/corvin_delegate/mcp_server.py:448` → `delegation.py:985/:1003` | `worker.spawn(prompt)` with zero `guard_prompt_head` references in the package; `_validate_prompt` is a type/length check, not an injection guard. Covered by the engine-level guard. |

**The structural change:** `guard_prompt_head()` is now called by
`ClaudeCodeEngine._build_args()` (positional-argv transport),
`ClaudeCodeEngine.spawn()` (which writes the initial `stream-json` stdin message itself)
and `ClaudeCodeEngine.inject()` (the second and later user messages). The invariant
changes from *"did every caller remember?"* to *"the engine cannot emit an unguarded
payload"* — the only shape that survives the discovery gap above. Existing call-site
guards are **kept**: the helper is idempotent, so guarding twice is byte-for-byte
identical (`test_guarding_twice_is_a_byte_for_byte_no_op`).

**Transport asymmetry** (measured, recorded because "stdin is safer" is a tempting and
wrong simplification): `@<path>` and byte-0 `/cmd` fire on **both** the positional-argv
and the `stream-json` stdin transport; byte-0 `!cmd` fires on the **positional-argv
transport only**; `#` routes to memory-add. The sentinel + joiner cover the union.

**Behaviour change:** the positional prompt of `ClaudeCodeEngine._build_args()` is now
the guarded payload, so argv snapshots carry the `User input:\n` sentinel
(`agents/test_engines_e2e.py::BuildArgsTests`,
`core/console/tests/test_task_worker_pool_argv.py`), and a `/btw` note echoed by a fake
CLI comes back with the sentinel line (`test_adapter_engine_path.py`).

Regression tests: `corvin_operator/bridges/shared/test_engine_guarded_spawn.py` — real
`subprocess.Popen` of a recording stand-in, asserting on the bytes that reached argv and
the stdin **pipe**, for `inject()`, the stdin transport, the argv transport, idempotency
and the A2A worker driven through the real `spawn_a2a_worker`;
`core/gateway/tests/test_dispatcher_prompt_guard.py` — real FastAPI `TestClient` against
the real router + real `RunDispatcher` + real `ClaudeCodeEngine`;
`core/console/tests/test_claude_spawn_site_ledger.py` (10 tests). All four fail on the
pre-fix engine. Live: `test_engine_guarded_spawn.py::test_live_engine_spawn_does_not_inline_a_canary_file`
(`CLAUDE_LIVE_E2E=1`) spawns the REAL CLI through the guarded engine with a token-start
`@<abs path>` to a temp-dir canary and asserts the token is absent from the reply —
guarded reply `'NONE'`, while the same prompt run unguarded returns the canary verbatim.

**Known, unrelated:** `acs_classify._llm_classify` passes `--no-tools`, which the installed
CLI rejects (`error: unknown option '--no-tools'`), so that Stage-2 fallback currently always
returns `path="llm_error"`. Guarding it is still correct — the flag is a separate defect and
was left untouched here.

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
Implementation: `corvin_operator/bridges/shared/settings_view.py` (pure-Python, best-effort).
Three blocks: WORKING/PFADE, SESSION, SYSTEM.

**Must NOT do:** Don't add sub-commands. Don't pull in PyYAML/Pydantic from the
bridge process. Don't write to audit chain from this aggregator. Every block
degrades to `—` on exception — never fail-loud.

---

## Where the in-chat commands' Python lives (load-bearing, 2026-07-28)

`in_chat_commands.js` shells out to ~23 Python CLIs — `session_reset.py`,
`roles.py`, `consent.py`, `disclosure.py`, `quota.py`, `audit_view.py`,
`proposal.py`, `goal.py`, `ulo.py`, `phase3_cli.py`, `dialectic.py`,
`engine_switch.py`, `ldd.py`, `spg.py`, `decision_registry.py`,
`personal_tools.py`, `settings_view.py`, plus `voice/scripts/{lang,profile,
memory,vault,schedule}_cli.py` and `corvin_a2a.py`.

**Resolve them through `bridge_paths.operatorRoot()`, never `__dirname/..`.**

A daemon started by `bridge_manager` (`start_channel_detached`, `_spawn`) or by
the ADR-0238 supervisor runs with `cwd=<corvin_home>/bridges/<channel>/`, and
`_materialise_shared_js()` mirrors ONLY `shared/js/*.{js,mjs,cjs,json}` next to
it. From that mirrored copy `__dirname/..` is `<corvin_home>/bridges/shared/` —
a directory holding `inbox/`, `outbox/` and `js/`, and not one Python file. So
every command above failed with ENOENT on **every wheel install**, and worked in
**every git checkout**, where `__dirname/..` happens to be the source tree.
That asymmetry is why neither CI nor a dev session ever saw it.

The three spawn sites export `CORVIN_BRIDGE_OPERATOR_ROOT`; `operatorRoot()`
validates it (a bogus value falls back rather than breaking a good layout) and
otherwise self-locates from `__dirname`, so a hand-started source-tree daemon
is unchanged. `shared/js/test_operator_root_resolution.js` builds the runtime
layout and fails if any constant goes back to walking up from `__dirname`.

Same reader≠writer split as `_adapter_queue_env`'s queue pinning, one layer up.
`settings.json` is the one path that *is* correct against the runtime dir — it
is user-managed state and genuinely lives there.

---

## Turn task: opened at pickup, closed once (ADR-0080, ADR-2081)

Every bridge turn's task record is opened by the `call_claude_streaming`
wrapper — before context assembly (60–80 s per turn, measured 2026-09-27) and
for EVERY engine (claude_code, opencode, codex; previously only the
claude_code path created one). It is `running` from pickup
(`task.started` with `stage: preparing` plus `owner_pid`/`owner_start` — the
owning adapter or bg-worker process); every engine path logs
`task.engine_started` when it runs (claude/codex/opencode with the process
`pid`), which the console reads as
"engine running". The wrapper closes it exactly once. Engine paths and gates
only *report* an attempt's outcome (`_TurnTask.report`, keyed by attempt;
`_turn_refused(reason)` before every refusal/gate/engine-error `return`): a
retry recurses — through the wrapper, or straight back into
`_call_claude_streaming_via_engine` (model escalation, stale resume marker) —
and the first attempt's `finally` runs AFTER the successful retry. Both kinds
of retry advance `_TurnTask.attempt`, and the LAST attempt's outcome decides,
whatever order the reports arrive in. Rules at close: a `/cancel` of this chat
(`_cancel_chat` records it in `_TURN_CANCEL_REQUESTS`) → `task.cancelled`
unless the attempt completed; an exception → `failed` (it outranks any report
— no reply went out); else the last attempt's report; else an `[adapter]…`
reply or an EMPTY reply → `failed` (`empty_reply`); else `completed`. Outcome
kinds: `engine_error`/`timeout` → `task.failed` (reaches the learning loop);
`/cancel` (claude: SIGTERM/SIGKILL without a timeout; other engines: the
cancel request) → `task.cancelled`; every other reason (budget, quota, engine
policy, L34, egress, engine trust, capability, house rules, gate, chain
integrity, missing engine) → `task.cancelled` with `refused: true` and
`result_summary: "refused: <reason>"` — nothing ran, so the learning loop
(completed/failed only) never sees it. `test_every_gate_return_reports` fails
on a gate `return` without `_turn_refused` right before it.
`result_summary` (reply preview, 280 chars) is stored ONLY for the operator's
own turns — someone else's conversation keeps `output_chars`, never text.
`chat_debug.jsonl`: one `turn.start` per turn (attempt 0; retries log
`turn.retry`) and one matching `turn.done`; a turn refused before
`turn.start` logs neither. The boot reaper (`TaskManager.reap_stale_running`,
run by the adapter AND by every console/gateway boot) never reaps a turn
whose engine pid (`ENGINE_START_EVENTS`) OR owning process
(`owner_pid` with the same `owner_start`, so a recycled pid is no owner) is
alive. The record's `input.from_operator` is true only when the sender is
explicitly on the daemon's channel whitelist (`_sender_is_operator`, via
`_load_channel_settings`, JID device suffix normalised) AND the chat is not
opened to everyone (`chat_profiles.<chat>|default.audience: all`); the uid
is not written.

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

**This is no longer the only caller (2026-09-21).** Sweeping every session dir is
not the same as sweeping on every host: an install that never starts a bridge
never ran this code, and a console-only install then dies permanently on its fifth
interrupted turn with `QuotaExceededError`. The same sweep now also runs from
`corvin_plugins.bootstrap._reap_stale_tasks()` via `boot_platform()`, which both
shipped console hosts call. The adapter keeps this call — a bridge is its own
process and reaps its own boot. Details and guards:
[layer-22-task-engine-m2.md](layer-22-task-engine-m2.md) § Stuck-task recovery.

**Must NOT do:**
- Don't call `reap_stale_running()` during normal operation — it is a boot-only
  sweep and calling it concurrently with active workers would cause double
  terminal events.
- Don't remove either boot call on the grounds that the other one covers it. They
  are different processes, and an install may run either without the other.

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
`{running, stalled_s, idle_s, precheck_stalled_s}`. Discord publishes
`stalled_s` as `poller_stalled_s` in `/status` so an external watchdog has
something actionable to poll — `paired: true` plus an open socket is **not**
evidence that anything is being delivered.

### `preCheck` can fail forever without ever tripping `stalled_s`

`stalled_s` only catches a tick that hangs *mid-await* — `running` stays
`true` across the whole tick. A `preCheck` (e.g. Discord's
`client.isReady()`) that returns `false` makes the tick return **before**
any `await`, so it settles in well under a millisecond every single time:
`running`/`runningSince` never look stalled, no matter how many hours the
gate stays shut. On 2026-07-27 this was exactly the blind spot: Discord's
gateway looked `paired: true` with `poller_stalled_s: 0` throughout while
`client.isReady()` stayed `false` for ~90 minutes and 5 finished replies sat
undelivered — zero log lines, because `if (preCheck && !preCheck()) return;`
had no logging path at all.

Fix: the poller tracks how long `preCheck` has been *continuously* refusing
(reset the moment it passes again) and, once that exceeds `stallWarnMs`
(same knob the tick-stall detector uses, deduped the same way — once per
60 s per unchanged condition), logs `outbox: preCheck has been blocking
delivery for Ns — N file(s) queued but nothing is being sent`. Exposed as
`precheck_stalled_s` in `stats()`; Discord's `/status` publishes it
alongside `poller_stalled_s`. `> 0` means the gate, not a hung send, is the
reason nothing is moving — don't restart-and-hope before checking which of
the two fields is nonzero.

### `pending_outbox` must count only the caller's own channel

`corvin_operator/bridges/shared/outbox/` is one directory shared by every bridge
daemon. Before 2026-07-27 every daemon's `/status` computed `pending_outbox`
as `readdirSync(OUTBOX).filter(f => f.endsWith('.json')).length` — the
**total** file count, not filtered by channel. Since the directory is
shared, whatsapp, discord and email all reported the identical number
whenever a backlog existed anywhere, regardless of which channel actually
owned it: on 2026-07-27 all three reported `pending_outbox: 5` while 100% of
the 5 files were `channel: "discord"`, sending incident triage down two dead
ends. Use the exported `countPending(outboxDir, channel)` from
`shared/js/outbox.js` instead — it parses each envelope's `channel` field
and counts only matches for the caller's own `CHANNEL` constant. Every
bridge daemon (`whatsapp`, `discord`, `telegram`, `slack`, `teams`, `email`,
`signal`) calls it the same way; `whatsapp`/`email` don't otherwise import
`shared/js/outbox.js` (they run their own legacy `processOutbox()` loop) but
still call `countPending` for the health field.

### Tests must never write to the live outbox

`corvin_operator/bridges/shared/outbox/` is polled by the *running* daemons every
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
the send timeout, the tick-stall detector, the preCheck-stall detector, and
`countPending`'s per-channel filtering), `discord/test_outbox_hardening.js`
(stall visibility in `/status` + the local snowflake guard).

**Must NOT do:**
- Don't compute `pending_outbox` with a raw `readdirSync(OUTBOX).length` —
  the directory is shared across every channel; use `countPending(OUTBOX,
  CHANNEL)` so the count reflects only envelopes this daemon actually owns.
- Don't treat `poller_stalled_s: 0` alone as "delivery is working" — a
  `preCheck` stuck `false` never sets `stalled_s` either. Check
  `precheck_stalled_s` too.
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
  `poller_stalled_s`, `precheck_stalled_s` and `pending_outbox` together.
  `poller_stalled_s`/`pending_outbox` both looked fine for the entire
  38-minute 2026-07-26 outage; `poller_stalled_s` alone also looked fine for
  the entire 90-minute 2026-07-27 outage, where `precheck_stalled_s` was the
  only field that would have shown a problem.
- Don't let a test write to the live `outbox/` — set `ADAPTER_OUTBOX`. Post-hoc
  cleanup loses the race against a 500 ms poll tick.

---

## Session state has one address (2026-08-28)

`corvin_operator/bridges/shared/session_state.py` is the single source of truth for
**where** a bridge chat's Claude conversation state lives and **what** counts as
that state. `adapter._reset_session_state()` and
`session_reset._wipe_voice_state()` both call into it; neither builds a path of
its own any more.

It exists because they did, and drifted. ADR-0007 Phase 1.2 moved the adapter
onto the tenant-aware resolver `paths.voice_session_dir()`
(`<corvin_home>/tenants/<tid>/sessions/voice/<channel>/<chat>/`); `session_reset`
was not moved with it and kept rmtree-ing `<corvin_home>/voice/sessions/…`, a
path that resolves to nothing under any configuration. The consequences:

| Layer | Hand-built path | What survived every `/new` |
|---|---|---|
| Claude conversation state | `<home>/voice/sessions/…` | `.main_session.json` → the next turn ran `claude --resume <old id>` and the chat continued verbatim |
| forge session workspace | `<home>/sessions/<chan>` | forge tools, `forge/memory.md`, worker sessions |
| `session_timeout_sweep` | both of the above | the daily timer fired, matched nothing, and no chat was ever aged out |

Discord channel 1501315335750684803 sat on session id `1e53620a…` for weeks
while `/new` cheerfully reported `voice state cleared: no`.

**A reset clears conversation state only.** `.main_session.json`,
`.session_started`, `.claude.json` and `.claude/` go; everything else in the
directory stays — `outputs/`, `tasks/`, the operator's project files, and the
L37-retained `cel-briefs/` audit sidecars. This is what the `/new` reply
promises in so many words, and it is why the reset deletes entries rather than
the directory.

Since 2026-10-02 `reset_claude_session_state(workdir, *, reason=...)` also
records the reset as a boundary in the chat's **session ledger** (next
section) whenever a session actually existed. Every caller passes its reason:
`manual` (`/new`, `/clear`, `/reset`, the in-adapter `_reset` branch),
`timeout` (inactivity sweep), `context_overflow`, `session_corrupted`. An
unknown reason counts as unwanted, so a new caller that forgets it fails
towards remembering.

## Session ledger — session content is never forgotten (ADR-2102, 2026-10-02)

The CLI transcript behind `--resume`/`--continue` loses content two ways the
adapter does not control: **auto-compaction** (measured on this install's
Discord bridge: 197 886 → 12 287 tokens, 185 599 dropped in one compaction) and
**session resets** (context overflow, corrupted/idle stream, the 7-day
inactivity sweep, `/new`). `corvin_operator/bridges/shared/session_ledger.py`
makes that loss structurally impossible for the chat's own turns:

| Rule | Where |
|---|---|
| **Record** every finished turn verbatim (user text, answer, `chat_key`, `sender`), append-only (`O_APPEND` + `flock` + fsync, 0600, a torn last line is terminated first) to `<tenant>/session_ledger/<channel>/<chat>/ledger.jsonl` — a sibling tree of the session tree, OUTSIDE the worker's cwd (`session_ledger.ledger_dir`; a pre-R5 `<workdir>/.corvin-ledger/` is moved there by `migrate_legacy_ledgers` when the adapter starts — ONCE PER INSTALL (marker `<tenant>/session_ledger/.legacy_migrated`), only files last modified before `LEGACY_CUTOFF`, merged by time if the store already exists, a legacy `reset` boundary never merged; a lazy or per-start move adopted a `.corvin-ledger/` a worker had planted, forged `/new` included (review R6-3/R6-4/R8-2). A ledger moved in without its counters still keeps its `/new` fence: `_append`, `manual_fence_seq` and `repair_fence` rebuild it from the records (R8-4); `adapter._cel_session` then carries the anchor FACTS added under the un-fenced key AFTER that `/new` to the fenced key — per fact by `added_at`, idempotent, the earliest post-fence goal winning (`anchor.adopt_store`, R9-3/R10-1). A turn whose engine call failed or was cancelled never promotes its candidate goal (`_TURN_OUTCOME.failed`, R10-4). Inside the cwd, `rm -rf "$PWD"`, `find . -delete` or a glob such as `.corvin-led?er/` reached it (review R5-1). `seq`/`n` come from a counters-only sidecar (`counters.json`), so an erasure never makes numbers repeat. No reset path removes the ledger; only GDPR erasure (`L-session-ledger`) does, and the worker cannot write it (`path_gate.is_protected_path`: any `session_ledger` / `.corvin-ledger` / `cel_anchors` / `pending_notifications` path component, and the console's `web_chat/**/*.turns.jsonl`; a Bash target with a glob is checked against what it matches; output options (`-o`/`-O`/`--output`/`-out`) and the targets of `mkdir`/`touch`/`patch`/`uniq`/`split`/`xxd` are write targets; a target whose variable cannot be resolved even from the environment fails closed only when it names one of these stores — a resolvable `$PWD`/`$HOME` target is not blocked, R9-4/R9-5). The worker READS `<workdir>/.corvin-history.md`, a view regenerated on every spawn with the same withholding as the block (`_publish`); the block names only that file, never the record (review R5-3). Every write the adapter makes there — view, counters, render state — goes through a fresh `mkstemp` temp and `os.replace`, and the ledger is opened `O_NOFOLLOW`: a symlink the worker plants is never written through (review R6-1). The gate is syntactic: a same-user worker that runs a program it wrote, or uses shell syntax the hook does not evaluate (variable chains, brace expansion, `$'…'`), can still write the store (as it can the audit chain); that boundary is OS-level isolation, see CLAUDE.md § Session Ledger. The start-up migration never follows a link: a symlinked `.corvin-ledger`, a non-regular or multiply linked legacy file is skipped and audited (review R7-2). On disk the texts are `user_text`/`assistant_text` — `user` is an identity key to erasure, so a message reading like someone's id would otherwise have been erased as theirs; `read_ledger` maps them back. | `process_one`, the moment the answer is final — before TTS, outbox and grading |
| **Verify coverage against the transcript**, not against bookkeeping: the user entries of the current CLI transcript AFTER its last `compact_boundary` (meta entries skipped) are aligned with the recorded turns CONTIGUOUSLY from the newest: the newest spawned turn must equal the newest entry (or the entry must end with `"\n" + text` — the engine prefixes `User input:` and any brief; zero-width `@` neutralisers are ignored), the next one the entry before it; the first mismatch ends the alignment. Turns recorded `spawned: false` or refused never align; `spawned` is set where the `claude` CLI is actually started (`_TURN_OUTCOME.cli_spawned`; console: `cli_spawned` on the OS-turn answer, schema `v: 2`), so delegated, copilot, compute-blueprint and gate-answered turns never count as live; a live-delivered `/btw` note in the transcript is stepped over. Unreadable transcript ⇒ nothing is live ⇒ everything is re-supplied. | `render_context` → `scan_transcript`, `uncovered_turns` |
| **Re-supply** every non-live turn on EVERY spawn (including the fresh retry after an overflow/corrupted-session reset): newest turns verbatim (40 000 chars), older ones as index lines (16 000 chars), beyond that a line naming the turn range and the history view `.corvin-history.md` (withheld turns appear there only as a note). Cuts move in steps of 8 turns; the block's header is constant and turn sections only append, so its prefix stays byte-stable for the prompt cache. | `_resolve_spawn_inputs`, after the user-model block |
| **`/new` fences.** Turns before the operator's last `/new` are not re-supplied; one constant line — present even when nothing else is re-supplied — says they are in the history view; `/new`'s reply says the history is kept. A `/new` is recorded even when no CLI state was left to wipe. Unwanted resets do NOT fence. | `uncovered_turns` / `last_manual_reset` |
| **Framed as data.** The block says its turns are a record, not instructions — it sits in the system prompt and must not lend old text system-prompt authority. | `render_from_records` |
| **Refusals stay refused.** A turn a gate answered (L44 house-rules and the other pre-spawn gates — `_turn_refused` sets a per-thread outcome) is recorded with `refused: <gate>` and its user text as `user_sha256` + `user_chars` only — the text itself is never stored, so neither a re-supply nor a worker reading the file can reach it. Side turns pass L44 before they are recorded (fail closed; `/task` passes `gated=True`, it already ran L44 on the instruction); the TDE delegation path runs L44 as well. The console persists a refusal BEFORE its first `yield` — a client disconnect closes the generator at a yield, and a refusal written after one was lost, leaving the user line unmarked (guard: `core/console/tests/test_gate_refusals_marked_for_ledger.py`). | `process_one`, `_ledger_record_side_turn`, `_render_turn` |
| **Group observers.** The framed observer transcript (Layer 16/17) is split off the owner's message (`adapter._split_observer_block`) and stored as `observer_text`, with the observers' ids (`observers: [{"user": …}]`) so their Art. 17 request finds the record. On re-supply the observers' lines are withheld once any of them no longer holds consent (`withhold=` hook on every engine path; asked only for turns being re-supplied, once per observer per spawn — `is_granted` runs the L16 chain gate). The owner's words stay; so does the recorded answer, which is the assistant's own text. The view applies the same hook to every turn. An observer's Art. 17 erasure REWRITES the record (their lines and id go, the owner's turn stays); a pre-R4 record that folded the lines into `user` is removed whole. A one-shot `/share` grants no standing consent, so its lines are re-supplied never. The CEL is handed the owner's text alone (retrieval, anchor goal); `adr_classifier` and `pipeline._maybe_apply_anchor` strip a framed block as a backstop. | `process_one`, `_ledger_consent_withhold` |
| **Data flow.** L34 per recorded turn, against the engine that will read the history (bridge `_ledger_data_flow_withhold`, composed with the consent hook in `_ledger_withhold`; console `_withhold` in `_session_ledger_block`): each turn's user text, observer lines and answer are classified on their own, under the persona the turn ran with (recorded as `persona`; an `inbox` persona's text is personal data — R10-6) (`classify_task` is pure; a `[class:…]` marker is read at the start of the text it was written in), each distinct class is checked once per spawn (`check_l34(classification=…)`, audited); a refused class withholds that turn — question AND answer — in the block and in the view, the rest of the chat stays; a classifier or gate error withholds the turn. The pre-spawn gate inspects only the new message, so a side turn or an engine switch could otherwise carry text the tenant's matrix forbids for that engine (review R7-4). A gate over the whole view (R8) hid every turn — after `/new` too — for one secret-looking string in one answer (R9-1). | the three spawn paths, console |
| **Every engine.** Codex/OpenCode keep no Claude transcript: their spawns get the block with nothing counted live (`engine_transcript=False`). `/btw` notes, `/task`/`/bg` commands and `/plugin-builder` interview answers are recorded too (`spawned: false`; a house-rules-refused `/task` as refused). A refused `/btw` is recorded refused (hash only). The console's `/btw` (`chat_runtime.inject_btw_web`, 2026-10-05) passes the same four pre-spawn gates as a turn first, and is stored in `turns.jsonl` with `btw: true`; `records_from_turn_log` turns it into the bridge's shape — its own `/btw …` record, `spawned: false`, before the turn it was injected into — and drops a gate-refused note entirely. Every completion notice is recorded in the originating bridge chat's ledger, ALWAYS derived from its channel and chat with the adapter's own candidate list (`session_state.claude_session_dirs`, legacy layouts included, R8-3) — never from a path in the queue record, and the queue (`pending_notifications/`) is path_gate-protected: a forged record wrote "the assistant said" into any chat's history (review R7-3). A background task's result is recorded when the completion notifier DELIVERS it (`completion_notify._record_in_ledger`, under the record's O_EXCL delivery lock — once). | `_call_*_via_engine`, btw / task / plugin-builder branches (`_ledger_record_side_turn`) |

The view always shows at least the newest re-supplied turn verbatim; budget
steps never push it into the index. The `/new` fence is also kept in the
counters sidecar, so the per-turn CEL session key does not parse the ledger.

Compactions are recorded as `compaction` boundaries after each turn
(`note_compactions`, idempotent by the boundary's uuid). Boundaries are labels
for the view and the audit trail — coverage never depends on them.

Audit (content-free): `session_ledger.boundary`,
`session_ledger.context_resupplied` (only when the re-supplied set changes),
`session_ledger.append_failed`. The CEL inspector snapshot (`cel-briefs/`) records the
ledger block's size only, never its text (bridge and console alike, R10-4) — it would be a second verbatim copy in the
worker's cwd that no withholding or erasure reaches (review R5-4).

The console web-chat applies the same renderer to its existing append-only
`turns.jsonl` (`chat_runtime._session_ledger_block`, `render_turn_log_context`)
— one rule, two surfaces, no second store. A console turn without a text answer
(cancelled, failed, artifact-only) is kept, marked "(no text answer)"; only the
in-flight last message is left out. Console slash commands the dispatcher answers
(`routes/chat.py` → `chat_runtime.record_side_turn`) are recorded before the reply is
sent, gated like a turn, `cli_spawned=False`. A pre-marker (v1) turn log's refusal is recognised by the tag every gate refusal starts with (`[house-rules]`, `[data-flow]`, `[egress]`, `[security]`). The streamed part of a cancelled answer is persisted, marked `[answer cancelled here]`. With `cel_cache_stable` on, the turn's message still reaches the ledger renderer (`ledger_prompt`), so an earlier unanswered message stays history (review R5-6). The worker is pointed at the generated view in the session workdir, never at `turns.jsonl` (which keeps a refused message's text for the chat window). The console's 50-chats-per-tenant cap still
deletes the oldest chat whole — a retention rule that predates the ledger.

**Tool-card diffs (ADR-2241, 2026-10-09).** For an Edit / MultiEdit / Write the console
streams `tool_use` with the tool-use `id`, then — when the tool actually RAN — a
`tool_diff` event carrying the hunks Claude Code computed itself
(`tool_use_result.structuredPatch` on the CLI's stream-json `user` event; a created file's
content as one all-added hunk). Nothing in CorvinOS diffs anything, and a denied or failed
tool (its result is an error string) shows no change. `chat_runtime._tool_result_diff`
bounds it (200 lines, 400 chars/line, `diff_truncated`; 400 000 chars per turn, then
`diff_withheld: turn_budget`) and withholds the WHOLE diff (`diff_withheld: credential |
scan_failed`) when the fail-closed source-text gate `core.pii.code_secrets` fires on the
shown lines, context lines included — a gate, not a scrubber. Personal data in a diff is
NOT gated (owner-only surface; see ADR-2241 Consequences). The diff is stored on the tool part in `turns.jsonl` so a reload shows it; it is
never a text part, so `records_from_turn_log` (text only) never re-supplies it to the worker,
and the audit chain keeps tool name + sequence only. The card renders it red/green
(`--diff-add` / `--diff-del` tokens) under a "Show changes" checkbox, checked by default.
**Bash changes (ADR-2241 amendment, 2026-10-10).** A `Bash` command (`sed -i`, a python
heredoc, `cat >`) has no `structuredPatch`, so its change is rebuilt from the files the command
NAMES, before vs. after — `console/corvin_console/bash_diff.py`. The console passes the CLI
`--settings <per-turn dir>/hook-settings.json` installing a `PreToolUse` hook on Bash (the same
file run as a script); the hook writes `<tool_use_id>.json` into `$CORVIN_BASH_DIFF_DIR` BEFORE
the command runs (a hook finishes before the tool starts — a snapshot taken when the stream
event arrives could race it). After the tool's result the console re-reads the same paths
(`asyncio.to_thread`), diffs (`difflib`, hunk headers `@@ <file> -a,b +c,d @@`), and feeds the
lines through the SAME bound + `code_secrets` gate + per-turn budget as an Edit
(`_finalize_diff_lines`). The hook never denies and never raises: a failure means no diff, never
a wrong one. Limits, stated: only files the command names (not a glob, `git checkout`, a build
tool's output); ≤12 files, ≤256 KB, ≤4000 lines each; no binaries; no `.git`/`node_modules`/
credential-named files (`.env`, `*.pem`, `id_rsa`, `*secret*` …); a concurrent writer of the same
file inside the tool window is attributed to the command. The snapshot dir is `0700`, removed in
the turn's `finally`, stale ones swept after 6 h. A failed command still shows what actually
changed. E2E: `core/console/tests/test_bash_diff.py` (a real subprocess that runs the real hook
and the real shell like the CLI does).
E2E: `core/console/tests/test_chat_diff_e2e.py` (real subprocess replaying a stream captured
from the claude CLI), `web-next/tests/unit/chat-tool-diff.test.tsx`.

**What it does not cover:** a turn whose recording fails (disk error — audited
as `append_failed`), and anything beyond the budget is in the view only as an
index line or a reference to the history view (the worker can Read/Grep
`.corvin-history.md` in its workdir — never the record itself; a persona in
`restricted` media mode cannot). E2E proof:
`shared/test_session_ledger_e2e.py` (adapter process + `session_reset.py`),
`tests/e2e/test_session_ledger_console_e2e.py` (console WebSocket).

### CEL session identity on the bridge

The adapter used to call the CEL with `session=None`, which pooled every chat
of a tenant into ONE `_nosession` anchor bucket (live: the anchor flag is on
via the console overlay). `_cel_session(channel, chat_key)` now passes
`<channel>:<chat>` plus `#<seq>` of the last `/new` from the ledger, so an
explicit start-over gets a fresh anchor and an unwanted reset keeps it; and
`pipeline._maybe_apply_anchor` / `maybe_capture_decision_point` write NOTHING
when there is no session key. A refused turn's candidate goal is discarded on
both surfaces (`pipeline.discard_pending_goal`, R8-CEL-4), and the store is
path_gate-protected (`cel_anchors` component). The goal is exempt from the fact CAP — evicting it
let the next turn re-arm a "goal" from whatever was said last (review R7-2).
Retrieved decision records are anchored as
`constraint`, never as `decision` — that kind is reserved for an option menu
the assistant actually offered, which they used to evict (review R6-3). The promoted goal carries the `sender` of the
turn it came from, so GDPR erasure for that person finds it in a group chat's
store (named after the chat, not after them). A turn's task is only a CANDIDATE goal until the
turn was answered (`anchor.set_pending_goal` inbound, `promote_pending_goal` in
the outbound hook, which both surfaces skip for refused turns, and which promotes
the candidate only when it IS the task of the turn being answered — a delegated
turn answered without the inbound hook must not promote a refused one) — the inbound
hook runs before the acceptable-use gates, and a refused task must never become
a goal re-injected every turn. On the active pipeline the anchor block is folded
into the synthesised prompt inside `_gate2_and_bind`, so Gate-2 inspects it and
both surfaces deliver it; the injection counter counts only after the gate.
E2E: `shared/test_cel_anchor_bridge_e2e.py` (per-chat stores AND the goal in the
worker prompt), `tests/e2e/test_cel_anchor_console_active_e2e.py`.

### `corvin_operator/forge/paths.py` was shadowing `bridges/shared/paths.py`

Removed the same day. It was a nine-line stub ("stub for audit_metrics
compatibility") whose symbols nothing imported — `audit_metrics` uses the
package-qualified `forge.paths` — but it sat on the top-level name `paths`, so
any process that put `corvin_operator/forge/` earlier on `sys.path` got it instead of
the real resolver. About 25 modules under `bridges/shared/` do
`from paths import corvin_home` (or `tenant_global_dir` / `voice_dir`) at import
time and raised ImportError under the shadowed name. Two mattered to the reset
and were failing into their own `except Exception`:

* `context_budget` — the Layer-20 quota was never unregistered, so `/new`
  reported `token budget reset: no` no matter what.
* `instance_identity` — the `session.reset` audit event shipped without its
  instance signature.

`session_reset.py` additionally re-asserts its own directory at the front of
`sys.path` after adding the forge tops, so the shadowing cannot come back
through a caller's path order.

---

## The channel list is one list (2026-07-28)

`corvin_operator/bridges/shared/channels.py::BRIDGE_CHANNELS` is the canonical set of
shipped messenger channels — currently seven: whatsapp, telegram, discord, slack,
email, signal, teams. `CHANNEL_LABELS` holds their short display names.

It exists because the list was copy-pasted into six places and three had gone
stale the same way, all omitting `signal` and `teams`:

| Copy | Consequence of the omission |
|---|---|
| `session_reset.VALID_CHANNELS` | `/new` + `/reset` died with `argparse: invalid choice: 'signal'`; the user saw "session reset failed" and the session was never reset |
| `settings_view._BRIDGE_CHANNELS` | `/settings` omitted the only row about a Signal/Teams operator's own bridge (its comment claimed "four-channel set", stale twice over) |
| `bridges_migrate._CHANNELS` | legacy pre-ADR-0008 state for both channels was never migrated |
| `CorvinInstaller.BRIDGES` | a fresh install could never select either bridge |
| installer's Windows uninstall sweep | their Scheduled Tasks kept auto-launching a bridge after uninstall |

`bridge_manager._CHANNELS` already held the correct seven, next to a comment
recording an *earlier* incarnation of the identical bug ("the console saved their
settings and then NOTHING could ever start the daemons"). Two independent
occurrences of one omission is what made this a test rather than a fix.

`corvin_plugins.bridges.supervisor.BRIDGE_CHANNELS` deliberately keeps a separate
copy — it lives in a different distribution package that must import without
`operator/` on `sys.path` — and `shared/test_channel_list_ssot.py` pins the two
together, plus every consumer above, plus both directions against
`corvin_operator/bridges/*/daemon.js` on disk.

**Must NOT do:** don't hand-write a channel list. Don't re-add a private
`_BRIDGE_CHANNELS` frozenset to any `paths.py` — channel identity there is a
CHARSET rule (`_BRIDGE_CHANNEL_RE`), and the four frozensets that used to sit
there were assigned, never read, and stale, so readers took them for canonical.
