/**
 * Pure helpers for the Initiatives board — kept out of the component so the
 * number→text mappings are unit-testable (ADR-0761 rule).
 */

/** Countdown text for `targetIso` at `nowMs`. Past targets read "overdue by …". */
export function formatCountdown(targetIso: string | null, nowMs: number): string {
  if (!targetIso) return "—";
  const t = Date.parse(targetIso);
  if (Number.isNaN(t)) return "—";
  const diff = t - nowMs;
  const s = Math.floor(Math.abs(diff) / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const body =
    d > 0 ? `${d}d ${h}h ${m}m`
    : h > 0 ? `${h}h ${m}m ${sec}s`
    : `${m}m ${sec}s`;
  return diff >= 0 ? body : `overdue by ${body}`;
}

/** Offset (ms) to add to the local clock so it matches the server's. */
export function clockSkewMs(serverIso: string | undefined, localAtReceiptMs: number): number {
  if (!serverIso) return 0;
  const s = Date.parse(serverIso);
  return Number.isNaN(s) ? 0 : s - localAtReceiptMs;
}

/** Fixed en-US UTC date formatting (ADR-0764). */
export function formatUtc(iso: string | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  return new Date(t).toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    hour12: false, timeZone: "UTC",
  }) + " UTC";
}

export const STATUS_LABEL: Record<string, string> = {
  running: "Running",
  at_risk: "At risk",
  blocked: "Blocked",
  scheduled: "Scheduled",
  done: "Done",
  pending: "Pending",
};
