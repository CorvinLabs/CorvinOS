/**
 * Task lifecycle management (Phase 3 + Phase 4)
 * - Phase 3: Cleanup old tasks (>30 days TTL) + Browser Wake-Lock
 * - Phase 4: Export/Import tasks as JSON
 *
 * ADR-0082 M2: Phase 3 (Cleanup/TTL) + Phase 4 (Export/Import)
 */

import {
  getAllTasks,
  cleanupOldTasks,
  exportTaskAsJSON,
  importTaskFromJSON,
} from "@/lib/task-db";

const DEFAULT_TASK_TTL_MS = 30 * 24 * 60 * 60 * 1000; // 30 days
const CLEANUP_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000; // Daily

let cleanupTimer: NodeJS.Timeout | null = null;

/**
 * Phase 3: Start automatic cleanup of old tasks.
 * Runs daily and removes tasks older than 30 days.
 */
export function startTaskCleanupSchedule(
  ttlMs: number = DEFAULT_TASK_TTL_MS
): void {
  if (cleanupTimer) {
    console.log("Task cleanup schedule already running");
    return;
  }

  console.log(
    `Starting task cleanup schedule (TTL: ${ttlMs / (1000 * 60 * 60 * 24)} days)`
  );

  // Run cleanup immediately
  performTaskCleanup(ttlMs);

  // Schedule daily cleanup
  cleanupTimer = setInterval(() => {
    performTaskCleanup(ttlMs);
  }, CLEANUP_CHECK_INTERVAL_MS);
}

export function stopTaskCleanupSchedule(): void {
  if (cleanupTimer) {
    clearInterval(cleanupTimer);
    cleanupTimer = null;
    console.log("Stopped task cleanup schedule");
  }
}

async function performTaskCleanup(ttlMs: number): Promise<void> {
  try {
    const deleted = await cleanupOldTasks(ttlMs);
    if (deleted > 0) {
      console.log(
        `Task cleanup: deleted ${deleted} old task(s) from IndexedDB`
      );
    }
  } catch (err) {
    console.error("Task cleanup failed:", err);
  }
}

/**
 * Phase 4: Export a single task as JSON
 */
export async function exportTask(taskId: string): Promise<string | null> {
  try {
    const json = await exportTaskAsJSON(taskId);
    if (!json) {
      console.warn(`Task not found for export: ${taskId}`);
      return null;
    }

    console.log(`✓ Exported task ${taskId}`);
    return json;
  } catch (err) {
    console.error(`Failed to export task ${taskId}:`, err);
    return null;
  }
}

/**
 * Phase 4: Export all tasks as JSONL (one task per line)
 */
export async function exportAllTasks(): Promise<string> {
  try {
    const tasks = await getAllTasks();
    const lines = tasks.map((t) => JSON.stringify(t));
    const jsonl = lines.join("\n");

    console.log(`✓ Exported ${tasks.length} task(s) as JSONL`);
    return jsonl;
  } catch (err) {
    console.error("Failed to export all tasks:", err);
    return "";
  }
}

/**
 * Phase 4: Import a task from JSON
 */
export async function importTask(jsonString: string): Promise<boolean> {
  try {
    const success = await importTaskFromJSON(jsonString);
    if (success) {
      console.log("✓ Imported 1 task");
    } else {
      console.error("Failed to import task (invalid schema)");
    }
    return success;
  } catch (err) {
    console.error("Failed to import task:", err);
    return false;
  }
}

/**
 * Phase 4: Import tasks from JSONL (one task per line)
 */
export async function importTasksFromJSONL(jsonl: string): Promise<number> {
  const lines = jsonl
    .split("\n")
    .filter((line) => line.trim().length > 0);
  let successCount = 0;

  for (const line of lines) {
    try {
      const success = await importTask(line);
      if (success) successCount++;
    } catch (err) {
      console.error("Failed to import task line:", err);
    }
  }

  console.log(`✓ Imported ${successCount} / ${lines.length} task(s)`);
  return successCount;
}

/**
 * Phase 4: Trigger a download of task JSON
 */
export function downloadTaskAsJSON(taskId: string, json: string): void {
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `task-${taskId}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  console.log(`✓ Downloaded task ${taskId}`);
}

/**
 * Phase 4: Trigger a download of all tasks as JSONL
 */
export async function downloadAllTasksAsJSONL(): Promise<void> {
  const jsonl = await exportAllTasks();
  const blob = new Blob([jsonl], { type: "application/x-jsonl" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `tasks-backup-${new Date().toISOString().split("T")[0]}.jsonl`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  console.log("✓ Downloaded all tasks");
}
