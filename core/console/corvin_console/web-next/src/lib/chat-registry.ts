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

import { useSyncExternalStore } from "react";
import { emitCCCEvent } from "./ccc-bus";

// ── Types ────────────────────────────────────────────────────────────────────

export type MessagePart =
  | { kind: "text"; text: string }
  | { kind: "tool"; name: string; input: Record<string, unknown> }
  | { kind: "artifact"; name: string; path: string; mime: string; size: number; sid: string; label?: string };

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  parts: MessagePart[];
  ts: number;
  streaming?: boolean;
  error?: string;
}

export interface StreamEvent {
  type: "ready" | "delta" | "tool_use" | "result" | "error" | "done" | "info" |
        "pong" | "session_title" | "artifact" | "ccc_action";
  text?: string;
  name?: string;
  input?: Record<string, unknown>;
  message?: string;
  title?: string;
  path?: string;
  mime?: string;
  size?: number;
  label?: string;  // M5 (ADR-0170): provenance badge ("Graph", "live", "compute")
  session?: unknown;
  usage?: Record<string, unknown>;
  // ccc_action fields (ADR-0168 M3)
  action_id?: string;
  entity_type?: string;
  entity_id?: string | null;
  status?: string;
  payload?: Record<string, unknown> | null;
}

export interface SessionState {
  messages: ChatMessage[];
  streaming: boolean;
  error: string | null;
  /** True while a reconnect attempt is in progress (backoff timer fired, WS connecting). */
  reconnecting: boolean;
  /** Full text of the last completed result (for TTS). Cleared when a new send starts. */
  latestResultText: string | null;
  /** Pending title from a session_title event (so ChatPane can update React Query cache). */
  pendingTitle: string | null;
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
  latestResultText: string | null;
  pendingTitle: string | null;
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
    sessions.set(sid, {
      ws: null,
      messages: [],
      streaming: false,
      error: null,
      latestResultText: null,
      pendingTitle: null,
      currentAssistantId: null,
      historyLoaded: false,
      reconnectTimer: null,
      reconnectDelay: 1_000,
      reconnecting: false,
      heartbeatInterval: null,
    });
  }
  return sessions.get(sid)!;
}

function makeSnapshot(entry: SessionEntry): SessionState {
  return {
    messages: entry.messages,
    streaming: entry.streaming,
    error: entry.error,
    reconnecting: entry.reconnecting,
    latestResultText: entry.latestResultText,
    pendingTitle: entry.pendingTitle,
  };
}

function notifyState(sid: string) {
  const entry = sessions.get(sid);
  if (entry) {
    // Replace snapshot with a new object so Object.is detects the change.
    snapshots.set(sid, makeSnapshot(entry));
  }
  stateListeners.get(sid)?.forEach((fn) => fn());
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
      entry.messages = entry.messages.map((m) =>
        m.id === aid
          ? { ...m, parts: [...m.parts, { kind: "tool", name: evt.name!, input: evt.input ?? {} }] }
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

    case "result": {
      if (evt.text) entry.latestResultText = evt.text;
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

    case "done": {
      entry.streaming = false;
      const aid = entry.currentAssistantId;
      if (aid) {
        entry.messages = entry.messages.map((m) =>
          m.id === aid ? { ...m, streaming: false } : m
        );
      }
      entry.currentAssistantId = null;
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
        ...(payload.type === "session_title" ? { title: payload.title } : {}),
        ...(payload.type === "ccc_action" ? { action_id: payload.action_id, entity_type: payload.entity_type } : {}),
      });
    }
    applyEvent(entry, sid, payload);
    notifyState(sid);
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
  // Allow re-load when historyLoaded was set on a previous visit but the entry
  // was never populated (e.g., racing WS arrival that came in empty, or component
  // unmounted before fetch resolved). Only skip when we already have content.
  if (entry.historyLoaded && entry.messages.length > 0) return;

  entry.historyLoaded = true;

  // Don't overwrite live streamed content that arrived before getChatTurns resolved.
  if (entry.streaming || entry.messages.length > 0) return;

  entry.messages = messages;
  notifyState(sid);
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
  entry.latestResultText = null;

  entry.ws.send(JSON.stringify({ type: "user", text }));
  _dbg(sid, "msg.send", { len: text.length, force_delegate: text.trim().toLowerCase().startsWith("/delegate") });
  notifyState(sid);

  return { userMsg, placeholder };
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
  sessions.forEach((e) => {
    if (e.reconnectTimer) clearTimeout(e.reconnectTimer);
    if (e.heartbeatInterval) clearInterval(e.heartbeatInterval);
  });
  sessions.clear();
  snapshots.clear();
  stateListeners.clear();
  eventListeners.clear();
}
