/**
 * Initiatives — live status of running and finished initiative tasks.
 *
 * Data: GET /v1/console/initiatives (routes/initiatives.py), polled every 5s.
 * The backend reads the operator-authored `<tenant>/global/initiatives.json`
 * and derives every time-dependent number on each read; between polls the
 * page only ticks countdowns against the server clock. Runs are split into
 * RUNNING (phase "active") and FINISHED (closed, or every task done); finished
 * runs stay in the file and form the history. A missing file renders
 * an empty state with the file location — never sample data.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle, AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, CircleDashed, Clock, Flag, History, Loader2,
  Lock, PlayCircle, RotateCcw, XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api/client";
import {
  closeInitiativeRun, getInitiatives, patchInitiativeTask, setInitiativeGate,
  type RunOutcome, type Check, type Gate, type GateDecision, type Initiative, type InitiativesBoard,
  type InitiativeTask, type TaskStatus,
} from "@/lib/api/initiatives";
import {
  STATUS_LABEL, clockSkewMs, formatCountdown, formatDuration, formatUtc, scheduleLabel,
} from "./initiatives-format";
import { cn } from "@/lib/utils";

const KEY = ["initiatives", "board"] as const;
type Filter = "all" | "active" | "done";
type RunView = "active" | "finished";

function statusVariant(s: string): "ok" | "warn" | "danger" | "secondary" | "outline" {
  if (s === "done") return "ok";
  if (s === "at_risk") return "danger";
  if (s === "blocked") return "warn";
  if (s === "running") return "outline";
  return "secondary";
}

function TaskIcon({ status }: { status: TaskStatus }) {
  if (status === "done") return <CheckCircle2 className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />;
  if (status === "running") return <PlayCircle className="h-4 w-4 text-amber-600 dark:text-amber-400" />;
  if (status === "blocked") return <Lock className="h-4 w-4 text-muted-foreground" />;
  return <CircleDashed className="h-4 w-4 text-muted-foreground" />;
}

function CheckRow({ c }: { c: Check }) {
  const icon = c.state === "ok"
    ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
    : c.state === "fail"
      ? <AlertCircle className="h-3.5 w-3.5 text-destructive" />
      : <Clock className="h-3.5 w-3.5 text-muted-foreground" />;
  return (
    <li className="flex items-start gap-2 text-sm">
      <span className="mt-0.5">{icon}</span>
      <span>{c.label}{c.detail && <span className="text-muted-foreground"> — {c.detail}</span>}</span>
    </li>
  );
}

function useNow(skewMs: number): number {
  const [now, setNow] = useState(() => Date.now() + skewMs);
  useEffect(() => {
    setNow(Date.now() + skewMs);
    const t = setInterval(() => setNow(Date.now() + skewMs), 1000);
    return () => clearInterval(t);
  }, [skewMs]);
  return now;
}

function TaskRow({
  ini, task, now, onPatch, busy,
}: {
  ini: Initiative; task: InitiativeTask; now: number; busy: boolean;
  onPatch: (iid: string, tid: string, body: { status?: TaskStatus; progress?: number }) => void;
}) {
  const [progress, setProgress] = useState(String(task.progress));
  useEffect(() => setProgress(String(task.progress)), [task.progress]);
  const commitProgress = () => {
    const n = Math.max(0, Math.min(100, Math.round(Number(progress))));
    if (Number.isFinite(n) && n !== task.progress) onPatch(ini.id, task.id, { progress: n });
    else setProgress(String(task.progress));
  };
  return (
    <li className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 py-2 sm:grid-cols-[auto_1fr_8rem_auto]">
      <span className="mt-0.5"><TaskIcon status={task.status} /></span>
      <div className="min-w-0">
        <div className={cn("text-sm font-medium", task.status === "done" && "text-muted-foreground line-through")}>
          {task.title}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
          {task.due && <span>Due {formatUtc(task.due)}</span>}
          {/* A finished run has nothing left to count down to. */}
          {task.due && task.status !== "done" && ini.phase !== "finished" && <span>{formatCountdown(task.due, now)}</span>}
          {task.completed_at && <span>Completed {formatUtc(task.completed_at)}</span>}
          {task.overdue && <Badge variant="danger">Overdue</Badge>}
          {task.note && <span>{task.note}</span>}
        </div>
      </div>
      <div className="col-start-2 flex items-center gap-2 sm:col-start-auto">
        <Progress value={task.progress} className="h-1.5 flex-1" aria-label={`${task.title} progress`} />
        <span className="w-9 text-right text-xs tabular-nums">{task.progress}%</span>
      </div>
      <div className="col-start-2 flex items-center gap-1 sm:col-start-auto">
        <select
          aria-label={`${task.title} status`}
          className="h-7 rounded-md border bg-background px-1.5 text-xs"
          value={task.status}
          disabled={busy}
          onChange={(e) => onPatch(ini.id, task.id, { status: e.target.value as TaskStatus })}
        >
          <option value="pending">Pending</option>
          <option value="running">Running</option>
          <option value="done">Done</option>
          <option value="blocked">Blocked</option>
        </select>
        <input
          aria-label={`${task.title} progress percent`}
          className="h-7 w-14 rounded-md border bg-background px-1.5 text-xs tabular-nums"
          type="number" min={0} max={100}
          value={progress}
          disabled={busy || task.status === "done"}
          onChange={(e) => setProgress(e.target.value)}
          onBlur={commitProgress}
          onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
        />
      </div>
    </li>
  );
}

function GateBlock({
  ini, gate, now, onDecide, busy,
}: {
  ini: Initiative; gate: Gate; now: number; busy: boolean;
  onDecide: (iid: string, gid: string, d: GateDecision) => void;
}) {
  const label = gate.decision === "go" ? "GO" : gate.decision === "no_go" ? "NO-GO" : "Pending";
  return (
    <div className="rounded-md border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Flag className="h-4 w-4" /> {gate.title}
          <Badge variant={gate.decision === "go" ? "ok" : gate.decision === "no_go" ? "danger" : "secondary"}>{label}</Badge>
        </div>
        {gate.at && (
          <span className="text-xs text-muted-foreground">
            {formatUtc(gate.at)}{gate.decision === "pending" && ` · ${formatCountdown(gate.at, now)}`}
          </span>
        )}
      </div>
      {gate.criteria.length > 0 && <ul className="mt-2 space-y-1">{gate.criteria.map((c, i) => <CheckRow key={i} c={c} />)}</ul>}
      {(gate.on_go || gate.on_no_go) && (
        <div className="mt-2 grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
          {gate.on_go && <div><span className="font-medium">Go →</span> {gate.on_go}</div>}
          {gate.on_no_go && <div><span className="font-medium">No-go →</span> {gate.on_no_go}</div>}
        </div>
      )}
      <div className="mt-2 flex gap-1">
        {(["go", "no_go", "pending"] as GateDecision[]).map((d) => (
          <Button key={d} size="sm" variant={gate.decision === d ? "default" : "outline"} disabled={busy || gate.decision === d}
            onClick={() => onDecide(ini.id, gate.id, d)}>
            {d === "go" ? "Go" : d === "no_go" ? "No-go" : "Reset"}
          </Button>
        ))}
      </div>
    </div>
  );
}

function RunActions({ ini, busy, onClose }: {
  ini: Initiative; busy: boolean; onClose: (iid: string, outcome: RunOutcome | null) => void;
}) {
  if (ini.phase === "finished") {
    return (
      <Button size="sm" variant="outline" disabled={busy} onClick={() => onClose(ini.id, null)}>
        <RotateCcw className="mr-1 h-3.5 w-3.5" /> Reopen
      </Button>
    );
  }
  return (
    <div className="flex gap-1">
      <Button size="sm" variant="outline" disabled={busy} onClick={() => onClose(ini.id, "completed")}>
        <CheckCircle2 className="mr-1 h-3.5 w-3.5" /> Mark completed
      </Button>
      <Button size="sm" variant="outline" disabled={busy} onClick={() => onClose(ini.id, "cancelled")}>
        <XCircle className="mr-1 h-3.5 w-3.5" /> Cancel run
      </Button>
    </div>
  );
}

function InitiativeCard({
  ini, now, filter, onPatch, onDecide, onClose, busy, flat,
}: {
  ini: Initiative; now: number; filter: Filter; busy: boolean;
  onPatch: (iid: string, tid: string, body: { status?: TaskStatus; progress?: number }) => void;
  onDecide: (iid: string, gid: string, d: GateDecision) => void;
  onClose: (iid: string, outcome: RunOutcome | null) => void;
  /** Rendered inside a finished-run row: no own card chrome, no header. */
  flat?: boolean;
}) {
  const finished = ini.phase === "finished";
  const sched = scheduleLabel(ini.schedule_delta_s);
  const tasks = ini.tasks.filter((t) =>
    filter === "all" ? true : filter === "done" ? t.status === "done" : t.status !== "done");
  const groups = useMemo(() => {
    const m = new Map<string, InitiativeTask[]>();
    for (const t of tasks) {
      const k = t.group ?? "";
      m.set(k, [...(m.get(k) ?? []), t]);
    }
    return [...m.entries()];
  }, [tasks]);

  const body = (
      <CardContent className={cn("space-y-4", flat && "p-0 pt-4")}>
        <div className="grid gap-3 text-sm sm:grid-cols-3">
          <div>
            <div className="text-xs text-muted-foreground">Window</div>
            <div>{formatUtc(ini.start)} → {formatUtc(ini.deadline)}</div>
          </div>
          {finished ? (
            <>
              <div>
                <div className="text-xs text-muted-foreground">Finished</div>
                <div>{formatUtc(ini.finished_at)}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Against deadline</div>
                <div><Badge variant={sched.tone}>{sched.text}</Badge>
                  {ini.duration_s !== null && <span className="ml-2 text-muted-foreground">ran {formatDuration(ini.duration_s)}</span>}</div>
              </div>
            </>
          ) : (
            <>
              <div>
                <div className="text-xs text-muted-foreground">Time left</div>
                <div className="tabular-nums">{formatCountdown(ini.deadline, now)}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Next checkpoint</div>
                <div>{ini.next_checkpoint
                  ? <>{ini.next_checkpoint.label} <span className="text-muted-foreground tabular-nums">· {formatCountdown(ini.next_checkpoint.at, now)}</span></>
                  : "—"}</div>
              </div>
            </>
          )}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <div className="mb-1 flex justify-between text-xs text-muted-foreground">
              <span>Time elapsed</span><span className="tabular-nums">{ini.time_progress_pct ?? "—"}%</span>
            </div>
            <Progress value={ini.time_progress_pct ?? 0} className="h-2" />
          </div>
          <div>
            <div className="mb-1 flex justify-between text-xs text-muted-foreground">
              <span>Task progress · {ini.task_counts.done}/{ini.task_counts.total} done</span>
              <span className="tabular-nums">{ini.task_progress_pct ?? "—"}%</span>
            </div>
            <Progress value={ini.task_progress_pct ?? 0} className="h-2" />
          </div>
        </div>

        {tasks.length > 0 ? groups.map(([group, list]) => (
          <div key={group}>
            {group && <div className="mt-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{group}</div>}
            <ul className="divide-y">
              {list.map((t) => <TaskRow key={t.id} ini={ini} task={t} now={now} onPatch={onPatch} busy={busy} />)}
            </ul>
          </div>
        )) : (
          <p className="text-sm text-muted-foreground">No {filter === "done" ? "finished" : filter === "active" ? "open" : ""} tasks.</p>
        )}

        {ini.preconditions.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Preconditions</div>
            <ul className="space-y-1">{ini.preconditions.map((c, i) => <CheckRow key={i} c={c} />)}</ul>
          </div>
        )}

        {ini.gates.length > 0 && (
          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Gates</div>
            {ini.gates.map((g) => <GateBlock key={g.id} ini={ini} gate={g} now={now} onDecide={onDecide} busy={busy} />)}
          </div>
        )}
        {ini.cadence && <p className="text-xs text-muted-foreground">Review cadence: {ini.cadence}</p>}
      </CardContent>
  );
  if (flat) return body;
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base">
              {ini.label && <span className="text-muted-foreground">{ini.label} · </span>}{ini.title}
            </CardTitle>
            {ini.description && <CardDescription>{ini.description}</CardDescription>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <RunActions ini={ini} busy={busy} onClose={onClose} />
            <Badge variant={statusVariant(ini.status)}>{STATUS_LABEL[ini.status] ?? ini.status}</Badge>
          </div>
        </div>
        {ini.blocked_by && (
          <div className="mt-2 flex items-center gap-2 rounded-md bg-amber-500/10 px-3 py-2 text-sm">
            <Lock className="h-4 w-4" />
            Waiting for gate “{ini.blocked_by.gate_title ?? ini.blocked_by.gate}” ({ini.blocked_by.initiative}) —
            currently {ini.blocked_by.decision === "no_go" ? "NO-GO" : ini.blocked_by.decision}
          </div>
        )}
      </CardHeader>
      {body}
    </Card>
  );
}

function FinishedRunRow(props: {
  ini: Initiative; now: number; busy: boolean;
  onPatch: (iid: string, tid: string, body: { status?: TaskStatus; progress?: number }) => void;
  onDecide: (iid: string, gid: string, d: GateDecision) => void;
  onClose: (iid: string, outcome: RunOutcome | null) => void;
}) {
  const { ini, busy, onClose } = props;
  const [open, setOpen] = useState(false);
  const sched = scheduleLabel(ini.schedule_delta_s);
  return (
    <li className="rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left"
          aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
          <span className="truncate text-sm font-medium">
            {ini.label && <span className="text-muted-foreground">{ini.label} · </span>}{ini.title}
          </span>
        </button>
        <Badge variant={statusVariant(ini.status)}>{STATUS_LABEL[ini.status] ?? ini.status}</Badge>
        <Badge variant={sched.tone}>{sched.text}</Badge>
        <span className="text-xs text-muted-foreground">
          Finished {formatUtc(ini.finished_at)} · ran {formatDuration(ini.duration_s)} · {ini.task_counts.done}/{ini.task_counts.total} tasks done
        </span>
        <RunActions ini={ini} busy={busy} onClose={onClose} />
      </div>
      {open && <InitiativeCard {...props} filter="all" flat />}
    </li>
  );
}

function Tile({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("text-2xl font-semibold tabular-nums", tone)}>{value}</div>
    </div>
  );
}

export default function InitiativesPage() {
  const qc = useQueryClient();
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const [filter, setFilter] = useState<Filter>("all");
  const [view, setView] = useState<RunView>("active");
  const [skew, setSkew] = useState(0);

  const q = useQuery({
    queryKey: [...KEY],
    queryFn: async ({ signal }) => {
      const b = await getInitiatives(signal);
      setSkew(clockSkewMs(b.server_time, Date.now()));
      return b;
    },
    refetchInterval: 5_000,
    refetchIntervalInBackground: false,
    retry: false,
  });
  const now = useNow(skew);

  const onSuccess = (b: InitiativesBoard) => qc.setQueryData([...KEY], b);
  const patch = useMutation({
    mutationFn: (v: { iid: string; tid: string; body: { status?: TaskStatus; progress?: number } }) =>
      patchInitiativeTask(v.iid, v.tid, v.body, csrf),
    onSuccess,
  });
  const gate = useMutation({
    mutationFn: (v: { iid: string; gid: string; d: GateDecision }) => setInitiativeGate(v.iid, v.gid, v.d, csrf),
    onSuccess,
  });
  const close = useMutation({
    mutationFn: (v: { iid: string; outcome: RunOutcome | null }) => closeInitiativeRun(v.iid, v.outcome, csrf),
    onSuccess,
  });
  const busy = patch.isPending || gate.isPending || close.isPending;
  const mutErr = (patch.error ?? gate.error ?? close.error) as Error | null;

  const board = q.data;
  const activeRuns = board?.initiatives.filter((i) => i.phase === "active") ?? [];
  // History: most recently finished first; runs without a finish time last.
  const finishedRuns = (board?.initiatives.filter((i) => i.phase === "finished") ?? [])
    .slice()
    .sort((a, b) => (Date.parse(b.finished_at ?? "") || 0) - (Date.parse(a.finished_at ?? "") || 0));
  const handlers = {
    onPatch: (iid: string, tid: string, body: { status?: TaskStatus; progress?: number }) => patch.mutate({ iid, tid, body }),
    onDecide: (iid: string, gid: string, d: GateDecision) => gate.mutate({ iid, gid, d }),
    onClose: (iid: string, outcome: RunOutcome | null) => close.mutate({ iid, outcome }),
  };
  const updatedAgo = q.dataUpdatedAt ? Math.max(0, Math.round((Date.now() - q.dataUpdatedAt) / 1000)) : null;

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 sm:p-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Initiatives</h1>
          <p className="text-sm text-muted-foreground">Live status of running and finished runs and their tasks.</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground" aria-live="polite">
          <span className={cn("inline-block h-2 w-2 rounded-full", q.isError ? "bg-destructive" : "bg-emerald-500 animate-pulse")} />
          {q.isError ? "Connection lost" : updatedAgo === null ? "Loading…" : `Live · updated ${updatedAgo}s ago`}
          {board && <span>· {formatUtc(new Date(now).toISOString())}</span>}
        </div>
      </div>

      {q.isLoading && <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Loading…</div>}

      {q.isError && (
        <Card><CardContent className="flex items-center gap-2 py-4 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4" />
          {q.error instanceof ApiError && q.error.status === 404
            ? "Initiatives are not available on this build."
            : `Could not load initiatives: ${(q.error as Error).message}`}
        </CardContent></Card>
      )}

      {mutErr && (
        <div className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">Update failed: {mutErr.message}</div>
      )}

      {board && board.initiatives.length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">No initiatives yet</CardTitle>
            <CardDescription>
              This board reads <code>&lt;tenant&gt;/global/initiatives.json</code>. The file does not exist on this install yet —
              create it and the board picks it up within five seconds.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {board && board.initiatives.length > 0 && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Tile label="Running runs" value={board.totals.runs_active} />
            <Tile label="Finished runs" value={board.totals.runs_finished} />
            <Tile label="Running tasks" value={board.totals.running} />
            <Tile label="Finished tasks" value={board.totals.done} />
            <Tile label="Overdue tasks" value={board.totals.overdue} tone={board.totals.overdue ? "text-destructive" : undefined} />
            <Tile label="Blocked runs" value={board.totals.initiatives_blocked} />
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-2">
            <div className="flex gap-1" role="tablist" aria-label="Runs">
              <Button role="tab" aria-selected={view === "active"} size="sm"
                variant={view === "active" ? "default" : "ghost"} onClick={() => setView("active")}>
                <PlayCircle className="mr-1 h-4 w-4" /> Running ({activeRuns.length})
              </Button>
              <Button role="tab" aria-selected={view === "finished"} size="sm"
                variant={view === "finished" ? "default" : "ghost"} onClick={() => setView("finished")}>
                <History className="mr-1 h-4 w-4" /> Finished ({finishedRuns.length})
              </Button>
            </div>
            {view === "active" && (
              <div className="flex gap-1" role="tablist" aria-label="Task filter">
                {([["all", "All"], ["active", "Open"], ["done", "Done"]] as [Filter, string][]).map(([f, l]) => (
                  <Button key={f} role="tab" aria-selected={filter === f} size="sm"
                    variant={filter === f ? "secondary" : "outline"} onClick={() => setFilter(f)}>{l}</Button>
                ))}
              </div>
            )}
          </div>

          {view === "active" && (activeRuns.length > 0 ? (
            <div className="space-y-4">
              {activeRuns.map((ini) => (
                <InitiativeCard key={ini.id} ini={ini} now={now} filter={filter} busy={busy} {...handlers} />
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No running runs — every run in the file is finished.</p>
          ))}

          {view === "finished" && (finishedRuns.length > 0 ? (
            <ul className="space-y-2">
              {finishedRuns.map((ini) => (
                <FinishedRunRow key={ini.id} ini={ini} now={now} busy={busy} {...handlers} />
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">
              No finished runs yet. A run moves here when every task is done or when it is closed with
              “Mark completed” or “Cancel run”.
            </p>
          ))}
        </>
      )}
    </div>
  );
}
