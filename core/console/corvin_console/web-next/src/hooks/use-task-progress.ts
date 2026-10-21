import { useEffect, useState } from 'react';

export interface TaskProgress {
  task_id: string;
  chat_key: string;
  /** Mirrors corvin_core/task_states.py (TaskState). */
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
  progress_pct?: number;
  latest_line?: string;
}

/**
 * Statuses after which a task is no longer running and leaves the list after a grace period.
 * `interrupted` (worker killed, PLAN-0958 P1) is not terminal on the backend — it can still be
 * cancelled — but it never runs again, so the bar treats it as ended too (BGT-14, 2026-10-21).
 */
export const ENDED_STATUSES: ReadonlyArray<TaskProgress['status']> = [
  'completed',
  'failed',
  'cancelled',
  'interrupted',
];

/**
 * Subscribe to cross-chat task progress via WebSocket pub/sub.
 * Used by TaskStatusBar to show all running tasks across chats.
 *
 * ADR-0082 M2.0: Cross-session task visibility via pub/sub.
 */
export function useTaskProgress() {
  const [tasks, setTasks] = useState<Map<string, TaskProgress>>(new Map());
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    const url = new URL('/v1/console/tasks/progress', window.location.origin);
    // Replace http:// with ws://, https:// with wss://
    url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';

    const ws = new WebSocket(url.toString());

    ws.onopen = () => {
      setIsConnected(true);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as {
          task_id: string;
          chat_key?: string;
          status?: string;
          event?: string;
          progress_pct?: number;
          latest_line?: string;
          [key: string]: unknown;
        };

        const taskId = data.task_id;
        setTasks((prev) => {
          const updated = new Map(prev);
          const existing = updated.get(taskId) || { task_id: taskId, chat_key: '', status: 'pending' as const };

          // Update task state
          const newTask: TaskProgress = {
            ...existing,
            chat_key: data.chat_key || existing.chat_key,
            status:
              (data.status as TaskProgress['status']) ||
              (data.event === 'task.completed' ? 'completed' : existing.status),
            progress_pct: data.progress_pct ?? existing.progress_pct,
            latest_line: data.latest_line ?? existing.latest_line,
          };

          updated.set(taskId, newTask);

          // Auto-cleanup ended tasks (completed/failed/cancelled/interrupted) after 5 min
          if (ENDED_STATUSES.includes(newTask.status)) {
            setTimeout(() => {
              setTasks((prev) => {
                const cleaned = new Map(prev);
                cleaned.delete(taskId);
                return cleaned;
              });
            }, 5 * 60 * 1000);
          }

          return updated;
        });
      } catch (err) {
        console.error('Failed to parse progress event:', err);
      }
    };

    ws.onerror = () => {
      setIsConnected(false);
    };

    ws.onclose = () => {
      setIsConnected(false);
    };

    return () => {
      ws.close();
    };
  }, []);

  return { tasks: Array.from(tasks.values()), isConnected };
}
