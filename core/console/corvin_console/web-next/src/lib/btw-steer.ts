/**
 * /btw v2 — steering notes as first-class chat messages (CONCEPT-0107).
 *
 * Pure functions over the message list; the chat registry and the chat page
 * call them, tests drive them directly. A note is a `role: "user"` message
 * carrying `btw`, placed directly BEFORE the in-flight assistant bubble — the
 * order the server persists (`user`, `btw`, `assistant`), so a reload renders
 * exactly what was seen live.
 *
 * States (honest — "delivered" is never shown as "taken into account" before
 * the turn that received it has ended):
 *
 *   sending ── injected ──▶ injected ── turn done ──▶ answered
 *   sending ── queued ────▶ queued ── btw_flushed ──▶ injected
 *                           queued ── btw_undelivered ▶ undelivered
 *   sending ── refused ───▶ refused
 *   sending ── no_active_stream ▶ undelivered
 *   sending ── network error ──▶ failed
 */
import type { ChatMessage } from "./chat-registry";

export type BtwState =
  | "sending"
  | "queued"
  | "injected"
  | "answered"
  | "refused"
  | "undelivered"
  | "failed";

export type BtwVia = "text" | "voice";

export interface BtwInfo {
  state: BtwState;
  via: BtwVia;
  clientId: string;
  /** Why it was refused (server reason: gate, rate_limited, queue_full, …). */
  reason?: string;
}

/** Server status of POST /chat/sessions/{sid}/btw. */
export type BtwServerStatus = "injected" | "queued" | "no_active_stream" | "refused";

const PREFIX_RE = /^\/btw(\s+|$)/i;

/** Strip an optional leading `/btw ` — the prefix is no longer required. */
export function stripBtwPrefix(text: string): string {
  return text.trim().replace(PREFIX_RE, "").trim();
}

/** Terminal states whose text the operator may need again (resend / edit). */
export function needsOperatorAction(state: BtwState): boolean {
  return state === "refused" || state === "undelivered" || state === "failed";
}

export function newClientId(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c?.randomUUID) return c.randomUUID().replace(/-/g, "");
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

export function makeSteerMessage(text: string, via: BtwVia, clientId: string, ts: number): ChatMessage {
  return {
    id: `btw-${clientId}`,
    role: "user",
    parts: [{ kind: "text", text }],
    ts,
    btw: { state: "sending", via, clientId },
  };
}

/** Insert *note* directly before the assistant bubble *assistantId* (or at the
 *  end when there is none). Returns a new array. */
export function insertSteerNote(
  messages: ChatMessage[],
  assistantId: string | null,
  note: ChatMessage,
): ChatMessage[] {
  const idx = assistantId ? messages.findIndex((m) => m.id === assistantId) : -1;
  if (idx < 0) return [...messages, note];
  return [...messages.slice(0, idx), note, ...messages.slice(idx)];
}

/** Map the server's answer onto the note's state. */
export function stateFromServer(status: BtwServerStatus): BtwState {
  switch (status) {
    case "injected": return "injected";
    case "queued": return "queued";
    case "refused": return "refused";
    default: return "undelivered";
  }
}

/** Allowed transitions — a late answer can never move a note backwards
 *  (e.g. a `btw_flushed` event racing the HTTP response that said `queued`). */
const RANK: Record<BtwState, number> = {
  sending: 0, queued: 1, injected: 2, answered: 3, refused: 3, undelivered: 3, failed: 3,
};

export function setSteerState(
  messages: ChatMessage[],
  clientId: string,
  state: BtwState,
  reason?: string,
): ChatMessage[] {
  let changed = false;
  const next = messages.map((m) => {
    if (!m.btw || m.btw.clientId !== clientId) return m;
    if (RANK[state] < RANK[m.btw.state]) return m;
    if (RANK[state] === RANK[m.btw.state] && state !== m.btw.state && RANK[state] === 3) return m;
    changed = true;
    return { ...m, btw: { ...m.btw, state, ...(reason ? { reason } : {}) } };
  });
  return changed ? next : messages;
}

/** The turn ended: every note it received has now been answered. */
export function settleOnDone(messages: ChatMessage[]): ChatMessage[] {
  if (!messages.some((m) => m.btw?.state === "injected")) return messages;
  return messages.map((m) =>
    m.btw?.state === "injected" ? { ...m, btw: { ...m.btw, state: "answered" as const } } : m,
  );
}

/** Persisted turn → BtwInfo (history hydration). Legacy records (before v2)
 *  carry only `btw: true` (+ `gate_refused` when a gate stopped them). */
export function btwFromTurn(t: {
  btw?: boolean; btw_status?: string; via?: string; client_id?: string; gate_refused?: string;
}, fallbackId: string): BtwInfo | undefined {
  if (!t.btw) return undefined;
  const status = t.btw_status ?? (t.gate_refused ? "refused" : "injected");
  const state: BtwState =
    status === "refused" ? "refused" : status === "undelivered" ? "undelivered" : "injected";
  return {
    state,
    via: t.via === "voice" ? "voice" : "text",
    clientId: t.client_id || fallbackId,
    ...(state === "refused" ? { reason: "gate" } : {}),
  };
}

/** After hydrating a finished history: a delivered note followed by an
 *  assistant answer was answered. */
export function settleHydrated(messages: ChatMessage[]): ChatMessage[] {
  let sawAnswer = false;
  const out = messages.slice();
  for (let i = out.length - 1; i >= 0; i--) {
    const m = out[i];
    if (m.role === "assistant" && !m.streaming) sawAnswer = true;
    else if (m.btw?.state === "injected" && sawAnswer) {
      out[i] = { ...m, btw: { ...m.btw, state: "answered" } };
    } else if (m.role === "user" && !m.btw) sawAnswer = false;
  }
  return out;
}

/** Short human label for the state chip (English — shipped UI). */
export function btwStateLabel(b: BtwInfo): string {
  switch (b.state) {
    case "sending": return "Sending…";
    case "queued": return "Waiting for the turn to start";
    case "injected": return "Delivered to the running reply";
    case "answered": return "Taken into account";
    case "refused":
      return b.reason === "rate_limited" ? "Not sent — one note every 2 s"
        : b.reason === "queue_full" ? "Not sent — too many notes waiting"
        : "Refused by a safety gate";
    case "undelivered": return "Not delivered — the reply had already finished";
    case "failed": return "Could not be sent";
  }
}
