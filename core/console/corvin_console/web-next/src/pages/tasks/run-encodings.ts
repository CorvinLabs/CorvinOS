/**
 * Runs inside the work views (ADR-2081 P4) — pure mappings, no React.
 *
 * Runs (chat turns, background tasks, A2A, ACS … from GET /initiatives/tasks)
 * are shown NEXT TO the work items in every view, never mixed into them: a run
 * has no priority, deadline or rollup, and a board card of a Discord turn must
 * not be draggable to "Complete". Each view gets them in its own form, and the
 * mapping from a run to a board column / timeline span lives here, testable.
 */
import type { TaskType, UnifiedStatus, UnifiedTask } from "@/lib/api/initiatives";
import type { ItemStatus } from "@/lib/api/task-tracking";

/** Every run type the work views show. Commits are results, not work — they
 *  already appear as evidence on the items they are linked to. */
export const WORK_RUN_TYPES: TaskType[] = [
  "chat", "background", "a2a", "acs", "workflow", "flow", "gateway", "forge", "compute", "scheduled", "skill_creator", "agent",
];

/** The run lane shows every active run plus this many of the latest finished ones. */
export const LANE_FINISHED_MAX = 40;

/** Finished runs older than this stay in Activity only. */
export const RUN_WINDOW_MS = 24 * 3600 * 1000;

const ACTIVE: UnifiedStatus[] = ["queued", "running", "paused", "scheduled", "stale"];

export const isActiveRun = (t: UnifiedTask) => ACTIVE.includes(t.status);

function endMs(t: UnifiedTask): number | null {
  const s = t.ended_at ?? t.started_at ?? t.created_at;
  return s ? Date.parse(s) : null;
}

/** All active runs plus those that finished inside the window, matching the
 *  page's search text; newest activity first. */
export function workRuns(active: UnifiedTask[], finished: UnifiedTask[], nowMs: number, q = ""): UnifiedTask[] {
  const needle = q.trim().toLowerCase();
  const recent = finished.filter((t) => { const e = endMs(t); return e !== null && nowMs - e <= RUN_WINDOW_MS; });
  return [...active, ...recent]
    .filter((t) => !needle || `${t.title} ${t.type_label} ${t.subtype ?? ""} ${t.detail ?? ""}`.toLowerCase().includes(needle))
    .sort((a, b) => Number(isActiveRun(b)) - Number(isActiveRun(a)) || b.sort_ts - a.sort_ts);
}

/** What is being worked on right now — running first, then waiting ones. */
export function runningNow(active: UnifiedTask[]): UnifiedTask[] {
  const rank: Partial<Record<UnifiedStatus, number>> = { running: 0, paused: 1, queued: 2, scheduled: 3 };
  return active.filter((t) => t.status in rank && t.type !== "commit")
    .sort((a, b) => (rank[a.status] ?? 9) - (rank[b.status] ?? 9) || b.sort_ts - a.sort_ts);
}

/** The board column a run belongs to, by the same meaning as an item's status. */
export function runBoardColumn(status: UnifiedStatus): Exclude<ItemStatus, "archived"> {
  switch (status) {
    case "running": case "paused": return "in_progress";
    case "failed": case "stale": return "blocked";
    case "done": case "cancelled": return "complete";
    default: return "open";   // queued, scheduled
  }
}

export interface RunGroup { key: string; label: string; type: TaskType; runs: UnifiedTask[]; active: number }

/** Tree groups: one per type and channel ("Chat · discord", "A2A · inbound"). */
export function groupRuns(runs: UnifiedTask[]): RunGroup[] {
  const groups = new Map<string, RunGroup>();
  for (const t of runs) {
    const key = `${t.type}:${t.subtype ?? ""}`;
    const g = groups.get(key) ?? { key, label: t.subtype ? `${t.type_label} · ${t.subtype}` : t.type_label, type: t.type, runs: [], active: 0 };
    g.runs.push(t);
    g.active += Number(isActiveRun(t));
    groups.set(key, g);
  }
  return [...groups.values()].sort((a, b) => b.active - a.active || (b.runs[0]?.sort_ts ?? 0) - (a.runs[0]?.sort_ts ?? 0));
}

export interface RunLaneRow { run: UnifiedTask; x0: number; x1: number }

/** The run timeline: its OWN domain, the last 24 h — minutes-long runs are
 *  invisible on the items' weeks-long axis, and two scales in one chart would
 *  read as comparable while not being so. Positions are fractions of the domain. */
export function runLane(runs: UnifiedTask[], nowMs: number): { domain: [number, number]; rows: RunLaneRow[]; hidden: number } {
  const t0 = nowMs - RUN_WINDOW_MS;
  const span = RUN_WINDOW_MS;
  const rows: RunLaneRow[] = [];
  const finished = runs.filter((r) => !isActiveRun(r)).sort((a, b) => b.sort_ts - a.sort_ts);
  const shown = [...runs.filter(isActiveRun), ...finished.slice(0, LANE_FINISHED_MAX)];
  const hidden = runs.length - shown.length;
  for (const run of shown) {
    const s = run.started_at ?? run.created_at;
    if (!s) continue;
    const start = Math.max(Date.parse(s), t0);
    const end = run.ended_at ? Date.parse(run.ended_at) : isActiveRun(run) ? nowMs : start;
    if (end < t0) continue;
    rows.push({ run, x0: (start - t0) / span, x1: Math.min(1, (Math.max(end, start) - t0) / span) });
  }
  return { domain: [t0, nowMs], rows, hidden };
}
