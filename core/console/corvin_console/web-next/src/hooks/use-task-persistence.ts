/**
 * Hook for task persistence across chat switches.
 * ADR-0082 M2: Phase 1 — Chat-Switch Integration
 *
 * When a chat is mounted, load persisted tasks from IndexedDB.
 * When tasks are created/updated, persist them to IndexedDB.
 */

import { useEffect, useRef } from "react";
import {
  getTasksByChatKey,
  saveTask,
  Task,
  deleteTask,
  deleteTasksByChatKey,
} from "@/lib/task-db";

export interface UseTaskPersistenceOptions {
  chatKey: string | null;
  onTasksLoaded?: (tasks: Task[]) => void;
  onTaskCreated?: (task: Task) => void;
  onTaskUpdated?: (task: Task) => void;
  onTaskDeleted?: (taskId: string) => void;
}

export function useTaskPersistence({
  chatKey,
  onTasksLoaded,
  onTaskCreated,
  onTaskUpdated,
  onTaskDeleted,
}: UseTaskPersistenceOptions) {
  const initialLoadDoneRef = useRef(false);

  // Load tasks from IndexedDB when chat is mounted
  useEffect(() => {
    if (!chatKey) {
      initialLoadDoneRef.current = false;
      return;
    }

    const loadPersistedTasks = async () => {
      try {
        const tasks = await getTasksByChatKey(chatKey);
        if (tasks.length > 0) {
          onTasksLoaded?.(tasks);
        }
        initialLoadDoneRef.current = true;
      } catch (err) {
        console.error("Failed to load persisted tasks:", err);
      }
    };

    loadPersistedTasks();
  }, [chatKey, onTasksLoaded]);

  // Persist a task
  const persistTask = async (task: Task) => {
    if (!chatKey) return;
    try {
      await saveTask({ ...task, chat_key: chatKey });
      onTaskCreated?.(task);
    } catch (err) {
      console.error("Failed to persist task:", err);
    }
  };

  // Update persisted task
  const updatePersistedTask = async (task: Task) => {
    try {
      await saveTask(task);
      onTaskUpdated?.(task);
    } catch (err) {
      console.error("Failed to update persisted task:", err);
    }
  };

  // Remove persisted task
  const removePersistedTask = async (taskId: string) => {
    try {
      await deleteTask(taskId);
      onTaskDeleted?.(taskId);
    } catch (err) {
      console.error("Failed to delete persisted task:", err);
    }
  };

  // Clear all tasks for this chat
  const clearChatTasks = async () => {
    if (!chatKey) return;
    try {
      await deleteTasksByChatKey(chatKey);
    } catch (err) {
      console.error("Failed to clear chat tasks:", err);
    }
  };

  return {
    persistTask,
    updatePersistedTask,
    removePersistedTask,
    clearChatTasks,
    initialLoadDone: initialLoadDoneRef.current,
  };
}
