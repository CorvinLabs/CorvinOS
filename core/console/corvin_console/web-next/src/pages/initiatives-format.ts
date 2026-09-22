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

/** Compact duration: "9d 4h", "5h 12m", "42m". */
export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  const s = Math.abs(Math.round(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/** How a finished run landed against its deadline. Within one minute = on time. */
export function scheduleLabel(deltaS: number | null): { text: string; tone: "ok" | "danger" | "secondary" } {
  if (deltaS === null) return { text: "No deadline data", tone: "secondary" };
  if (Math.abs(deltaS) < 60) return { text: "On time", tone: "ok" };
  return deltaS > 0
    ? { text: `${formatDuration(deltaS)} early`, tone: "ok" }
    : { text: `${formatDuration(deltaS)} late`, tone: "danger" };
}

/** "3m ago", "2h 5m ago", "just now". */
export function formatAgo(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "never";
  if (seconds < 60) return "just now";
  return `${formatDuration(seconds)} ago`;
}

/** One-line evidence text: "70/72 tests passing · 1/2 paths present". */
export function evidenceText(v: {
  passed: number; failed: number; errors: number; paths_present: number; paths_total: number; state: string;
}): string {
  if (v.state === "unverified") return "Evidence not verified yet";
  const parts: string[] = [];
  const tests = v.passed + v.failed + v.errors;
  if (tests > 0) {
    let t = `${v.passed}/${tests} tests passing`;
    const bad = [v.failed && `${v.failed} failed`, v.errors && `${v.errors} errors`].filter(Boolean);
    if (bad.length) t += ` (${bad.join(", ")})`;
    parts.push(t);
  }
  if (v.paths_total > 0) parts.push(`${v.paths_present}/${v.paths_total} paths present`);
  return parts.join(" · ");
}

export const STATUS_LABEL: Record<string, string> = {
  running: "Running",
  at_risk: "At risk",
  blocked: "Blocked",
  scheduled: "Scheduled",
  done: "Completed",
  cancelled: "Cancelled",
  pending: "Pending",
};
