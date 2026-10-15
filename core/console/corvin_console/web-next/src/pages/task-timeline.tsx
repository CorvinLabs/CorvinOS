/**
 * Task Timeline - what happened to a chat task, in order: every transition and every
 * notice the system sent you (interrupted, resumed, ended). Read-only: the timeline is
 * folded server-side from the task's append-only event log and is never a second writer.
 */
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api/client";
import { listChatSessions } from "@/lib/api/sessions";
import { currentStatus, formatAt, rowLabel, rowTone, type TimelineRow } from "@/lib/task-timeline";

interface TaskSummary {
  task_id: string;
  status: string;
  created_at: number | null;
  ended_at: number | null;
}

const TONE_CLASS = {
  ok: "text-emerald-600 dark:text-emerald-400",
  warn: "text-amber-600 dark:text-amber-400",
  bad: "text-destructive",
  info: "text-muted-foreground",
} as const;

export default function TaskTimelinePage() {
  const [sid, setSid] = React.useState<string>("");
  const [taskId, setTaskId] = React.useState<string>("");

  const sessions = useQuery({ queryKey: ["tt-sessions"], queryFn: ({ signal }) => listChatSessions(signal) });
  const tasks = useQuery({
    queryKey: ["tt-tasks", sid],
    enabled: !!sid,
    queryFn: ({ signal }) =>
      api<{ tasks: TaskSummary[] }>(`/chat/sessions/${encodeURIComponent(sid)}/tasks?view=summary&limit=30`, { signal }),
  });
  const timeline = useQuery({
    queryKey: ["tt-timeline", sid, taskId],
    enabled: !!sid && !!taskId,
    queryFn: ({ signal }) =>
      api<{ rows: TimelineRow[] }>(
        `/chat/sessions/${encodeURIComponent(sid)}/tasks/${encodeURIComponent(taskId)}/timeline`, { signal }),
  });
  const rows = timeline.data?.rows ?? [];

  return (
    <div className="space-y-4 p-6 max-w-3xl">
      <div className="flex items-center gap-2">
        <History className="h-5 w-5 text-muted-foreground" />
        <h1 className="text-lg font-semibold">Task Timeline</h1>
      </div>
      <p className="text-sm text-muted-foreground">
        Pick a chat and a task to see what happened to it: when it started, when it was interrupted,
        what the system did about it and what you were told.
      </p>

      <div className="flex flex-wrap gap-3">
        <select aria-label="Chat" className="border rounded px-2 py-1 text-sm bg-background" value={sid}
                onChange={(e) => { setSid(e.target.value); setTaskId(""); }}>
          <option value="">Select a chat…</option>
          {(sessions.data?.sessions ?? []).map((s) => (
            <option key={s.sid} value={s.sid}>{s.title || "New Chat"}</option>
          ))}
        </select>
        <select aria-label="Task" className="border rounded px-2 py-1 text-sm bg-background" value={taskId}
                disabled={!sid} onChange={(e) => setTaskId(e.target.value)}>
          <option value="">Select a task…</option>
          {(tasks.data?.tasks ?? []).map((t) => (
            <option key={t.task_id} value={t.task_id}>{formatAt(t.created_at)} - {t.status}</option>
          ))}
        </select>
      </div>

      {taskId && (
        <Card>
          <CardContent className="pt-4 space-y-3">
            <p className="text-sm font-medium" data-testid="timeline-status">
              Status: {currentStatus(rows)}
            </p>
            {timeline.isLoading && <p className="text-xs text-muted-foreground">Loading…</p>}
            {timeline.isError && <p className="text-xs text-destructive">The timeline could not be loaded.</p>}
            <ol className="space-y-2" data-testid="timeline-rows">
              {rows.map((r) => (
                <li key={r.seq} data-tone={rowTone(r)} className="text-sm">
                  <span className="text-xs text-muted-foreground mr-2">{formatAt(r.at)}</span>
                  <span className={TONE_CLASS[rowTone(r)]}>{rowLabel(r)}</span>
                  {r.text && <p className="text-xs text-muted-foreground mt-0.5">{r.text}</p>}
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
