# Console web chat — the stream WebSocket and live turns

`WS /v1/console/chat/sessions/{sid}/stream` (`core/console/corvin_console/routes/chat.py::chat_stream`).
Same-origin cookie auth (`corvin_console_sid`); the session must belong to the cookie's tenant.

## Invariant (ADR-2244)

**A running turn's output reaches every socket of its chat, including sockets that connect
while it runs.** A tab that reloads mid-turn (the `console_auto_reload` redeploy reload, F5),
reconnects after a network blip, or is opened as a second tab shows the running turn live and
its final answer when it ends — without a manual reload.

Why this is load-bearing: until 2026-10-09 a turn streamed only to the socket that started it.
The turn outlived that socket (`_detach_turn`) and was persisted, but a reloaded tab's new
socket received nothing, and the client-side recovery could not apply the record (see
"Client" below). Measured: a redeploy reloaded the operator's tab 8 s into a 95 s turn; the chat
then showed no agent output at all.

## Server → client events

| type | when | notes |
|---|---|---|
| `ready` | on connect | `{session}` projection |
| `turn_attach` | on connect while a turn runs, or when another socket starts a turn | `{turn_id, started_at, truncated}`; followed by the **replay** of every event the turn produced so far, then live events |
| `turn_started` | first event of a turn, to the socket that started it | `{turn_id}` — the id a later `turn_attach` carries, so the same page rebuilds its bubble instead of doubling it |
| `delta`, `tool_use`, `tool_diff`, `result`, `artifact`, `notice`, `engine`, `engine_progress`, `language`, `bg_status`, `session_title`, `ccc_action`, `error` | during a turn | produced by `chat_runtime.stream_turn`; fanned out to attached sockets too |
| `done` | end of a turn | the starting socket: `{type: "done"}`; attached sockets: `{type: "done", attached: true, turn_id}` |
| `turn_resync` | an attached socket fell `live_turns.MAX_QUEUE` events behind | it receives nothing more for this turn; its `done` reconciles |
| `voice` | after a turn, when its audio is archived | separate forwarder (ADR-0194). Carries `msg_id` when the `/voice/tts` request named one (the per-message "Voice summary" button): the client attaches the player to THAT message, also after a reload, and ignores an id it does not hold; without `msg_id` it attaches to the current, else the last completed turn |
| `pong`, `info` | replies | `info` for a `cancel` outside the socket's own turn: `"stopping the running turn"` or `"no active turn"` |

Client → server: `user {text, voice_on}`, `cancel`, `ping`.

**Host and Origin (review SRF-1, 2026-11-05).** The socket is refused with close code 1008
before the route runs when its `Host` is not a loopback name, an IP literal or an
operator-allowlisted host, or when it carries an `Origin` that is not the same host:port as
its `Host`, the vite dev server (`localhost:5173`) or an entry of `CORVIN_ALLOWED_ORIGINS`
(`corvin_console/host_guard.py`; see `console-security-surfaces.md` § DNS rebinding).

### Model and token line (ADR-2263)

* `engine` (OS engine, before the spawn) carries `routed_model`: the model
  `model_selector.resolve_os_model` chose. It names the model of a RUNNING turn.
* `turn_usage` (after the CLI `result`) carries `{model, routed_model, usage}`: `model` is the
  model the CLI CONFIRMED in its `init` event only (never filled from `routed_model`), `usage`
  the raw `result.usage` with every unreported count `null`, never 0.
* The same payload is persisted as `turn_usage` on the final assistant turn in `turns.jsonl`
  (`chat_runtime._append_turn(turn_usage=...)`); `GET /chat/sessions/{sid}/turns` passes it
  through and `hydrateChatTurn` rebuilds the line, so a reload shows what the live stream showed.
  Only a turn that reached `turn_usage` has it: a cancelled or failed turn persists none.
* The badge (`web-next/src/lib/turn-usage.ts`): after `turn_usage`,
  `Model: <confirmed> · Tokens: <in> in / <out> out · Cache: …`, with `(routed: X)` only when the
  routed model differs from the confirmed one (a dated snapshot equals its family alias) or the
  confirmed one is unknown; `unknown` only for a value the engine did not report. While the turn
  runs: `Model: <routed> (routed) · Tokens: pending` (`formatPendingTurnUsage`) — never
  "unknown". A turn that ENDS without `turn_usage` shows the engine label, the same line a reload
  shows for it.
* Proofs: `core/console/tests/test_chat_turn_usage_stream_e2e.py` (real WebSocket route + fake
  claude subprocess; `routed_model` on `engine`, `turn_usage` and the persisted turn),
  `web-next/tests/unit/chat-turn-usage.test.ts`, `tests/integration/chat/chat-page.test.tsx`.

### Per-message "Voice summary"

Every finished assistant message without an archived player shows a "Voice summary" button
(`VoiceSummaryFooter`, `pages/chat.tsx`). It calls `/voice/tts` with the message text, the chat
`sid` and `msg_id` = that message's id; the route archives the audio and echoes `msg_id` in the
live `voice` event, so the player replaces the button on the CLICKED message (before 2026-11-05 it
landed on the newest message, and after a reload not at all). After a reload the archived audio
is matched to its turn by the text key (`attach_voice_artifacts`). A stale CSRF token on
`/voice/tts` is retried ONCE with the token from `/auth/whoami` (`ttsBlob`,
`tests/unit/tts-csrf-retry.test.ts`); a second refusal is surfaced, never bypassed.
Proofs: `core/console/tests/test_voice_summary_target_message.py` (HTTP),
`web-next/tests/unit/lib/chat-registry.test.ts`, `tests/integration/chat/chat-page.test.tsx`.

## The hub (`corvin_console/live_turns.py`)

* `_run_turn` calls `begin` → `publish` (every event, **also when its own socket is gone**) →
  `end` exactly once, after `stream_turn` closed — i.e. after the turn was persisted, so an
  attached socket's `done` can read the finished record.
* Every socket `subscribe`s on connect; snapshot (`turn_attach` + buffered events) and queue are
  taken under one lock and the snapshot is sent before the queue is drained: nothing lost,
  nothing doubled. The starting socket is excluded from fan-out by its token.
* Bounds: replay buffer `MAX_BUFFER_BYTES` (4 MiB; past it `truncated: true`), per-subscriber
  queue `MAX_QUEUE` (4096; past it one `turn_resync`). Either way the view reconciles with the
  record at `done` — the replay is a live convenience, `turns.jsonl` is the truth.
* A stale owner cannot publish into or end a newer turn (turn-id check).
* In-process: both hosts run the chat WebSocket and its turns in one process. Nothing is
  persisted by the hub; the turns file, session ledger and audit chain are unchanged.
* Stop from an attached tab: `cancel` → `live_turns.request_cancel` + `chat_runtime.cancel_live_turn`
  (cancels without unregistering; the turn's teardown unregisters). The starting socket then
  closes the turn without the "This chat was deleted." message a deletion produces.

## Client (`web-next/src/lib/chat-registry.ts`)

* **The server's history is the record.** `loadHistory` replaces what the tab holds unless the
  page is streaming a turn; then the turn's live messages are kept after the record (a live
  user message the record already holds is not doubled).
* The sessionStorage mirror (restored on page load so a reload does not flash an empty chat)
  marks its messages `restored`: never live, never counted against the record, replaced as soon
  as the history arrives. Before 2026-10-09 a restored `[user, empty placeholder]` both blocked
  `loadHistory` and defeated `refreshHistory`'s length comparison against the finished
  `[user, answer]` — the answer never appeared.
* **sessionStorage mirror write schedule** (`chat-registry.ts`, `chat-message-persistence.ts`):
  full list at turn end (idle callback) and on `pagehide` / hidden tab; while a turn streams, only
  the newest 50 messages, at most every 2 s. Writes happen only when the message list changed
  (identity), never per delta. A quota error keeps the newest 20 messages and logs a warning;
  a failed write is logged, not swallowed. A reload mid-answer restores the checkpoint, and the
  server's history replaces it when read. Before 2026-10-20 a 400 ms debounce reset on every
  delta, so nothing was written while a stream ran longer than that.
* `refreshHistory` compares by content fingerprint (role, part count, text), not length; it
  never applies a snapshot shorter than the page's own non-restored content.
* `turn_attach` creates (or, for a known `turn_id`, resets) the assistant bubble and marks the
  entry `needsReconcile`; the attached `done` re-reads the record via the reader the chat page
  registers (`registerHistoryReader`).
* `useRefreshOnTaskEnd` (fallback when no socket was attached) retries while the record still
  ends on the user's message: `chat_runtime` records `task.completed` before it appends the
  assistant turn.
* `useBuildFreshness` defers the auto-reload while `anyChatStreaming()` (or the operator types)
  and reloads once both are clear.

## Tests

* `core/console/tests/test_chat_live_turn_attach.py` — real WebSocket route: a socket connecting
  mid-turn gets `turn_attach` + replay + live + attached `done`; no attach after the turn;
  stop from the attached tab; hub bounds; stale-owner isolation.
* `web-next/tests/unit/chat-live-turn-reattach.test.ts` — the restored-mirror case, re-attach
  without doubling, history arriving after the replay, reconcile on attached `done`.

## Slash commands and the per-chat audit views (gap/chat, 2026-10-24)

**Every palette entry acts.** `SlashCommandPalette.tsx::SLASH_COMMANDS` lists only
commands with a handler: the deterministic dispatcher (`slash_commands.py`: `/help /whoami
/role /quota /engine /audit /federation /plugin-builder`), the WebSocket loop (`/btw`,
`/resume`), the turn pipeline (`/delegate /use-engine /engine-auto /debug-engine`,
`/create task`, `/erase`) or the composer itself (`/new` creates a chat; `/stop`). Bridge-only
commands (`/go /propose /share /persona`) and pointer-only ones (`/skills /memory
/dialectic-* /forget /clear /reset`) are not offered; typed by hand they still get an honest
one-line answer, never a model turn. `/engine` only shows the configured engine (the engine is
tenant-wide, set in the Engines tab). `/audit [last <n>]` reads the newest records of the
tenant chain (`tenant_audit_chain`) and shows event type + time only.

**CCC commands answer themselves.** A typed `/create ...` or `/erase ...` is dispatched by the
turn pipeline even with `ccc_command_routing` off (the flag only governs extraction from free
text), needs no engine, and ENDS the turn with the command's own result: no model turn.
`/create workflow|tool|skill` return status `not_available` with no entity id (they used to
invent `wf_/tool_/skill_` ids and answer "queued"); `/erase` is two-step (see
[layer-36-erasure.md](layer-36-erasure.md)).

**The three per-chat audit views read the writer's chain.** `os-turns`, `engine-spans` and
`execution-log` (and the WDAT graph / worker trace) read
`routes/chat.py::_session_chain_paths` = `tenant_audit_chain(tenant_id)` first, then the
pre-ADR-0654 files as read-only history, always filtered by the chat's `chat_key` / run dir.
Before, they read the host-global bridge file and a tenant-level file that no current writer
appends to, so a fresh chat turn never appeared (S-11b). The console's engine-span END now
carries `chat_key` like the START, otherwise the view never paired it.

**Audit panel.** The chat header carries the `Audit` button (`data-testid="chat-audit-toggle"`)
and the panel a close control (`chat-audit-close`); Auto-Repair (`Debug Log` view) sends the
CSRF token (`repairSession(sid, csrf, dryRun)`).

Proof: `tests/sandbox/test_gap_chat.py` (real uvicorn console, HTTP + WebSocket),
`web-next/tests/e2e/chat-gap-release.spec.ts` (Playwright: audit opener/close, Auto-Repair,
palette, `/new`, `/audit`, `/create workflow`), `web-next/tests/unit/repair-session-csrf.test.ts`,
`web-next/tests/unit/slash-command-insertion.test.ts`.
