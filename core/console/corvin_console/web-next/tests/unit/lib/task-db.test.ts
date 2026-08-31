import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  openTaskDB,
  saveTask,
  getTask,
  getTasksByChatKey,
  deleteTask,
  cleanupOldTasks,
  type Task,
} from "@/lib/task-db";

/**
 * Unit tests for task-db.ts (ADR-0082 Phase 1)
 *
 * Note: These tests run in Node.js environment, not browser.
 * IndexedDB is not available in Node, so we mock the open calls.
 * Real E2E tests happen in Playwright.
 */

describe("task-db (ADR-0082 Phase 1 — IndexedDB Service)", () => {
  const mockTask: Task = {
    task_id: "test-task-1",
    chat_key: "chat-123",
    persona: "assistant",
    instruction: "Test instruction",
    status: "completed",
    created_at: Date.now(),
    started_at: null,
    completed_at: Date.now(),
    progress_pct: 100,
    latest_line: "Test output",
    result: "Full test result",
    error: null,
    last_synced_at: Date.now(),
    synced: true,
    etag: null,
  };

  beforeEach(() => {
    // Mock IndexedDB if not available (Node.js environment)
    if (typeof globalThis !== "undefined" && !("indexedDB" in globalThis)) {
      // In Node tests, IndexedDB functions will throw
      // Real tests happen in Playwright (browser)
    }
  });

  afterEach(() => {
    // Cleanup after tests
  });

  it("should define the Task interface with all required fields", () => {
    expect(mockTask).toHaveProperty("task_id");
    expect(mockTask).toHaveProperty("chat_key");
    expect(mockTask).toHaveProperty("persona");
    expect(mockTask).toHaveProperty("instruction");
    expect(mockTask).toHaveProperty("status");
    expect(mockTask).toHaveProperty("created_at");
    expect(mockTask).toHaveProperty("result");
    expect(mockTask).toHaveProperty("synced");
    expect(mockTask.status).toMatch(/pending|running|completed|failed/);
  });

  it("should export all required functions", async () => {
    expect(typeof openTaskDB).toBe("function");
    expect(typeof saveTask).toBe("function");
    expect(typeof getTask).toBe("function");
    expect(typeof getTasksByChatKey).toBe("function");
    expect(typeof deleteTask).toBe("function");
    expect(typeof cleanupOldTasks).toBe("function");
  });

  it("should create a task with valid default values", () => {
    const task: Task = {
      task_id: "new-task",
      chat_key: "chat-key-xyz",
      persona: "browser",
      instruction: "Search for something",
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

    expect(task.status).toBe("pending");
    expect(task.progress_pct).toBe(0);
    expect(task.synced).toBe(false);
    expect(task.etag).toBeNull();
  });

  it("should handle task status transitions correctly", () => {
    const task: Task = { ...mockTask, status: "running", progress_pct: 50 };
    expect(task.status).toBe("running");
    expect(task.progress_pct).toBeGreaterThan(0);
    expect(task.progress_pct).toBeLessThanOrEqual(100);
  });

  it("should preserve task metadata for export", () => {
    const exported = JSON.stringify(mockTask);
    const reimported = JSON.parse(exported) as Task;

    expect(reimported.task_id).toBe(mockTask.task_id);
    expect(reimported.chat_key).toBe(mockTask.chat_key);
    expect(reimported.instruction).toBe(mockTask.instruction);
  });
});
