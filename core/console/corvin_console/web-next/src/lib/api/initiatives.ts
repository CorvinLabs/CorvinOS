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
}

export interface Check {
  label: string;
  state: CheckState;
  detail: string | null;
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
  totals: Record<TaskStatus, number> & {
    total: number; overdue: number; initiatives_blocked: number; runs_active: number; runs_finished: number;
  };
}

export function getInitiatives(signal?: AbortSignal): Promise<InitiativesBoard> {
  return api<InitiativesBoard>("/initiatives", { signal });
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
