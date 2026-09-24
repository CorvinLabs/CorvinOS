/**
 * Every number→mark and data→layout mapping of the Tasks panel, as pure
 * functions (ADR-0761: encodings live in a testable module, not in the
 * component — a wrong mapping is invisible in a screenshot until a tenant hits it).
 *
 * Colour rules:
 *  - status is the only data colour, from the validated `--viz-status-*`
 *    tokens; "open" is a hollow mark, never a fill;
 *  - status never appears as colour alone — every mark also carries an icon
 *    or a text label;
 *  - priority is ORDINAL, so it is an ordinal amber ramp keyed on the tier
 *    (`--viz-tier-*`), never on a measured value.
 */
import type { Item, ItemKind, ItemStatus, Priority } from "@/lib/api/task-tracking";

export const STATUS_ORDER: ItemStatus[] = ["open", "in_progress", "blocked", "complete", "archived"];
export const BOARD_COLUMNS: ItemStatus[] = ["open", "in_progress", "blocked", "complete"];
export const PRIORITY_ORDER: Priority[] = ["critical", "high", "medium", "low"];
export const KIND_ORDER: ItemKind[] = ["initiative", "epic", "story", "task", "subtask", "issue", "proposal"];

export const STATUS_META: Record<ItemStatus, {
  label: string; badge: "ok" | "warn" | "danger" | "secondary" | "outline"; fill: string | null;
}> = {
  open: { label: "Open", badge: "outline", fill: null },
  in_progress: { label: "In progress", badge: "warn", fill: "var(--viz-status-progress)" },
  blocked: { label: "Blocked", badge: "danger", fill: "var(--viz-status-blocked)" },
  complete: { label: "Complete", badge: "ok", fill: "var(--viz-status-complete)" },
  archived: { label: "Archived", badge: "secondary", fill: null },
};

export const PRIORITY_META: Record<Priority, { label: string; short: string; rank: number; stripe: string | null }> = {
  critical: { label: "Critical", short: "P0", rank: 0, stripe: "var(--viz-tier-3)" },
  high: { label: "High", short: "P1", rank: 1, stripe: "var(--viz-tier-2)" },
  medium: { label: "Medium", short: "P2", rank: 2, stripe: "var(--viz-tier-1)" },
  low: { label: "Low", short: "P3", rank: 3, stripe: null },
};

export const KIND_META: Record<ItemKind, { label: string; container: boolean }> = {
  initiative: { label: "Initiative", container: true },
  epic: { label: "Epic", container: true },
  story: { label: "Story", container: true },
  task: { label: "Task", container: false },
  subtask: { label: "Subtask", container: false },
  issue: { label: "Issue", container: false },
  proposal: { label: "Proposal", container: false },
};

/** Which parent kinds a new item of `kind` may sit under — mirrors models.PARENT_RULES. */
export const PARENT_RULES: Record<ItemKind, (ItemKind | null)[]> = {
  initiative: [null],
  epic: ["initiative"],
  story: ["initiative", "epic"],
  task: [null, "initiative", "epic", "story", "issue", "proposal"],
  issue: [null, "initiative", "epic", "story"],
  proposal: [null, "initiative", "epic", "story"],
  subtask: ["epic", "story", "task", "issue", "proposal"],
};

export const CATEGORY_LABEL: Record<string, string> = {
  gate: "Gate", precondition: "Precondition", checkpoint: "Checkpoint", criterion: "Criterion",
};

/** Progress shown for an item: the rollup for a parent, its own value for a leaf. */
export function displayProgress(it: Pick<Item, "status" | "progress" | "rollup" | "child_ids">): number {
  if (it.child_ids.length > 0 && it.rollup?.progress != null) return it.rollup.progress;
  if (it.status === "complete") return 100;
  return it.progress ?? 0;
}

// ── Filters ─────────────────────────────────────────────────────────────────

export interface Filters {
  q: string;
  statuses: ItemStatus[];      // empty = all except archived
  priorities: Priority[];      // empty = all
  kinds: ItemKind[];           // empty = all
  overdueOnly: boolean;
  approvalsOnly: boolean;
  doneRecently: boolean;       // completed within the last 7 days
  showClosed: boolean;         // show complete + archived
}

export const WORK_KINDS: ItemKind[] = ["task", "subtask", "issue", "proposal"];
const WEEK = 7 * 86_400_000;

export const EMPTY_FILTERS: Filters = {
  q: "", statuses: [], priorities: [], kinds: [], overdueOnly: false, approvalsOnly: false, doneRecently: false,
  showClosed: true,
};

export function matches(it: Item, f: Filters, nowMs: number = Date.now()): boolean {
  if (f.statuses.length ? !f.statuses.includes(it.status) : it.status === "archived") return false;
  if (!f.showClosed && !f.statuses.length && it.status === "complete") return false;
  if (f.priorities.length && !f.priorities.includes(it.priority)) return false;
  if (f.kinds.length && !f.kinds.includes(it.kind)) return false;
  if (f.overdueOnly && !it.overdue) return false;
  if (f.approvalsOnly && it.approval_state !== "pending") return false;
  if (f.doneRecently) {
    const at = it.completed_at ? Date.parse(it.completed_at) : Number.NaN;
    if (it.status !== "complete" || !(nowMs - at <= WEEK)) return false;
  }
  const q = f.q.trim().toLowerCase();
  if (q) {
    const hay = [it.title, it.description, it.assignee, it.owner, it.category, it.id, ...it.labels]
      .filter(Boolean).join(" ").toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}

export function filtersActive(f: Filters): boolean {
  return Boolean(f.q.trim() || f.statuses.length || f.priorities.length || f.kinds.length
    || f.overdueOnly || f.approvalsOnly || f.doneRecently || !f.showClosed);
}

/** Filters ↔ URL query, so a view can be linked and survives a reload. */
export function filtersToQuery(f: Filters): Record<string, string> {
  const q: Record<string, string> = {};
  // Not trimmed: the search box is bound to the URL, so trimming here would eat
  // the space the operator is typing between two words.
  if (f.q.trim()) q.q = f.q;
  if (f.statuses.length) q.status = f.statuses.join(",");
  if (f.priorities.length) q.priority = f.priorities.join(",");
  if (f.kinds.length) q.kind = f.kinds.join(",");
  if (f.overdueOnly) q.overdue = "1";
  if (f.approvalsOnly) q.approvals = "1";
  if (f.doneRecently) q.done7 = "1";
  if (!f.showClosed) q.closed = "0";
  return q;
}

export function filtersFromQuery(p: URLSearchParams): Filters {
  const list = <T extends string>(key: string, allowed: readonly T[]): T[] =>
    (p.get(key) ?? "").split(",").filter((x): x is T => (allowed as readonly string[]).includes(x));
  return {
    q: p.get("q") ?? "",
    statuses: list("status", STATUS_ORDER),
    priorities: list("priority", PRIORITY_ORDER),
    kinds: list("kind", KIND_ORDER),
    overdueOnly: p.get("overdue") === "1",
    approvalsOnly: p.get("approvals") === "1",
    doneRecently: p.get("done7") === "1",
    showClosed: p.get("closed") !== "0",
  };
}

// ── Tree ────────────────────────────────────────────────────────────────────

export interface TreeRow { item: Item; depth: number; hasChildren: boolean; matched: boolean }

/**
 * Depth-first rows in server order. With a filter, an item is kept when it
 * matches OR one of its descendants does (context rows stay, marked
 * `matched: false`, so a hit is never shown without its path). Collapsed
 * items hide their subtree unless a filter is active.
 */
export function buildTree(items: Item[], f: Filters, collapsed: Set<string>): TreeRow[] {
  const byId = new Map(items.map((i) => [i.id, i]));
  const kids = new Map<string | null, Item[]>();
  for (const it of items) {
    const parent = it.parent_id && byId.has(it.parent_id) ? it.parent_id : null;
    const arr = kids.get(parent) ?? [];
    arr.push(it);
    kids.set(parent, arr);
  }
  const active = filtersActive(f);
  const keep = new Map<string, boolean>();
  const visit = (it: Item, depth: number): boolean => {
    if (depth > 64) return false;
    let any = matches(it, f);
    for (const c of kids.get(it.id) ?? []) any = visit(c, depth + 1) || any;
    keep.set(it.id, any);
    return any;
  };
  for (const r of kids.get(null) ?? []) visit(r, 0);

  // Top level: most recent activity in the subtree first, so current work leads
  // and a finished plan sinks. Children keep their stored order (sort_key).
  const latest = new Map<string, number>();
  const recency = (it: Item, depth: number): number => {
    const own = Math.max(...[it.updated_at, it.status_changed_at, it.completed_at].map((t) => (t ? Date.parse(t) || 0 : 0)));
    const sub = depth > 64 ? 0 : Math.max(0, ...(kids.get(it.id) ?? []).map((c) => recency(c, depth + 1)));
    const v = Math.max(own, sub);
    latest.set(it.id, v);
    return v;
  };
  const roots = [...(kids.get(null) ?? [])];
  for (const r of roots) recency(r, 0);
  roots.sort((a, b) => (latest.get(b.id) ?? 0) - (latest.get(a.id) ?? 0));

  const rows: TreeRow[] = [];
  const walk = (it: Item, depth: number) => {
    if (!keep.get(it.id) || depth > 64) return;
    const children = (kids.get(it.id) ?? []).filter((c) => keep.get(c.id));
    rows.push({ item: it, depth, hasChildren: children.length > 0, matched: matches(it, f) });
    if (collapsed.has(it.id) && !active) return;
    for (const c of children) walk(c, depth + 1);
  };
  for (const r of roots) walk(r, 0);
  return rows;
}

// ── Board ───────────────────────────────────────────────────────────────────

/** Work items (not containers) per status column, most urgent first. */
export function boardColumns(items: Item[], f: Filters): Record<ItemStatus, Item[]> {
  const cols = Object.fromEntries(STATUS_ORDER.map((s) => [s, [] as Item[]])) as Record<ItemStatus, Item[]>;
  for (const it of items) {
    if (KIND_META[it.kind].container || !matches(it, f)) continue;
    cols[it.status].push(it);
  }
  const dl = (i: Item) => (i.deadline ? Date.parse(i.deadline) : Number.POSITIVE_INFINITY);
  for (const s of STATUS_ORDER) {
    cols[s].sort((a, b) => Number(b.overdue) - Number(a.overdue)
      || PRIORITY_META[a.priority].rank - PRIORITY_META[b.priority].rank
      || dl(a) - dl(b));
  }
  return cols;
}

// ── Timeline ────────────────────────────────────────────────────────────────

export interface TimelineRow {
  item: Item;
  depth: number;
  /** 0..1 along the domain; `null` when the item has no datable span. */
  x0: number | null;
  x1: number | null;
  /** A dated point without a span. */
  point: number | null;
  /** "milestone" = a gate or checkpoint (diamond); "deadline" = a due date with no usable start (dot). */
  pointKind: "milestone" | "deadline" | null;
}

export interface Timeline {
  domain: [number, number] | null;
  now: number | null;
  rows: TimelineRow[];
  ticks: { at: number; x: number; label: string }[];
  datable: number;
}

const DAY = 86_400_000;

function ts(iso: string | null, endOfDay = false): number | null {
  if (!iso) return null;
  const t = Date.parse(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(t)) return null;
  return iso.length === 10 && endOfDay ? t + DAY - 1000 : t;
}

/** The span an item occupies: start (or created) → deadline (or completion). */
export function itemSpan(it: Item): { start: number | null; end: number | null } {
  const end = ts(it.deadline, true) ?? (it.status === "complete" ? ts(it.completed_at) : null);
  const start = ts(it.start_at) ?? (end !== null ? ts(it.created_at) : null);
  return { start, end };
}

const isPointItem = (it: Item) => it.category === "gate" || it.category === "checkpoint";

/**
 * Rows in tree order with positions on one shared domain. Below two datable
 * items there is no meaningful axis — the caller renders a list instead of an
 * empty chart (ADR-0761: the form degrades with the data).
 */
export function buildTimeline(rows: TreeRow[], nowMs: number): Timeline {
  const spans = rows.map((r) => ({ r, ...itemSpan(r.item) }));
  const pts: number[] = [];
  for (const s of spans) {
    if (s.end !== null) pts.push(s.end);
    if (s.start !== null && !isPointItem(s.r.item)) pts.push(s.start);
  }
  const datable = spans.filter((s) => s.end !== null).length;
  if (datable < 2 || pts.length < 2) {
    return { domain: null, now: null, rows: rows.map((r) => ({ item: r.item, depth: r.depth, x0: null, x1: null, point: null, pointKind: null })), ticks: [], datable };
  }
  let lo = Math.min(...pts, nowMs);
  let hi = Math.max(...pts, nowMs);
  const pad = Math.max((hi - lo) * 0.03, DAY / 2);
  lo -= pad;
  hi += pad;
  const x = (t: number) => (t - lo) / (hi - lo);
  const out: TimelineRow[] = spans.map(({ r, start, end }) => {
    if (end === null) return { item: r.item, depth: r.depth, x0: null, x1: null, point: null, pointKind: null };
    if (isPointItem(r.item)) return { item: r.item, depth: r.depth, x0: null, x1: null, point: x(end), pointKind: "milestone" };
    if (start === null || start >= end) {
      return { item: r.item, depth: r.depth, x0: null, x1: null, point: x(end), pointKind: "deadline" };
    }
    return { item: r.item, depth: r.depth, x0: x(start), x1: x(end), point: null, pointKind: null };
  });
  return { domain: [lo, hi], now: x(nowMs), rows: out, ticks: timeTicks(lo, hi).map((t) => ({ ...t, x: x(t.at) })), datable };
}

/** Day/week/month ticks for the domain — at most ~8, labels en-US UTC. */
export function timeTicks(lo: number, hi: number): { at: number; label: string }[] {
  const span = hi - lo;
  const steps = [DAY, 2 * DAY, 7 * DAY, 14 * DAY, 30 * DAY, 91 * DAY, 182 * DAY];
  let step = steps.find((s) => span / s <= 8) ?? 365 * DAY;
  while (span / step > 8) step *= 2;  // an outlier date (year 9999) must not yield thousands of ticks
  const first = Math.ceil(lo / DAY) * DAY;
  const out: { at: number; label: string }[] = [];
  for (let t = first; t <= hi; t += step) {
    out.push({ at: t, label: new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" }) });
  }
  return out;
}

// ── Deadline text ───────────────────────────────────────────────────────────

/** Deadline as a date+time label; a bare date reads as that day (it is due at its end). */
export function formatDeadline(deadline: string | null): string {
  if (!deadline) return "—";
  const t = ts(deadline);
  if (t === null) return "—";
  if (deadline.length === 10) {
    return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  }
  return new Date(t).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" }) + " UTC";
}

/** "due in 3d", "due in 5h", "2d overdue", "due today" — compact, en-US. */
export function deadlineText(deadline: string | null, nowMs: number, closed: boolean): string | null {
  const t = ts(deadline, true);
  if (t === null) return null;
  if (closed) return `due ${new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" })}`;
  const diff = t - nowMs;
  const abs = Math.abs(diff);
  const unit = abs >= DAY ? `${Math.floor(abs / DAY)}d` : abs >= 3_600_000 ? `${Math.floor(abs / 3_600_000)}h` : `${Math.max(1, Math.floor(abs / 60_000))}m`;
  return diff >= 0 ? `due in ${unit}` : `${unit} overdue`;
}
