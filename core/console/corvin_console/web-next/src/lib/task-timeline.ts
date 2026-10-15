/**
 * Presentation model for a chat task's timeline (rows folded server-side from the
 * task's append-only event log). Kept out of the page so the state -> label mapping
 * is testable: an interruption shown as "done" would be invisible in a screenshot.
 */
export type TimelineState =
  | "pending" | "running" | "completed" | "failed" | "cancelled" | "interrupted" | null;

export interface TimelineRow {
  seq: number;
  at: number | null;
  /** Task state entered by this row; null for a notice row. */
  state: TimelineState;
  /** "transition" or the notice kind (interrupted_resuming, recovered, gave_up, needs_user). */
  kind: string;
  /** Rendered notice text (fixed template, no ids); null for plain transitions. */
  text: string | null;
}

const STATE_LABEL: Record<string, string> = {
  pending: "Queued",
  running: "Started",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Stopped by you",
  interrupted: "Interrupted",
};

const NOTICE_LABEL: Record<string, string> = {
  interrupted_resuming: "You were told: resuming automatically",
  recovered: "You were told: finished after a restart",
  gave_up: "You were told: ended after the last attempt",
  needs_user: "You were told: waiting for your decision",
};

export type Tone = "ok" | "warn" | "bad" | "info";

export function rowLabel(row: TimelineRow): string {
  if (row.state) return STATE_LABEL[row.state] ?? row.state;
  return NOTICE_LABEL[row.kind] ?? "Notice";
}

export function rowTone(row: TimelineRow): Tone {
  if (row.state === "completed") return "ok";
  if (row.state === "failed") return "bad";
  if (row.state === "interrupted") return "warn";
  if (row.kind === "gave_up") return "bad";
  if (row.kind === "needs_user") return "warn";
  if (row.kind === "recovered") return "ok";
  return "info";
}

/** The sentence that answers "what is the status now?" - from the last state row. */
export function currentStatus(rows: TimelineRow[]): string {
  const last = [...rows].reverse().find((r) => r.state);
  if (!last) return "No history yet";
  const hasLaterNotice = rows.some((r) => r.seq > last.seq && r.kind === "gave_up");
  if (hasLaterNotice) return "Ended - no automatic attempts left";
  return rowLabel(last);
}

export function formatAt(at: number | null): string {
  return at ? new Date(at * 1000).toLocaleString("en-US") : "-";
}
