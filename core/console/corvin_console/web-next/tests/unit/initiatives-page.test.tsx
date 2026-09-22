/**
 * Initiatives board against MSW: tiles count tasks by status; a blocked
 * initiative names its gate; the "Finished" filter hides open tasks; a status
 * change PATCHes the real route with CSRF; a missing file shows the empty
 * state (never sample data); a 404 build says so.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ session: { csrf_token: "csrf-1" } }) }));

import InitiativesPage from "@/pages/initiatives";
import { clockSkewMs, formatCountdown } from "@/pages/initiatives-format";

const task = (id: string, title: string, status: string, progress = 0, extra = {}) => ({
  id, title, group: null, status, progress, due: null, overdue: false, completed_at: null, note: null, ...extra,
});
const ini = (over: Record<string, unknown>) => ({
  id: "x", label: null, title: "", description: null, cadence: null,
  start: "2026-09-22T00:00:00Z", deadline: "2026-10-06T23:59:00Z", status: "running", blocked_by: null,
  time_progress_pct: 7, task_progress_pct: 8, task_counts: { pending: 0, running: 0, done: 0, blocked: 0, total: 0, overdue: 0 },
  tasks: [], preconditions: [], gates: [], next_checkpoint: null, ...over,
});
const BOARD = {
  server_time: "2026-09-22T15:30:00Z", revision: "1-2", source: "initiatives.json",
  totals: { pending: 1, running: 1, done: 1, blocked: 0, total: 3, overdue: 0, initiatives_blocked: 1 },
  initiatives: [
    ini({ id: "loop-a", label: "Loop A", title: "3D PoC",
      task_counts: { pending: 1, running: 1, done: 1, blocked: 0, total: 3, overdue: 0 },
      tasks: [task("1", "Blender Setup", "running", 50), task("2", "YAML + TTS", "pending"), task("0", "Kickoff", "done", 100)],
      preconditions: [{ label: "GPU cluster access", state: "ok", detail: null }] }),
    ini({ id: "loop-c", label: "Loop C", title: "Phase 10 Production", status: "blocked",
      blocked_by: { initiative: "loop-b", gate: "blocker", gate_title: "Blocker Gate", decision: "pending" } }),
  ],
};

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><InitiativesPage /></QueryClientProvider>);
}
afterEach(() => cleanup());

describe("initiatives-format", () => {
  it("formats countdowns both ways", () => {
    const now = Date.parse("2026-09-22T15:30:00Z");
    expect(formatCountdown("2026-10-06T23:59:00Z", now)).toBe("14d 8h 29m");
    expect(formatCountdown("2026-09-22T15:00:00Z", now)).toBe("overdue by 30m 0s");
    expect(formatCountdown(null, now)).toBe("—");
    expect(clockSkewMs("2026-09-22T15:30:05Z", now)).toBe(5000);
  });
});

describe("Initiatives page", () => {
  it("renders tiles, tasks, preconditions and the blocking gate", async () => {
    server.use(http.get("/v1/console/initiatives", () => HttpResponse.json(BOARD)));
    renderIt();
    expect(await screen.findByText("Blender Setup")).toBeTruthy();
    expect(screen.getByText("Running tasks").nextSibling?.textContent).toBe("1");
    expect(screen.getByText("Finished tasks").nextSibling?.textContent).toBe("1");
    expect(screen.getByText("GPU cluster access")).toBeTruthy();
    expect(screen.getByText(/Waiting for gate “Blocker Gate”/)).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Finished" }));
    expect(screen.queryByText("Blender Setup")).toBeNull();
    expect(screen.getByText("Kickoff")).toBeTruthy();
  });

  it("PATCHes a status change with the CSRF token", async () => {
    let seen: { body: unknown; csrf: string | null } | null = null;
    server.use(
      http.get("/v1/console/initiatives", () => HttpResponse.json(BOARD)),
      http.patch("/v1/console/initiatives/loop-a/tasks/1", async ({ request }) => {
        seen = { body: await request.json(), csrf: request.headers.get("X-CSRF-Token") };
        return HttpResponse.json(BOARD);
      }),
    );
    renderIt();
    const sel = await screen.findByLabelText("Blender Setup status");
    fireEvent.change(sel, { target: { value: "done" } });
    await waitFor(() => expect(seen).not.toBeNull());
    expect(seen).toEqual({ body: { status: "done" }, csrf: "csrf-1" });
  });

  it("shows the empty state for a missing file, never sample data", async () => {
    server.use(http.get("/v1/console/initiatives", () => HttpResponse.json({
      ...BOARD, revision: null, source: null, initiatives: [],
      totals: { pending: 0, running: 0, done: 0, blocked: 0, total: 0, overdue: 0, initiatives_blocked: 0 },
    })));
    renderIt();
    expect(await screen.findByText("No initiatives yet")).toBeTruthy();
    expect(screen.queryByText("Running tasks")).toBeNull();
  });

  it("says so on a build without the route", async () => {
    server.use(http.get("/v1/console/initiatives", () => new HttpResponse(null, { status: 404 })));
    renderIt();
    expect(await screen.findByText("Initiatives are not available on this build.")).toBeTruthy();
  });
});
