/**
 * Initiatives board API — routes/initiatives.py.
 *
 * Every time-dependent number (time_progress_pct, next_checkpoint, overdue,
 * status) is derived server-side from the operator-authored file on each read;
 * the client only ticks countdowns between polls.
 */
import { api } from "./client";

export type TaskStatus = "pending" | "running" | "done" | "blocked";
export type CheckState = "ok" | "pending" | "fail";
export type GateDecision = "pending" | "go" | "no_go";
export type InitiativeStatus = "running" | "at_risk" | "blocked" | "scheduled" | "done" | "cancelled" | string;
export type RunOutcome = "completed" | "cancelled";

/** Last evidence run for a task/precondition (initiatives_verify). */
export interface Verification {
  state: "ok" | "partial" | "failing" | "unverified";
  at: string | null;
  age_s: number | null;
  stale: boolean;
  passed: number;
  failed: number;
  errors: number;
  skipped: number;
  paths_present: number;
  paths_total: number;
  missing_paths: string[];
  summary: string;
  score_pct: number | null;
  first_ok_at?: string | null;
}

export interface InitiativeTask {
  id: string;
  title: string;
  group: string | null;
  status: TaskStatus;
  progress: number;
  due: string | null;
  overdue: boolean;
  completed_at: string | null;
  note: string | null;
  /** "evidence": status + progress are derived from the last verification. */
  progress_source: "manual" | "evidence";
  verification: Verification | null;
  /** Hand-marked done, but the evidence says otherwise. */
  claim_conflict: boolean;
}

export interface Check {
  label: string;
  state: CheckState;
  detail: string | null;
  source?: "manual" | "evidence" | "tasks";
  verification?: Verification | null;
}

export interface Gate {
  id: string;
  title: string;
  at: string | null;
  decision: GateDecision;
  on_go: string | null;
  on_no_go: string | null;
  criteria: Check[];
}

export interface Initiative {
  id: string;
  label: string | null;
  title: string;
  description: string | null;
  cadence: string | null;
  start: string | null;
  deadline: string | null;
  status: InitiativeStatus;
  blocked_by: { initiative: string; gate: string; gate_title: string | null; decision: string } | null;
  time_progress_pct: number | null;
  task_progress_pct: number | null;
  task_counts: Record<TaskStatus, number> & { total: number; overdue: number };
  tasks: InitiativeTask[];
  preconditions: Check[];
  gates: Gate[];
  next_checkpoint: { at: string; label: string } | null;
  /** "finished" = closed by the operator, or every task done. */
  phase: "active" | "finished";
  outcome: RunOutcome | null;
  finished_at: string | null;
  /** Finished: start → finished_at. Active: start → now. */
  duration_s: number | null;
  /** Finished only: seconds before (+) / after (−) the deadline. */
  schedule_delta_s: number | null;
}

export interface InitiativesBoard {
  server_time: string;
  revision: string | null;
  source: string | null;
  initiatives: Initiative[];
  verification: {
    last_at: string | null;
    running: boolean;
    stale_tasks: number;
    claim_conflicts: number;
    stale_after_s: number;
  };
  totals: Record<TaskStatus, number> & {
    total: number; overdue: number; initiatives_blocked: number; runs_active: number; runs_finished: number;
  };
}

/** Per-poll budget: a request the server has not answered in 8 s is aborted
 *  and retried rather than holding up every poll queued behind it. */
export const POLL_TIMEOUT_MS = 8_000;

export function getInitiatives(signal?: AbortSignal): Promise<InitiativesBoard> {
  return api<InitiativesBoard>("/initiatives", { signal, timeoutMs: POLL_TIMEOUT_MS });
}

export function patchInitiativeTask(
  iid: string,
  tid: string,
  body: { status?: TaskStatus; progress?: number },
  csrf: string,
): Promise<InitiativesBoard> {
  return api<InitiativesBoard>(
    `/initiatives/${encodeURIComponent(iid)}/tasks/${encodeURIComponent(tid)}`,
    { method: "PATCH", body, csrf },
  );
}

export function setInitiativeGate(
  iid: string,
  gid: string,
  decision: GateDecision,
  csrf: string,
): Promise<InitiativesBoard> {
  return api<InitiativesBoard>(
    `/initiatives/${encodeURIComponent(iid)}/gates/${encodeURIComponent(gid)}`,
    { method: "PUT", body: { decision }, csrf },
  );
}

/** Close a run (completed/cancelled) or reopen it with `null`. */
export function closeInitiativeRun(
  iid: string,
  outcome: RunOutcome | null,
  csrf: string,
): Promise<InitiativesBoard> {
  return api<InitiativesBoard>(
    `/initiatives/${encodeURIComponent(iid)}/close`,
    { method: "PUT", body: { outcome }, csrf },
  );
}

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
