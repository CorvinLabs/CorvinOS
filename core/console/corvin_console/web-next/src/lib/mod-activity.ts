/**
 * Encodings for the "Mod activity" card of the Forge Skills tab (ADR-2285). They live here, not in the
 * component, so a wrong mapping (a window shown as the whole history, a malformed quiz shown as "0 quiz
 * cards") is a failing unit test rather than something a screenshot has to catch.
 *
 * Numbers are formatted with a PINNED locale: the same figure must read the same on every operator's machine.
 */

export interface ModActivityWindow {
  bytes_scanned: number;
  chain_bytes: number;
  /** The whole chain was read AND the list was not cut at the limit. */
  complete: boolean;
  /** The list was cut at its limit: older records may exist even if the whole chain was read. */
  limit_reached?: boolean;
}

export interface ModActivityEvent {
  ts: number | null;
  mod: string | null;
  command: string | null;
  skill: string | null;
  score: number | null;
}

export interface ModActivityResponse {
  tenant_id: string;
  reported_by: "mod";
  window: ModActivityWindow;
  limit: number;
  count: number;
  events: ModActivityEvent[];
}

const MIB = 1024 * 1024;

function mib(bytes: number): string {
  return (bytes / MIB).toLocaleString("en-US", { maximumFractionDigits: 1 });
}

/** The part of the audit chain that was read — always shown next to the list. */
export function describeWindow(w: ModActivityWindow): string {
  if (w.complete) return `Whole audit chain read (${mib(w.chain_bytes)} MiB).`;
  // Cut at the limit: how much was read is beside the point, the list is "the newest N", not "all".
  if (w.limit_reached) return "Showing only the newest mod activity; older records are not shown.";
  return `Only the last ${mib(w.bytes_scanned)} MiB of ${mib(w.chain_bytes)} MiB were read; older mod activity is not shown.`;
}

/** What an empty list means. "None in the window" must never read as "none ever". */
export function emptyMessage(w: ModActivityWindow): string {
  return w.complete
    ? "No mod activity has been recorded."
    : "No mod activity in the part of the audit chain that was read.";
}

/** A grade as two decimals; null (the command graded nothing, e.g. a lookup) as an em dash. */
export function formatScore(score: number | null): string {
  return score === null || score === undefined ? "—" : score.toFixed(2);
}

/** Seconds since the epoch as UTC, because the operator's clock is not the chain's. */
export function formatTime(ts: number | null): string {
  if (ts === null || ts === undefined || !Number.isFinite(ts)) return "unknown time";
  return `${new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19)} UTC`;
}

/**
 * Quiz-card badge text. `null` means UNKNOWN (no generator on this build, or a malformed quiz section) and
 * gets no badge: showing "0 quiz cards" would claim the skill has none. Zero gets no badge either.
 */
export function quizBadge(count: number | null | undefined): string | null {
  if (count === null || count === undefined || count <= 0) return null;
  return count === 1 ? "1 quiz card" : `${count} quiz cards`;
}
