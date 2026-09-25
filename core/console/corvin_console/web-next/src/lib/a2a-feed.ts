/**
 * Pure helpers for the Agent Hub live feed (A2A messages with media).
 * Kept out of the component so the message → rendering decisions are
 * unit-testable (tests/unit/a2a-feed.test.ts).
 */
import type { A2AFeedAttachment, A2AFeedMessage } from "@/lib/api";

export type MediaKind = "image" | "audio" | "video" | "pdf" | "text" | "file";

const AUDIO_EXT = ["mp3", "wav", "ogg", "oga", "m4a", "flac", "aac", "opus", "weba"];
const VIDEO_EXT = ["mp4", "webm", "mov", "m4v", "ogv"];
const IMAGE_EXT = ["png", "jpg", "jpeg", "gif", "webp", "avif"];
const TEXT_EXT = ["txt", "md", "csv", "json", "log"];

/** Classify an attachment for rendering. SVG/HTML are deliberately "file":
 *  the server never serves them inline (they can carry script). */
export function mediaKind(att: Pick<A2AFeedAttachment, "name" | "mime">): MediaKind {
  const mime = (att.mime || "").toLowerCase().split(";")[0].trim();
  const ext = (att.name.split(".").pop() || "").toLowerCase();
  if (mime === "image/svg+xml" || ext === "svg" || mime === "text/html" || ext === "html") return "file";
  if (mime.startsWith("image/") || IMAGE_EXT.includes(ext)) return "image";
  if (mime.startsWith("audio/") || AUDIO_EXT.includes(ext)) return "audio";
  if (mime.startsWith("video/") || VIDEO_EXT.includes(ext)) return "video";
  if (mime === "application/pdf" || ext === "pdf") return "pdf";
  if (mime.startsWith("text/") || mime === "application/json" || TEXT_EXT.includes(ext)) return "text";
  return "file";
}

const TEXT_KEYS = ["text", "answer", "response", "result", "summary", "message", "output", "content", "reply"];

/**
 * The human-readable body of a message plus whatever structured data is left.
 * Tasks carry their instruction in `text`; responses carry a worker's
 * result_schema-filtered dict in `data`, where the prose usually sits under
 * one of a few conventional keys.
 */
export function messageBody(m: Pick<A2AFeedMessage, "text" | "data">): {
  text: string;
  rest: Record<string, unknown>;
} {
  if (m.text && m.text.trim()) return { text: m.text, rest: m.data ?? {} };
  const data = { ...(m.data ?? {}) };
  for (const k of TEXT_KEYS) {
    const v = data[k];
    if (typeof v === "string" && v.trim()) {
      delete data[k];
      return { text: v, rest: data };
    }
  }
  return { text: "", rest: data };
}

/** Task ids that were sent (by either side) and have no response yet. */
export function pendingTaskIds(messages: A2AFeedMessage[]): Set<string> {
  const answered = new Set(messages.filter((m) => m.kind === "response").map((m) => m.task_id));
  return new Set(
    messages.filter((m) => m.kind === "task" && !answered.has(m.task_id)).map((m) => m.task_id),
  );
}

/** A response that was delivered but carries nothing to show — the peer
 *  accepted the task without running a worker (M1) or filtered everything. */
export function isEmptyDelivery(m: A2AFeedMessage): boolean {
  if (m.kind !== "response" || !["ok", "filtered"].includes(m.status)) return false;
  const { text, rest } = messageBody(m);
  return !text && Object.keys(rest).length === 0 && m.attachments.length === 0 && !m.error;
}

export type StatusTone = "ok" | "warn" | "error" | "neutral";

export function statusTone(status: string): StatusTone {
  if (status === "ok" || status === "sent" || status === "received") return "ok";
  if (status === "filtered") return "warn";
  if (["rejected", "timeout", "error"].includes(status)) return "error";
  return "neutral";
}

/** Plain-language reason for a failed response that carries no detail.
 *  A rejection's cause stays on the answering side by protocol design. */
export function failureHint(m: A2AFeedMessage, mine: boolean): string | null {
  if (m.kind !== "response") return null;
  // A busy refusal is recorded with data.reason on both sides (older receiver
  // records carried error "busy" instead — show the hint for those too).
  const busy = (m.data as { reason?: unknown })?.reason === "busy" || m.error === "busy";
  if (m.status === "rejected" && busy)
    return mine
      ? "Your instance was busy with this agent's other tasks and refused it — they can retry."
      : "The agent is busy with your other tasks right now — try again in a moment.";
  if (m.error) return null;
  if (m.status === "rejected")
    return mine
      ? "Your instance refused this task (policy, quota or safety gate — see the Audit trail tab)."
      : "The peer refused this task. Its policy, quota or safety gates decided — the reason stays on the peer's side.";
  if (m.status === "timeout") return "No answer before the deadline.";
  if (m.status === "error") return "The message could not be delivered.";
  return null;
}

/** Merge a polled page into the known list: dedupe by id, keep append order
 *  (``seq``; ``ts`` only breaks ties for records written before seq existed). */
export function mergeMessages(known: A2AFeedMessage[], incoming: A2AFeedMessage[]): A2AFeedMessage[] {
  if (incoming.length === 0) return known;
  const seen = new Set(known.map((m) => m.id));
  const fresh = incoming.filter((m) => !seen.has(m.id));
  if (fresh.length === 0) return known;
  return [...known, ...fresh].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0) || a.ts - b.ts);
}

/** Highest seq in a list — the next live cursor. */
export function maxSeq(messages: A2AFeedMessage[], fallback = 0): number {
  return messages.reduce((m, x) => (Number.isFinite(x.seq) ? Math.max(m, x.seq as number) : m), fallback);
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

/** Initials for a peer avatar that stay distinguishable in a list:
 *  "corvin_a2a_2" → "C2", "agent-01" → "A1", "gpu server" → "GS", "gpu" → "GP". */
export function initials(label: string): string {
  const parts = label.replace(/[_\-.]+/g, " ").trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  const last = parts[parts.length - 1];
  if (/^\d+$/.test(last)) {
    // A numeric suffix is what tells "agent-01" from "agent-02".
    return (parts[0][0] + String(Number(last))).slice(0, 3).toUpperCase();
  }
  return (parts[0][0] + last[0]).toUpperCase();
}

/** One-line plain-text preview of a message body (no Markdown syntax). */
export function plainPreview(text: string, max = 120): string {
  return text
    .replace(/```[\s\S]*?```/g, " [code] ")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_`~>#]+/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, max);
}

/** Protocol cap (a2a_attachments.MAX_ATTACHMENTS_TOTAL_BYTES). */
export const MAX_ATTACHMENTS_TOTAL_BYTES = 1024 * 1024;
export const MAX_ATTACHMENTS_COUNT = 16;

/** Protocol filename rule: [A-Za-z0-9._-], no leading dot, ≤128 chars. */
export function sanitizeAttachmentName(name: string): string {
  let s = name.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/\.{2,}/g, ".").replace(/^[.]+/, "");
  if (!s) s = "file";
  return s.slice(0, 128);
}
