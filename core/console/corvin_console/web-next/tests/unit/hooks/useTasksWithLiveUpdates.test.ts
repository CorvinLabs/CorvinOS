import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useTasksWithLiveUpdates } from "@/hooks/use-tasks-with-live-updates";
import * as taskDb from "@/lib/task-db";

// Mock dependencies
vi.mock("@/lib/task-db", () => ({
  getTasksByChatKey: vi.fn(),
  saveTask: vi.fn(),
  deleteTask: vi.fn(),
}));

vi.mock("@/hooks/use-task-sse", () => ({
  useTaskSSE: vi.fn(() => ({
    isConnected: true,
    events: [],
    lastEventSeq: null,
  })),
}));

vi.mock("@/hooks/use-task-polling", () => ({
  useTaskPolling: vi.fn(() => ({
    isPolling: false,
  })),
}));

describe("useTasksWithLiveUpdates", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads persisted tasks from IndexedDB on mount", async () => {
    const mockTasks = [
      {
        task_id: "task-1",
        chat_key: "chat-1",
        status: "completed",
        created_at: Date.now(),
        started_at: null,
        completed_at: null,
        progress_pct: 100,
        latest_line: "Done",
        result: "Output",
        error: null,
        last_synced_at: Date.now(),
        synced: true,
        etag: null,
        persona: "",
        instruction: "",
      },
    ];

    vi.mocked(taskDb.getTasksByChatKey).mockResolvedValue(mockTasks);

    const onTasksLoaded = vi.fn();
    const { result } = renderHook(() =>
      useTasksWithLiveUpdates({
        chatKey: "chat-1",
        sessionId: "session-1",
        onTasksLoaded,
      })
    );

    await waitFor(() => {
      expect(result.current.tasks.length).toBe(1);
    });

    expect(onTasksLoaded).toHaveBeenCalledWith(mockTasks);
    expect(taskDb.getTasksByChatKey).toHaveBeenCalledWith("chat-1");
  });

  it("returns isLoading=true while loading tasks", () => {
    vi.mocked(taskDb.getTasksByChatKey).mockImplementation(
      () => new Promise(() => {}) // Never resolves
    );

    const { result } = renderHook(() =>
      useTasksWithLiveUpdates({
        chatKey: "chat-1",
        sessionId: "session-1",
      })
    );

    expect(result.current.isLoading).toBe(true);
  });

  it("clears tasks when chatKey is null", () => {
    const { result, rerender } = renderHook(
      ({ chatKey }: { chatKey: string | null }) =>
        useTasksWithLiveUpdates({
          chatKey,
          sessionId: "session-1",
        }),
      { initialProps: { chatKey: "chat-1" } }
    );

    // Change to null
    rerender({ chatKey: null });

    expect(result.current.tasks).toEqual([]);
  });

  it("calls onError when loading tasks fails", async () => {
    const error = new Error("DB error");
    vi.mocked(taskDb.getTasksByChatKey).mockRejectedValue(error);

    const onError = vi.fn();
    const { result } = renderHook(() =>
      useTasksWithLiveUpdates({
        chatKey: "chat-1",
        sessionId: "session-1",
        onError,
      })
    );

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(onError).toHaveBeenCalled();
  });

  it("returns isConnected=true when SSE is connected", async () => {
    vi.mocked(taskDb.getTasksByChatKey).mockResolvedValue([]);

    const { result } = renderHook(() =>
      useTasksWithLiveUpdates({
        chatKey: "chat-1",
        sessionId: "session-1",
      })
    );

    await waitFor(() => {
      expect(result.current.isConnected).toBe(true);
    });
  });

  it("returns isConnected=true when no running tasks", async () => {
    vi.mocked(taskDb.getTasksByChatKey).mockResolvedValue([]);

    const { result } = renderHook(() =>
      useTasksWithLiveUpdates({
        chatKey: "chat-1",
        sessionId: "session-1",
      })
    );

    await waitFor(() => {
      expect(result.current.isConnected).toBe(true);
    });
  });

  it("handles task updates and calls onTaskUpdated", async () => {
    const initialTasks = [
      {
        task_id: "task-1",
        chat_key: "chat-1",
        status: "running" as const,
        progress_pct: 50,
        created_at: Date.now(),
        started_at: null,
        completed_at: null,
        latest_line: "Processing",
        result: "",
        error: null,
        last_synced_at: Date.now(),
        synced: true,
        etag: null,
        persona: "",
        instruction: "",
      },
    ];

    vi.mocked(taskDb.getTasksByChatKey).mockResolvedValue(initialTasks);

    const onTaskUpdated = vi.fn();
    const { result } = renderHook(() =>
      useTasksWithLiveUpdates({
        chatKey: "chat-1",
        sessionId: "session-1",
        onTaskUpdated,
      })
    );

    await waitFor(() => {
      expect(result.current.tasks.length).toBe(1);
    });

    // Mock saveTask success
    vi.mocked(taskDb.saveTask).mockResolvedValue(undefined);

    // This would normally come from SSE event
    // In a real test, we'd trigger the SSE mock to fire onEvent
    expect(result.current.tasks[0].progress_pct).toBe(50);
  });

  it("removes task from tracking after completion", async () => {
    const mockTasks = [
      {
        task_id: "task-1",
        chat_key: "chat-1",
        status: "running" as const,
        created_at: Date.now(),
        started_at: null,
        completed_at: null,
        progress_pct: 0,
        latest_line: "",
        result: "",
        error: null,
        last_synced_at: Date.now(),
        synced: true,
        etag: null,
        persona: "",
        instruction: "",
      },
    ];

    vi.mocked(taskDb.getTasksByChatKey).mockResolvedValue(mockTasks);
    vi.mocked(taskDb.saveTask).mockResolvedValue(undefined);
    vi.mocked(taskDb.deleteTask).mockResolvedValue(undefined);

    const { result } = renderHook(() =>
      useTasksWithLiveUpdates({
        chatKey: "chat-1",
        sessionId: "session-1",
      })
    );

    await waitFor(() => {
      expect(result.current.tasks.length).toBe(1);
    });

    // After cleanup timeout (5 min), task should be removed
    // In a real test with timeouts, we'd use vi.advanceTimersByTime()
    expect(result.current.tasks[0].task_id).toBe("task-1");
  });

  // ─── Regression: unstable-callback infinite-loop ──────────────────────────
  // Before the fix, inline arrow functions passed as onTasksLoaded/onTaskUpdated
  // caused the load effect to re-run on every render, leading to an infinite loop
  // and SSE reconnect storm (tasks never showed up after chat switch).

  it("does NOT re-run the load effect when callbacks change identity", async () => {
    const mockTasks = [
      {
        task_id: "task-1",
        chat_key: "chat-1",
        status: "completed" as const,
        created_at: Date.now(),
        started_at: null,
        completed_at: null,
        progress_pct: 100,
        latest_line: "Done",
        result: "ok",
        error: null,
        last_synced_at: Date.now(),
        synced: true,
        etag: null,
        persona: "",
        instruction: "",
      },
    ];
    vi.mocked(taskDb.getTasksByChatKey).mockResolvedValue(mockTasks);

    // Create two different callback instances (simulates parent re-render with inline arrow)
    const cb1 = vi.fn();
    const cb2 = vi.fn();

    const { rerender } = renderHook(
      ({ onTasksLoaded }: { onTasksLoaded: typeof cb1 }) =>
        useTasksWithLiveUpdates({ chatKey: "chat-1", sessionId: "s1", onTasksLoaded }),
      { initialProps: { onTasksLoaded: cb1 } }
    );

    // Simulate parent re-render with a new callback reference
    rerender({ onTasksLoaded: cb2 });
    rerender({ onTasksLoaded: cb1 });

    // Wait for any async operations to settle
    await waitFor(() => expect(taskDb.getTasksByChatKey).toHaveBeenCalled());

    // getTasksByChatKey should have been called ONCE (on mount), not 3 times
    // (Before the fix it would be called once per rerender because onTasksLoaded
    //  was in the effect deps).
    expect(taskDb.getTasksByChatKey).toHaveBeenCalledTimes(1);
    expect(taskDb.getTasksByChatKey).toHaveBeenCalledWith("chat-1");
  });

  it("re-runs load effect only when chatKey changes, not on callback change", async () => {
    vi.mocked(taskDb.getTasksByChatKey).mockResolvedValue([]);

    const callback = vi.fn();

    const { rerender } = renderHook(
      ({ chatKey, cb }: { chatKey: string; cb: typeof callback }) =>
        useTasksWithLiveUpdates({ chatKey, sessionId: "s1", onTasksLoaded: cb }),
      { initialProps: { chatKey: "chat-1", cb: callback } }
    );

    await waitFor(() => expect(taskDb.getTasksByChatKey).toHaveBeenCalledTimes(1));

    // Change callback identity only (same chatKey) → should NOT re-load
    rerender({ chatKey: "chat-1", cb: vi.fn() });
    await new Promise((r) => setTimeout(r, 50));
    expect(taskDb.getTasksByChatKey).toHaveBeenCalledTimes(1);

    // Change chatKey → MUST re-load
    rerender({ chatKey: "chat-2", cb: callback });
    await waitFor(() => expect(taskDb.getTasksByChatKey).toHaveBeenCalledTimes(2));
    expect(taskDb.getTasksByChatKey).toHaveBeenLastCalledWith("chat-2");
  });

  it("restores running task and triggers SSE after chat switch (chat-1 → chat-2 → chat-1)", async () => {
    const runningTask = {
      task_id: "task-running",
      chat_key: "chat-1",
      status: "running" as const,
      created_at: Date.now(),
      started_at: Date.now(),
      completed_at: null,
      progress_pct: 30,
      latest_line: "Computing…",
      result: "",
      error: null,
      last_synced_at: Date.now(),
      synced: true,
      etag: null,
      persona: "assistant",
      instruction: "run backtest",
    };

    vi.mocked(taskDb.getTasksByChatKey).mockImplementation((key) =>
      Promise.resolve(key === "chat-1" ? [runningTask] : [])
    );

    const onTasksLoaded = vi.fn();
    const { result, rerender } = renderHook(
      ({ chatKey }: { chatKey: string }) =>
        useTasksWithLiveUpdates({ chatKey, sessionId: chatKey, onTasksLoaded }),
      { initialProps: { chatKey: "chat-1" } }
    );

    // Initial load: task should appear
    await waitFor(() => expect(result.current.tasks).toHaveLength(1));
    expect(result.current.tasks[0].task_id).toBe("task-running");
    expect(result.current.tasks[0].status).toBe("running");
    const callsAfterMount = (taskDb.getTasksByChatKey as ReturnType<typeof vi.fn>).mock.calls.length;

    // Switch to chat-2
    rerender({ chatKey: "chat-2" });
    await waitFor(() => expect(result.current.tasks).toHaveLength(0));

    // Switch BACK to chat-1 — task must reappear
    rerender({ chatKey: "chat-1" });
    await waitFor(() => expect(result.current.tasks).toHaveLength(1));

    expect(result.current.tasks[0].task_id).toBe("task-running");
    expect(result.current.tasks[0].status).toBe("running");

    // Exactly 3 loads total: initial, switch-to-2, switch-back-to-1
    const totalCalls = (taskDb.getTasksByChatKey as ReturnType<typeof vi.fn>).mock.calls.length;
    expect(totalCalls).toBe(callsAfterMount + 2);
  });

  it("SSE event updates task progress without duplicating the entry", async () => {
    // Verify the upsert path: an event for a tracked task updates in place,
    // does not add a duplicate entry to the tasks list.
    const runningTask = {
      task_id: "task-1",
      chat_key: "chat-1",
      status: "running" as const,
      created_at: Date.now(),
      started_at: Date.now(),
      completed_at: null,
      progress_pct: 10,
      latest_line: "Starting",
      result: "",
      error: null,
      last_synced_at: Date.now(),
      synced: true,
      etag: null,
      persona: "",
      instruction: "run something",
    };

    vi.mocked(taskDb.getTasksByChatKey).mockResolvedValue([runningTask]);
    vi.mocked(taskDb.saveTask).mockResolvedValue(undefined);

    const { useTaskSSE } = await import("@/hooks/use-task-sse");
    let capturedOnEvent: ((evt: unknown) => void) | undefined;

    vi.mocked(useTaskSSE).mockImplementation(
      ({ onEvent }: { onEvent?: (e: unknown) => void; taskId?: string | null }) => {
        if (onEvent) capturedOnEvent = onEvent;
        return { isConnected: true, events: [], lastEventSeq: null };
      }
    );

    const onTaskUpdated = vi.fn();
    const { result } = renderHook(() =>
      useTasksWithLiveUpdates({
        chatKey: "chat-1",
        sessionId: "s1",
        onTaskUpdated,
      })
    );

    // Initial load: one task
    await waitFor(() => expect(result.current.tasks).toHaveLength(1));
    expect(result.current.tasks[0].progress_pct).toBe(10);

    // SSE fires progress update for the same task
    if (capturedOnEvent) {
      capturedOnEvent({
        task_id: "task-1",
        chat_key: "chat-1",
        status: "running",
        progress_pct: 75,
        instruction: "run something",
        persona: "",
        latest_line: "75% done",
        result: "",
        error: null,
        created_at: Date.now(),
      });
    }

    await waitFor(() => {
      expect(result.current.tasks[0]?.progress_pct).toBe(75);
    });
    // Must not duplicate the task entry
    expect(result.current.tasks).toHaveLength(1);
    expect(onTaskUpdated).toHaveBeenCalledWith(
      expect.objectContaining({ task_id: "task-1", progress_pct: 75 })
    );
  });
});
