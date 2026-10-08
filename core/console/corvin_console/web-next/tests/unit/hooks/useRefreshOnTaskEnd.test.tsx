import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRefreshOnTaskEnd } from "@/hooks/use-refresh-on-task-end";

// Shape of routes/chat.py::list_session_tasks?view=summary (epoch SECONDS).
function body(status: "running" | "completed") {
  const now = Date.now() / 1000;
  return {
    sid: "s1",
    chat_key: "web:s1",
    now,
    count: 1,
    tasks: [
      {
        task_id: "t1",
        chat_key: "web:s1",
        status,
        created_at: now - 20,
        started_at: now - 19,
        ended_at: status === "completed" ? now - 1 : null,
        exit_code: status === "completed" ? 0 : null,
        duration_ms: status === "completed" ? 18_000 : null,
        instruction_preview: "long task",
        last_event_at: now - 1,
      },
    ],
  };
}

const json = (b: unknown) =>
  new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } });

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

/** The task log answers "running" for the first `runningPolls` requests, then "completed". */
function taskLog(runningPolls: number) {
  let n = 0;
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    json(body(n++ < runningPolls ? "running" : "completed")),
  );
}

afterEach(() => vi.restoreAllMocks());

describe("useRefreshOnTaskEnd", () => {
  it("re-reads the history once the running task has ended (a task that outlived a reload)", async () => {
    taskLog(1);
    const refresh = vi.fn(async () => {});
    renderHook(() => useRefreshOnTaskEnd("s1", false, refresh), { wrapper });
    expect(refresh).not.toHaveBeenCalled(); // running: nothing to read yet
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1), { timeout: 9_000 });
  }, 12_000);

  it("does not refresh while this tab is itself streaming the turn", async () => {
    const fetchSpy = taskLog(1);
    const refresh = vi.fn(async () => {});
    renderHook(() => useRefreshOnTaskEnd("s1", true, refresh), { wrapper });
    // Wait until the poll that reports "completed" has happened.
    await waitFor(() => expect(fetchSpy.mock.calls.length).toBeGreaterThanOrEqual(2), { timeout: 9_000 });
    await new Promise((r) => setTimeout(r, 200));
    expect(refresh).not.toHaveBeenCalled();
  }, 12_000);

  it("does nothing for a task that was already finished when the page opened", async () => {
    const fetchSpy = taskLog(0);
    const refresh = vi.fn(async () => {});
    renderHook(() => useRefreshOnTaskEnd("s1", false, refresh), { wrapper });
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 200));
    expect(refresh).not.toHaveBeenCalled();
  });

  it("swallows a failing re-read instead of throwing into the chat", async () => {
    taskLog(1);
    const refresh = vi.fn(async () => {
      throw new Error("network");
    });
    renderHook(() => useRefreshOnTaskEnd("s1", false, refresh), { wrapper });
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1), { timeout: 9_000 });
  }, 12_000);
});
