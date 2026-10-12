/** Read-only run API (/v1/console/forge/runs…), one record + event log for every Forge kind. */
import { api } from "@/lib/api";

export type RunEventType =
  | "progress" | "iteration" | "file_written" | "test_result" | "finding" | "gate" | "intake" | "error" | "truncated";

export interface RunEvent {
  seq: number;
  ts: string;
  phase: string | null;
  level: "info" | "warning" | "error";
  type: RunEventType;
  message: string;
  data: Record<string, string | number | boolean | null>;
}

export interface RunEventsPage {
  run_id: string;
  events: RunEvent[];
  next_after: number;
}

export function getRunEvents(runId: string, after: number, signal?: AbortSignal): Promise<RunEventsPage> {
  return api<RunEventsPage>(
    `/forge/runs/${encodeURIComponent(runId)}/events?after=${after}&limit=500`,
    { signal },
  );
}

export interface RunSnapshot {
  n: number;
  label: string;
  ts: string;
  files: string[];
}

export interface RunDiffFile {
  path: string;
  status: "added" | "removed" | "changed" | "same";
  diff: string;
}

export interface RunDiff {
  run_id: string;
  from: { n: number; label: string };
  to: { n: number; label: string };
  files: RunDiffFile[];
}

export function getRunSnapshots(runId: string, signal?: AbortSignal): Promise<{ run_id: string; snapshots: RunSnapshot[] }> {
  return api(`/forge/runs/${encodeURIComponent(runId)}/snapshots`, { signal });
}

export function getRunDiff(runId: string, from: string, to: string, signal?: AbortSignal): Promise<RunDiff> {
  return api<RunDiff>(
    `/forge/runs/${encodeURIComponent(runId)}/diff?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`,
    { signal },
  );
}

/** The newest run of a kind (optionally only a given status), or null. Used to attach to a synchronous operation
 *  (a layer PLAN/CREATE) whose run id only arrives with the response, while it is still going. */
export async function latestRun(kind: string, status?: string, signal?: AbortSignal): Promise<{ run_id: string; status: string } | null> {
  const q = `kind=${encodeURIComponent(kind)}${status ? `&status=${encodeURIComponent(status)}` : ""}&limit=1`;
  const r = await api<{ runs: { run_id: string; status: string }[] }>(`/forge/runs?${q}`, { signal });
  return r.runs[0] ?? null;
}

export interface RunSummary {
  run_id: string;
  kind: "skill" | "tool" | "plugin" | "layer";
  operation?: string;
  status: "running" | "success" | "failed";
  phase?: string;
  progress?: number;
  message?: string;
  engine?: string;
  model?: string;
  started_at?: string | null;
  ended_at?: string | null;
  artifact_ref?: { kind: string; id: string } | null;
  error?: string | null;
  failure_code?: string | null;
  live?: boolean;
  retryable?: boolean;
}

export function listRuns(
  filters: { kind?: string; status?: string; limit?: number },
  signal?: AbortSignal,
): Promise<{ runs: RunSummary[]; count: number }> {
  const q = new URLSearchParams();
  if (filters.kind) q.set("kind", filters.kind);
  if (filters.status) q.set("status", filters.status);
  q.set("limit", String(filters.limit ?? 100));
  return api(`/forge/runs?${q.toString()}`, { signal });
}

export function retryToolRun(runId: string, csrf: string): Promise<{ run_id: string }> {
  return api(`/forge-creator/tool/retry/${encodeURIComponent(runId)}`, { method: "POST", csrf });
}
