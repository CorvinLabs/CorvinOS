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
