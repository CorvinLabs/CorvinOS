/**
 * Task-Tracking SSOT API — routes/task_tracking.py (core/task_tracking/).
 *
 * Rollups (progress, counts, overdue) and evidence are derived server-side on
 * every read; the client only groups, filters and lays them out.
 */
import { api } from "./client";

export type ItemKind = "initiative" | "epic" | "story" | "task" | "subtask" | "issue" | "proposal";
export type ItemStatus = "open" | "in_progress" | "blocked" | "complete" | "archived";
export type Priority = "critical" | "high" | "medium" | "low";
export type ApprovalState = "none" | "suggested" | "pending" | "approved" | "rejected";

export interface Evidence {
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
}

export interface Rollup {
  progress: number | null;
  descendants: number;
  counts: Partial<Record<ItemStatus, number>>;
  overdue: number;
}

export interface Item {
  id: string;
  kind: ItemKind;
  parent_id: string | null;
  title: string;
  description: string | null;
  status: ItemStatus;
  status_reason: string | null;
  status_changed_at: string | null;
  priority: Priority;
  owner: string | null;
  assignee: string | null;
  start_at: string | null;
  deadline: string | null;
  progress: number | null;
  work_estimate: number | null;
  work_actual: number | null;
  target_milestone: string | null;
  category: string | null;
  labels: string[];
  approval_state: ApprovalState;
  external_ref: string | null;
  sort_key: number;
  created_at: string;
  created_by: string;
  updated_at: string;
  completed_at: string | null;
  deleted_at: string | null;
  version: number;
  // derived
  overdue: boolean;
  depends_on: string[];
  waiting_on: string[];
  child_ids: string[];
  run_count: number;
  /** Linked runs still active (running / paused / queued / scheduled) — derived per read. */
  live_runs?: number;
  /** Of those, running right now. */
  running_runs?: number;
  /** Up to three "Agent session: <title>" labels of the active linked runs. */
  live_run_titles?: string[];
  rollup: Rollup | null;
  evidence: Evidence | null;
  claim_conflict: boolean;
}

export interface Summary {
  open: number;
  in_progress: number;
  blocked: number;
  complete: number;
  archived: number;
  total: number;
  overdue: number;
  approvals_pending: number;
  done_7d: number;
  initiatives_active: number;
}

export interface ItemList {
  server_time: string;
  items: Item[];
  summary: Summary;
  import_available: boolean;
}

export type ItemBrief = Pick<Item, "id" | "kind" | "title" | "status" | "priority" | "deadline" | "overdue"
  | "deleted_at" | "category" | "approval_state">;

export interface LinkedRun {
  run_type: string;
  run_ref: string;
  linked_at: string;
  linked_by: string;
  found?: boolean;
  title?: string | null;
  status?: string | null;
  type_label?: string;
  started_at?: string | null;
  ended_at?: string | null;
}

export interface HistoryEntry {
  event_id: string;
  event_type: string;
  ts: string;
  actor: string;
  delta: Record<string, unknown>;
  chain_hash: string | null;
}

export interface ItemDetail {
  server_time: string;
  item: Item;
  ancestors: ItemBrief[];
  children: (ItemBrief & { rollup: Rollup | null })[];
  depends_on: (ItemBrief & { dep_type: string })[];
  required_by: (ItemBrief & { dep_type: string })[];
  runs: LinkedRun[];
  history: HistoryEntry[];
}

export interface ItemCreateBody {
  kind: ItemKind;
  title: string;
  parent_id?: string | null;
  status?: ItemStatus;
  priority?: Priority;
  assignee?: string | null;
  deadline?: string | null;
  start_at?: string | null;
  description?: string | null;
  category?: string | null;
  approval_state?: ApprovalState;
}

export type ItemPatchBody = Partial<Omit<ItemCreateBody, "kind" | "title">> & {
  version: number;
  kind?: ItemKind;
  title?: string;
  progress?: number | null;
  status_reason?: string | null;
  work_estimate?: number | null;
  work_actual?: number | null;
};

const BASE = "/task-tracking";
const POLL_TIMEOUT_MS = 8_000;

export function getTaskItems(signal?: AbortSignal, includeDeleted = false): Promise<ItemList> {
  return api<ItemList>(`${BASE}/items${includeDeleted ? "?include_deleted=true" : ""}`,
    { signal, timeoutMs: POLL_TIMEOUT_MS });
}

export function getTaskItem(id: string, signal?: AbortSignal): Promise<ItemDetail> {
  return api<ItemDetail>(`${BASE}/items/${encodeURIComponent(id)}`, { signal, timeoutMs: POLL_TIMEOUT_MS });
}

export function createTaskItem(body: ItemCreateBody, csrf: string): Promise<Item> {
  return api<Item>(`${BASE}/items`, { method: "POST", body, csrf });
}

export function patchTaskItem(id: string, body: ItemPatchBody, csrf: string): Promise<Item> {
  return api<Item>(`${BASE}/items/${encodeURIComponent(id)}`, { method: "PATCH", body, csrf });
}

export function deleteTaskItem(id: string, csrf: string): Promise<Item> {
  return api<Item>(`${BASE}/items/${encodeURIComponent(id)}/delete`, { method: "POST", csrf });
}

export function restoreTaskItem(id: string, csrf: string): Promise<Item> {
  return api<Item>(`${BASE}/items/${encodeURIComponent(id)}/restore`, { method: "POST", csrf });
}

export function decideTaskItem(id: string, decision: "pending" | "approved" | "rejected", version: number,
  csrf: string): Promise<Item> {
  return api<Item>(`${BASE}/items/${encodeURIComponent(id)}/decision`,
    { method: "POST", body: { decision, version }, csrf });
}

export function addTaskDependency(id: string, dependsOnId: string, csrf: string): Promise<{ ok: boolean }> {
  return api(`${BASE}/items/${encodeURIComponent(id)}/dependencies`,
    { method: "POST", body: { depends_on_id: dependsOnId, dep_type: "depends_on" }, csrf });
}

export function removeTaskDependency(id: string, dependsOnId: string, csrf: string): Promise<{ ok: boolean }> {
  return api(`${BASE}/items/${encodeURIComponent(id)}/dependencies/${encodeURIComponent(dependsOnId)}`,
    { method: "DELETE", csrf });
}

export function linkTaskRun(id: string, runType: string, runRef: string, csrf: string): Promise<{ ok: boolean }> {
  return api(`${BASE}/items/${encodeURIComponent(id)}/runs`,
    { method: "POST", body: { run_type: runType, run_ref: runRef }, csrf });
}

export function unlinkTaskRun(id: string, runType: string, runRef: string, csrf: string): Promise<{ ok: boolean }> {
  const q = new URLSearchParams({ run_type: runType, run_ref: runRef });
  return api(`${BASE}/items/${encodeURIComponent(id)}/runs?${q}`, { method: "DELETE", csrf });
}

export function importInitiatives(csrf: string): Promise<{ inserted: number; skipped: number; planned: number }> {
  return api(`${BASE}/import`, { method: "POST", csrf });
}
