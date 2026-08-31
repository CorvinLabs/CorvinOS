import { describe, it, expect, vi } from "vitest";
import {
  startTaskCleanupSchedule,
  stopTaskCleanupSchedule,
  exportTask,
  exportAllTasks,
  importTask,
  importTasksFromJSONL,
} from "@/lib/task-lifecycle";
import * as taskDb from "@/lib/task-db";

/**
 * Unit tests for task-lifecycle.ts (ADR-0082 Phase 3 & Phase 4)
 */

describe("Task Lifecycle (ADR-0082 Phase 3 & 4)", () => {
  describe("Phase 3: Cleanup & TTL", () => {
    it("should start cleanup schedule", async () => {
      // vitest@4: mock cleanupOldTasks so it resolves synchronously, then
      // flush the microtask queue so the spy records the call before we assert.
      vi.useFakeTimers();
      const cleanupSpy = vi.spyOn(taskDb, "cleanupOldTasks").mockResolvedValue(0);
      startTaskCleanupSchedule(24 * 60 * 60 * 1000); // 1 day TTL

      await Promise.resolve(); // flush microtask queue — needed in vitest@4

      expect(cleanupSpy).toHaveBeenCalled();

      stopTaskCleanupSchedule();
      vi.useRealTimers();
      vi.restoreAllMocks();
    });
  });

  describe("Phase 4: Export/Import", () => {
    it("should export a task as JSON", async () => {
      const mockTask: taskDb.Task = {
        task_id: "task-1",
        chat_key: "chat-123",
        persona: "assistant",
        instruction: "Test task",
        status: "completed",
        created_at: Date.now(),
        started_at: null,
        completed_at: Date.now(),
        progress_pct: 100,
        latest_line: "Done",
        result: "Result",
        error: null,
        last_synced_at: Date.now(),
        synced: true,
        etag: null,
      };

      vi.spyOn(taskDb, "exportTaskAsJSON").mockResolvedValue(
        JSON.stringify(mockTask, null, 2)
      );

      const json = await exportTask("task-1");
      expect(json).toBeTruthy();
      expect(JSON.parse(json!).task_id).toBe("task-1");
    });

    it("should export all tasks as JSONL", async () => {
      const mockTasks: taskDb.Task[] = [
        {
          task_id: "task-1",
          chat_key: "chat-123",
          persona: "assistant",
          instruction: "Test task 1",
          status: "completed",
          created_at: Date.now(),
          started_at: null,
          completed_at: Date.now(),
          progress_pct: 100,
          latest_line: "Done",
          result: "Result",
          error: null,
          last_synced_at: Date.now(),
          synced: true,
          etag: null,
        },
        {
          task_id: "task-2",
          chat_key: "chat-123",
          persona: "browser",
          instruction: "Test task 2",
          status: "pending",
          created_at: Date.now(),
          started_at: null,
          completed_at: null,
          progress_pct: 0,
          latest_line: "",
          result: "",
          error: null,
          last_synced_at: Date.now(),
          synced: false,
          etag: null,
        },
      ];

      vi.spyOn(taskDb, "getAllTasks").mockResolvedValue(mockTasks);

      const jsonl = await exportAllTasks();
      const lines = jsonl.split("\n").filter((l) => l.length > 0);
      expect(lines.length).toBe(2);
      expect(JSON.parse(lines[0]).task_id).toBe("task-1");
      expect(JSON.parse(lines[1]).task_id).toBe("task-2");
    });

    it("should import a task from JSON", async () => {
      const mockTask: taskDb.Task = {
        task_id: "import-task",
        chat_key: "chat-456",
        persona: "assistant",
        instruction: "Imported task",
        status: "pending",
        created_at: Date.now(),
        started_at: null,
        completed_at: null,
        progress_pct: 0,
        latest_line: "",
        result: "",
        error: null,
        last_synced_at: Date.now(),
        synced: false,
        etag: null,
      };

      vi.spyOn(taskDb, "importTaskFromJSON").mockResolvedValue(true);

      const success = await importTask(JSON.stringify(mockTask));
      expect(success).toBe(true);
    });

    it("should import multiple tasks from JSONL", async () => {
      const task1 = { task_id: "task-1", chat_key: "chat-1" };
      const task2 = { task_id: "task-2", chat_key: "chat-2" };
      const jsonl = JSON.stringify(task1) + "\n" + JSON.stringify(task2);

      vi.spyOn(taskDb, "importTaskFromJSON").mockResolvedValue(true);

      const count = await importTasksFromJSONL(jsonl);
      expect(count).toBe(2);
    });
  });
});
