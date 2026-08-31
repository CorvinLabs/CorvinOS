/**
 * Task Panel Component
 * Displays persisted tasks for the current chat
 * ADR-0082: Frontend Persistence Layer
 */

import { formatDate } from "@/lib/utils";
import { Task } from "@/lib/task-db";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Trash2, Download } from "lucide-react";
import { Button } from "@/components/ui/button";

interface TaskPanelProps {
  tasks: Task[];
  onDeleteTask?: (taskId: string) => void;
  onExportTask?: (taskId: string) => void;
  isLoading?: boolean;
}

export function TaskPanel({
  tasks,
  onDeleteTask,
  onExportTask,
  isLoading = false,
}: TaskPanelProps) {
  if (isLoading) {
    return (
      <div className="space-y-2 p-4">
        <div className="h-4 w-full animate-pulse rounded bg-muted" />
        <div className="h-4 w-3/4 animate-pulse rounded bg-muted" />
      </div>
    );
  }

  if (tasks.length === 0) {
    return null;
  }

  return (
    <Card className="border-amber-200 bg-amber-50/50">
      <CardContent className="p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-semibold text-sm text-amber-900">
            {tasks.length} Persisted Task{tasks.length !== 1 ? "s" : ""}
          </h3>
          <Badge variant="outline" className="text-xs">
            IndexedDB
          </Badge>
        </div>

        <div className="space-y-2">
          {tasks.map((task) => (
            <TaskCard
              key={task.task_id}
              task={task}
              onDelete={() => onDeleteTask?.(task.task_id)}
              onExport={() => onExportTask?.(task.task_id)}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function TaskCard({
  task,
  onDelete,
  onExport,
}: {
  task: Task;
  onDelete: () => void;
  onExport: () => void;
}) {
  const statusColors: Record<Task["status"], string> = {
    pending: "bg-gray-100 text-gray-800",
    running: "bg-blue-100 text-blue-800",
    completed: "bg-green-100 text-green-800",
    failed: "bg-red-100 text-red-800",
  };

  const syncStatus = task.synced ? "✓ Synced" : "✧ Local";

  return (
    <div className="rounded-md border border-amber-100 bg-white p-3 text-xs">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="truncate font-mono text-gray-600 text-[10px]">
            {task.task_id.slice(0, 8)}...
          </p>
          <p className="truncate text-gray-700 font-medium">
            {task.instruction.slice(0, 50)}
            {task.instruction.length > 50 ? "…" : ""}
          </p>
        </div>
        <Badge className={`shrink-0 ${statusColors[task.status]}`}>
          {task.status}
        </Badge>
      </div>

      <div className="mb-2 space-y-1 text-gray-600">
        <div className="flex justify-between">
          <span>Progress:</span>
          <span className="font-mono">{task.progress_pct}%</span>
        </div>
        <div className="flex justify-between">
          <span>Created:</span>
          <span>{formatDate(task.created_at || '')}</span>
        </div>
        <div className="flex justify-between">
          <span>Synced:</span>
          <span
            className={task.synced ? "text-green-600" : "text-yellow-600"}
          >
            {syncStatus}
          </span>
        </div>
      </div>

      {task.latest_line && (
        <div className="mb-2 rounded bg-gray-50 p-2 font-mono text-gray-700">
          <p className="truncate text-[10px]">{task.latest_line}</p>
        </div>
      )}

      <div className="flex gap-1">
        <Button
          size="sm"
          variant="ghost"
          onClick={onExport}
          className="h-6 px-2 text-xs"
          title="Export as JSON"
          aria-label="Export task as JSON"
        >
          <Download className="h-3 w-3" />
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={onDelete}
          className="h-6 px-2 text-xs text-red-600 hover:text-red-700"
          title="Delete from IndexedDB"
          aria-label="Delete task from IndexedDB"
        >
          <Trash2 className="h-3 w-3" />
        </Button>
      </div>
    </div>
  );
}
