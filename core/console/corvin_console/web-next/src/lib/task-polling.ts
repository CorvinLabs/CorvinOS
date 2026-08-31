/**
 * HTTP polling fallback for task status updates (Phase 2)
 * When WebSocket is unavailable, this service polls the backend every 3 seconds
 * using Etag-based caching to minimize bandwidth.
 *
 * ADR-0082 M2: Phase 2 — Polling Fallback + Etag Caching
 */

import { saveTask, Task } from "@/lib/task-db";

interface TaskPollingOptions {
  taskId: string;
  pollInterval?: number; // milliseconds (default 3000)
  onTaskUpdate?: (task: Task) => void;
  onError?: (error: Error) => void;
}

export class TaskPoller {
  private taskId: string;
  private pollInterval: number;
  private onTaskUpdate?: (task: Task) => void;
  private onError?: (error: Error) => void;
  private pollTimer: NodeJS.Timeout | null = null;
  private etag: string | null = null;
  private lastTask: Task | null = null;

  constructor({
    taskId,
    pollInterval = 3000,
    onTaskUpdate,
    onError,
  }: TaskPollingOptions) {
    this.taskId = taskId;
    this.pollInterval = pollInterval;
    this.onTaskUpdate = onTaskUpdate;
    this.onError = onError;
  }

  start(): void {
    if (this.pollTimer) {
      console.warn("TaskPoller already started for", this.taskId);
      return;
    }

    console.log(
      `Starting task polling for ${this.taskId} (interval: ${this.pollInterval}ms)`
    );
    this.pollOnce();
    this.pollTimer = setInterval(() => this.pollOnce(), this.pollInterval);
  }

  stop(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
      console.log(`Stopped task polling for ${this.taskId}`);
    }
  }

  private async pollOnce(): Promise<void> {
    try {
      const response = await fetch(
        `/v1/console/tasks/${encodeURIComponent(this.taskId)}`,
        {
          method: "GET",
          credentials: "include",
          headers: this.etag ? { "If-None-Match": this.etag } : {},
          signal: AbortSignal.timeout(5000),
        }
      );

      // 304 Not Modified — no changes, use cached task
      if (response.status === 304) {
        return;
      }

      if (!response.ok) {
        throw new Error(
          `HTTP ${response.status}: ${response.statusText || "Unknown error"}`
        );
      }

      // Update Etag for next poll
      const newEtag = response.headers.get("ETag");
      if (newEtag) {
        this.etag = newEtag;
      }

      const data = (await response.json()) as { task: Task };
      const task = data.task;

      // Save to IndexedDB for persistence
      await saveTask(task);
      this.lastTask = task;

      // Notify listener if task changed
      if (
        !this.lastTask ||
        this.lastTask.progress_pct !== task.progress_pct ||
        this.lastTask.status !== task.status
      ) {
        this.onTaskUpdate?.(task);
      }
    } catch (err) {
      const error =
        err instanceof Error ? err : new Error(String(err));
      console.error(
        `Failed to poll task ${this.taskId}:`,
        error.message
      );
      this.onError?.(error);
    }
  }

  getLastTask(): Task | null {
    return this.lastTask;
  }
}

// Global registry of active pollers to manage them across components
const pollerRegistry = new Map<string, TaskPoller>();

export function startTaskPoller(options: TaskPollingOptions): TaskPoller {
  const key = options.taskId;
  let poller = pollerRegistry.get(key);

  if (poller) {
    console.log(`TaskPoller for ${key} already exists, reusing`);
    return poller;
  }

  poller = new TaskPoller(options);
  poller.start();
  pollerRegistry.set(key, poller);

  return poller;
}

export function stopTaskPoller(taskId: string): void {
  const poller = pollerRegistry.get(taskId);
  if (poller) {
    poller.stop();
    pollerRegistry.delete(taskId);
  }
}

export function stopAllTaskPollers(): void {
  for (const [, poller] of pollerRegistry) {
    poller.stop();
  }
  pollerRegistry.clear();
}

export function getTaskPoller(taskId: string): TaskPoller | undefined {
  return pollerRegistry.get(taskId);
}

export function getActivePollersCount(): number {
  return pollerRegistry.size;
}
