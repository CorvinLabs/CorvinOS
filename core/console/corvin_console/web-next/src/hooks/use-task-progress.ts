import { useEffect, useState } from 'react';

export interface TaskProgress {
  task_id: string;
  chat_key: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress_pct?: number;
  latest_line?: string;
}

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

          // Auto-cleanup completed/failed after 5 min
          if (['completed', 'failed', 'cancelled'].includes(newTask.status)) {
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
