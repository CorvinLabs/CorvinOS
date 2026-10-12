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
