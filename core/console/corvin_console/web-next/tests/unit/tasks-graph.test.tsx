/**
 * Tasks panel — Graph view (pages/tasks/graph-layout.ts + graph-view.tsx).
 *
 * Layout: prerequisites sit left of what waits for them, children right of
 * their parent; a cycle across the two edge kinds is broken, not looped on; a
 * wide layer wraps; positions depend on structure only, so a status poll never
 * moves a node. State: running (a linked run runs now) > blocked > waiting >
 * in progress > ready. Page: the Graph tab renders every item as a node with
 * its state, and a click opens the drawer.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import type { Item } from "@/lib/api/task-tracking";
import {
  MAX_PER_COLUMN, NODE_W, externalIds, layoutGraph, nodeState, scopeItems, stateCounts,
} from "@/pages/tasks/graph-layout";

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ session: { csrf_token: "csrf-1" } }) }));

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

const x = (l: ReturnType<typeof layoutGraph>, id: string) => l.nodes.find((n) => n.id === id)!.x;

describe("graph layout", () => {
  it("puts prerequisites left of dependents and children right of their parent", () => {
    const l = layoutGraph([
      item("ini", { kind: "initiative", sort_key: 1 }),
      item("a", { parent_id: "ini", sort_key: 2 }),
      item("b", { parent_id: "ini", sort_key: 3, depends_on: ["a"] }),
      item("c", { parent_id: "ini", sort_key: 4, depends_on: ["b"] }),
    ]);
    expect(x(l, "ini")).toBeLessThan(x(l, "a"));
    expect(x(l, "a")).toBeLessThan(x(l, "b"));
    expect(x(l, "b")).toBeLessThan(x(l, "c"));
    expect(l.edges.filter((e) => e.kind === "dependency").map((e) => e.id)).toEqual(["d:a>b", "d:b>c"]);
  });

  it("breaks a cycle across hierarchy and dependency instead of hanging", () => {
    // parent depends on its own child: hierarchy p→c plus dependency c→p
    const l = layoutGraph([item("p", { kind: "initiative", depends_on: ["c"] }), item("c", { parent_id: "p" })]);
    expect(l.nodes).toHaveLength(2);
  });

  it("wraps a wide layer into sub-columns", () => {
    const kids = Array.from({ length: MAX_PER_COLUMN * 2 + 1 }, (_, i) => item(`k${i}`, { parent_id: "ini", sort_key: i + 2 }));
    const l = layoutGraph([item("ini", { kind: "initiative", sort_key: 1 }), ...kids]);
    const cols = new Set(l.nodes.filter((n) => n.id !== "ini").map((n) => n.x));
    expect(cols.size).toBe(3);
    expect(Math.min(...cols)).toBeGreaterThanOrEqual(NODE_W);
  });

  it("does not move a node when only its status changes", () => {
    const base = [item("ini", { kind: "initiative" }), item("a", { parent_id: "ini" }), item("b", { parent_id: "ini" })];
    const before = layoutGraph(base);
    const after = layoutGraph(base.map((i) => (i.id === "a" ? { ...i, status: "complete" as const, running_runs: 1 } : i)));
    expect(after.nodes).toEqual(before.nodes);
    expect(after.key).toBe(before.key);
  });

  it("derives where each item stands", () => {
    expect(nodeState(item("r", { status: "in_progress", running_runs: 1 }))).toBe("running");
    expect(nodeState(item("b", { status: "blocked" }))).toBe("blocked");
    expect(nodeState(item("w", { waiting_on: ["x"] }))).toBe("waiting");
    expect(nodeState(item("p", { status: "in_progress" }))).toBe("active");
    expect(nodeState(item("o"))).toBe("ready");
    expect(nodeState(item("d", { status: "complete" }))).toBe("done");
    // a run working on a completed item right now is still where we stand
    expect(nodeState(item("dr", { status: "complete", running_runs: 1 }))).toBe("running");
    const counts = stateCounts([item("ini", { kind: "initiative" }), item("o"), item("p", { status: "in_progress" })],
      (i) => i.kind === "initiative");
    expect(counts.ready).toBe(1);  // the container does not count
    expect(counts.active).toBe(1);
  });

  it("scopes to one initiative and hides closed leaves but keeps containers with open work", () => {
    const items = [
      item("i1", { kind: "initiative", status: "complete" }), item("e", { kind: "epic", parent_id: "i1", status: "complete" }),
      item("t1", { parent_id: "e", status: "complete" }), item("t2", { parent_id: "e" }),
      item("i2", { kind: "initiative" }), item("u", { parent_id: "i2" }),
    ];
    expect(scopeItems(items, "i1", false).map((i) => i.id).sort()).toEqual(["e", "i1", "t1", "t2"]);
    expect(scopeItems(items, "i1", true).map((i) => i.id).sort()).toEqual(["e", "i1", "t2"]);
    expect(scopeItems(items, "all", false)).toHaveLength(6);
  });

  it("brings an outside prerequisite into a scope, marked as external", () => {
    const items = [
      item("a", { kind: "initiative" }), item("gate", { parent_id: "a", category: "gate" }),
      item("b", { kind: "initiative", status: "blocked", depends_on: ["gate"], waiting_on: ["gate"] }), item("s", { parent_id: "b" }),
    ];
    expect(scopeItems(items, "b", false).map((i) => i.id).sort()).toEqual(["b", "gate", "s"]);
    expect([...externalIds(items, "b")]).toEqual(["gate"]);
    const l = layoutGraph(scopeItems(items, "b", false));
    expect(l.edges.map((e) => e.id)).toContain("d:gate>b");
    expect(x(l, "gate")).toBeLessThan(x(l, "b"));
  });
});

describe("Graph tab", () => {
  // React Flow constructs ResizeObservers with `new`; the global setup mock is an arrow (not constructible).
  class RO { observe() {} unobserve() {} disconnect() {} }
  const prevRO = globalThis.ResizeObserver;
  beforeEach(() => { globalThis.ResizeObserver = RO as unknown as typeof ResizeObserver; });
  afterEach(() => { cleanup(); globalThis.ResizeObserver = prevRO; });

  it("renders each item as a node with its state and opens the drawer on click", { timeout: 20000 }, async () => {
    const NOW = "2026-09-24T12:00:00Z";
    const items = [
      item("ini", { kind: "initiative", title: "Engineering (from git)", child_ids: ["run", "wait", "done"], status: "in_progress", sort_key: 1 }),
      item("run", { parent_id: "ini", title: "A2A pairing", status: "in_progress", running_runs: 1, live_runs: 1,
        live_run_titles: ["Agent session: A2A pairing"], sort_key: 2 }),
      item("done", { parent_id: "ini", title: "Audit recovery", status: "complete", sort_key: 3 }),
      item("wait", { parent_id: "ini", title: "Release", depends_on: ["run"], waiting_on: ["run"], sort_key: 4 }),
    ];
    server.use(
      http.get("/v1/console/task-tracking/items", () => HttpResponse.json({
        server_time: NOW, items, import_available: false,
        summary: { open: 1, in_progress: 1, blocked: 0, complete: 1, archived: 0, total: 3, overdue: 0,
          approvals_pending: 0, done_7d: 1, initiatives_active: 1 },
      })),
      http.get("/v1/console/initiatives/tasks", () => HttpResponse.json({
        server_time: NOW, scan_ms: 1, types: [], active: [], finished: [], finished_total: 0,
        totals: { active: 0, running: 0, stale: 0, finished_24h: 0, failed_24h: 0, all: 0 },
      })),
      http.get("/v1/console/task-tracking/items/:id", ({ params }) => HttpResponse.json({
        server_time: NOW, item: items.find((i) => i.id === params.id), ancestors: [], children: [], depends_on: [],
        required_by: [], runs: [], history: [],
      })),
    );
    const { default: TasksPage } = await import("@/pages/tasks");
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/app/initiatives?view=graph"]}><TasksPage /></MemoryRouter>
      </QueryClientProvider>,
    );
    const graph = await screen.findByTestId("graph-view", {}, { timeout: 5000 });
    const runNode = await within(graph).findByTestId("graph-node-run", {}, { timeout: 5000 });
    expect(runNode.getAttribute("data-state")).toBe("running");
    expect(within(runNode).getByText("1 running")).toBeTruthy();
    expect(within(graph).getByTestId("graph-node-wait").getAttribute("data-state")).toBe("waiting");
    expect(within(graph).getByTestId("graph-node-done").getAttribute("data-state")).toBe("done");
    expect(within(screen.getByTestId("graph-count-running")).getByText("1")).toBeTruthy();
    fireEvent.click(runNode);
    expect(await screen.findByDisplayValue("A2A pairing", {}, { timeout: 5000 })).toBeTruthy();  // drawer title field
  });
});
