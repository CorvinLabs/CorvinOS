/**
 * Task Graph page (ADR-0400).
 * Route: /app/task-graph
 *
 * Lists every task that has a persisted checkpoint and renders the selected
 * task's DAG. The viewer itself needs a task id; discovery lives here so the
 * page is usable without knowing one up front.
 */
import { useEffect, useState } from "react";
import { TaskGraphVisualizerV2 } from "../components/TaskGraphVisualizerV2";
import { useTaskList } from "../hooks/useTaskGraph";

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function TaskGraphPage() {
  const { tasks, loading: tasksLoading, error: tasksError, refetch } = useTaskList();
  const [selectedTaskId, setSelectedTaskId] = useState<string>("");

  // Auto-select the newest task once the list arrives, but never override a
  // choice the operator already made.
  useEffect(() => {
    if (!selectedTaskId && tasks.length > 0) {
      setSelectedTaskId(tasks[0].task_id);
    }
  }, [tasks, selectedTaskId]);

  return (
    <div className="task-graph-page" data-testid="task-graph-page">
      <header className="task-graph-page-header">
        <div>
          <h1 className="task-graph-page-title">Task Graph</h1>
          <p className="task-graph-page-subtitle">
            Decisions, errors, checkpoints and subgoals of a task run as a DAG.
          </p>
        </div>
        <button
          type="button"
          className="task-graph-btn"
          onClick={refetch}
          title="Reload task list"
        >
          Reload tasks
        </button>
      </header>

      <section className="task-graph-picker" data-testid="task-graph-picker">
        <label htmlFor="task-graph-select" className="task-graph-picker-label">
          Task
        </label>
        <select
          id="task-graph-select"
          data-testid="task-graph-select"
          className="task-graph-select"
          value={selectedTaskId}
          onChange={(e) => setSelectedTaskId(e.target.value)}
          disabled={tasksLoading || tasks.length === 0}
        >
          {tasks.length === 0 && <option value="">No tasks available</option>}
          {tasks.map((t) => (
            <option key={t.task_id} value={t.task_id}>
              {t.task_id} — {t.phase} · iteration {t.iteration_num} ·{" "}
              {formatTimestamp(t.timestamp)}
            </option>
          ))}
        </select>
        {tasksLoading && <span className="task-graph-picker-hint">Loading tasks…</span>}
      </section>

      {tasksError && (
        <div className="task-graph-message task-graph-message-error" role="alert">
          Could not load task list: {tasksError}
        </div>
      )}

      {!tasksLoading && !tasksError && tasks.length === 0 && (
        <div className="task-graph-message" data-testid="task-graph-empty">
          <strong>No task graphs yet.</strong>
          <p>
            A graph appears here once a task writes a checkpoint. Checkpoints are
            stored in <code>~/.corvin/vibe/checkpoints/</code>.
          </p>
        </div>
      )}

      {selectedTaskId && (
        <TaskGraphVisualizerV2 taskId={selectedTaskId} />
      )}
    </div>
  );
}
