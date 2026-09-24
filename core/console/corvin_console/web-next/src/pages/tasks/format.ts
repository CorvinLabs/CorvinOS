/**
 * Pure time/text helpers for the Tasks panel — kept out of the components so
 * the number→text mappings are unit-testable (ADR-0761 rule).
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

/** Compact duration: "9d 4h", "5h 12m", "42m", "17s". */
export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  const s = Math.abs(Math.round(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m` : `${s}s`;
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

/** One fixed colour per task TYPE (nominal → identity, never by value).
 *  Full class names so Tailwind keeps them. */
export const TYPE_CLASS: Record<string, string> = {
  initiative: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  chat: "bg-sky-500/15 text-sky-800 dark:text-sky-300",
  background: "bg-indigo-500/15 text-indigo-800 dark:text-indigo-300",
  acs: "bg-violet-500/15 text-violet-800 dark:text-violet-300",
  workflow: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-300",
  flow: "bg-teal-500/15 text-teal-800 dark:text-teal-300",
  gateway: "bg-blue-500/15 text-blue-800 dark:text-blue-300",
  forge: "bg-orange-500/15 text-orange-800 dark:text-orange-300",
  compute: "bg-fuchsia-500/15 text-fuchsia-800 dark:text-fuchsia-300",
  scheduled: "bg-lime-500/15 text-lime-800 dark:text-lime-300",
  skill_creator: "bg-pink-500/15 text-pink-800 dark:text-pink-300",
  agent: "bg-cyan-500/15 text-cyan-800 dark:text-cyan-300",
  commit: "bg-stone-500/15 text-stone-800 dark:text-stone-300",
};

export const UNIFIED_STATUS: Record<string, { label: string; tone: "ok" | "danger" | "warn" | "secondary" | "outline" }> = {
  queued: { label: "Queued", tone: "secondary" },
  running: { label: "Running", tone: "outline" },
  paused: { label: "Paused", tone: "secondary" },
  scheduled: { label: "Scheduled", tone: "secondary" },
  done: { label: "Done", tone: "ok" },
  failed: { label: "Failed", tone: "danger" },
  cancelled: { label: "Cancelled", tone: "secondary" },
  stale: { label: "Stale", tone: "warn" },
};

/** Live duration for a running record, fixed duration otherwise. */
export function taskDuration(t: { status: string; started_at: string | null; created_at: string | null; duration_s: number | null }, nowMs: number): string {
  if (t.status === "running") {
    const s = Date.parse(t.started_at ?? t.created_at ?? "");
    if (!Number.isNaN(s)) return formatDuration(Math.max(0, (nowMs - s) / 1000));
  }
  return t.duration_s === null ? "—" : formatDuration(t.duration_s);
}
