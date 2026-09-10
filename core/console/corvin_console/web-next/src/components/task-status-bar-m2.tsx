import { useState } from 'react';
import { useTaskProgress } from '@/hooks/use-task-progress';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

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
    <div className="bg-card border-b border-border px-4 py-2">
      <div className="flex items-center justify-between">
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="flex items-center gap-2 text-sm font-medium"
        >
          <span className="text-muted-foreground">Tasks</span>
          <Badge variant="accent" className="text-xs">
            {runningTasks.length} running
          </Badge>
          {completedCount > 0 && (
            <Badge variant="secondary" className="text-xs">
              {completedCount} done
            </Badge>
          )}
          {!isConnected && (
            <span className="ml-2 text-xs text-amber-600 dark:text-amber-400">📶 Polling fallback active</span>
          )}
        </button>
      </div>

      {isExpanded && runningTasks.length > 0 && (
        <div className="mt-3 space-y-2">
          {runningTasks.map((task) => (
            <div key={task.task_id} className={cn("rounded p-2 text-sm", "bg-accent/10")}>
              <div className="font-mono text-xs text-muted-foreground">{task.task_id.slice(0, 8)}...</div>
              <div className="truncate text-foreground">{task.chat_key}</div>
              {task.progress_pct !== undefined && (
                <div className="mt-1 h-1 w-full bg-muted">
                  <div
                    className="h-full bg-accent"
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
