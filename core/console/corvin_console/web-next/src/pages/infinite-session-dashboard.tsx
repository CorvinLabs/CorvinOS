/**
 * Infinite Session Dashboard (Phase D) — ADR-0545
 *
 * Reads the per-task snapshot chain of the infinite-session engine
 * (`/v1/console/api/infinite-session/*`, backed ONLY by the tenant-bound
 * EventStore), shows drift levels per snapshot, a state diff between two
 * snapshots, and lets the operator revert — which appends a new, chained
 * `rollback_recovery` snapshot (nothing is deleted).
 *
 * The revert POST carries the session CSRF token (`X-CSRF-Token`) because the
 * backend sits behind `require_csrf`. The `reason` field is an operator
 * confirmation only; the backend never persists it.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ChevronDown, RotateCcw, Search, TrendingUp } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { fetchConsoleJson } from "@/lib/api-utils";
import { useAuth } from "@/lib/auth";

// ─ Types (mirror routes/infinite_session_api.py) ───────────────────────────

interface CheckpointRecord {
  checkpoint_id: string;
  phase_id: string;
  snapshot_type: string;
  timestamp: string;
  seq: number;
  content_hash: string;
  prev_snapshot_hash: string | null;
  config_state: Record<string, unknown>;
  drift_level: string;
  drift_alert: string | null;
}

interface TaskHistory {
  task_id: string;
  tenant_id: string;
  started_at: string;
  last_updated: string;
  checkpoint_count: number;
  chain_valid: boolean;
  chain_error: string | null;
  checkpoints: CheckpointRecord[];
  current_config: Record<string, unknown>;
  total_drift: number;
}

interface TaskSummary {
  task_id: string;
  phase_id: string;
  started_at: string;
  last_updated: string;
  checkpoint_count: number;
  current_drift_level: string;
  last_drift_alert: string | null;
  status: string;
}

interface TaskListResponse {
  tasks: TaskSummary[];
  total_count: number;
  tenant_id: string;
  timestamp: string;
}

interface ConfigDiff {
  from_checkpoint_id: string;
  to_checkpoint_id: string;
  timestamp: string;
  additions: Record<string, unknown>;
  removals: Record<string, unknown>;
  modifications: Record<string, { before: unknown; after: unknown }>;
}

interface RevertResult {
  success: boolean;
  task_id: string;
  reverted_to_checkpoint: string;
  new_checkpoint_id: string;
  transaction_id: string;
  timestamp: string;
  error: string | null;
}

// ─ API client (paths exactly as declared by the router) ────────────────────

const BASE = "/v1/console/api/infinite-session";

function fetchTasksList(limit = 50, offset = 0): Promise<TaskListResponse> {
  return fetchConsoleJson<TaskListResponse>(`${BASE}/tasks?limit=${limit}&offset=${offset}`);
}

function fetchTaskHistory(taskId: string): Promise<TaskHistory> {
  return fetchConsoleJson<TaskHistory>(`${BASE}/task/${encodeURIComponent(taskId)}/history`);
}

function fetchConfigDiff(taskId: string, from: string, to: string): Promise<ConfigDiff> {
  const q = new URLSearchParams({ from_checkpoint: from, to_checkpoint: to });
  return fetchConsoleJson<ConfigDiff>(
    `${BASE}/task/${encodeURIComponent(taskId)}/context-diff?${q.toString()}`,
  );
}

function revertToCheckpoint(
  taskId: string,
  targetCheckpoint: string,
  reason: string,
  csrf: string,
): Promise<RevertResult> {
  return fetchConsoleJson<RevertResult>(`${BASE}/task/${encodeURIComponent(taskId)}/revert`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify({ task_id: taskId, target_checkpoint_id: targetCheckpoint, reason }),
  });
}

// ─ Presentation helpers ────────────────────────────────────────────────────

type BadgeVariant = "default" | "secondary" | "outline" | "ok" | "warn" | "danger";

function driftBadgeVariant(level: string): BadgeVariant {
  switch (level.toUpperCase()) {
    case "CRITICAL":
      return "danger";
    case "WARNING":
      return "warn";
    case "NORMAL":
      return "ok";
    default:
      return "secondary";
  }
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const TH = "px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground";
const TD = "px-3 py-2 align-middle";

// ─ Config diff viewer ──────────────────────────────────────────────────────

function ConfigDiffViewer({ taskId, from, to }: { taskId: string; from: string; to: string }) {
  const { data: diff, isLoading, error } = useQuery({
    queryKey: ["infinite-session", "diff", taskId, from, to],
    queryFn: () => fetchConfigDiff(taskId, from, to),
    enabled: from.length > 0 && to.length > 0 && from !== to,
  });

  if (from === to) return <div className="text-sm text-muted-foreground">Select an earlier checkpoint to compare.</div>;
  if (isLoading) return <Skeleton className="h-24" />;
  if (error) return <div className="text-sm text-destructive">Diff unavailable: {(error as Error).message}</div>;
  if (!diff) return null;

  const sections: Array<[string, Record<string, unknown>, string]> = [
    ["Additions", diff.additions, "bg-emerald-500/10"],
    ["Removals", diff.removals, "bg-destructive/10"],
    ["Modifications", diff.modifications, "bg-accent/10"],
  ];
  const empty = sections.every(([, v]) => Object.keys(v).length === 0);

  return (
    <div className="space-y-3">
      {sections.map(([title, value, cls]) =>
        Object.keys(value).length === 0 ? null : (
          <div key={title} className={`rounded-md border p-3 ${cls}`}>
            <h4 className="mb-2 text-sm font-semibold">{title}</h4>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs">
              {JSON.stringify(value, null, 2)}
            </pre>
          </div>
        ),
      )}
      {empty && <div className="text-sm italic text-muted-foreground">No differences</div>}
    </div>
  );
}

// ─ Task history modal ──────────────────────────────────────────────────────

function TaskHistoryModal({
  taskId,
  open,
  onOpenChange,
}: {
  taskId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string>("");
  const [reason, setReason] = useState<string>("");
  const [confirming, setConfirming] = useState<boolean>(false);
  const [lastResult, setLastResult] = useState<string | null>(null);

  const { data: history, isLoading, error } = useQuery({
    queryKey: ["infinite-session", "history", taskId],
    queryFn: () => fetchTaskHistory(taskId),
    enabled: open,
  });

  const revert = useMutation({
    mutationFn: () => revertToCheckpoint(taskId, selected, reason, csrf),
    onSuccess: (result: RevertResult) => {
      setConfirming(false);
      setReason("");
      setLastResult(`Reverted to ${result.reverted_to_checkpoint.slice(0, 8)} — new checkpoint ${result.new_checkpoint_id.slice(0, 8)} (tx ${result.transaction_id.slice(0, 8)})`);
      void queryClient.invalidateQueries({ queryKey: ["infinite-session"] });
    },
    onError: (err: Error) => setLastResult(`Revert failed: ${err.message}`),
  });

  const headId = history?.checkpoints[history.checkpoints.length - 1]?.checkpoint_id ?? "";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-4xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Task chain: {taskId}</DialogTitle>
          <DialogDescription>Snapshot timeline, drift per snapshot, diff and revert</DialogDescription>
        </DialogHeader>

        {isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-8" />
            <Skeleton className="h-8" />
          </div>
        )}
        {error && <div className="text-sm text-destructive">{(error as Error).message}</div>}

        {history && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
              <div>
                <div className="text-muted-foreground">Started</div>
                <div className="font-mono text-xs">{fmtDate(history.started_at)}</div>
              </div>
              <div>
                <div className="text-muted-foreground">Last updated</div>
                <div className="font-mono text-xs">{fmtDate(history.last_updated)}</div>
              </div>
              <div>
                <div className="text-muted-foreground">Snapshots</div>
                <div className="font-semibold">{history.checkpoint_count}</div>
              </div>
              <div>
                <div className="text-muted-foreground">Chain</div>
                <Badge variant={history.chain_valid ? "ok" : "danger"}>
                  {history.chain_valid ? "verified" : `broken: ${history.chain_error ?? ""}`}
                </Badge>
              </div>
            </div>

            <div className="overflow-x-auto rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr>
                    <th className={TH}>#</th>
                    <th className={TH}>Checkpoint</th>
                    <th className={TH}>Phase</th>
                    <th className={TH}>Type</th>
                    <th className={TH}>Timestamp</th>
                    <th className={TH}>Drift</th>
                    <th className={TH}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {history.checkpoints.map((cp: CheckpointRecord) => (
                    <tr key={cp.checkpoint_id} className="border-t">
                      <td className={TD}>{cp.seq}</td>
                      <td className={`${TD} font-mono text-xs`} title={cp.content_hash}>
                        {cp.checkpoint_id.slice(0, 8)}
                      </td>
                      <td className={`${TD} font-mono text-xs`}>{cp.phase_id}</td>
                      <td className={TD}>
                        <Badge variant={cp.snapshot_type === "rollback_recovery" ? "warn" : "outline"}>
                          {cp.snapshot_type}
                        </Badge>
                      </td>
                      <td className={`${TD} text-xs`}>{fmtDate(cp.timestamp)}</td>
                      <td className={TD}>
                        <Badge variant={driftBadgeVariant(cp.drift_level)} title={cp.drift_alert ?? undefined}>
                          {cp.drift_level}
                        </Badge>
                      </td>
                      <td className={TD}>
                        <Button
                          size="sm"
                          variant={selected === cp.checkpoint_id ? "secondary" : "ghost"}
                          onClick={() => {
                            setSelected(cp.checkpoint_id);
                            setLastResult(null);
                          }}
                        >
                          {selected === cp.checkpoint_id ? "Selected" : "Select"}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {selected && (
              <div className="space-y-3 rounded-md border bg-muted/30 p-3">
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-semibold">
                    Diff: {selected.slice(0, 8)} → head {headId.slice(0, 8)}
                  </h4>
                  <Button
                    size="sm"
                    disabled={selected === headId || !history.chain_valid}
                    onClick={() => setConfirming(true)}
                    className="gap-1"
                  >
                    <RotateCcw className="h-3 w-3" />
                    Revert to this checkpoint
                  </Button>
                </div>
                <ConfigDiffViewer taskId={taskId} from={selected} to={headId} />
              </div>
            )}

            {lastResult && <div className="text-sm">{lastResult}</div>}
          </div>
        )}

        <Dialog open={confirming} onOpenChange={setConfirming}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Confirm revert</DialogTitle>
              <DialogDescription>
                Appends a new chained recovery snapshot with the state of {selected.slice(0, 8)}. Nothing is
                deleted; the operation is audited.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-3">
              <label className="text-sm font-medium" htmlFor="revert-reason">
                Reason (confirmation only — not stored)
              </label>
              <Textarea
                id="revert-reason"
                rows={3}
                value={reason}
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setReason(e.target.value)}
                placeholder="Why are you reverting?"
              />
              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => revert.mutate()}
                  disabled={revert.isPending || reason.trim().length === 0}
                >
                  {revert.isPending ? "Reverting…" : "Confirm revert"}
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </DialogContent>
    </Dialog>
  );
}

// ─ Main dashboard ──────────────────────────────────────────────────────────

export function InfiniteSessionDashboard() {
  const [filter, setFilter] = useState<string>("");
  const [selectedTask, setSelectedTask] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["infinite-session", "tasks"],
    queryFn: () => fetchTasksList(),
    refetchInterval: 30_000,
  });

  const tasks: TaskSummary[] = data?.tasks ?? [];
  const needle = filter.toLowerCase();
  const visible = tasks.filter(
    (t: TaskSummary) => t.task_id.toLowerCase().includes(needle) || t.phase_id.toLowerCase().includes(needle),
  );
  const critical = tasks.filter((t: TaskSummary) => t.current_drift_level === "CRITICAL");

  return (
    <div className="flex flex-col gap-6 p-6" data-panel="infinite-session-dashboard">
      <div>
        <h1 className="flex items-center gap-2 text-3xl font-bold">
          <TrendingUp className="h-8 w-8" />
          Infinite Session Dashboard
        </h1>
        <p className="mt-1 text-muted-foreground">
          Snapshot chains per task, drift detection, and audited revert (ADR-0545)
        </p>
      </div>

      {critical.length > 0 && (
        <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm">
          <AlertCircle className="mt-0.5 h-4 w-4 text-destructive" />
          <div>
            <div className="font-semibold">Critical drift on {critical.length} task(s)</div>
            <div className="text-muted-foreground">
              {critical.map((t: TaskSummary) => t.task_id).join(", ")} — review the chain and consider a revert.
            </div>
          </div>
        </div>
      )}

      <div className="relative">
        <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder="Filter by task id or phase…"
          value={filter}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setFilter(e.target.value)}
          className="pl-8"
        />
      </div>

      {isLoading && (
        <div className="space-y-2">
          <Skeleton className="h-10" />
          <Skeleton className="h-10" />
        </div>
      )}
      {error && <div className="text-sm text-destructive">{(error as Error).message}</div>}

      {!isLoading && !error && visible.length === 0 && (
        <div className="py-8 text-center text-muted-foreground">
          {filter ? "No tasks match the filter" : "No snapshotted tasks yet"}
        </div>
      )}

      {visible.length > 0 && (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50">
              <tr>
                <th className={TH}>Task</th>
                <th className={TH}>Phase</th>
                <th className={TH}>Started</th>
                <th className={TH}>Updated</th>
                <th className={TH}>Snapshots</th>
                <th className={TH}>Drift</th>
                <th className={TH}>Status</th>
                <th className={TH}></th>
              </tr>
            </thead>
            <tbody>
              {visible.map((task: TaskSummary) => (
                <tr key={task.task_id} className="border-t">
                  <td className={`${TD} font-mono text-xs`}>{task.task_id}</td>
                  <td className={TD}>{task.phase_id}</td>
                  <td className={`${TD} text-xs text-muted-foreground`}>{fmtDate(task.started_at)}</td>
                  <td className={`${TD} text-xs text-muted-foreground`}>{fmtDate(task.last_updated)}</td>
                  <td className={`${TD} font-semibold`}>{task.checkpoint_count}</td>
                  <td className={TD}>
                    <Badge variant={driftBadgeVariant(task.current_drift_level)} title={task.last_drift_alert ?? undefined}>
                      {task.current_drift_level}
                    </Badge>
                  </td>
                  <td className={TD}>
                    <Badge variant={task.status === "reverted" ? "warn" : "outline"}>{task.status}</Badge>
                  </td>
                  <td className={TD}>
                    <Button size="sm" variant="ghost" className="gap-1" onClick={() => setSelectedTask(task.task_id)}>
                      <ChevronDown className="h-4 w-4" />
                      View
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && (
        <div className="text-sm text-muted-foreground">
          Showing {visible.length} of {data.total_count} tasks
        </div>
      )}

      {selectedTask && (
        <TaskHistoryModal
          taskId={selectedTask}
          open={selectedTask !== null}
          onOpenChange={(open: boolean) => {
            if (!open) setSelectedTask(null);
          }}
        />
      )}
    </div>
  );
}

export default InfiniteSessionDashboard;
