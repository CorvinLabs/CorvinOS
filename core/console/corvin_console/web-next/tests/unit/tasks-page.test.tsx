/**
 * Tasks panel (Task-Tracking SSOT) against MSW, plus the pure encodings.
 *
 * Page: the tree renders the hierarchy with rollups; a KPI tile filters; the
 * board puts work items (never containers) in status columns; the timeline
 * degrades to a message below two dated items; the drawer PATCHes with the
 * version it read + CSRF and says so on a 409; an empty store offers the
 * initiatives.json import instead of sample data; a 404 build says so.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ session: { csrf_token: "csrf-1" } }) }));

import TasksPage from "@/pages/tasks";
import { CreateDialog } from "@/pages/tasks/create-dialog";
import {
  EMPTY_FILTERS, boardColumns, buildTimeline, buildTree, deadlineText, displayProgress, filtersFromQuery,
  filtersToQuery, matches,
} from "@/pages/tasks/encodings";
import type { Item } from "@/lib/api/task-tracking";

const NOW = "2026-09-24T12:00:00Z";

function item(id: string, over: Partial<Item> = {}): Item {
  return {
    id, kind: "task", parent_id: null, title: id, description: null, status: "open", status_reason: null,
    status_changed_at: null, priority: "medium", owner: null, assignee: null, start_at: null, deadline: null,
    progress: null, work_estimate: null, work_actual: null, target_milestone: null, category: null, labels: [],
    approval_state: "none", external_ref: null, sort_key: 0, created_at: "2026-09-20T00:00:00Z", created_by: "operator",
    updated_at: "2026-09-20T00:00:00Z", completed_at: null, deleted_at: null, version: 1, overdue: false,
    depends_on: [], waiting_on: [], child_ids: [], run_count: 0, rollup: { progress: 0, descendants: 0, counts: {}, overdue: 0 },
    evidence: null, claim_conflict: false, ...over,
  };
}

const ITEMS: Item[] = [
  item("ini", { kind: "initiative", title: "Loop B — Phase 9 fixes", child_ids: ["epic"], start_at: "2026-09-22T00:00:00Z",
    deadline: "2026-09-26T08:00:00Z", status: "in_progress",
    rollup: { progress: 50, descendants: 3, counts: { complete: 1, in_progress: 1, open: 1 }, overdue: 1 } }),
  item("epic", { kind: "epic", parent_id: "ini", title: "P0 — critical", child_ids: ["a", "b"], status: "in_progress",
    rollup: { progress: 50, descendants: 2, counts: { complete: 1, open: 1 }, overdue: 1 } }),
  item("a", { parent_id: "epic", title: "Audit backend wiring", status: "complete", priority: "critical",
    completed_at: "2026-09-23T10:00:00Z", deadline: "2026-09-24T18:00:00Z" }),
  item("b", { parent_id: "epic", title: "Privilege escalation patches", status: "open", priority: "high",
    deadline: "2026-09-23T18:00:00Z", overdue: true, assignee: "claude" }),
  item("gate", { parent_id: "ini", title: "Blocker Gate", category: "gate", approval_state: "pending",
    deadline: "2026-09-26T08:00:00Z", priority: "high" }),
];
const SUMMARY = { open: 2, in_progress: 0, blocked: 0, complete: 1, archived: 0, total: 3, overdue: 1,
  approvals_pending: 1, done_7d: 1, initiatives_active: 1 };
const LIST = { server_time: NOW, items: ITEMS, summary: SUMMARY, import_available: false };
const DETAIL = (it: Item) => ({
  server_time: NOW, item: it, ancestors: [], children: [], depends_on: [], required_by: [], runs: [],
  history: [{ event_id: "e1", event_type: "task_item.created", ts: "2026-09-20T00:00:00Z", actor: "importer",
    delta: { imported_from: "initiatives.json#loop-b/task/b" }, chain_hash: "abc" }],
});

let patches: { body: unknown; csrf: string | null }[] = [];
beforeEach(() => {
  patches = [];
  server.use(
    http.get("/v1/console/task-tracking/items", () => HttpResponse.json(LIST)),
    http.get("/v1/console/task-tracking/items/:id", ({ params }) => {
      const it = ITEMS.find((x) => x.id === params.id);
      return it ? HttpResponse.json(DETAIL(it)) : HttpResponse.json({ detail: "nope" }, { status: 404 });
    }),
    http.patch("/v1/console/task-tracking/items/:id", async ({ request }) => {
      patches.push({ body: await request.json(), csrf: request.headers.get("X-CSRF-Token") });
      return HttpResponse.json({ detail: { message: "item changed since it was read", current: ITEMS[3] } }, { status: 409 });
    }),
  );
});
afterEach(() => cleanup());

function renderIt(path = "/app/initiatives") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={qc}><TasksPage /></QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("tasks encodings", () => {
  it("keeps the path to a filter hit and marks context rows", () => {
    const f = { ...EMPTY_FILTERS, q: "privilege" };
    const rows = buildTree(ITEMS, f, new Set());
    expect(rows.map((r) => [r.item.id, r.depth, r.matched])).toEqual([
      ["ini", 0, false], ["epic", 1, false], ["b", 2, true],
    ]);
    // collapsing hides a subtree only while no filter is active
    expect(buildTree(ITEMS, EMPTY_FILTERS, new Set(["epic"])).map((r) => r.item.id)).toEqual(["ini", "epic", "gate"]);
  });

  it("shows the rollup for a parent and its own value for a leaf", () => {
    expect(displayProgress(ITEMS[1])).toBe(50);
    expect(displayProgress(ITEMS[2])).toBe(100);
    expect(displayProgress(item("x", { progress: 30 }))).toBe(30);
  });

  it("puts work items, never containers, on the board — overdue and critical first", () => {
    const cols = boardColumns([...ITEMS, item("c", { priority: "critical" })], EMPTY_FILTERS);
    expect(cols.open.map((i) => i.id)).toEqual(["b", "c", "gate"]);
    expect(cols.in_progress).toEqual([]);  // initiative + epic are containers
  });

  it("degrades the timeline below two dated items and places points for gates", () => {
    const rows = buildTree(ITEMS, EMPTY_FILTERS, new Set());
    const tl = buildTimeline(rows, Date.parse(NOW));
    expect(tl.domain).not.toBeNull();
    const gate = tl.rows.find((r) => r.item.id === "gate")!;
    expect(gate.point).not.toBeNull();
    expect(gate.x0).toBeNull();
    const ini = tl.rows.find((r) => r.item.id === "ini")!;
    expect(ini.x0!).toBeLessThan(ini.x1!);
    expect(buildTimeline(rows.slice(0, 1), Date.parse(NOW)).domain).toBeNull();
  });

  it("round-trips filters through the URL and hides archived by default", () => {
    const f = { ...EMPTY_FILTERS, statuses: ["blocked" as const], priorities: ["high" as const], overdueOnly: true, showClosed: false };
    expect(filtersFromQuery(new URLSearchParams(filtersToQuery(f)))).toEqual(f);
    expect(filtersFromQuery(new URLSearchParams("status=bogus,open"))).toMatchObject({ statuses: ["open"] });
    expect(matches(item("z", { status: "archived" }), EMPTY_FILTERS)).toBe(false);
  });

  it("writes deadlines as compact en-US text", () => {
    const now = Date.parse(NOW);
    expect(deadlineText("2026-09-27T12:00:00Z", now, false)).toBe("due in 3d");
    expect(deadlineText("2026-09-23T12:00:00Z", now, false)).toBe("1d overdue");
    expect(deadlineText("2026-09-24", now, false)).toBe("due in 11h");  // a bare date means the end of that day
    expect(deadlineText(null, now, false)).toBeNull();
  });
});

describe("Tasks page", () => {
  it("renders the tree with rollups, KPIs and the decision banner", async () => {
    renderIt();
    expect(await screen.findByText("Loop B — Phase 9 fixes")).toBeTruthy();
    expect(screen.getByTestId("tree-view")).toBeTruthy();
    expect(screen.getByText("Privilege escalation patches")).toBeTruthy();
    expect(within(screen.getByTestId("kpi-overdue")).getByText("1")).toBeTruthy();
    expect(screen.getByText(/1 gate is waiting for a go \/ no-go decision/)).toBeTruthy();
    expect(screen.getByText("1/3 done")).toBeTruthy();
    expect(screen.getByText(/^\d+[dhm] overdue$/)).toBeTruthy();  // clock-relative
  });

  it("filters from a KPI tile and keeps the path to the hit", async () => {
    renderIt();
    fireEvent.click(await screen.findByTestId("kpi-overdue"));
    await waitFor(() => expect(screen.queryByText("Blocker Gate")).toBeNull());
    expect(screen.getByText("Privilege escalation patches")).toBeTruthy();
    expect(screen.getByText("P0 — critical")).toBeTruthy();  // context row
  });

  it("shows work items in board columns", async () => {
    renderIt("/app/initiatives?view=board");
    const board = await screen.findByTestId("board-view");
    const complete = within(board).getByRole("region", { name: "Complete" });
    expect(within(complete).getByText("Audit backend wiring")).toBeTruthy();
    expect(within(board).queryByText("P0 — critical")).toBeNull();
  });

  it("opens the drawer, PATCHes with version + CSRF and explains a 409", async () => {
    renderIt("/app/initiatives?item=b");
    const drawer = await screen.findByTestId("detail-drawer");
    await within(drawer).findByText("Imported batch", {}, { timeout: 2000 }).catch(() => null);
    fireEvent.change(within(drawer).getByLabelText("Status"), { target: { value: "in_progress" } });
    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).toEqual({ body: { status: "in_progress", version: 1 }, csrf: "csrf-1" });
    expect(await within(drawer).findByText(/changed elsewhere/)).toBeTruthy();
  });

  it("offers the import on an empty store instead of sample data", async () => {
    let imported = 0;
    server.use(
      http.get("/v1/console/task-tracking/items", () =>
        HttpResponse.json({ ...LIST, items: [], summary: { ...SUMMARY, total: 0 }, import_available: true })),
      http.post("/v1/console/task-tracking/import", () => { imported += 1; return HttpResponse.json({ inserted: 5, skipped: 0, planned: 5 }); }),
    );
    renderIt();
    const empty = await screen.findByTestId("tasks-empty");
    fireEvent.click(within(empty).getByRole("button", { name: /Import from initiatives.json/ }));
    await waitFor(() => expect(imported).toBe(1));
    expect(screen.queryByTestId("tree-view")).toBeNull();
  });

  it("says so on a build without the task store", async () => {
    server.use(http.get("/v1/console/task-tracking/items", () => HttpResponse.json({ detail: "Not Found" }, { status: 404 })));
    renderIt();
    expect(await screen.findByText("The task store is not available on this build.")).toBeTruthy();
  });
});

describe("regressions from the adversarial review", () => {
  it("keeps the space the operator types into the search box", async () => {
    renderIt();
    const box = await screen.findByLabelText("Search tasks");
    fireEvent.change(box, { target: { value: "audit " } });
    expect((screen.getByLabelText("Search tasks") as HTMLInputElement).value).toBe("audit ");
  });

  it("does not PATCH when a field is only focused and left", async () => {
    const withTrailing = { ...ITEMS[3], description: "hello\n" };
    server.use(http.get("/v1/console/task-tracking/items/:id", () => HttpResponse.json(DETAIL(withTrailing))));
    renderIt("/app/initiatives?item=b");
    const drawer = await screen.findByTestId("detail-drawer");
    for (const label of ["Description", "Title", "Assignee", "Deadline", "Estimate hours"]) {
      const el = within(drawer).getByLabelText(label, { selector: "input, textarea" });
      fireEvent.focus(el);
      fireEvent.blur(el);
    }
    await new Promise((r) => setTimeout(r, 50));
    expect(patches).toHaveLength(0);
  });

  it("an emptied date input does not clear the deadline; the explicit button does", async () => {
    renderIt("/app/initiatives?item=b");
    const drawer = await screen.findByTestId("detail-drawer");
    const date = within(drawer).getByLabelText("Deadline", { selector: "input" });
    fireEvent.focus(date);
    fireEvent.change(date, { target: { value: "" } });
    fireEvent.blur(date);
    await new Promise((r) => setTimeout(r, 50));
    expect(patches).toHaveLength(0);
    fireEvent.click(within(drawer).getByRole("button", { name: "Clear deadline" }));
    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0].body).toEqual({ deadline: null, version: 1 });
  });

  it("drops a parent that the new kind cannot have instead of getting stuck", () => {
    const onCreate = vi.fn();
    render(<CreateDialog open onOpenChange={() => {}} items={ITEMS} defaultParent={ITEMS[0]} busy={false} error={null} onCreate={onCreate} />);
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "initiative" } });
    fireEvent.change(screen.getByLabelText("New item title"), { target: { value: "New loop" } });
    const create = screen.getByRole("button", { name: "Create" }) as HTMLButtonElement;
    expect(create.disabled).toBe(false);
    fireEvent.click(create);
    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ kind: "initiative", parent_id: null }));
  });

  it("does not let an outlier date explode the timeline ticks", () => {
    const rows = buildTree([item("p", { deadline: "2026-09-25" }), item("q", { deadline: "9999-01-01" })], EMPTY_FILTERS, new Set());
    expect(buildTimeline(rows, Date.parse(NOW)).ticks.length).toBeLessThanOrEqual(8);
  });
});

