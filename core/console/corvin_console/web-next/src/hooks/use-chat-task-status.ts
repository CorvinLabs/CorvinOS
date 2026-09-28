/**
 * Hook to monitor task status in a chat.
 * Returns both aggregate status and detailed info on the active task
 * (name, progress, elapsed time) for richer sidebar notifications.
 * Monitors IndexedDB for task status changes.
 */

import { useEffect, useRef, useState } from "react";
import { getTasksByChatKey, Task } from "@/lib/task-db";

export interface ChatTaskStatus {
  hasRunningTasks: boolean;
  taskCount: number;
  status: "idle" | "running" | "pending";
  activeTask: Task | null;  // The currently running or most recent pending task
  elapsedSeconds: number;   // How long the active task has been running
}

// Adaptive polling: fast (2 s) when a task is active, slow (10 s) when idle.
// With N sidebar items, naive 2 s polling fires N reads/s regardless of whether
// any task is actually running. Adaptive mode reduces that to N/10 reads/s at rest.
const POLL_ACTIVE_MS = 2_000;
const POLL_IDLE_MS = 10_000;

export function useChatTaskStatus(chatKey: string): ChatTaskStatus {
  const [status, setStatus] = useState<ChatTaskStatus>({
    hasRunningTasks: false,
    taskCount: 0,
    status: "idle",
    activeTask: null,
    elapsedSeconds: 0,
  });

  // Track the current status in a ref so the interval callback can read it
  // without being recreated on every status change.
  const statusRef = useRef(status);
  statusRef.current = status;

  // Separate interval for elapsed time (updates every second when task is running)
  // This avoids triggering full task checks just for time display.
  useEffect(() => {
    if (!status.activeTask || status.status === "idle") return;

    const interval = setInterval(() => {
      const startTime = status.activeTask?.started_at || status.activeTask?.created_at;
      if (startTime) {
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        setStatus(prev => ({ ...prev, elapsedSeconds: elapsed }));
      }
    }, 1000);

    return () => clearInterval(interval);
  }, [status.activeTask, status.status]);

  useEffect(() => {
    if (!chatKey) {
      setStatus({ hasRunningTasks: false, taskCount: 0, status: "idle", activeTask: null, elapsedSeconds: 0 });
      return;
    }

    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const checkTasks = async () => {
      try {
        const tasks = await getTasksByChatKey(chatKey);
        if (cancelled) return;

        const runningTasks = tasks.filter((t) => t.status === "running");
        const pendingTasks = tasks.filter((t) => t.status === "pending");

        // Pick the active task: running task first, else most recent pending, else null.
        // Sort by creation time descending to get the most recent.
        const sortedTasks = [...tasks].sort((a, b) => b.created_at - a.created_at);
        const activeTask = runningTasks[0] || pendingTasks[0] || null;

        const startTime = activeTask?.started_at || activeTask?.created_at || 0;
        const elapsedSeconds = startTime ? Math.floor((Date.now() - startTime) / 1000) : 0;

        const newStatus: ChatTaskStatus = {
          hasRunningTasks: runningTasks.length > 0,
          taskCount: tasks.length,
          status:
            runningTasks.length > 0
              ? "running"
              : pendingTasks.length > 0
              ? "pending"
              : "idle",
          activeTask,
          elapsedSeconds,
        };

        // Only call setStatus when something actually changed to avoid spurious re-renders.
        const prev = statusRef.current;
        if (
          prev.hasRunningTasks !== newStatus.hasRunningTasks ||
          prev.taskCount !== newStatus.taskCount ||
          prev.status !== newStatus.status ||
          prev.activeTask?.task_id !== newStatus.activeTask?.task_id
        ) {
          setStatus(newStatus);
        }

        // Reschedule: fast poll while tasks are active, slow poll otherwise.
        const delay = newStatus.hasRunningTasks || newStatus.status === "pending"
          ? POLL_ACTIVE_MS
          : POLL_IDLE_MS;
        if (!cancelled) timeoutId = setTimeout(checkTasks, delay);
      } catch (err) {
        console.warn(`[ChatTaskStatus] Failed to load tasks for ${chatKey}:`, err);
        if (!cancelled) timeoutId = setTimeout(checkTasks, POLL_IDLE_MS);
      }
    };

    // Initial check immediately, then adaptive reschedule.
    checkTasks();
    return () => {
      cancelled = true;
      if (timeoutId !== null) clearTimeout(timeoutId);
    };
  }, [chatKey]);

  return status;
}
