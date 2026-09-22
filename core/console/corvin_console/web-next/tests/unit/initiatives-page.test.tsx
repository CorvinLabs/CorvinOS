/**
 * Initiatives board against MSW: tiles count runs and tasks; a blocked
 * initiative names its gate; running and finished runs are separate tabs and
 * the finished tab is a history (newest first, schedule verdict, reopen); the
 * "Done" task filter hides open tasks; closing a run PUTs the real route; a status
 * change PATCHes the real route with CSRF; a missing file shows the empty
 * state (never sample data); a 404 build says so.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ session: { csrf_token: "csrf-1" } }) }));

import InitiativesPage from "@/pages/initiatives";
import { clockSkewMs, evidenceText, formatCountdown, scheduleLabel } from "@/pages/initiatives-format";

const task = (id: string, title: string, status: string, progress = 0, extra = {}) => ({
  id, title, group: null, status, progress, due: null, overdue: false, completed_at: null, note: null, ...extra,
});
const ini = (over: Record<string, unknown>) => ({
  id: "x", label: null, title: "", description: null, cadence: null,
  start: "2026-09-22T00:00:00Z", deadline: "2026-10-06T23:59:00Z", status: "running", blocked_by: null,
  time_progress_pct: 7, task_progress_pct: 8, task_counts: { pending: 0, running: 0, done: 0, blocked: 0, total: 0, overdue: 0 },
  tasks: [], preconditions: [], gates: [], next_checkpoint: null,
  phase: "active", outcome: null, finished_at: null, duration_s: 86400, schedule_delta_s: null, ...over,
});
const BOARD = {
  server_time: "2026-09-22T15:30:00Z", revision: "1-2", source: "initiatives.json",
  verification: { last_at: "2026-09-22T15:25:00Z", running: false, stale_tasks: 0, claim_conflicts: 1, stale_after_s: 7200 },
  totals: { pending: 1, running: 1, done: 3, blocked: 0, total: 5, overdue: 0, initiatives_blocked: 1, runs_active: 2, runs_finished: 2 },
  initiatives: [
    ini({ id: "loop-a", label: "Loop A", title: "3D PoC",
      task_counts: { pending: 1, running: 1, done: 1, blocked: 0, total: 3, overdue: 0 },
      tasks: [task("1", "Blender Setup", "running", 50, { progress_source: "evidence", claim_conflict: true,
                verification: { state: "partial", at: "2026-09-22T15:25:00Z", age_s: 300, stale: false, passed: 8, failed: 0,
                  errors: 2, skipped: 1, paths_present: 1, paths_total: 2, missing_paths: ["tools/gen.py"], summary: "", score_pct: 80 } }),
              task("2", "YAML + TTS", "pending"), task("0", "Kickoff", "done", 100)],
      preconditions: [{ label: "GPU cluster access", state: "ok", detail: null }] }),
    ini({ id: "loop-c", label: "Loop C", title: "Phase 10 Production", status: "blocked",
      blocked_by: { initiative: "loop-b", gate: "blocker", gate_title: "Blocker Gate", decision: "pending" } }),
    ini({ id: "old-1", label: "Loop 0", title: "Older run", status: "done", phase: "finished", outcome: "completed",
      finished_at: "2026-08-01T00:00:00Z", duration_s: 9 * 86400, schedule_delta_s: 86400,
      task_counts: { pending: 0, running: 0, done: 1, blocked: 0, total: 1, overdue: 0 },
      tasks: [task("t", "Old task", "done", 100)] }),
    ini({ id: "old-2", label: "Loop Z", title: "Newer run", status: "cancelled", phase: "finished", outcome: "cancelled",
      finished_at: "2026-09-10T00:00:00Z", duration_s: 3600, schedule_delta_s: -7200 }),
  ],
};

const utask = (id: string, type: string, type_label: string, title: string, status: string, extra = {}) => ({
  id, type, type_label, subtype: null, title, status, raw_status: status, created_at: "2026-09-22T15:00:00Z",
  started_at: "2026-09-22T15:00:00Z", ended_at: null, sort_ts: 1, duration_s: null, stale_reason: null, detail: null, ...extra,
});
const tsum = (type: string, label: string, active: number, finished: number, stale = 0, note: string | null = null) =>
  ({ type, label, active, finished, stale, failed: 0, note, error: null });
const ALL_TASKS = {
  server_time: "2026-09-22T15:30:00Z", scan_ms: 12,
  types: [tsum("initiative", "Initiative", 2, 1), tsum("chat", "Chat", 0, 2), tsum("acs", "ACS", 1, 0),
          tsum("forge", "Forge tool", 0, 0, 1),
          tsum("workflow", "Workflow", 0, 0, 0, "The workflow plugin does not load on this build.")],
  active: [
    utask("initiative:loop-a/1", "initiative", "Initiative", "Loop A · Blender Setup", "running"),
    utask("acs:acs-9", "acs", "ACS", "spotify_chart_insights", "running"),
    utask("forge:f-old", "forge", "Forge tool", "svg_to_png", "stale", { stale_reason: "no sign of life for 876 h — no end record was written" }),
  ],
  finished: [
    utask("chat:d1", "chat", "Chat", "Discord message · assistant", "done", { ended_at: "2026-09-22T15:20:00Z", duration_s: 29 }),
    utask("chat:w1", "chat", "Chat", "summarise the report", "failed", { ended_at: "2026-09-22T15:10:00Z" }),
    utask("initiative:loop-b/p0", "initiative", "Initiative", "Loop B · Audit backend wiring", "done"),
  ],
  finished_total: 3,
  totals: { active: 3, running: 2, stale: 1, finished_24h: 3, failed_24h: 1, all: 6 },
};
let tasksRequests: string[] = [];
beforeEach(() => {
  tasksRequests = [];
  server.use(http.get("/v1/console/initiatives/tasks", ({ request }) => {
    tasksRequests.push(new URL(request.url).search);
    const types = new URL(request.url).searchParams.get("types")?.split(",");
    if (!types) return HttpResponse.json(ALL_TASKS);
    const keep = (t: { type: string }) => types.includes(t.type);
    return HttpResponse.json({ ...ALL_TASKS, active: ALL_TASKS.active.filter(keep), finished: ALL_TASKS.finished.filter(keep) });
  }));
});

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
    expect(scheduleLabel(30).text).toBe("On time");
    expect(scheduleLabel(-90000)).toEqual({ text: "1d 1h late", tone: "danger" });
    expect(scheduleLabel(null).tone).toBe("secondary");
    expect(evidenceText({ passed: 3, failed: 0, errors: 0, paths_present: 0, paths_total: 0, state: "ok" })).toBe("3/3 tests passing");
    expect(evidenceText({ passed: 0, failed: 0, errors: 0, paths_present: 0, paths_total: 1, state: "unverified" })).toBe("Evidence not verified yet");
  });
});

describe("Initiatives page", () => {
  it("renders tiles, tasks, preconditions and the blocking gate", async () => {
    server.use(http.get("/v1/console/initiatives", () => HttpResponse.json(BOARD)));
    renderIt();
    expect(await screen.findByText("Blender Setup")).toBeTruthy();
    expect(screen.getByText("Running runs").nextSibling?.textContent).toBe("2");
    expect(await screen.findByText("Stale tasks")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Running tasks").nextSibling?.textContent).toBe("2"));
    // Finished runs are not on the Running tab.
    expect(screen.queryByText("Older run")).toBeNull();
    expect(screen.getByText("GPU cluster access")).toBeTruthy();
    expect(screen.getByText(/Waiting for gate “Blocker Gate”/)).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Done" }));
    expect(screen.queryByText("Blender Setup")).toBeNull();
    expect(screen.getByText("Kickoff")).toBeTruthy();
  });

  it("lists finished runs as history, newest first, with their schedule verdict", async () => {
    server.use(http.get("/v1/console/initiatives", () => HttpResponse.json(BOARD)));
    renderIt();
    fireEvent.click(await screen.findByRole("tab", { name: /Finished \(/ }));
    expect(screen.queryByText("Blender Setup")).toBeNull();
    const titles = screen.getAllByText(/(Older|Newer) run/).map((e) => e.textContent);
    expect(titles).toEqual(["Loop Z · Newer run", "Loop 0 · Older run"]);
    expect(screen.getByText("2h 0m late")).toBeTruthy();
    expect(screen.getByText("1d 0h early")).toBeTruthy();
    expect(screen.getByText("Cancelled")).toBeTruthy();
    // Expanding a finished run shows its tasks.
    fireEvent.click(screen.getByRole("button", { name: /Older run/ }));
    expect(screen.getByText("Old task")).toBeTruthy();
  });

  it("closes a running run and reopens a finished one via the real route", async () => {
    const seen: unknown[] = [];
    server.use(
      http.get("/v1/console/initiatives", () => HttpResponse.json(BOARD)),
      http.put("/v1/console/initiatives/:iid/close", async ({ request, params }) => {
        seen.push({ iid: params.iid, body: await request.json(), csrf: request.headers.get("X-CSRF-Token") });
        return HttpResponse.json(BOARD);
      }),
    );
    renderIt();
    await screen.findByText("Blender Setup");
    fireEvent.click(screen.getAllByRole("button", { name: /Mark completed/ })[0]);
    await waitFor(() => expect(seen).toHaveLength(1));
    fireEvent.click(screen.getByRole("tab", { name: /Finished \(/ }));
    fireEvent.click(screen.getAllByRole("button", { name: /Reopen/ })[0]);
    await waitFor(() => expect(seen).toHaveLength(2));
    expect(seen).toEqual([
      { iid: "loop-a", body: { outcome: "completed" }, csrf: "csrf-1" },
      { iid: "old-2", body: { outcome: null }, csrf: "csrf-1" },
    ]);
  });

  it("shows evidence, freezes derived controls and starts a verification run", async () => {
    const posts: { url: string; csrf: string | null }[] = [];
    server.use(
      http.get("/v1/console/initiatives", () => HttpResponse.json(BOARD)),
      http.post("/v1/console/initiatives/verify", ({ request }) => {
        posts.push({ url: new URL(request.url).search, csrf: request.headers.get("X-CSRF-Token") });
        return HttpResponse.json({ started: false, running: false, reason: "unchanged" }, { status: 202 });
      }),
    );
    renderIt();
    expect(await screen.findByText("8/10 tests passing (2 errors) · 1/2 paths present")).toBeTruthy();
    expect(screen.getByText(/missing: tools\/gen.py/)).toBeTruthy();
    expect(screen.getByText("Marked done — evidence disagrees")).toBeTruthy();
    expect((screen.getByLabelText("Blender Setup status") as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByLabelText("YAML + TTS status") as HTMLSelectElement).disabled).toBe(false);
    expect(screen.getByText("1 done-claims contradicted")).toBeTruthy();
    // Opening the page asked the server to re-check only if something changed.
    await waitFor(() => expect(posts).toEqual([{ url: "?if_changed=true", csrf: "csrf-1" }]));
    fireEvent.click(screen.getByRole("button", { name: /Verify now/ }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]).toEqual({ url: "", csrf: "csrf-1" }); // the button forces a run
    expect(await screen.findByText(/Verifying evidence/)).toBeTruthy(); // immediate feedback
  });

  it("keeps the last good data on screen and says it is reconnecting when polls fail", async () => {
    let fail = false;
    server.use(http.get("/v1/console/initiatives", () =>
      fail ? new HttpResponse(null, { status: 502 }) : HttpResponse.json(BOARD)));
    renderIt();
    expect(await screen.findByText("Blender Setup")).toBeTruthy();
    fail = true;
    const indicator = await screen.findByText(/Reconnecting… showing data from/, undefined, { timeout: 12_000 });
    expect(indicator).toBeTruthy();
    expect(screen.getByText("Blender Setup")).toBeTruthy();          // data still there
    expect(screen.queryByText(/Could not load initiatives/)).toBeNull(); // no error takeover
    fail = false;
    await waitFor(() => expect(screen.getByTestId("live-indicator").textContent).toMatch(/^Live/), { timeout: 12_000 });
  }, 30_000);

  it("lists every task type, marked by type, split into running and finished", async () => {
    server.use(http.get("/v1/console/initiatives", () => HttpResponse.json(BOARD)));
    renderIt();
    // Running tab: ACS run + stale forge run, typed; initiative tasks are in the cards, not duplicated in the table.
    const acs = await screen.findByTestId("utask-acs:acs-9");
    expect(within(acs).getByTestId("type-badge").textContent).toBe("ACS");
    const forge = screen.getByTestId("utask-forge:f-old");
    expect(within(forge).getByText("Stale")).toBeTruthy();
    expect(within(forge).getByText(/no sign of life for 876 h/)).toBeTruthy();
    expect(screen.queryByTestId("utask-initiative:loop-a/1")).toBeNull();
    expect(screen.getByRole("tab", { name: "Running (3 · 1 stale)" })).toBeTruthy(); // stale counted apart
    // Finished tab: chat tasks typed, newest first; initiative tasks included.
    fireEvent.click(screen.getByRole("tab", { name: /Finished \(3\)/ }));
    const rows = screen.getAllByTestId(/^utask-/).map((r) => r.getAttribute("data-testid"));
    expect(rows).toEqual(["utask-chat:d1", "utask-chat:w1", "utask-initiative:loop-b/p0"]);
    expect(within(screen.getByTestId("utask-chat:d1")).getByTestId("type-badge").textContent).toBe("Chat");
    expect(within(screen.getByTestId("utask-chat:d1")).getByText("29s")).toBeTruthy();
    // A source that is empty for a reason says why.
    expect(screen.getByText(/workflow plugin does not load/)).toBeTruthy();
  });

  it("filters by type through the API", async () => {
    server.use(http.get("/v1/console/initiatives", () => HttpResponse.json(BOARD)));
    renderIt();
    await screen.findByTestId("utask-acs:acs-9");
    fireEvent.click(screen.getByTestId("type-chip-forge"));
    await waitFor(() => expect(tasksRequests).toContain("?types=forge&finished_limit=100"));
    await waitFor(() => expect(screen.queryByTestId("utask-acs:acs-9")).toBeNull());
    expect(screen.getByTestId("utask-forge:f-old")).toBeTruthy();
    expect(screen.queryByTestId("run-loop-a")).toBeNull(); // initiatives filtered out too
    fireEvent.click(screen.getByRole("button", { name: "All types" }));
    expect(await screen.findByTestId("utask-acs:acs-9")).toBeTruthy();
  });

  it("PATCHes a status change with the CSRF token", async () => {
    let seen: { body: unknown; csrf: string | null } | null = null;
    server.use(
      http.get("/v1/console/initiatives", () => HttpResponse.json(BOARD)),
      http.patch("/v1/console/initiatives/loop-a/tasks/2", async ({ request }) => {
        seen = { body: await request.json(), csrf: request.headers.get("X-CSRF-Token") };
        return HttpResponse.json(BOARD);
      }),
    );
    renderIt();
    const sel = await screen.findByLabelText("YAML + TTS status");
    fireEvent.change(sel, { target: { value: "done" } });
    await waitFor(() => expect(seen).not.toBeNull());
    expect(seen).toEqual({ body: { status: "done" }, csrf: "csrf-1" });
  });

  it("shows the empty state for a missing file, never sample data", async () => {
    server.use(http.get("/v1/console/initiatives", () => HttpResponse.json({
      ...BOARD, revision: null, source: null, initiatives: [],
      verification: { last_at: null, running: false, stale_tasks: 0, claim_conflicts: 0, stale_after_s: 7200 },
      totals: { pending: 0, running: 0, done: 0, blocked: 0, total: 0, overdue: 0, initiatives_blocked: 0, runs_active: 0, runs_finished: 0 },
    })));
    renderIt();
    // No initiatives file: the board says so, but every other task type still shows.
    expect(await screen.findByText(/No initiatives on this install/)).toBeTruthy();
    expect(await screen.findByText("spotify_chart_insights")).toBeTruthy();
  });

  it("says so on a build without the route", async () => {
    server.use(http.get("/v1/console/initiatives", () => new HttpResponse(null, { status: 404 })));
    renderIt();
    expect(await screen.findByText("Initiatives are not available on this build.")).toBeTruthy();
  });
});
