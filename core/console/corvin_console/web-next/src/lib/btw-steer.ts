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
 *  (e.g. a `btw_flushed` event racing the HTTP response that said `queued`).
 *  `failed` is only the CLIENT's view (the request did not come back): what the
 *  server reports later (`btw_flushed`, a retry's first outcome) overrides it. */
const RANK: Record<BtwState, number> = {
  sending: 0, failed: 0, queued: 1, injected: 2, answered: 3, refused: 3, undelivered: 3,
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

/** The turn ended. A note still waiting can no longer be delivered (the
 *  server records it undelivered); a delivered note counts as answered only
 *  when the turn ended normally — after an error or a stop it stays
 *  "delivered", since nobody knows whether the model got to it. */
export function settleOnDone(messages: ChatMessage[], { answered }: { answered: boolean }): ChatMessage[] {
  if (!messages.some((m) => m.btw?.state === "injected" || m.btw?.state === "queued")) return messages;
  return messages.map((m) => {
    if (m.btw?.state === "queued") return { ...m, btw: { ...m.btw, state: "undelivered" as const } };
    if (m.btw?.state === "injected" && answered) return { ...m, btw: { ...m.btw, state: "answered" as const } };
    return m;
  });
}

/** "Send again" after a network failure reuses the note's id, so a request
 *  that did reach the server is not injected twice (idempotent server side). */
export function retrySteerState(messages: ChatMessage[], clientId: string): ChatMessage[] {
  return messages.map((m) =>
    m.btw?.clientId === clientId && m.btw.state === "failed"
      ? { ...m, btw: { ...m.btw, state: "sending" as const, reason: undefined } }
      : m,
  );
}

/** Persisted turn → BtwInfo (history hydration). Legacy records (before v2)
 *  carry only `btw: true` (+ `gate_refused` when a gate stopped them). */
export function btwFromTurn(t: {
  btw?: boolean; btw_status?: string; btw_reason?: string; via?: string; client_id?: string;
  gate_refused?: string;
}, fallbackId: string): BtwInfo | undefined {
  if (!t.btw) return undefined;
  const status = t.btw_status ?? (t.gate_refused ? "refused" : "injected");
  const state: BtwState =
    status === "refused" ? "refused" : status === "undelivered" ? "undelivered" : "injected";
  const reason = t.btw_reason ?? (t.gate_refused ? "gate" : undefined);
  return {
    state,
    via: t.via === "voice" ? "voice" : "text",
    clientId: t.client_id || fallbackId,
    ...(reason ? { reason } : {}),
  };
}

const CANCELLED_MARKS = ["[answer cancelled here]", "[stopped before the safety check finished]"];

/** After hydrating a finished history: a delivered note followed by an
 *  assistant answer was answered. */
export function settleHydrated(messages: ChatMessage[]): ChatMessage[] {
  let sawAnswer = false;
  const out = messages.slice();
  for (let i = out.length - 1; i >= 0; i--) {
    const m = out[i];
    if (m.role === "assistant" && !m.streaming) {
      const t = m.parts.map((p) => (p.kind === "text" ? p.text : "")).join("");
      sawAnswer = !m.error && !CANCELLED_MARKS.some((c) => t.includes(c));
    }
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
    case "queued": return "Waiting to be delivered";
    case "injected": return "Delivered to the running reply";
    case "answered": return "Taken into account";
    case "refused":
      return b.reason === "rate_limited" ? "Not sent — one note every 2 s"
        : b.reason === "queue_full" ? "Not sent — too many notes waiting"
        : "Refused by a safety gate";
    case "undelivered": return "Not delivered — the reply could no longer take it";
    case "failed": return "Could not be sent";
  }
}
