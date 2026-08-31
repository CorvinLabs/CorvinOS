import { describe, it, expect, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useTaskPersistence } from "@/hooks/use-task-persistence";
import * as taskDb from "@/lib/task-db";

/**
 * Integration test for useTaskPersistence hook (ADR-0082 Phase 1)
 * Tests the hook that integrates IndexedDB with React components
 */

describe("useTaskPersistence Hook (ADR-0082 Phase 1)", () => {
  // Mock the task-db module
  vi.mock("@/lib/task-db");

  it("should call onTasksLoaded when chat key changes", async () => {
    const mockTasks = [
      {
        task_id: "task-1",
        chat_key: "chat-123",
        persona: "assistant",
        instruction: "Test task 1",
        status: "completed" as const,
        created_at: Date.now(),
        started_at: null,
        completed_at: Date.now(),
        progress_pct: 100,
        latest_line: "Done",
        result: "Result 1",
        error: null,
        last_synced_at: Date.now(),
        synced: true,
        etag: null,
      },
    ];

    const onTasksLoaded = vi.fn();
    vi.spyOn(taskDb, "getTasksByChatKey").mockResolvedValue(mockTasks);

    const { rerender } = renderHook(
      ({ chatKey }) => {
        return useTaskPersistence({ chatKey, onTasksLoaded });
      },
      { initialProps: { chatKey: "chat-123" } }
    );

    // Wait for the effect to run and call onTasksLoaded
    await waitFor(() => {
      expect(onTasksLoaded).toHaveBeenCalled();
    });

    // Verify the tasks were passed correctly
    expect(onTasksLoaded).toHaveBeenCalledWith(mockTasks);

    // Switch to a different chat key
    const newMockTasks = [
      {
        ...mockTasks[0],
        task_id: "task-2",
        chat_key: "chat-456",
      },
    ];

    vi.spyOn(taskDb, "getTasksByChatKey").mockResolvedValue(newMockTasks);
    rerender({ chatKey: "chat-456" });

    // Wait for the new effect to run
    await waitFor(() => {
      expect(onTasksLoaded).toHaveBeenCalledWith(newMockTasks);
    });
  });

  it("should call persistTask when creating a task", async () => {
    const mockTask = {
      task_id: "new-task",
      chat_key: "chat-789",
      persona: "browser",
      instruction: "New task",
      status: "pending" as const,
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

    vi.spyOn(taskDb, "saveTask").mockResolvedValue(void 0);
    vi.spyOn(taskDb, "getTasksByChatKey").mockResolvedValue([]);

    const onTaskCreated = vi.fn();
    const { result } = renderHook(() =>
      useTaskPersistence({
        chatKey: "chat-789",
        onTaskCreated,
      })
    );

    // Call persistTask
    await result.current.persistTask(mockTask);

    // Verify saveTask was called
    expect(taskDb.saveTask).toHaveBeenCalledWith(
      expect.objectContaining({
        task_id: mockTask.task_id,
        chat_key: "chat-789",
      })
    );

    // Verify onTaskCreated callback was fired
    await waitFor(() => {
      expect(onTaskCreated).toHaveBeenCalledWith(mockTask);
    });
  });

  it("should delete a task and call onTaskDeleted", async () => {
    vi.spyOn(taskDb, "deleteTask").mockResolvedValue(void 0);
    vi.spyOn(taskDb, "getTasksByChatKey").mockResolvedValue([]);

    const onTaskDeleted = vi.fn();
    const { result } = renderHook(() =>
      useTaskPersistence({
        chatKey: "chat-123",
        onTaskDeleted,
      })
    );

    const taskId = "task-to-delete";
    await result.current.removePersistedTask(taskId);

    // Verify deleteTask was called
    expect(taskDb.deleteTask).toHaveBeenCalledWith(taskId);

    // Verify onTaskDeleted callback was fired
    await waitFor(() => {
      expect(onTaskDeleted).toHaveBeenCalledWith(taskId);
    });
  });

  it("should clear all tasks for a chat", async () => {
    vi.spyOn(taskDb, "deleteTasksByChatKey").mockResolvedValue(void 0);
    vi.spyOn(taskDb, "getTasksByChatKey").mockResolvedValue([]);

    const { result } = renderHook(() =>
      useTaskPersistence({
        chatKey: "chat-123",
      })
    );

    await result.current.clearChatTasks();

    // Verify deleteTasksByChatKey was called
    expect(taskDb.deleteTasksByChatKey).toHaveBeenCalledWith("chat-123");
  });
});
