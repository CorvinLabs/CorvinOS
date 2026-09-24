/**
 * What remains of the initiatives API (routes/initiatives.py) after the
 * Task-Tracking SSOT cutover: the evidence verifier trigger and the read-only
 * list of every runtime run (task_sources.py) shown as "Activity". Work items
 * themselves live in ./task-tracking.ts.
 */
import { api } from "./client";

/** Per-poll budget: a request the server has not answered in 8 s is aborted
 *  and retried rather than holding up every poll queued behind it. */
export const POLL_TIMEOUT_MS = 8_000;

/** Start an evidence verification run in the background (202). With
 *  `ifChanged` the server starts one only if the repo/evidence changed. */
export function startInitiativesVerify(
  csrf: string, ifChanged = false,
): Promise<{ started: boolean; running: boolean; reason?: string }> {
  return api(`/initiatives/verify${ifChanged ? "?if_changed=true" : ""}`, { method: "POST", csrf });
}

// ── Every task type (routes/initiatives.py GET /initiatives/tasks) ───────────

export type TaskType =
  | "initiative" | "chat" | "background" | "acs" | "workflow" | "flow"
  | "gateway" | "forge" | "compute" | "scheduled" | "skill_creator";

export type UnifiedStatus =
  | "queued" | "running" | "paused" | "scheduled"
  | "done" | "failed" | "cancelled" | "stale";

export interface UnifiedTask {
  id: string;
  type: TaskType;
  type_label: string;
  subtype: string | null;
  title: string;
  status: UnifiedStatus;
  raw_status: string | null;
  created_at: string | null;
  started_at: string | null;
  ended_at: string | null;
  sort_ts: number;
  duration_s: number | null;
  /** Set when a record claims to be active but shows no sign of life. */
  stale_reason: string | null;
  detail: string | null;
}

export interface TaskTypeSummary {
  type: TaskType;
  label: string;
  active: number;
  finished: number;
  stale: number;
  failed: number;
  /** e.g. "the workflow plugin does not load on this build" */
  note: string | null;
  /** the source could not be read this time */
  error: string | null;
}

export interface AllTasks {
  server_time: string;
  scan_ms: number;
  types: TaskTypeSummary[];
  active: UnifiedTask[];
  finished: UnifiedTask[];
  finished_total: number;
  totals: { active: number; running: number; stale: number; finished_24h: number; failed_24h: number; all: number };
}

export function getAllTasks(
  opts: { types?: TaskType[]; finishedLimit?: number } = {},
  signal?: AbortSignal,
): Promise<AllTasks> {
  const q = new URLSearchParams();
  if (opts.types?.length) q.set("types", opts.types.join(","));
  if (opts.finishedLimit) q.set("finished_limit", String(opts.finishedLimit));
  const qs = q.toString();
  return api<AllTasks>(`/initiatives/tasks${qs ? `?${qs}` : ""}`, { signal, timeoutMs: POLL_TIMEOUT_MS });
}
