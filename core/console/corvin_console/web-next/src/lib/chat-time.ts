/**
 * Time presentation shared by every chat surface: a time-only stamp per message and a
 * Today / Yesterday / date separator wherever the thread crosses a local calendar day.
 * Timestamps are unix SECONDS (fractional allowed). en-US formatting, pinned.
 */

const DAY_FMT = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric" });
const DAY_FMT_YEAR = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric" });
const TIME_FMT = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });
const FULL_FMT = new Intl.DateTimeFormat("en-US", { dateStyle: "full", timeStyle: "short" });

/** Local calendar day of a unix-seconds timestamp, as a comparable key. */
export function localDayKey(ts: number): string {
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

/** "Today" / "Yesterday" / "Mon, Oct 12" (with the year once it is not the current one). */
export function dayLabel(ts: number, nowS: number): string {
  if (localDayKey(ts) === localDayKey(nowS)) return "Today";
  const now = new Date(nowS * 1000);
  // Calendar arithmetic, not "minus 86 400 s": a DST day is 23 or 25 hours long.
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1, 12);
  if (localDayKey(ts) === localDayKey(Math.floor(yesterday.getTime() / 1000))) return "Yesterday";
  const d = new Date(ts * 1000);
  return (d.getFullYear() === new Date(nowS * 1000).getFullYear() ? DAY_FMT : DAY_FMT_YEAR).format(d);
}

/** The time a row shows — its day is carried by the separator above it. */
export function messageTime(ts: number): string {
  return TIME_FMT.format(new Date(ts * 1000));
}

/** Full date and time, for the tooltip. */
export function messageTimeFull(ts: number): string {
  return FULL_FMT.format(new Date(ts * 1000));
}

/** True where a separator belongs above message `i`: the first one, or a new local day. */
export function startsNewDay(tsList: readonly number[], i: number): boolean {
  return i === 0 || localDayKey(tsList[i - 1]) !== localDayKey(tsList[i]);
}

/** Decorative actor glyph for the two-actor assistant chat; the visible name stays next to it. */
export const CHAT_ROLE_MARK = { user: "🧑", assistant: "🤖" } as const;
