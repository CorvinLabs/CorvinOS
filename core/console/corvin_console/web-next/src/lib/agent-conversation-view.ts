/**
 * View model for an agent conversation: how transcript records become rows, and which
 * side of the thread they sit on. Pure and DOM-free so the mapping (the part that goes
 * wrong silently — e.g. a private note shown as public) is unit-tested.
 */
import type {
  Conversation, ConversationEvent, ConversationMessage, ConversationSettings,
} from "@/lib/api/federation";

export type Row =
  | { kind: "message"; seq: number; message: ConversationMessage }
  | { kind: "event"; seq: number; event: ConversationEvent };

/** Messages and system events in transcript order (both carry the moderator's single `seq`). */
export function timeline(c: Pick<Conversation, "messages" | "events">): Row[] {
  const rows: Row[] = [
    ...c.messages.map((message): Row => ({ kind: "message", seq: message.seq, message })),
    ...(c.events ?? []).map((event): Row => ({ kind: "event", seq: event.seq, event })),
  ];
  return rows.sort((a, b) => a.seq - b.seq);
}

export type Side = "mine" | "peer";

/** Operator + your agent sit on the right (like "me" in the peer chat); the peer agent on the left. */
export function sideOf(m: Pick<ConversationMessage, "speaker">): Side {
  return m.speaker === "peer" ? "peer" : "mine";
}

/** Who an operator line was addressed to, for the bubble caption. */
export function audienceLabel(
  m: Pick<ConversationMessage, "speaker" | "target">,
  names: { local?: string; peer?: string },
): string | null {
  if (m.speaker !== "operator") return null;
  if (m.target === "local") return `to ${names.local ?? "your agent"} only`;
  if (m.target === "peer") return `to ${names.peer ?? "the peer agent"} only`;
  return "to both agents";
}

export function eventText(e: ConversationEvent): string {
  if (e.event === "paused") return "Paused";
  if (e.event === "resumed") return "Resumed";
  const s = e.settings;
  return s ? `Settings changed — ${s.max_words} words per reply, ${s.pace_s} s between turns` : "Settings changed";
}

export const DEFAULT_SETTINGS: ConversationSettings = {
  max_words: 250, pace_s: 0, role_notes: { local: "", peer: "" },
};

export const LIMITS = { max_words: [50, 600], pace_s: [0, 60], role_note: 500, message: 1000 } as const;

export function clampInt(v: number, [lo, hi]: readonly [number, number]): number {
  return Math.min(hi, Math.max(lo, Math.round(Number.isFinite(v) ? v : lo)));
}

/** Only the fields that differ from `base` — the PATCH body for a live settings change. */
export function settingsDiff(
  base: ConversationSettings, next: ConversationSettings,
): Partial<ConversationSettings> {
  const out: Partial<ConversationSettings> = {};
  if (next.max_words !== base.max_words) out.max_words = next.max_words;
  if (next.pace_s !== base.pace_s) out.pace_s = next.pace_s;
  if (next.role_notes.local !== base.role_notes.local || next.role_notes.peer !== base.role_notes.peer) {
    out.role_notes = next.role_notes;
  }
  return out;
}
