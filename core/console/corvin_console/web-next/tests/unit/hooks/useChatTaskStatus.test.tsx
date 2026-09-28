import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useChatTaskStatus } from "@/hooks/use-chat-task-status";

// Response body in the exact shape routes/chat.py::list_session_tasks returns
// for view=summary (epoch SECONDS, server clock in `now`).
function summaryBody(serverNow: number) {
  return {
    sid: "abc",
    chat_key: "web:abc",
    now: serverNow,
    count: 2,
    tasks: [
      {
        task_id: "new",
        chat_key: "web:abc",
        status: "running",
        created_at: serverNow - 70,
        started_at: serverNow - 65,
        ended_at: null,
        exit_code: null,
        duration_ms: null,
        instruction_preview: "Draft the release notes",
        last_event_at: serverNow - 2,
      },
      {
        task_id: "old",
        chat_key: "web:abc",
        status: "completed",
        created_at: serverNow - 900,
        started_at: serverNow - 899,
        ended_at: serverNow - 800,
        exit_code: 0,
        duration_ms: 99_000,
        instruction_preview: "earlier",
        last_event_at: serverNow - 800,
      },
    ],
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

afterEach(() => vi.restoreAllMocks());

describe("useChatTaskStatus", () => {
  it("polls the summary view and measures time on the server clock", async () => {
    // Server clock one hour ahead of the browser: elapsed must still be ~65 s.
    const serverNow = Date.now() / 1000 + 3_600;
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(summaryBody(serverNow)));

    const { result } = renderHook(() => useChatTaskStatus("abc"), { wrapper });

    await waitFor(() => expect(result.current.phase).toBe("running"));
    expect(result.current.task?.task_id).toBe("new");
    expect(result.current.activeCount).toBe(1);
    expect(result.current.stale).toBe(false);
    const elapsed = result.current.nowS - (result.current.task!.started_at as number);
    expect(elapsed).toBeGreaterThanOrEqual(64);
    expect(elapsed).toBeLessThan(120);

    const url = String(fetchSpy.mock.calls[0][0]);
    expect(url).toContain("/v1/console/chat/sessions/abc/tasks");
    expect(url).toContain("view=summary");
  });

  it("stays idle and does not fetch without a session id", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => useChatTaskStatus(""), { wrapper });
    expect(result.current.phase).toBe("idle");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("stops polling a session that answers 404", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const fetchSpy = vi
        .spyOn(globalThis, "fetch")
        .mockImplementation(async () => json({ detail: "session not found" }, 404));
      renderHook(() => useChatTaskStatus("gone"), { wrapper });
      await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
      await vi.advanceTimersByTimeAsync(60_000);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
