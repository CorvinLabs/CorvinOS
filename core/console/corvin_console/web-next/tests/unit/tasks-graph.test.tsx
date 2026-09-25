/**
 * Tasks panel — Graph view: one breakdown graph per initiative
 * (pages/tasks/graph-layout.ts + graph-view.tsx).
 *
 * Layout: the initiative on top, each level of its breakdown below, parents
 * centred over their parts in stored order; many leaf parts become a framed
 * grid with ONE breakdown edge onto the frame; outside prerequisites sit left;
 * positions depend on structure only, so a status poll never moves a node.
 * State: running (a linked run runs now) > blocked > waiting > in progress >
 * ready. Page: a picker lists every initiative; choosing one draws its
 * breakdown with each item's state; a click opens the drawer.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import type { Item } from "@/lib/api/task-tracking";
import {
  GROUP_AT, LOOSE_SCOPE, NODE_H, breakdownStats, externalIds, graphChoices, nodeState, scopeItems, stateCounts,
  treeLayout,
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

const pos = (l: ReturnType<typeof treeLayout>, id: string) => l.nodes.find((n) => n.id === id)!;

describe("breakdown layout", () => {
  const plan = [
    item("ini", { kind: "initiative", sort_key: 1 }),
    item("e1", { kind: "epic", parent_id: "ini", sort_key: 2 }),
    item("e2", { kind: "epic", parent_id: "ini", sort_key: 3 }),
    item("t1", { parent_id: "e1", sort_key: 4 }),
    item("t2", { parent_id: "e1", sort_key: 5 }),
    item("t3", { parent_id: "e2", sort_key: 6, depends_on: ["t2"] }),
    item("s1", { kind: "subtask", parent_id: "t3", sort_key: 7 }),
  ];

  it("puts the initiative on top and each breakdown level below, parents centred over their parts", () => {
    const l = treeLayout(plan);
    expect(pos(l, "ini").depth).toBe(0);
    expect(pos(l, "e1").depth).toBe(1);
    expect(pos(l, "s1").depth).toBe(3);
    expect(pos(l, "ini").y).toBeLessThan(pos(l, "e1").y);
    expect(pos(l, "e1").y).toBeLessThan(pos(l, "t1").y);
    expect(pos(l, "t3").y).toBeLessThan(pos(l, "s1").y);
    expect(pos(l, "e1").x).toBeLessThan(pos(l, "e2").x);           // stored order
    expect(pos(l, "t1").x).toBeLessThan(pos(l, "t2").x);
    expect(pos(l, "e1").x).toBe((pos(l, "t1").x + pos(l, "t2").x) / 2);  // centred over its parts
    const deps = l.edges.filter((e) => e.kind === "dependency").map((e) => e.id);
    expect(deps).toEqual(["d:t2>t3"]);                              // across the breakdown
    expect(l.frames).toEqual([]);
  });

  it("frames many leaf parts as a grid with one breakdown edge onto the frame", () => {
    const n = GROUP_AT * 4;
    const kids = Array.from({ length: n }, (_, i) => item(`k${i}`, { parent_id: "ini", sort_key: i + 2 }));
    const l = treeLayout([item("ini", { kind: "initiative", sort_key: 1 }), ...kids]);
    expect(l.frames).toHaveLength(1);
    expect(l.frames[0].count).toBe(n);
    expect(l.frames[0].groupKey).toBe("task");
    const hier = l.edges.filter((e) => e.kind === "hierarchy");
    expect(hier.map((e) => e.target)).toEqual(["grid:ini:task"]);
    const rows = new Set(kids.map((k) => pos(l, k.id).y));
    expect(rows.size).toBeGreaterThan(1);
    for (const k of kids) expect(pos(l, k.id).y).toBeGreaterThan(pos(l, "ini").y + NODE_H);
  });

  it("groups a mixed breakdown by type and keeps parts with their own breakdown as subtrees", () => {
    // Loop A's shape: tasks, preconditions, one checkpoint, one gate with criteria.
    const parts = [
      ...[1, 2, 3].map((n) => item(`t${n}`, { parent_id: "ini", sort_key: n + 1 })),
      ...[1, 2].map((n) => item(`p${n}`, { parent_id: "ini", category: "precondition", sort_key: n + 10 })),
      item("cp", { parent_id: "ini", category: "checkpoint", sort_key: 20 }),
      item("gate", { parent_id: "ini", category: "gate", sort_key: 21 }),
      item("c1", { kind: "subtask", parent_id: "gate", category: "criterion", sort_key: 22 }),
      item("c2", { kind: "subtask", parent_id: "gate", category: "criterion", sort_key: 23 }),
    ];
    const l = treeLayout([item("ini", { kind: "initiative", sort_key: 1 }), ...parts]);
    expect(l.frames.map((f) => `${f.groupKey}:${f.count}`)).toEqual(["task:3", "precondition:2"]);
    const hierTargets = l.edges.filter((e) => e.kind === "hierarchy").map((e) => e.target).sort();
    // the lone checkpoint and the gate subtree keep their own edges; the gate's criteria hang below it
    expect(hierTargets).toEqual(["c1", "c2", "cp", "gate", "grid:ini:precondition", "grid:ini:task"]);
    expect(pos(l, "c1").y).toBeGreaterThan(pos(l, "gate").y);
    // groups sit in the order of their first part: tasks, preconditions, checkpoint, gate
    const fx = Object.fromEntries(l.frames.map((f) => [f.groupKey, f.x]));
    expect(fx.task).toBeLessThan(fx.precondition);
    expect(fx.precondition).toBeLessThan(pos(l, "cp").x);
    expect(pos(l, "cp").x).toBeLessThan(pos(l, "gate").x);
  });

  it("does not move a node when only its status changes", () => {
    const before = treeLayout(plan);
    const after = treeLayout(plan.map((i) => (i.id === "t1" ? { ...i, status: "complete" as const, running_runs: 1 } : i)));
    expect(after.nodes).toEqual(before.nodes);
    expect(after.key).toBe(before.key);
  });

  it("describes the shape of the breakdown", () => {
    const l = treeLayout(plan);
    const st = breakdownStats(l, new Map(plan.map((i) => [i.id, i])));
    expect(st.levels).toBe(4);
    expect(st.items).toBe(7);
    expect(Object.fromEntries(st.perKind)).toEqual({ epic: 2, task: 3, subtask: 1 });
    expect(st.dependencies).toBe(1);
  });

  it("offers one graph per top-level initiative, plus loose items as one more", () => {
    const items = [item("i1", { kind: "initiative" }), item("t", { parent_id: "i1" }), item("loose")];
    expect(graphChoices(items).map((c) => c.id)).toEqual(["i1", LOOSE_SCOPE]);
    expect(scopeItems(items, LOOSE_SCOPE, false).map((i) => i.id)).toEqual(["loose"]);
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
    const ext = externalIds(items, "b");
    expect([...ext]).toEqual(["gate"]);
    const l = treeLayout(scopeItems(items, "b", false), ext);
    expect(l.edges.map((e) => e.id)).toContain("d:gate>b");
    expect(pos(l, "gate").depth).toBe(-1);
    expect(pos(l, "gate").x).toBeLessThan(pos(l, "b").x);
    expect(pos(l, "b").depth).toBe(0);  // the gate's parent is not drawn as a second root
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
      item("loopb", { kind: "initiative", title: "Loop B — Phase 9 fixes", child_ids: ["p0"], status: "in_progress", sort_key: 5,
        updated_at: "2026-09-19T00:00:00Z" }),
      item("p0", { kind: "epic", parent_id: "loopb", title: "P0 — 13 critical", child_ids: ["fix"], sort_key: 6 }),
      item("fix", { parent_id: "p0", title: "Audit backend wiring", status: "in_progress", sort_key: 7 }),
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
    try { localStorage.removeItem("corvin.tasks.graph.scope"); } catch { /* no storage */ }
    const graph = await screen.findByTestId("graph-view", {}, { timeout: 5000 });
    // The picker lists both initiatives; the one with a running session opens first.
    expect(within(graph).getByTestId("graph-pick-ini").getAttribute("aria-selected")).toBe("true");
    expect(within(graph).getByTestId("graph-pick-loopb")).toBeTruthy();
    expect(within(graph).queryByTestId("graph-node-fix")).toBeNull();
    expect(within(graph).getByTestId("graph-breakdown").textContent).toMatch(/broken down into 3 items over 1 level: 3 tasks · 1 dependency$/);
    const runNode = await within(graph).findByTestId("graph-node-run", {}, { timeout: 5000 });
    expect(runNode.getAttribute("data-state")).toBe("running");
    expect(within(runNode).getByText("1 running")).toBeTruthy();
    expect(within(graph).getByTestId("graph-node-wait").getAttribute("data-state")).toBe("waiting");
    expect(within(graph).getByTestId("graph-node-done").getAttribute("data-state")).toBe("done");
    expect(within(screen.getByTestId("graph-count-running")).getByText("1")).toBeTruthy();
    fireEvent.click(runNode);
    expect(await screen.findByDisplayValue("A2A pairing", {}, { timeout: 5000 })).toBeTruthy();  // drawer title field
    // Switch to Loop B: its breakdown (initiative → epic → task) replaces the first graph.
    fireEvent.click(within(graph).getByTestId("graph-pick-loopb"));
    expect(await within(graph).findByTestId("graph-node-fix", {}, { timeout: 5000 })).toBeTruthy();
    expect(within(graph).queryByTestId("graph-node-run")).toBeNull();
    expect(within(graph).getByTestId("graph-breakdown").textContent).toMatch(/broken down into 2 items over 2 levels: 1 epic · 1 task$/);
  });
});
