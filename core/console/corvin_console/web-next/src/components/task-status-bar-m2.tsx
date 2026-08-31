import { useState } from 'react';
import { useTaskProgress } from '@/hooks/use-task-progress';

/**
 * Task status bar M2 — powered by pub/sub (no polling).
 * Shows all running tasks across sessions in real-time.
 *
 * ADR-0082 M2: Cross-session task visibility via WebSocket pub/sub.
 */
export function TaskStatusBarM2() {
  const { tasks, isConnected } = useTaskProgress();
  const [isExpanded, setIsExpanded] = useState(false);

  const runningTasks = tasks.filter((t) => t.status === 'running');
  const completedCount = tasks.filter((t) =>
    ['completed', 'failed', 'cancelled'].includes(t.status)
  ).length;

  return (
    <div className="bg-white border-b border-gray-200 px-4 py-2">
      <div className="flex items-center justify-between">
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex items-center gap-2 text-sm font-medium"
        >
          <span className="text-gray-600">Tasks</span>
          <span className="rounded-full bg-blue-100 px-2 py-1 text-xs text-blue-700">
            {runningTasks.length} running
          </span>
          {completedCount > 0 && (
            <span className="rounded-full bg-gray-100 px-2 py-1 text-xs text-gray-600">
              {completedCount} done
            </span>
          )}
          {!isConnected && (
            <span className="ml-2 text-xs text-orange-600">📶 Polling fallback active</span>
          )}
        </button>
      </div>

      {isExpanded && runningTasks.length > 0 && (
        <div className="mt-3 space-y-2">
          {runningTasks.map((task) => (
            <div key={task.task_id} className="rounded bg-blue-50 p-2 text-sm">
              <div className="font-mono text-xs text-gray-600">{task.task_id.slice(0, 8)}...</div>
              <div className="truncate text-gray-900">{task.chat_key}</div>
              {task.progress_pct !== undefined && (
                <div className="mt-1 h-1 w-full bg-gray-200">
                  <div
                    className="h-full bg-blue-500"
                    style={{ width: `${task.progress_pct}%` }}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
