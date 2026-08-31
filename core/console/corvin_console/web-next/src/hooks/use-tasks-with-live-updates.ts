/**
 * useTasksWithLiveUpdates — Complete task lifecycle management
 *
 * Combines three phases:
 * 1. Load: Restore persisted tasks from IndexedDB on mount
 * 2. Stream: Subscribe to live updates via SSE (primary) or polling (fallback)
 * 3. Persist: Save updates back to IndexedDB and UI state
 *
 * Resilience: SSE failure → automatic fallback to polling
 * Error handling: All failures are non-fatal; IndexedDB becomes source-of-truth
 */

import { useEffect, useRef, useState, useCallback } from "react";
import {
  getTasksByChatKey,
  saveTask,
  Task,
  deleteTask,
} from "@/lib/task-db";
import { useTaskSSE } from "@/hooks/use-task-sse";
import { useTaskPolling } from "@/hooks/use-task-polling";

export interface UseTasksWithLiveUpdatesOptions {
  chatKey: string | null;
  sessionId: string | null;
  onTasksLoaded?: (tasks: Task[]) => void;
  onTaskUpdated?: (task: Task) => void;
  onError?: (error: Error) => void;
}

export interface UseTasksWithLiveUpdatesReturn {
  tasks: Task[];
  isLoading: boolean;
  isConnected: boolean; // SSE connected?
  isPolling: boolean;   // Polling fallback active?
}

export function useTasksWithLiveUpdates({
  chatKey,
  sessionId,
  onTasksLoaded,
  onTaskUpdated,
  onError,
}: UseTasksWithLiveUpdatesOptions): UseTasksWithLiveUpdatesReturn {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isPolling, setIsPolling] = useState(false);
  const [runningTaskIds, setRunningTaskIds] = useState<Set<string>>(new Set());
  const activeSSEHooksRef = useRef<Map<string, boolean>>(new Map());
  // Per-task cleanup timers. A single shared timer was wrong: when task B
  // completes after task A, the shared timer for A is cancelled and A is never
  // removed from IndexedDB / UI state.
  const cleanupTimeoutsRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  // Refs for callbacks so they never appear in effect deps. Without this,
  // inline arrow functions from the parent (ChatPane) create a new reference
  // on every render, which triggers the load effect to re-run → infinite loop
  // → SSE reconnect storm.
  const onTasksLoadedRef = useRef(onTasksLoaded);
  const onTaskUpdatedRef = useRef(onTaskUpdated);
  const onErrorRef = useRef(onError);
  useEffect(() => { onTasksLoadedRef.current = onTasksLoaded; });
  useEffect(() => { onTaskUpdatedRef.current = onTaskUpdated; });
  useEffect(() => { onErrorRef.current = onError; });

  // Load phase: Restore tasks from IndexedDB when chat key changes.
  // Only chatKey in deps — callbacks go through refs so a parent re-render
  // that recreates callback closures never re-triggers this effect.
  useEffect(() => {
    if (!chatKey) {
      setTasks([]);
      return;
    }

    const loadTasks = async () => {
      setIsLoading(true);
      try {
        const cachedTasks = await getTasksByChatKey(chatKey);
        setTasks(cachedTasks);
        onTasksLoadedRef.current?.(cachedTasks);

        // Track running tasks for streaming
        const runningIds = new Set(
          cachedTasks
            .filter((t) => t.status === "running" || t.status === "pending")
            .map((t) => t.task_id)
        );
        setRunningTaskIds(runningIds);
      } catch (err) {
        const error = err instanceof Error ? err : new Error(String(err));
        console.error(`[Task-Updates] Failed to load tasks for ${chatKey}:`, error);
        onErrorRef.current?.(error);
      } finally {
        setIsLoading(false);
      }
    };

    loadTasks();
  }, [chatKey]); // Only chatKey — callbacks via refs above

  // Handle SSE event: update task and persist to IndexedDB.
  // Only chatKey in deps — callbacks go through refs so that a parent
  // re-render never causes this callback (and therefore useTaskSSE's connect)
  // to be recreated.
  const handleTaskEvent = useCallback(
    async (taskId: string, eventOrTask: Record<string, unknown> | Partial<Task>) => {
      try {
        // Extract relevant fields from SSE event or Task object
        const event = eventOrTask as Record<string, unknown>;
        const updatedTask: Task = {
          task_id: taskId,
          chat_key: chatKey || "",
          persona: (event.persona as string) || "",
          instruction: (event.instruction as string) || "",
          status: (event.status as Task["status"]) || "running",
          created_at: (event.created_at as number) || Date.now(),
          started_at: (event.started_at as number | null) || null,
          completed_at: (event.completed_at as number | null) || null,
          progress_pct: (event.progress_pct as number) || 0,
          latest_line: (event.latest_line as string) || "",
          result: (event.result as string) || "",
          error: (event.error as string | null) || null,
          last_synced_at: Date.now(),
          synced: true,
          etag: (event.etag as string | null) || null,
        };

        // Persist to IndexedDB
        await saveTask(updatedTask);

        // Upsert UI state: add if new, update if existing.
        // Previously this was only a map() — new tasks from SSE were silently
        // dropped if they hadn't been loaded from IndexedDB first.
        setTasks((prev) => {
          const exists = prev.some((t) => t.task_id === taskId);
          if (exists) return prev.map((t) => (t.task_id === taskId ? updatedTask : t));
          return [...prev, updatedTask];
        });

        // Call user callback via ref
        onTaskUpdatedRef.current?.(updatedTask);

        // Track running/pending tasks; remove completed/failed ones
        if (updatedTask.status === "running" || updatedTask.status === "pending") {
          setRunningTaskIds((prev) => {
            if (prev.has(taskId)) return prev; // no change → no re-render
            const next = new Set(prev);
            next.add(taskId);
            return next;
          });
        } else if (
          updatedTask.status === "completed" ||
          updatedTask.status === "failed"
        ) {
          setRunningTaskIds((prev) => {
            if (!prev.has(taskId)) return prev; // no change → no re-render
            const next = new Set(prev);
            next.delete(taskId);
            return next;
          });
          activeSSEHooksRef.current.set(taskId, false);

          // Schedule per-task cleanup (remove from IndexedDB + UI after 5 min).
          // Use a Map so each task has its own independent timer; a shared ref
          // would cancel the previous task's cleanup when a second task finishes.
          const existing = cleanupTimeoutsRef.current.get(taskId);
          if (existing) clearTimeout(existing);
          cleanupTimeoutsRef.current.set(taskId, setTimeout(async () => {
            cleanupTimeoutsRef.current.delete(taskId);
            try {
              await deleteTask(taskId);
              setTasks((prev) => prev.filter((t) => t.task_id !== taskId));
            } catch (err) {
              console.warn(`[Task-Updates] Cleanup failed for ${taskId}:`, err);
            }
          }, 5 * 60 * 1000)); // 5 minutes
        }

        console.log(
          `[Task-Updates] Task ${taskId} → ${updatedTask.status} (progress: ${updatedTask.progress_pct}%)`
        );
      } catch (err) {
        const error = err instanceof Error ? err : new Error(String(err));
        console.error(`[Task-Updates] Failed to persist task event:`, error);
        onErrorRef.current?.(error);
      }
    },
    [chatKey] // Only chatKey — callbacks via refs
  );

  // Stream phase: Subscribe to live updates via SSE for the primary running task
  // If multiple tasks run, prioritize the first one (M3 can extend to multi-task)
  const primaryTaskId = Array.from(runningTaskIds)[0] || null;

  // SSE stream for primary running task
  const { isConnected: sseConnected } = useTaskSSE({
    taskId: primaryTaskId,
    sessionId: sessionId || null,
    onEvent: (event) => {
      if (primaryTaskId) {
        handleTaskEvent(primaryTaskId, event as Record<string, unknown>);
      }
    },
    onError: (error) => {
      console.warn(
        `[Task-Updates] SSE failed for ${primaryTaskId}, enabling polling:`,
        error
      );
      if (primaryTaskId) {
        activeSSEHooksRef.current.set(primaryTaskId, false);
      }
      setIsPolling(true);
    },
  });

  // Update connection status when SSE connects
  useEffect(() => {
    if (sseConnected && primaryTaskId) {
      activeSSEHooksRef.current.set(primaryTaskId, true);
    }
  }, [primaryTaskId, sseConnected]);

  // Polling fallback: Enable when SSE not connected
  const { isPolling: pollingActive } = useTaskPolling({
    taskId: sseConnected || !primaryTaskId ? null : primaryTaskId,
    enabled: !sseConnected && runningTaskIds.size > 0,
    onTaskUpdate: async (polledTask) => {
      if (primaryTaskId) {
        await handleTaskEvent(primaryTaskId, polledTask);
      }
    },
    onError: (error) => {
      console.warn(`[Task-Updates] Polling failed for ${primaryTaskId}:`, error);
      onError?.(error);
    },
    pollInterval: 3000, // 3 seconds
  });

  // Update polling state
  useEffect(() => {
    if (pollingActive) {
      setIsPolling(true);
    } else if (sseConnected) {
      setIsPolling(false);
    }
  }, [pollingActive, sseConnected]);

  // Determine overall connection status
  const isConnected = sseConnected || runningTaskIds.size === 0;

  // Cleanup on unmount: cancel all pending task cleanup timers.
  useEffect(() => {
    return () => {
      cleanupTimeoutsRef.current.forEach(clearTimeout);
      cleanupTimeoutsRef.current.clear();
      activeSSEHooksRef.current.clear();
    };
  }, []);

  return {
    tasks,
    isLoading,
    isConnected,
    isPolling,
  };
}
