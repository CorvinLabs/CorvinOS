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
| `voice` | after a turn, when its audio is archived | separate forwarder (ADR-0194) |
| `pong`, `info` | replies | `info` for a `cancel` outside the socket's own turn: `"stopping the running turn"` or `"no active turn"` |

Client → server: `user {text, voice_on}`, `cancel`, `ping`.

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
