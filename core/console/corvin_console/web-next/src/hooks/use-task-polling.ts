/**
 * Hook for task polling integration (Phase 2)
 * When WebSocket is unavailable or as a fallback, this hook manages polling.
 *
 * ADR-0082 M2: Phase 2 — Polling Fallback
 */

import { useEffect, useRef } from "react";
import {
  startTaskPoller,
  stopTaskPoller,
  type TaskPoller,
} from "@/lib/task-polling";
import { Task } from "@/lib/task-db";

export interface UseTaskPollingOptions {
  taskId: string | null;
  
  enabled?: boolean; // Default true. Set false to disable polling.
  onTaskUpdate?: (task: Task) => void;
  onError?: (error: Error) => void;
  pollInterval?: number; // milliseconds (default 3000)
}

export function useTaskPolling({
  taskId,
  
  enabled = true,
  onTaskUpdate,
  onError,
  pollInterval = 3000,
}: UseTaskPollingOptions) {
  const pollerRef = useRef<TaskPoller | null>(null);

  useEffect(() => {
    if (!enabled || !taskId) {
      // Stop polling if disabled or no task
      if (pollerRef.current) {
        stopTaskPoller(taskId || "");
        pollerRef.current = null;
      }
      return;
    }

    // Start polling for this task
    pollerRef.current = startTaskPoller({
      taskId,
      
      pollInterval,
      onTaskUpdate,
      onError,
    });

    return () => {
      // Cleanup: stop polling when component unmounts
      if (pollerRef.current) {
        stopTaskPoller(taskId);
        pollerRef.current = null;
      }
    };
  }, [taskId,  enabled, pollInterval, onTaskUpdate, onError]);

  return {
    isPolling: enabled && !!taskId,
  };
}
