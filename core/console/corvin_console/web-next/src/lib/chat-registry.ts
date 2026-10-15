/**
 * Module-level registry of persistent WebSocket chat sessions.
 *
 * Problem solved: ChatPane is keyed by sid and unmounts on every chat switch,
 * which closes the WebSocket mid-stream. By moving the WS into a module-level
 * Map we keep the connection alive across unmounts so streaming continues in
 * the background.
 *
 * Consumers (ChatPane) subscribe to state changes and to raw stream events
 * (for TTS and title side-effects). They never manage the WS lifecycle.
 */

import { parseTurnUsage, type TurnUsage } from "@/lib/turn-usage";
import { useSyncExternalStore } from "react";
import { emitCCCEvent } from "./ccc-bus";
import { getBool, PREF_KEYS } from "./preferences";
import { persistMessages, loadPersistedMessages, clearPersistedMessages } from "./chat-message-persistence";
import { insertSteerNote, retrySteerState, setSteerState, settleOnDone, type BtwInfo, type BtwState } from "./btw-steer";

// ── Types ────────────────────────────────────────────────────────────────────

export type MessagePart =
  | { kind: "text"; text: string }
  // ADR-2241: an executed Edit/MultiEdit/Write gets the CLI's own diff (or the
  // reason it is withheld) from a `tool_diff` event keyed by the tool-use id.
  | { kind: "tool"; name: string; input: Record<string, unknown>; id?: string;
      diff?: string; diffTruncated?: boolean; diffWithheld?: string }
  | { kind: "artifact"; name: string; path: string; mime: string; size: number; sid: string; label?: string }
  // Live-only, never persisted in turns.jsonl: an in-band runtime message about
  // THIS turn that is not part of the model's answer — an ACS quota fallback,
  // an engine degrade, an artifact-list truncation. The backend has emitted
  // `{"type":"notice"}` since ADR-0201, and until 2026-07-28 nothing in the
  // frontend handled the event at all: every one of them was dropped on the
  // floor, so `quota_fallback`, `acs_fallback` and `artifacts_truncated` were
  // "announced" only in the server's own logs.
  | { kind: "notice"; subtype: string; text: string };

/** ADR-0214 Phase 2: TDE progress metrics for audit graph visualization.
 *
 * ADR-0215 honesty note: token_savings_pct is always `null` (no real
 * per-call token-usage instrumentation exists yet) — render "not measured",
 * never treat a missing/null value as 0% savings. latency_delta_pct IS a
 * real, measured wall-clock comparison (delegated vs. local steps within
 * this turn) and safe to render as-is.
 */
export interface TdeProgress {
  run_id: string;
  total_steps: number;
  completed_steps: number;
  delegated_count: number;
  local_count: number;
  l34_forced: boolean;
  token_savings_pct?: number | null;
  token_usage_instrumented?: boolean;
  // ADR-0219 R2: real measured token numbers (null until instrumented).
  total_tokens?: number | null;
  tokens_delegated?: number | null;
  tokens_local?: number | null;
  tokens_by_model?: Record<string, number> | null;
  tokens_by_kind?: Record<string, number> | null;
  cost_usd?: number | null;
  latency_delta_pct?: number | null;
  /** ADR-0216 shared agentic-compute pool: today's usage/cap for this run's
   *  chokepoint. quota_limit is `null` on an unlimited tier — render
   *  "unlimited" (omit the "/N" suffix), never fabricate a cap. */
  quota_used_today?: number;
  quota_limit?: number | null;
  /** From the turn's InitialAnalysisRequest.classification. */
  task_type?: string;
  complexity?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  parts: MessagePart[];
  ts: number;
  streaming?: boolean;
  error?: string;
  /** ADR-0214: agentic-compute engine that produced this turn
   *  ("claude_code" | "acs" | "tiered_delegation"). Live-only
   *  (stamped from the `engine` stream event; not persisted in turns.jsonl). */
  engine?: string;
  /** Human-readable engine label for the badge. */
  engineLabel?: string;
  /** Model + token usage the engine reported for this turn (`turn_usage` event).
   *  Live-only like `engine`; `null` = the engine reported none ("unknown"). */
  turnModel?: string | null;
  turnUsage?: TurnUsage | null;
  turnUsageSeen?: boolean;
  /** ADR-0214: TDE turn correlation id ("tde-<epoch>-<hex>"), stamped from
   *  the `engine` stream event when this turn ran through the Tiered
   *  Delegation Engine. Lets the Audit panel's TDE Graph tab find the
   *  matching GET /compute/tde/{run_id}/graph payload. Live: `engine` +
   *  `engine_progress` events; reload: re-derived from the persisted
   *  tde_progress.run_id during history hydration (k=8). */
  tdeRunId?: string;
  /** ADR-0214 Phase 2: TDE delegation progress (steps, counts, L34 status).
   *  Persisted to turns.jsonl so audit graph survives reload/reconnect.
   *  Only present on TDE turns (when engine === "tiered_delegation"). */
  tdeProgress?: TdeProgress;
  /** Live-turn id from `turn_started` / `turn_attach` (live_turns.py): lets a
   *  reconnecting socket find the bubble of the turn it re-attaches to. */
  turnId?: string;
  /** Restored from the sessionStorage mirror after a page load: what the tab
   *  last SAW, not the record. Never live, and superseded by the server's
   *  history as soon as it arrives. */
  restored?: boolean;
  /** CONCEPT-0107: this user message is a /btw steering note for a running
   *  turn — its delivery state, how it was given and its client id. */
  btw?: BtwInfo;
}

export interface StreamEvent {
  type: "ready" | "delta" | "tool_use" | "result" | "error" | "done" | "info" |
        "pong" | "session_title" | "artifact" | "ccc_action" | "voice" | "engine" |
        "engine_progress" | "notice" | "language" | "bg_status" | "tool_diff" |
        "turn_started" | "turn_attach" | "turn_resync" |
        "btw_flushed" | "btw_undelivered" | "btw_window_closed" | "turn_usage";
  /** `turn_usage`: confirmed model id (null if unknown). */
  model?: string | null;
  /** `btw_flushed` / `btw_undelivered`: the steering note's client id. */
  client_id?: string;
  /** `done` only: the turn was stopped (e.g. from another tab), not finished. */
  cancelled?: boolean;
  /** `turn_started` / `turn_attach` / attached `done`: the live turn's id. */
  turn_id?: string;
  /** `done` only: this socket did not start the turn, it attached to it —
   *  its view must reconcile with the persisted record (live_turns.py). */
  attached?: boolean;
  /** `turn_attach` only: the replay is incomplete (buffer cap). */
  truncated?: boolean;
  /** `notice` only: which runtime message this is ("quota_fallback",
   *  "acs_fallback", "artifacts_truncated"). Rendered as a distinct chip so a
   *  degrade is never mistaken for part of the model's answer. */
  subtype?: string;
  text?: string;
  name?: string;
  input?: Record<string, unknown>;
  /** `tool_use` (file tools) / `tool_diff`: the tool-use id the diff belongs to. */
  id?: string;
  /** `tool_diff` only (ADR-2241): unified-diff hunks Claude Code computed for an
   *  EXECUTED Edit/MultiEdit/Write, bounded; or why it is withheld. */
  diff?: string;
  diff_truncated?: boolean;
  diff_withheld?: string;
  message?: string;
  title?: string;
  path?: string;
  mime?: string;
  size?: number;
  label?: string;  // M5 (ADR-0170): provenance badge ("Graph", "live", "compute")
  session?: unknown;
  usage?: Record<string, unknown>;
  // ADR-0194: set on a `result` event that is NOT the final one — a second,
  // annotated result is guaranteed to follow. Consumers that act on the reply
  // (notably TTS, which costs a real synthesis per call) must wait for the
  // final event; consumers that only render text can use either.
  annotation_pending?: boolean;
  // ADR-2236: a turn whose claude process keeps background tasks alive (Bash/Monitor/
  // Agent in the background) emits one `result` per wake-up. `interim` marks every one
  // that is NOT the closing message (do not speak it, do not treat the turn as over);
  // exactly one `final` result closes the turn and is what gets spoken.
  interim?: boolean;
  final?: boolean;
  pending_children?: number;
  // `bg_status` event: how many background tasks are open right now (kind/age only).
  open?: number;
  children?: { id?: string; kind: string; state: string; age_s: number; label?: string }[];
  // ADR-0214: engine event — which agentic-compute engine runs this turn.
  // Last event of a turn wins (fallback paths re-stamp the actual engine).
  engine?: string;
  // ADR-0214: TDE turn correlation id, present only on "engine" events
  // emitted by the TDE turn path (chat_runtime.py::_stream_tde_turn).
  tde_run_id?: string;
  // ADR-0214 TDE Phase 1: engine_progress event emitted on TDE turn completion.
  // Fields: total_steps, completed_steps, delegated_count, local_count.
  // Used by chat badge + audit graph for visualization.
  //
  // ADR-0215 honesty note (2026-07-24): token_savings_pct is ALWAYS `null`
  // for now — the TDE pipeline has no real per-call token-usage
  // instrumentation (see nerve_builtins.py's TokenSavingsFiber docstring).
  // A prior version of this field silently defaulted to 0 (looked like a
  // real "0% savings" measurement); it is now explicitly nullable so
  // consumers can render "not measured" instead of a fabricated number.
  // latency_delta_pct IS a real, measured value (delegated vs. local
  // per-step wall-clock duration_ms within this turn) — safe to render.
  run_id?: string;
  total_steps?: number;
  completed_steps?: number;
  delegated_count?: number;
  local_count?: number;
  token_savings_pct?: number | null;
  token_usage_instrumented?: boolean;
  // ADR-0219 R2: real measured token numbers (null until instrumented).
  total_tokens?: number | null;
  tokens_delegated?: number | null;
  tokens_local?: number | null;
  tokens_by_model?: Record<string, number> | null;
  tokens_by_kind?: Record<string, number> | null;
  cost_usd?: number | null;
  latency_delta_pct?: number | null;
  l34_forced?: boolean;
  // ADR-0216 shared agentic-compute pool fields + analysis classification
  // (badge concept, docs/claude-ref/tde-graph-concept.md).
  quota_used_today?: number;
  quota_limit?: number | null;
  task_type?: string;
  complexity?: string;
  // ccc_action fields (ADR-0168 M3)
  action_id?: string;
  entity_type?: string;
  entity_id?: string | null;
  status?: string;
  payload?: Record<string, unknown> | null;
}

/** One background child of the running turn (ADR-2236 `bg_status`). */
export interface BgChild {
  id: string;
  /** bash | monitor | agent | workflow | other */
  kind: string;
  /** running | completed | failed | … */
  state: string;
  /** Scrubbed one-line description (server-side). */
  label: string;
  /** Age in seconds when the event was sent. */
  ageS: number;
}

export interface SessionState {
  messages: ChatMessage[];
  streaming: boolean;
  error: string | null;
  /** Background tasks of the running turn that have not ended yet (ADR-2236). */
  bgOpen: number;
  /** The turn's background children (ADR-2236 `bg_status`) with the time the list arrived. */
  bgChildren: BgChild[];
  bgChildrenAt: number;
  /** True while a reconnect attempt is in progress (backoff timer fired, WS connecting). */
  reconnecting: boolean;
  /** Full text of the last completed result (for TTS). Cleared when a new send starts. */
  latestResultText: string | null;
  /** Pending title from a session_title event (so ChatPane can update React Query cache). */
  pendingTitle: string | null;
  /** CONCEPT-0107: false once the running reply can no longer be steered
   *  (`btw_window_closed` — its first answer closed the CLI's input). */
  steerOpen: boolean;
}

// ── Frontend structured debug log ─────────────────────────────────────────────
// Keeps the last MAX_DBG events in memory per session (sessionStorage-backed).
// Read via chatDebugLog(sid) for the in-browser debug panel.
const MAX_DBG_EVENTS = 500;
const _dbgBuffers = new Map<string, object[]>();
// Debounced sessionStorage flush: batch writes to max 1 per 300 ms per session.
// Without this, every streaming delta caused a synchronous JSON.stringify(100 events)
// + sessionStorage.setItem on the main thread — a constant I/O bottleneck.
const _dbgFlushTimers = new Map<string, ReturnType<typeof setTimeout>>();

function _dbg(sid: string, event: string, fields: Record<string, unknown> = {}): void {
  const rec = { ts: new Date().toISOString(), event, sid, ...fields };
  // Structured browser console (coloured by category)
  const prefix = event.startsWith("ws.") ? "🔌" :
                 event.startsWith("stream.") ? "⚡" :
                 event.startsWith("tool.") ? "🔧" :
                 event.startsWith("msg.") ? "📨" :
                 event.startsWith("error") ? "🚨" : "📋";
  console.debug(`[corvin-chat ${prefix}] ${event}`, rec);
  // In-memory buffer (per session, capped)
  let buf = _dbgBuffers.get(sid);
  if (!buf) { buf = []; _dbgBuffers.set(sid, buf); }
  buf.push(rec);
  if (buf.length > MAX_DBG_EVENTS) buf.splice(0, buf.length - MAX_DBG_EVENTS);
  // Debounced sessionStorage write — flush at most once per 300 ms.
  const existing = _dbgFlushTimers.get(sid);
  if (existing) clearTimeout(existing);
  _dbgFlushTimers.set(sid, setTimeout(() => {
    _dbgFlushTimers.delete(sid);
    try {
      const b = _dbgBuffers.get(sid);
      if (b) sessionStorage.setItem(`corvin_dbg_${sid}`, JSON.stringify(b.slice(-100)));
    } catch { /* storage quota — ignore */ }
  }, 300));
}

/** Return the in-memory debug log for a session (for the Debug panel). */
export function chatDebugLog(sid: string): object[] {
  try {
    const stored = sessionStorage.getItem(`corvin_dbg_${sid}`);
    if (stored) {
      const parsed: object[] = JSON.parse(stored);
      const mem = _dbgBuffers.get(sid) ?? [];
      // Merge: stored is the persisted tail, mem may have newer events
      if (mem.length === 0) return parsed;
      return mem;
    }
  } catch { /* ignore */ }
  return _dbgBuffers.get(sid) ?? [];
}

// ── Internal data ─────────────────────────────────────────────────────────────

interface SessionEntry {
  ws: WebSocket | null;
  currentAssistantId: string | null;
  // Set to currentAssistantId's value right before "done" clears it — the only
  // way a late "voice" event (published by /voice/tts AFTER the WS already sent
  // "done" for the turn — see chat_runtime.publish_voice_event) can still find
  // the message to attach its player to.
  lastAssistantId: string | null;
  historyLoaded: boolean;
  // Reconnect state
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  reconnectDelay: number;   // current backoff ms (1 s → 2 s → 4 s → 30 s cap)
  reconnecting: boolean;
  // Heartbeat keep-alive (prevents 300s idle timeout)
  heartbeatInterval: ReturnType<typeof setInterval> | null;
  // Mutable working copy of state fields:
  messages: ChatMessage[];
  streaming: boolean;
  error: string | null;
  bgOpen: number;
  bgChildren: BgChild[];
  bgChildrenAt: number;
  latestResultText: string | null;
  pendingTitle: string | null;
  steerOpen: boolean;
  /** Set when this view attached to a turn another socket started, or fell
   *  behind it: reconcile with the server's record when the turn ends. */
  needsReconcile: boolean;
}

const sessions = new Map<string, SessionEntry>();

// Monotonic counter for message IDs — avoids Date.now() collisions when
// two IDs are generated within the same millisecond tick.
let _msgSeq = 0;
const _nextMsgId = () => ++_msgSeq;

/**
 * Per-session snapshot for useSyncExternalStore.
 * A NEW SessionState object is created on each mutation so Object.is detects
 * the change and React re-renders correctly.
 */
const snapshots = new Map<string, SessionState>();

/** State-change subscribers: called on every message/streaming/error update. */
const stateListeners = new Map<string, Set<() => void>>();

/** Raw-event subscribers: called for every StreamEvent (TTS, title, etc.). */
const eventListeners = new Map<string, Set<(evt: StreamEvent) => void>>();

// ── Internal helpers ──────────────────────────────────────────────────────────

function getOrCreate(sid: string): SessionEntry {
  if (!sessions.has(sid)) {
    // Root cause (analysis 2026-09-28): this Map is module-level, which keeps
    // messages alive across a chat SWITCH (route unmount/remount) within one
    // page load — but a full page (re)load re-evaluates this module from
    // scratch, so the Map starts empty again. That reload happens more often
    // than it looks: the console's own auto-reload-on-new-build watcher
    // (`console_auto_reload`, see CLAUDE.md "Console Frontend") reloads an
    // open tab whenever a new build lands, which during active development
    // silently wiped every in-flight chat's last output. Rehydrate from the
    // sessionStorage mirror written below so a reload restores exactly what
    // the tab last saw, not an empty chat.
    sessions.set(sid, {
      ws: null,
      // A restored message is never live: this page has no socket on the turn
      // that produced it (live_turns.py re-attaches a running one), and the
      // server's history replaces it as soon as it is read.
      messages: (loadPersistedMessages(sid) ?? []).map((m) => ({
        ...m, streaming: false, restored: true,
      })),
      streaming: false,
      error: null,
      bgOpen: 0,
      bgChildren: [],
      bgChildrenAt: 0,
      latestResultText: null,
      pendingTitle: null,
      steerOpen: true,
      currentAssistantId: null,
      lastAssistantId: null,
      historyLoaded: false,
      reconnectTimer: null,
      reconnectDelay: 1_000,
      reconnecting: false,
      heartbeatInterval: null,
      needsReconcile: false,
    });
  }
  return sessions.get(sid)!;
}

// Debounced sessionStorage flush for message persistence — mirrors the
// `_dbgFlushTimers` pattern below: batching avoids a synchronous
// JSON.stringify + sessionStorage.setItem on every streamed token.
const _persistFlushTimers = new Map<string, ReturnType<typeof setTimeout>>();

function _schedulePersist(sid: string): void {
  const existing = _persistFlushTimers.get(sid);
  if (existing) clearTimeout(existing);
  _persistFlushTimers.set(sid, setTimeout(() => {
    _persistFlushTimers.delete(sid);
    const entry = sessions.get(sid);
    if (entry) persistMessages(sid, entry.messages);
  }, 400));
}

function makeSnapshot(entry: SessionEntry): SessionState {
  return {
    messages: entry.messages,
    streaming: entry.streaming,
    error: entry.error,
    bgOpen: entry.bgOpen,
    bgChildren: entry.bgChildren,
    bgChildrenAt: entry.bgChildrenAt,
    reconnecting: entry.reconnecting,
    latestResultText: entry.latestResultText,
    pendingTitle: entry.pendingTitle,
    steerOpen: entry.steerOpen,
  };
}

function notifyState(sid: string) {
  // Any other state change delivers right now and supersedes a pending delta tick.
  const pending = _deltaNotifyTimers.get(sid);
  if (pending) {
    clearTimeout(pending);
    _deltaNotifyTimers.delete(sid);
  }
  const entry = sessions.get(sid);
  if (entry) {
    // Replace snapshot with a new object so Object.is detects the change.
    snapshots.set(sid, makeSnapshot(entry));
    _schedulePersist(sid);
  }
  stateListeners.get(sid)?.forEach((fn) => fn());
}

// Streamed text arrives token by token. Re-rendering the whole chat pane
// (and re-parsing the growing message's markdown) for every token competes
// with the composer's keystrokes for the main thread, so deltas update the
// snapshot at once (a read in between sees the latest text) but wake the
// subscribers at most once per DELTA_NOTIFY_MS.
const DELTA_NOTIFY_MS = 40;
const _deltaNotifyTimers = new Map<string, ReturnType<typeof setTimeout>>();

function notifyDelta(sid: string) {
  const entry = sessions.get(sid);
  if (!entry) return;
  snapshots.set(sid, makeSnapshot(entry));
  _schedulePersist(sid);
  if (_deltaNotifyTimers.has(sid)) return;
  _deltaNotifyTimers.set(sid, setTimeout(() => {
    _deltaNotifyTimers.delete(sid);
    stateListeners.get(sid)?.forEach((fn) => fn());
  }, DELTA_NOTIFY_MS));
}

function notifyEvent(sid: string, evt: StreamEvent) {
  eventListeners.get(sid)?.forEach((fn) => fn(evt));
}

/** Apply a single StreamEvent to the session's message list (pure mutation). */
function applyEvent(entry: SessionEntry, sid: string, evt: StreamEvent): void {
  switch (evt.type) {
    case "ready":
      return;

    case "delta": {
      const aid = entry.currentAssistantId;
      if (!aid || !evt.text) return;
      // Find the streaming assistant message by scanning from the end (it's
      // almost always the last message). Avoids a full array.map() on every
      // token, which was O(n) over ALL messages including history.
      const msgs = entry.messages;
      let idx = msgs.length - 1;
      while (idx >= 0 && msgs[idx].id !== aid) idx--;
      if (idx < 0) return;
      const msg = msgs[idx];
      const last = msg.parts[msg.parts.length - 1];
      const newParts = last?.kind === "text"
        ? [...msg.parts.slice(0, -1), { kind: "text" as const, text: last.text + evt.text }]
        : [...msg.parts, { kind: "text" as const, text: evt.text }];
      // Splice only the changed message into the array copy.
      const next = msgs.slice();
      next[idx] = { ...msg, parts: newParts };
      entry.messages = next;
      return;
    }

    case "tool_use": {
      const aid = entry.currentAssistantId;
      if (!aid || !evt.name) return;
      const toolPart: MessagePart = {
        kind: "tool",
        name: evt.name!,
        input: evt.input ?? {},
        ...(evt.id ? { id: evt.id } : {}),
      };
      entry.messages = entry.messages.map((m) =>
        m.id === aid
          ? { ...m, parts: [...m.parts, toolPart] }
          : m
      );
      return;
    }

    case "tool_diff": {
      const aid = entry.currentAssistantId;
      if (!aid || !evt.id) return;
      entry.messages = entry.messages.map((m) =>
        m.id === aid
          ? {
              ...m,
              parts: m.parts.map((p) =>
                p.kind === "tool" && p.id === evt.id
                  ? {
                      ...p,
                      ...(evt.diff !== undefined ? { diff: evt.diff, diffTruncated: !!evt.diff_truncated } : {}),
                      ...(evt.diff_withheld ? { diffWithheld: evt.diff_withheld } : {}),
                    }
                  : p
              ),
            }
          : m
      );
      return;
    }

    case "artifact": {
      const aid = entry.currentAssistantId;
      if (aid && evt.name && evt.path && evt.mime) {
        const part: MessagePart = {
          kind: "artifact",
          name: evt.name,
          path: evt.path,
          mime: evt.mime,
          size: evt.size ?? 0,
          sid,
          ...(evt.label ? { label: evt.label } : {}),
        };
        entry.messages = entry.messages.map((m) =>
          m.id === aid ? { ...m, parts: [...m.parts, part] } : m
        );
      }
      return;
    }

    case "notice": {
      const aid = entry.currentAssistantId;
      const text = evt.message;
      if (!aid || !text) return;
      entry.messages = entry.messages.map((m) =>
        m.id === aid
          ? { ...m, parts: [...m.parts, {
              kind: "notice" as const,
              subtype: evt.subtype ?? "notice",
              text,
            }] }
          : m
      );
      return;
    }

    case "result": {
      // An interim result (ADR-2236) is progress, not the answer to replay or speak.
      if (evt.text && !evt.interim) entry.latestResultText = evt.text;
      return;
    }

    case "bg_status": {
      entry.bgOpen = Math.max(0, Number(evt.open ?? 0));
      entry.bgChildren = (evt.children ?? []).map((c, i) => ({
        id: c.id ?? `${c.kind}-${i}`,
        kind: String(c.kind ?? "other"),
        state: String(c.state ?? "running"),
        label: typeof c.label === "string" ? c.label : "",
        ageS: Math.max(0, Number(c.age_s ?? 0)),
      }));
      entry.bgChildrenAt = Date.now();
      return;
    }

    case "turn_usage": {
      const aid = entry.currentAssistantId;
      if (aid) {
        const usage = parseTurnUsage(evt.usage);
        entry.messages = entry.messages.map((m) =>
          m.id === aid
            ? { ...m, turnModel: evt.model ?? null, turnUsage: usage, turnUsageSeen: true }
            : m
        );
      }
      return;
    }

    case "engine": {
      // ADR-0214: stamp the agentic-compute engine on the current turn.
      // Multiple engine events per turn are possible (e.g. ACS chosen, then
      // quota-fallback to claude_code) — the last one reflects reality.
      const aid = entry.currentAssistantId;
      if (aid && evt.engine) {
        entry.messages = entry.messages.map((m) =>
          m.id === aid
            ? {
                ...m,
                engine: evt.engine,
                engineLabel: evt.label ?? evt.engine,
                // Keep a prior tdeRunId if this particular engine event
                // didn't carry one (e.g. a non-TDE fallback re-stamp).
                ...(evt.tde_run_id ? { tdeRunId: evt.tde_run_id } : {}),
              }
            : m
        );
      }
      return;
    }

    case "engine_progress": {
      // ADR-0214 Phase 4b / k=8 fix (adversarial review 2026-07-24): this
      // event previously had NO reducer — tdeProgress was declared on
      // ChatMessage but assigned nowhere, so the TDE metrics card never
      // rendered live. The backend persists the same dict to turns.jsonl
      // (snake_case `tde_progress`); hydration maps that on reload, this
      // case covers the live turn. The event arrives before "done", so
      // currentAssistantId is still set (lastAssistantId is the fallback for
      // a late event racing "done").
      const aid = entry.currentAssistantId ?? entry.lastAssistantId;
      if (aid && evt.engine === "tiered_delegation" && evt.run_id) {
        const tp: TdeProgress = {
          run_id: String(evt.run_id),
          total_steps: Number(evt.total_steps ?? 0),
          completed_steps: Number(evt.completed_steps ?? 0),
          delegated_count: Number(evt.delegated_count ?? 0),
          local_count: Number(evt.local_count ?? 0),
          l34_forced: Boolean(evt.l34_forced),
          token_savings_pct: evt.token_savings_pct ?? null,
          token_usage_instrumented: Boolean(evt.token_usage_instrumented),
          total_tokens: (evt.total_tokens as number | null | undefined) ?? null,
          tokens_delegated: (evt.tokens_delegated as number | null | undefined) ?? null,
          tokens_local: (evt.tokens_local as number | null | undefined) ?? null,
          tokens_by_model: (evt.tokens_by_model as Record<string, number> | null | undefined) ?? null,
          tokens_by_kind: (evt.tokens_by_kind as Record<string, number> | null | undefined) ?? null,
          cost_usd: (evt.cost_usd as number | null | undefined) ?? null,
          latency_delta_pct: evt.latency_delta_pct ?? null,
          quota_used_today: evt.quota_used_today ?? undefined,
          quota_limit: evt.quota_limit ?? null,
          task_type: evt.task_type ?? undefined,
          complexity: evt.complexity ?? undefined,
        };
        entry.messages = entry.messages.map((m) =>
          m.id === aid
            ? { ...m, tdeProgress: tp, tdeRunId: m.tdeRunId ?? tp.run_id }
            : m
        );
      }
      return;
    }

    case "error": {
      const aid = entry.currentAssistantId;
      if (aid) {
        entry.messages = entry.messages.map((m) =>
          m.id === aid ? { ...m, error: evt.message ?? "error" } : m
        );
      }
      return;
    }

    case "turn_started": {
      // This socket started the turn: tag its placeholder so a reconnect can
      // find it again (turn_attach carries the same id).
      const aid = entry.currentAssistantId;
      if (aid && evt.turn_id) {
        entry.messages = entry.messages.map((m) =>
          m.id === aid ? { ...m, turnId: evt.turn_id } : m
        );
      }
      return;
    }

    case "turn_attach": {
      // A turn of this chat is running that this socket did not start (this
      // tab before a reload, another tab). The server replays what it produced
      // so far right after this event, then streams it live.
      if (!evt.turn_id) return;
      const existing = entry.messages.find(
        (m) => m.role === "assistant" && m.turnId === evt.turn_id
      );
      let aid: string;
      if (existing) {
        // Same page, socket re-opened: the replay rebuilds the bubble from
        // the start, so drop what it held instead of doubling it.
        aid = existing.id;
        entry.messages = entry.messages.map((m) =>
          m.id === aid ? { ...m, parts: [], streaming: true, error: undefined } : m
        );
      } else {
        // Any other bubble still marked streaming belongs to a turn that is
        // over as far as this socket can tell.
        aid = `a-${_nextMsgId()}`;
        entry.messages = [
          ...entry.messages.map((m) => (m.streaming ? { ...m, streaming: false } : m)),
          {
            id: aid,
            role: "assistant",
            parts: [],
            ts: Date.now() / 1000,
            streaming: true,
            turnId: evt.turn_id,
          },
        ];
      }
      entry.currentAssistantId = aid;
      entry.streaming = true;
      entry.bgOpen = 0;
      entry.bgChildren = [];
      entry.needsReconcile = true;
      return;
    }

    case "turn_resync": {
      // The server stopped feeding this socket (it fell behind); the end of
      // the turn brings the full record.
      entry.needsReconcile = true;
      return;
    }

    case "btw_window_closed":
      entry.steerOpen = false;
      return;

    case "btw_flushed":
    case "btw_undelivered": {
      if (!evt.client_id) return;
      entry.messages = setSteerState(
        entry.messages, evt.client_id,
        evt.type === "btw_flushed" ? "injected" : "undelivered",
      );
      return;
    }

    case "done": {
      {
        // Delivered notes were answered — unless the turn ended in an error.
        const cur = entry.currentAssistantId
          ? entry.messages.find((m) => m.id === entry.currentAssistantId) : undefined;
        // An attached socket did not see the turn run: whether a delivered note
        // was answered is the record's to say (reconcile + settleHydrated).
        entry.messages = settleOnDone(
          entry.messages, { answered: !cur?.error && !evt.cancelled && !evt.attached });
      }
      entry.steerOpen = true;
      if (evt.attached || entry.needsReconcile) {
        entry.needsReconcile = false;
        _reconcileWithServer(sid);
      }
      entry.streaming = false;
      entry.bgOpen = 0;
      entry.bgChildren = [];
      const aid = entry.currentAssistantId;
      if (aid) {
        entry.messages = entry.messages.map((m) =>
          m.id === aid ? { ...m, streaming: false } : m
        );
        // Remember this turn's message id — /voice/tts archives and publishes
        // its "voice" event AFTER this "done" already cleared currentAssistantId,
        // so a late player-attach still needs somewhere to land.
        entry.lastAssistantId = aid;
      }
      entry.currentAssistantId = null;
      return;
    }

    case "voice": {
      // Live counterpart of history hydration's attach_voice_artifacts: attach
      // the just-archived audio to the CURRENT turn if it's still streaming,
      // otherwise to the most recently completed one — /voice/tts normally
      // resolves just after "done" already fired. Additive only: this never
      // touches the ephemeral playTts() auto-speak-once path.
      const aid = entry.currentAssistantId ?? entry.lastAssistantId;
      if (aid && evt.name && evt.path && evt.mime) {
        const part: MessagePart = {
          kind: "artifact",
          name: evt.name,
          path: evt.path,
          mime: evt.mime,
          size: evt.size ?? 0,
          sid,
          ...(evt.label ? { label: evt.label } : {}),
        };
        entry.messages = entry.messages.map((m) =>
          m.id === aid ? { ...m, parts: [...m.parts, part] } : m
        );
      }
      return;
    }

    case "session_title": {
      if (evt.title) entry.pendingTitle = evt.title;
      return;
    }

    case "ccc_action": {
      if (evt.action_id && evt.entity_type && evt.status) {
        emitCCCEvent({
          action_id:   evt.action_id,
          entity_type: evt.entity_type,
          entity_id:   evt.entity_id ?? null,
          status:      evt.status,
          message:     evt.message ?? "",
          payload:     evt.payload ?? null,
        });
      }
      return;
    }

    default:
      return;
  }
}

// ── Public API ────────────────────────────────────────────────────────────────

const _MAX_RECONNECT_DELAY = 30_000;

/** Schedule a reconnect attempt with exponential backoff. */
function _scheduleReconnect(sid: string): void {
  const entry = sessions.get(sid);
  if (!entry) return;
  // Don't reconnect for auth or not-found closes.
  if (entry.error?.includes("expired") || entry.error?.includes("not found")) return;
  if (entry.reconnectTimer) return; // already pending

  entry.reconnecting = true;
  notifyState(sid);

  entry.reconnectTimer = setTimeout(() => {
    entry.reconnectTimer = null;
    if (!sessions.has(sid)) return; // session was closed while we waited
    ensureConnected(sid);
  }, entry.reconnectDelay);

  // Exponential backoff, capped at 30 s
  entry.reconnectDelay = Math.min(entry.reconnectDelay * 2, _MAX_RECONNECT_DELAY);
}

/**
 * Ensure a persistent WebSocket is open for this session.
 * No-op if already open or connecting.
 */
export function ensureConnected(sid: string): void {
  const entry = getOrCreate(sid);
  const ws = entry.ws;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const newWs = new WebSocket(
    `${proto}//${window.location.host}/v1/console/chat/sessions/${encodeURIComponent(sid)}/stream`
  );
  entry.ws = newWs;

  _dbg(sid, "ws.connecting", { url: newWs.url });

  newWs.onopen = () => {
    // Successful connection — reset backoff and clear reconnecting state.
    entry.error = null;
    entry.reconnecting = false;
    entry.reconnectDelay = 1_000;
    _dbg(sid, "ws.open");

    // Start heartbeat to keep connection alive (prevents 300s idle timeout from server)
    if (entry.heartbeatInterval) clearInterval(entry.heartbeatInterval);
    entry.heartbeatInterval = setInterval(() => {
      if (newWs.readyState === WebSocket.OPEN) {
        newWs.send(JSON.stringify({ type: "ping" }));
      }
    }, 25_000); // 25 s — well under most proxy idle-timeout floors (typically 60 s)

    notifyState(sid);
  };

  newWs.onmessage = (ev) => {
    let payload: StreamEvent;
    try {
      payload = JSON.parse(ev.data) as StreamEvent;
    } catch {
      _dbg(sid, "ws.parse_error", { raw: String(ev.data).slice(0, 200) });
      return;
    }
    // Log every event type (not full payload — avoid logging user text)
    if (payload.type !== "pong") {
      _dbg(sid, `stream.${payload.type}`, {
        ...(payload.type === "delta"   ? { chars: (payload.text ?? "").length } : {}),
        ...(payload.type === "tool_use" ? { tool: payload.name } : {}),
        ...(payload.type === "error"    ? { message: payload.message } : {}),
        ...(payload.type === "result"   ? { chars: (payload.text ?? "").length, usage: payload.usage } : {}),
        ...(payload.type === "artifact" ? { name: payload.name, mime: payload.mime, size: payload.size } : {}),
        ...(payload.type === "voice" ? { name: payload.name, mime: payload.mime, size: payload.size, label: payload.label } : {}),
        ...(payload.type === "session_title" ? { title: payload.title } : {}),
        ...(payload.type === "ccc_action" ? { action_id: payload.action_id, entity_type: payload.entity_type } : {}),
      });
    }
    applyEvent(entry, sid, payload);
    if (payload.type === "delta") notifyDelta(sid);
    else notifyState(sid);
    notifyEvent(sid, payload);
  };

  newWs.onerror = (ev) => {
    _dbg(sid, "ws.error", { type: (ev as ErrorEvent).type ?? "error" });
  };

  newWs.onclose = (ev) => {
    _dbg(sid, "ws.close", { code: ev.code, reason: ev.reason, wasClean: ev.wasClean });
    entry.streaming = false;
    // Stop heartbeat on close
    if (entry.heartbeatInterval) {
      clearInterval(entry.heartbeatInterval);
      entry.heartbeatInterval = null;
    }
    // Resolve any orphaned assistant placeholder so it doesn't hang indefinitely.
    // Content-aware: an empty placeholder (no deltas received) is removed entirely
    // so no ghost "Connection lost" bubble lingers in the chat; a partial response
    // (some content arrived) is ended cleanly — the truncated text is self-evident
    // and the session-level reconnecting/error state carries the context.
    if (entry.currentAssistantId) {
      const aid = entry.currentAssistantId;
      const hasContent = entry.messages.some(
        m => m.id === aid && m.parts.some(p => {
          if (p.kind === "text") return p.text.length > 0;
          return true; // tool_use or artifact = has content
        })
      );
      if (hasContent) {
        entry.messages = entry.messages.map(m =>
          m.id === aid ? { ...m, streaming: false } : m
        );
      } else {
        entry.messages = entry.messages.filter(m => m.id !== aid);
      }
      entry.currentAssistantId = null;
    }
    if (ev.code === 4401) {
      entry.error = "Session expired — please re-login.";
      entry.reconnecting = false;
    } else if (ev.code === 4404) {
      entry.error = "Chat session not found.";
      entry.reconnecting = false;
    } else {
      // Unexpected close — this is a TRANSIENT condition we auto-recover from,
      // not a terminal error. The most common trigger is a uvicorn restart
      // (code 1012, e.g. an operator deploy) or a brief network blip. We clear
      // any stale terminal error and enter the calm `reconnecting` state; the
      // backoff timer re-opens the socket. Surfacing "Connection error" here
      // was misleading — the command-centre simply reconnects.
      entry.error = null;
      _scheduleReconnect(sid);
    }
    notifyState(sid);
  };

  newWs.onerror = () => {
    // In browsers, onerror always fires immediately before onclose (abnormal
    // close). Treat it as a transient reconnect, NOT a terminal error: clear
    // any stale error and enter the calm `reconnecting` state. The onclose
    // handler will schedule the reconnect (or onerror schedules it directly if
    // onclose never fires, e.g. in unit-test mocks).
    entry.streaming = false;
    // Stop heartbeat on error
    if (entry.heartbeatInterval) {
      clearInterval(entry.heartbeatInterval);
      entry.heartbeatInterval = null;
    }
    entry.error = null;
    _scheduleReconnect(sid);
    notifyState(sid);
  };
}

/**
 * Load historical messages from the server (getChatTurns).
 *
 * Guard: only applied on the first visit (historyLoaded === false) AND only
 * if no live content has arrived yet. If the WS has already delivered messages
 * or is mid-stream, live content takes precedence and history is silently
 * skipped — we mark historyLoaded so we don't attempt again.
 */
export function loadHistory(sid: string, messages: ChatMessage[]): void {
  const entry = getOrCreate(sid);
  const firstLoad = !entry.historyLoaded;
  entry.historyLoaded = true;
  // The server's history is the record. Until 2026-10-09 any non-empty entry
  // skipped it, so a sessionStorage mirror restored mid-turn hid the finished
  // answer forever. Now: while this page streams a turn (sent before the
  // history arrived, or attached via turn_attach) its live messages are kept
  // after the record; otherwise the record replaces whatever the tab holds —
  // finished turns are in it, restored mirror content is superseded by it.
  if (entry.streaming) {
    const live = entry.messages.filter((m) => !m.restored && !m.id.startsWith("h-"));
    entry.messages = mergeLiveTail(messages, live);
    notifyState(sid);
    return;
  }
  if (messages.length === 0 && entry.messages.some((m) => !m.restored)) return; // never wipe live content with an empty read
  if (fingerprint(messages) === fingerprint(entry.messages)) {
    // Same content: still drop the `restored` marks so later reads compare cleanly.
    if (entry.messages.some((m) => m.restored)) {
      entry.messages = messages;
      notifyState(sid);
    } else if (firstLoad) {
      notifyState(sid);
    }
    return;
  }
  entry.messages = messages;
  notifyState(sid);
}

/** The record followed by live messages it does not contain yet. A live user
 *  message the record already holds (persisted at turn start) is not doubled. */
function mergeLiveTail(record: ChatMessage[], live: ChatMessage[]): ChatMessage[] {
  if (live.length === 0) return record;
  // Steering notes of the running turn may already be in the record after its
  // user line (queued notes are recorded at spawn): compare the user lines.
  let idx = record.length - 1;
  while (idx >= 0 && record[idx].btw) idx--;
  const last = idx >= 0 ? record[idx] : undefined;
  const liveUserIdx = live.findIndex((m) => !m.btw);
  const firstLive = liveUserIdx >= 0 ? live[liveUserIdx] : undefined;
  const sameUser =
    last && firstLive && last.role === "user" && firstLive.role === "user" &&
    textOf(last) === textOf(firstLive);
  if (!sameUser) return [...record, ...live];
  const liveNotes = new Set(live.filter((m) => m.btw).map((m) => m.btw!.clientId));
  const recordNotes = record.slice(idx + 1).filter((m) => m.btw && !liveNotes.has(m.btw.clientId));
  return [
    ...record.slice(0, idx),
    ...live.slice(0, liveUserIdx + 1),
    ...recordNotes,
    ...live.slice(liveUserIdx + 1),
  ];
}

function textOf(m: ChatMessage): string {
  return m.parts.map((p) => (p.kind === "text" ? p.text : "")).join("");
}

/** Identity of a message list for reconcile decisions: role, text and part
 *  count per message. Replaces the old length comparison, which read a stale
 *  [user, empty placeholder] as equal to the finished [user, answer]. */
function fingerprint(msgs: ChatMessage[]): string {
  return msgs.map((m) =>
    `${m.role}:${m.btw?.state ?? ""}:${m.parts.length}:${textOf(m).length}:${textOf(m).slice(-64)}`).join("|");
}

/**
 * Replace a chat's messages with a fresh server read — for history that grew
 * while no socket of this page streamed the turn (a reload, a closed tab, a
 * turn finished on another tab).
 *
 * Never while this page is streaming a turn (live content owns the display
 * then). Otherwise the record wins whenever it differs from what the tab
 * shows; restored mirror content always loses.
 */
export function refreshHistory(sid: string, messages: ChatMessage[]): void {
  const entry = sessions.get(sid);
  if (!entry || entry.streaming) return;
  // A snapshot shorter than what this page itself streamed is older than it
  // (e.g. read before the answer was persisted) — never let it erase content.
  // Restored mirror content does not count: it is not the page's own.
  const own = entry.messages.filter((m) => !m.restored).length;
  if (messages.length < own) return;
  if (fingerprint(messages) === fingerprint(entry.messages)) return;
  entry.historyLoaded = true;
  entry.messages = messages;
  notifyState(sid);
}

/** True while ANY chat of this page streams a turn (sent or attached) — the
 *  build-freshness auto-reload waits for this to clear. */
export function anyChatStreaming(): boolean {
  for (const e of sessions.values()) if (e.streaming) return true;
  return false;
}

// ── Server reconcile (attached turns) ─────────────────────────────────────────
// The registry cannot hydrate turns itself (hydrateChatTurn lives with the chat
// page); the page registers the reader once at module load.
type HistoryReader = (sid: string) => Promise<ChatMessage[]>;
let _historyReader: HistoryReader | null = null;

export function registerHistoryReader(fn: HistoryReader): void {
  _historyReader = fn;
}

/** After an attached turn ends, the persisted record is authoritative: the
 *  replay may have been truncated, and only the record carries voice parts.
 *  The attached `done` is sent after the turn was persisted, so one read
 *  suffices; a short retry covers a failed request. */
function _reconcileWithServer(sid: string, attempt = 0): void {
  const reader = _historyReader;
  if (!reader) return;
  setTimeout(() => {
    reader(sid)
      .then((msgs) => refreshHistory(sid, msgs))
      .catch(() => {
        if (attempt < 3) _reconcileWithServer(sid, attempt + 1);
      });
  }, attempt === 0 ? 0 : 1000 * 2 ** attempt);
}

/**
 * Send a user message and create the assistant placeholder.
 * Returns the two new messages, or null if the WS is not ready.
 */
export function sendMessage(
  sid: string,
  text: string
): { userMsg: ChatMessage; placeholder: ChatMessage } | null {
  const entry = sessions.get(sid);
  if (!entry) return null;
  if (!entry.ws || entry.ws.readyState !== WebSocket.OPEN) return null;
  if (!text.trim() || entry.streaming) return null;

  const now = Date.now() / 1000;
  const userMsg: ChatMessage = {
    id: `u-${_nextMsgId()}`,
    role: "user",
    parts: [{ kind: "text", text }],
    ts: now,
  };
  const aid = `a-${_nextMsgId()}`;
  const placeholder: ChatMessage = {
    id: aid,
    role: "assistant",
    parts: [],
    ts: now,
    streaming: true,
  };

  entry.messages = [...entry.messages, userMsg, placeholder];
  entry.currentAssistantId = aid;
  entry.streaming = true;
  entry.bgOpen = 0;
  entry.bgChildren = [];
  entry.latestResultText = null;
  entry.steerOpen = true;

  entry.ws.send(JSON.stringify({ type: "user", text, voice_on: getBool(PREF_KEYS.voiceOut, true) }));
  _dbg(sid, "msg.send", { len: text.length, force_delegate: text.trim().toLowerCase().startsWith("/delegate") });
  notifyState(sid);

  return { userMsg, placeholder };
}

/**
 * CONCEPT-0107: put a steering note into the history, directly before the
 * running turn's assistant bubble. False when there is no such session.
 */
export function addSteerNote(sid: string, note: ChatMessage): boolean {
  const entry = sessions.get(sid);
  if (!entry) return false;
  entry.messages = insertSteerNote(entry.messages, entry.currentAssistantId, note);
  notifyState(sid);
  return true;
}

/** Move a steering note to *state* (transitions never go backwards). */
export function updateSteerNote(sid: string, clientId: string, state: BtwState, reason?: string): void {
  const entry = sessions.get(sid);
  if (!entry) return;
  const next = setSteerState(entry.messages, clientId, state, reason);
  if (next === entry.messages) return;
  entry.messages = next;
  notifyState(sid);
}

/** "Send again" after a network failure: the same note, the same id. */
export function retrySteerNote(sid: string, clientId: string): void {
  const entry = sessions.get(sid);
  if (!entry) return;
  entry.messages = retrySteerState(entry.messages, clientId);
  notifyState(sid);
}

/** Drop a steering note (voice undo, or resend as a new message). */
export function removeSteerNote(sid: string, clientId: string): void {
  const entry = sessions.get(sid);
  if (!entry) return;
  entry.messages = entry.messages.filter((m) => m.btw?.clientId !== clientId);
  notifyState(sid);
}

/** Get the current state snapshot (used by useChatSession internally). */
export function getSessionState(sid: string): SessionState {
  // Return cached snapshot if available; create one for unseen sessions.
  if (!snapshots.has(sid)) {
    const entry = getOrCreate(sid);
    snapshots.set(sid, makeSnapshot(entry));
  }
  return snapshots.get(sid)!;
}

/**
 * Send a cancel signal to the server for the currently streaming turn.
 *
 * The primary cancellation path is closing and immediately reopening the
 * WebSocket: the resulting WebSocketDisconnect on the server side triggers
 * contextlib.aclosing() which calls gen.aclose(), terminating the subprocess.
 * A {"type": "cancel"} message is sent first so that future server versions
 * (which handle it inline) also receive the signal.
 *
 * The registry resets streaming state immediately so the UI unlocks at once
 * without waiting for the reconnect round-trip.
 */
export function cancelTurn(sid: string): void {
  const entry = sessions.get(sid);
  if (!entry) return;
  _dbg(sid, "msg.cancel", { streaming: entry.streaming });

  // Send cancel message if the socket is open (best-effort; may be ignored by v1 server).
  if (entry.ws && entry.ws.readyState === WebSocket.OPEN) {
    try { entry.ws.send(JSON.stringify({ type: "cancel" })); } catch { /* ignore */ }
  }

  // Optimistically mark the streaming placeholder as done so the UI unlocks.
  entry.streaming = false;
  // Notes still waiting die with the turn (the server records them
  // undelivered); delivered ones stay "delivered" — a stopped turn did not answer.
  entry.messages = settleOnDone(entry.messages, { answered: false });
  entry.steerOpen = true;
  const aid = entry.currentAssistantId;
  if (aid) {
    entry.messages = entry.messages.map((m) =>
      m.id === aid ? { ...m, streaming: false } : m
    );
    entry.currentAssistantId = null;
  }
  notifyState(sid);

  // Close the WebSocket — the server-side WebSocketDisconnect path calls
  // gen.aclose() via contextlib.aclosing(), which terminates the subprocess.
  if (entry.ws) {
    entry.ws.close();
    entry.ws = null;
  }

  // Immediately reconnect so the channel is ready for the next user turn.
  // Use a short delay to let the server-side cleanup complete first.
  setTimeout(() => ensureConnected(sid), 300);
}

/** Clear the pending title after ChatPane has consumed it. */
export function consumePendingTitle(sid: string): void {
  const entry = sessions.get(sid);
  if (entry) {
    entry.pendingTitle = null;
    notifyState(sid);
  }
}

/** Close and remove the session (call on chat delete to avoid WS/memory leaks). */
export function closeSession(sid: string): void {
  const entry = sessions.get(sid);
  // Cancel any pending reconnect timer so it doesn't fire after cleanup.
  if (entry?.reconnectTimer) {
    clearTimeout(entry.reconnectTimer);
    entry.reconnectTimer = null;
  }
  // Clear heartbeat interval
  if (entry?.heartbeatInterval) {
    clearInterval(entry.heartbeatInterval);
    entry.heartbeatInterval = null;
  }
  // Cancel any pending debounced persist and drop the sessionStorage mirror —
  // otherwise a deleted chat's last messages would resurrect on the next sid
  // reuse (session ids are not reused in practice, but a stale multi-MB
  // sessionStorage entry left behind on every delete is its own leak).
  const persistTimer = _persistFlushTimers.get(sid);
  if (persistTimer) {
    clearTimeout(persistTimer);
    _persistFlushTimers.delete(sid);
  }
  clearPersistedMessages(sid);
  // The debug log (session title, errors, artifact names) is chat content
  // too — it must not outlive the chat's deletion.
  const dbgTimer = _dbgFlushTimers.get(sid);
  if (dbgTimer) clearTimeout(dbgTimer);
  _dbgFlushTimers.delete(sid);
  _dbgBuffers.delete(sid);
  try { sessionStorage.removeItem(`corvin_dbg_${sid}`); } catch { /* storage unavailable */ }
  // Remove listeners BEFORE closing the WS so that the close event's
  // notifyState call (which fires synchronously in some environments) does
  // not reach already-deregistered subscribers.
  sessions.delete(sid);
  snapshots.delete(sid);
  stateListeners.delete(sid);
  eventListeners.delete(sid);
  if (entry?.ws) {
    entry.ws.close();
    entry.ws = null;
  }
}

// ── Subscription API ──────────────────────────────────────────────────────────

/** Subscribe to state changes (re-render trigger). */
export function subscribeState(sid: string, fn: () => void): () => void {
  if (!stateListeners.has(sid)) stateListeners.set(sid, new Set());
  stateListeners.get(sid)!.add(fn);
  return () => stateListeners.get(sid)?.delete(fn);
}

/** Subscribe to raw stream events (TTS, title update, etc.). */
export function subscribeEvents(sid: string, fn: (evt: StreamEvent) => void): () => void {
  if (!eventListeners.has(sid)) eventListeners.set(sid, new Set());
  eventListeners.get(sid)!.add(fn);
  return () => eventListeners.get(sid)?.delete(fn);
}

// ── React hook ────────────────────────────────────────────────────────────────

/**
 * Hook: returns the current SessionState for a chat and re-renders on changes.
 *
 * Uses useSyncExternalStore (React 18) for correct concurrent-mode semantics:
 * - No tearing between messages and streaming flag in the same render pass.
 * - React automatically re-renders when subscribeState fires the listener.
 * Does NOT manage the WS lifecycle — call ensureConnected separately.
 */
export function useChatSession(sid: string): SessionState {
  return useSyncExternalStore(
    (fn) => subscribeState(sid, fn),
    () => getSessionState(sid)
  );
}

// ── Test utilities ─────────────────────────────────────────────────────────────

/** Reset all module state — only for unit tests. */
export function __resetForTests(): void {
  _historyReader = null;
  sessions.forEach((e) => {
    if (e.reconnectTimer) clearTimeout(e.reconnectTimer);
    if (e.heartbeatInterval) clearInterval(e.heartbeatInterval);
  });
  _persistFlushTimers.forEach((t) => clearTimeout(t));
  _persistFlushTimers.clear();
  _deltaNotifyTimers.forEach((t) => clearTimeout(t));
  _deltaNotifyTimers.clear();
  sessions.clear();
  snapshots.clear();
  stateListeners.clear();
  eventListeners.clear();
}
