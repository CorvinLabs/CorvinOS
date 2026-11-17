/**
 * The "New" view of the Knowledge Graph: what was added or changed lately.
 *
 * Pure functions only (grouping by day, relative time, filters, empty-state copy), so the mapping from the
 * server's list to what a reader sees is testable without a browser. Dates are formatted with a pinned
 * locale (en-US), like every other number in the console, so a screenshot reads the same on every machine.
 */

export type RecentKind = "new" | "updated";

export interface RecentItem {
  /** the node key in the graph (uid, or the human id when the entry has none) */
  id: string;
  /** the human id, e.g. ADR-2326 */
  label: string;
  type: string;
  title: string;
  status: string;
  /** ISO time of the newest commit that touched the entry */
  changed_at: string;
  /** subject line of that commit (a short sentence, never the entry text) */
  summary: string;
  commits: number;
  kind: RecentKind;
  first_seen_at?: string;
}

export type RecentState = "ok" | "empty" | "missing" | "no_commits" | "no_history" | "busy" | "error";

export interface RecentPayload {
  state: RecentState;
  error?: string;
  window_days: number;
  items: RecentItem[];
  /** the tiles' numbers: the period, counted within the active type */
  total: number;
  new_count: number;
  updated_count: number;
  /** how many entries match the active filter (items is the newest of them, capped) */
  matching: number;
  /** entries per type of the period, counted within the active kind (every type of the period is present, zero included) */
  type_counts: Record<string, number>;
  generated_at: string;
  project: string | null;
  /** the history was longer than the server reads: the oldest changes of the period may be missing */
  truncated?: boolean;
  /** the history names entries the graph does not know yet (a pull, a commit nobody indexed): they are missing from the list */
  graph_stale?: boolean;
  /** how many such entries */
  unindexed?: number;
  /** the check ran out of its read budget or time: the count is a lower bound */
  unindexed_capped?: boolean;
  /** the graph could not be re-indexed; the list comes from the last one (the reason, a short sentence) */
  index_error?: string | null;
}

export const WINDOW_CHOICES = [7, 14, 30] as const;
export type WindowDays = (typeof WINDOW_CHOICES)[number];
export const DEFAULT_WINDOW: WindowDays = 14;

export type KindFilter = "all" | RecentKind;

/** Reader-facing names for the entity types of the knowledge base. Unknown types show as they are. */
export const TYPE_LABEL: Record<string, string> = {
  decision: "Decision", concept: "Concept", idea: "Idea", plan: "Plan", review: "Review",
  note: "Note", task: "Task", epic: "Epic", initiative: "Initiative",
};

/** A value of a map keyed by data: only the map's own entries, never a name on Object.prototype ("constructor"). */
export function own<T>(map: Record<string, T>, key: string): T | undefined {
  return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : undefined;
}

export function typeLabel(type: string): string {
  return own(TYPE_LABEL, type) ?? (type ? type[0].toUpperCase() + type.slice(1) : "Entry");
}

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

/** "just now" · "12 min ago" · "5 h ago" · "3 d ago" · a date for anything older than two weeks. */
export function relativeTime(iso: string, nowMs: number): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "date unknown";
  const d = nowMs - t;
  if (d < 0 || d < MIN) return "just now";
  if (d < HOUR) return `${Math.floor(d / MIN)} min ago`;
  if (d < DAY) return `${Math.floor(d / HOUR)} h ago`;
  if (d < 14 * DAY) return `${Math.floor(d / DAY)} d ago`;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/** The reader's local calendar day of an ISO time, as a sortable key (YYYY-MM-DD); "unknown" for a bad date. */
export function dayKey(iso: string): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "unknown";
  const d = new Date(t);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** "Today" · "Yesterday" · "Mon, Nov 10" (with the year when it is not this year). */
export function dayLabel(key: string, nowMs: number): string {
  if (key === "unknown") return "Date unknown";
  if (key === dayKey(new Date(nowMs).toISOString())) return "Today";
  if (key === dayKey(new Date(nowMs - DAY).toISOString())) return "Yesterday";
  const [y, m, d] = key.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  const sameYear = y === new Date(nowMs).getFullYear();
  return date.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}

/** Words an automatic knowledge-base commit adds around the title ("kb: new task T-0003 — <title>"): they carry no news. */
const BOILERPLATE = new Set(["kb", "new", "task", "decision", "concept", "epic", "initiative", "idea", "plan", "review", "note", "adr", "t", "e", "i",
  // small words: "Corvin itself as a built-in project" under the title "Corvin as a built-in project" adds nothing
  "a", "an", "the", "as", "for", "of", "to", "in", "on", "and", "with", "it", "itself", "is", "its"]);

/** The commit line is shown only when it says something the title does not. An automatic "kb: new task T-1 — <title>" commit
 *  only repeats the title, so it is left out; "kb: rename the Tasks board columns" under a title "Tasks" adds news and stays. */
export function showSummary(it: Pick<RecentItem, "summary" | "title">): boolean {
  const s = it.summary.trim().toLowerCase();
  if (!s) return false;
  const words = (x: string) => x.split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  const titleWords = words(it.title.trim().toLowerCase());
  // news = a word the title does not have (a word the title STARTS with counts: a subject cut off at 140 characters ends mid-word)
  const news = words(s).filter((w) => !BOILERPLATE.has(w) && !/\d/.test(w)).filter((w) => !titleWords.some((tw) => tw === w || tw.startsWith(w)));
  return news.length > 0;
}

export interface DayGroup { key: string; label: string; items: RecentItem[] }

/** Items grouped by local day, newest day first; inside a day the order the server gave (newest first) is kept. */
export function groupByDay(items: RecentItem[], nowMs: number): DayGroup[] {
  const order: string[] = [];
  const by = new Map<string, RecentItem[]>();
  for (const it of items) {
    const k = dayKey(it.changed_at);
    if (!by.has(k)) { by.set(k, []); order.push(k); }
    by.get(k)!.push(it);
  }
  order.sort((a, b) => (a === "unknown" ? 1 : b === "unknown" ? -1 : a < b ? 1 : a > b ? -1 : 0));
  return order.map((k) => ({ key: k, label: dayLabel(k, nowMs), items: by.get(k)! }));
}

/** The types of a period, most frequent first (ties alphabetical), for the type row. */
export function sortedTypes(counts: Record<string, number>): { type: string; count: number }[] {
  return Object.entries(counts).map(([type, count]) => ({ type, count })).sort((a, b) => b.count - a.count || a.type.localeCompare(b.type));
}

/** What to tell the reader when there is nothing to list. Never invents data: it names the state. */
export function emptyCopy(state: RecentState, days: number, filtered: boolean): { title: string; hint: string } {
  if (state === "missing") {
    return { title: "This knowledge base has not been indexed yet", hint: "Run `kb index` in its checkout (Pull and Sync only fetch). New entries show up here once the graph exists." };
  }
  if (state === "busy") {
    return { title: "The graph is being rebuilt", hint: "This list looks again a few times. Press Refresh if it does not appear." };
  }
  if (state === "no_commits") {
    return { title: "No commits yet", hint: "The repository has no commit. The first commit starts this list." };
  }
  if (state === "no_history") {
    return { title: "No change history to read", hint: "This folder has no git repository of its own that can be read here (a plain folder, a linked worktree or a submodule), so there is no record of when entries were added." };
  }
  if (state === "error") {
    return { title: "The change history could not be read", hint: "The knowledge base is there, but its history or its graph could not be read. Refresh to try again; if it persists, check the repository." };
  }
  if (filtered) {
    return { title: "Nothing matches these filters", hint: "Clear a filter to see the other changes in this period." };
  }
  return { title: `Nothing new in the last ${days} days`, hint: "Try a longer period. New and changed entries appear here as soon as they are committed." };
}

/** Deploy marker (a string literal): only the "New" view's bundle contains it. */
export const MARKER_RECENT = "Everything that was added or changed in this knowledge base, newest first. Read-only: opening an entry never edits it.";
