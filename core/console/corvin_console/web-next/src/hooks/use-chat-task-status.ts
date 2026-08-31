/**
 * Hook to check if a chat has any running tasks.
 * Monitors IndexedDB for task status changes.
 */

import { useEffect, useRef, useState } from "react";
import { getTasksByChatKey } from "@/lib/task-db";

export interface ChatTaskStatus {
  hasRunningTasks: boolean;
  taskCount: number;
  status: "idle" | "running" | "pending";
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
  });

  // Track the current status in a ref so the interval callback can read it
  // without being recreated on every status change.
  const statusRef = useRef(status);
  statusRef.current = status;

  useEffect(() => {
    if (!chatKey) {
      setStatus({ hasRunningTasks: false, taskCount: 0, status: "idle" });
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
        const newStatus: ChatTaskStatus = {
          hasRunningTasks: runningTasks.length > 0,
          taskCount: tasks.length,
          status:
            runningTasks.length > 0
              ? "running"
              : pendingTasks.length > 0
              ? "pending"
              : "idle",
        };

        // Only call setStatus when something actually changed to avoid spurious re-renders.
        const prev = statusRef.current;
        if (
          prev.hasRunningTasks !== newStatus.hasRunningTasks ||
          prev.taskCount !== newStatus.taskCount ||
          prev.status !== newStatus.status
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
