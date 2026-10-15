import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TaskTimelinePage from "@/pages/task-timeline";

const apiMock = vi.fn(async (path: string) => {
  if (path.includes("/timeline")) {
    return { rows: [
      { seq: 0, at: 1700000000, state: "pending", kind: "transition", text: null },
      { seq: 1, at: 1700000001, state: "running", kind: "transition", text: null },
      { seq: 2, at: 1700000100, state: "interrupted", kind: "transition", text: null },
      { seq: 3, at: 1700000101, state: null, kind: "interrupted_resuming", text: "Your task was interrupted by a service restart." },
    ] };
  }
  return { tasks: [{ task_id: "t1", status: "failed", created_at: 1700000000, ended_at: null }] };
});
vi.mock("@/lib/api/client", async (orig) => ({ ...(await orig<object>()), api: (p: string) => apiMock(p) }));
vi.mock("@/lib/api/sessions", async (orig) => ({
  ...(await orig<object>()),
  listChatSessions: async () => ({ tenant_id: "_default", count: 1, sessions: [{ sid: "s1", title: "Report", chat_key: "web:s1", created_at: 0, last_active_at: 0, turn_count: 1, workdir: "" }] }),
}));

describe("Task Timeline page", () => {
  it("shows the interruption and the notice the user was sent", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><TaskTimelinePage /></QueryClientProvider>);
    await waitFor(() => expect(screen.getByRole("option", { name: "Report" })).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Chat"), { target: { value: "s1" } });
    await waitFor(() => expect(screen.getAllByRole("option").some((o) => /failed/.test(o.textContent ?? ""))).toBe(true));
    fireEvent.change(screen.getByLabelText("Task"), { target: { value: "t1" } });
    await waitFor(() => expect(screen.getByTestId("timeline-status").textContent).toMatch(/Interrupted/));
    const rows = screen.getByTestId("timeline-rows");
    expect(rows.textContent).toMatch(/Your task was interrupted by a service restart/);
    expect(rows.querySelector('[data-tone="warn"]')).not.toBeNull();
  });
});
