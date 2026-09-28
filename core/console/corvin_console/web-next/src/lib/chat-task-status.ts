/**
 * Pure encodings for the chat sidebar's task indicator. The server's
 * TaskManager event log is the source of truth; its timestamps are epoch
 * SECONDS (Python time.time()), never milliseconds, and are compared
 * against the SERVER clock the response carries — never the browser's.
 */
import { api } from "@/lib/api/client";

export type ServerTaskStatus = "pending" | "running" | "completed" | "failed" | "cancelled";

export interface ServerTask {
  task_id: string;
  chat_key: string;
  status: ServerTaskStatus;
  created_at: number;
  started_at: number | null;
  ended_at: number | null;
  exit_code: number | null;
  duration_ms: number | null;
  /** Server-capped prefix of the task's instruction (view=summary). */
  instruction_preview?: string;
  /** Epoch seconds of the task's last logged event, if any. */
  last_event_at?: number | null;
}

export interface SessionTaskSummary {
  tasks: ServerTask[];
  /** Server clock (epoch seconds) at response time. */
  now: number;
}

export type IndicatorPhase = "idle" | "pending" | "running" | "completed" | "failed" | "cancelled";

export interface TaskIndicator {
  phase: IndicatorPhase;
  task: ServerTask | null;
  /** Number of tasks currently pending or running in this chat. */
  activeCount: number;
  /** In flight, but nothing logged for STALE_AFTER_S — possibly hung. */
  stale: boolean;
}

/** A finished task stays visible this long so the user sees how it ended. */
export const RECENT_FINISH_WINDOW_S = 120;
export const LABEL_MAX_CHARS = 48;
export const STALE_AFTER_S = 600;

export function sessionTasksKey(sid: string) {
  return ["chat-session-tasks", sid] as const;
}

export async function fetchSessionTasks(sid: string): Promise<SessionTaskSummary> {
  const res = await api<{ tasks?: ServerTask[]; now?: number }>(
    `/chat/sessions/${encodeURIComponent(sid)}/tasks?view=summary&limit=20`,
  );
  return { tasks: res.tasks ?? [], now: res.now ?? Date.now() / 1000 };
}

/** Seconds since the task last logged anything (falls back to its start). */
export function idleSeconds(task: ServerTask, nowS: number): number {
  const last = task.last_event_at ?? task.started_at ?? task.created_at;
  return Math.max(0, Math.floor(nowS - last));
}

/** `tasks` is newest-first, as the server returns it. `nowS` is epoch seconds. */
export function pickIndicator(tasks: ServerTask[], nowS: number): TaskIndicator {
  const active = tasks.filter((t) => t.status === "running" || t.status === "pending");
  const running = active.find((t) => t.status === "running");
  if (running) {
    const stale = idleSeconds(running, nowS) >= STALE_AFTER_S;
    return { phase: "running", task: running, activeCount: active.length, stale };
  }
  if (active[0]) {
    const stale = idleSeconds(active[0], nowS) >= STALE_AFTER_S;
    return { phase: "pending", task: active[0], activeCount: active.length, stale };
  }

  const latest = tasks[0];
  if (latest && latest.ended_at != null && nowS - latest.ended_at <= RECENT_FINISH_WINDOW_S) {
    const phase = latest.status as IndicatorPhase;
    if (phase === "completed" || phase === "failed" || phase === "cancelled") {
      return { phase, task: latest, activeCount: 0, stale: false };
    }
  }
  return { phase: "idle", task: null, activeCount: 0, stale: false };
}

/** Seconds the task has spent in its current phase (running → since start, finished → total). */
export function elapsedSeconds(task: ServerTask, phase: IndicatorPhase, nowS: number): number {
  if (phase === "completed" || phase === "failed" || phase === "cancelled") {
    if (task.duration_ms != null) return Math.max(0, Math.round(task.duration_ms / 1000));
    const end = task.ended_at ?? nowS;
    return Math.max(0, Math.round(end - (task.started_at ?? task.created_at)));
  }
  const start = phase === "running" ? task.started_at ?? task.created_at : task.created_at;
  return Math.max(0, Math.floor(nowS - start));
}

export function formatElapsed(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

export function taskLabel(task: ServerTask): string {
  const raw = (task.instruction_preview ?? "").replace(/\s+/g, " ").trim();
  if (!raw) return "Background task";
  return raw.length > LABEL_MAX_CHARS ? `${raw.slice(0, LABEL_MAX_CHARS - 1)}…` : raw;
}

export const PHASE_TEXT: Record<Exclude<IndicatorPhase, "idle">, string> = {
  pending: "Queued",
  running: "Running",
  completed: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function statusLine(ind: TaskIndicator, nowS: number): string {
  if (ind.phase === "idle" || !ind.task) return "";
  const time = formatElapsed(elapsedSeconds(ind.task, ind.phase, nowS));
  const more = ind.activeCount > 1 ? ` · +${ind.activeCount - 1} more` : "";
  const quiet = ind.stale ? ` · no activity for ${formatElapsed(idleSeconds(ind.task, nowS))}` : "";
  return ind.phase === "pending"
    ? `${PHASE_TEXT.pending} · waiting ${time}${quiet}${more}`
    : `${PHASE_TEXT[ind.phase]} · ${time}${quiet}${more}`;
}
