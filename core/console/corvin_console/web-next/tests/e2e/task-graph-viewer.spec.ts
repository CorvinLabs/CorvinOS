/**
 * E2E tests for the Task Graph page (ADR-0400).
 *
 * These drive the real console through the browser: navigate by clicking the
 * sidebar entry, let the SPA router mount the page, and assert on what the
 * d3 render actually produced in the DOM.
 *
 * The graph endpoint is stubbed with the REAL wire shape so the suite is
 * deterministic and does not depend on checkpoints existing on the machine —
 * `nodes` is a LIST (TaskGraphResponse.nodes: List[NodeResponse]), not a map.
 * That distinction is the regression this file exists to guard: the viewer
 * indexes nodes by id, and feeding it a list made Object.entries() produce
 * "0", "1", "2" as ids, so every edge endpoint failed to resolve and d3's
 * forceLink aborted the render with `node not found: undefined`.
 *
 * One test deliberately talks to the live backend instead, to catch the
 * opposite failure: a frontend that agrees with a stub the server no longer
 * matches.
 */

import { test, expect, Page } from "@playwright/test";

const TASK_ID = "e2e-task-graph";

const TASK_LIST = {
  tasks: [
    {
      task_id: TASK_ID,
      checkpoint_id: "cp-e2e-0001",
      iteration_num: 2,
      timestamp: "2026-08-24T21:31:36.000000",
      phase: "implementation",
      goal: "Render a task graph in the console",
      checkpoint_count: 1,
    },
  ],
  count: 1,
};

/** Mirrors TaskGraphResponse: nodes as a list, edges as from_id/to_id pairs. */
const GRAPH = {
  task_id: TASK_ID,
  created_at: "2026-08-24T21:31:36.000000",
  nodes: [
    { id: "cp-e2e-0001", type: "checkpoint", timestamp: "2026-08-24T21:31:36.000000", data: { phase: "implementation" } },
    { id: "decision_0", type: "decision", timestamp: "2026-08-24T21:31:36.000000", data: { choice: "wire-then-verify" } },
    { id: "decision_1", type: "decision", timestamp: "2026-08-24T21:31:37.000000", data: { choice: "typecheck-first" } },
    { id: "subgoal_0", type: "subgoal", timestamp: "2026-08-24T21:31:38.000000", data: { status: "done" } },
    { id: "context_0", type: "context", timestamp: "2026-08-24T21:31:39.000000", data: { reduction_pct: 88 } },
  ],
  edges: [
    { from_id: "decision_0", to_id: "decision_1", edge_type: "decision_sequence", label: "decision sequence", metadata: {} },
    { from_id: "decision_1", to_id: "subgoal_0", edge_type: "hard_dependency", label: "produces", metadata: {} },
  ],
  nodes_by_type: {
    checkpoint: ["cp-e2e-0001"],
    decision: ["decision_0", "decision_1"],
    subgoal: ["subgoal_0"],
    context: ["context_0"],
  },
  iterations: {},
  stats: { node_count: 5, edge_count: 2 },
};

async function stubGraphApi(page: Page, graph: unknown = GRAPH, list: unknown = TASK_LIST) {
  await page.route("**/v1/console/api/tasks/graphs", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(list) })
  );
  await page.route("**/v1/console/api/tasks/*/graph", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(graph) })
  );
}

/**
 * Navigate by clicking the sidebar entry rather than page.goto().
 * A direct visit to /console/app/task-graph is bounced to the last chat
 * session by the app's own session-restore, so the click is the honest path
 * AND it proves the nav entry is wired.
 */
async function openTaskGraph(page: Page) {
  await page.goto("/");
  const nav = page.getByRole("link", { name: "Task Graph", exact: true }).first();
  await nav.waitFor({ state: "visible", timeout: 45_000 });
  await nav.click();
  await page.waitForSelector('[data-testid="task-graph-page"]', { timeout: 20_000 });
}

test.describe("Task Graph page", () => {
  test("is reachable from the sidebar", async ({ page }) => {
    await stubGraphApi(page);
    await openTaskGraph(page);

    await expect(page).toHaveURL(/\/app\/task-graph$/);
    await expect(page.getByRole("heading", { name: "Task Graph" })).toBeVisible();
  });

  test("lists tasks and selects the newest one", async ({ page }) => {
    await stubGraphApi(page);
    await openTaskGraph(page);

    const select = page.getByTestId("task-graph-select");
    await expect(select).toBeVisible();
    await expect(select).toHaveValue(TASK_ID);
    await expect(select.locator("option")).toHaveCount(1);
  });

  test("renders every node and edge from the API", async ({ page }) => {
    await stubGraphApi(page);
    await openTaskGraph(page);

    await page.waitForSelector("svg circle.node", { timeout: 20_000 });
    await expect(page.locator("svg circle.node")).toHaveCount(GRAPH.nodes.length);
    await expect(page.locator("svg line.edge")).toHaveCount(GRAPH.edges.length);
  });

  test("resolves edge endpoints against real node ids", async ({ page }) => {
    // Guards the list-vs-map regression: with mis-keyed nodes the edges
    // collapse onto 0,0 because positions[from_id] is undefined.
    await stubGraphApi(page);
    await openTaskGraph(page);
    await page.waitForSelector("svg line.edge", { timeout: 20_000 });

    const coords = await page.locator("svg line.edge").evaluateAll((els) =>
      els.map((el) => ({
        x1: Number(el.getAttribute("x1")),
        y1: Number(el.getAttribute("y1")),
        x2: Number(el.getAttribute("x2")),
        y2: Number(el.getAttribute("y2")),
      }))
    );

    expect(coords.length).toBe(GRAPH.edges.length);
    for (const c of coords) {
      expect(Number.isFinite(c.x1) && Number.isFinite(c.y1)).toBe(true);
      expect(c.x1 !== 0 || c.y1 !== 0).toBe(true);
      expect(c.x2 !== 0 || c.y2 !== 0).toBe(true);
    }
  });

  test("a node click opens the detail modal, ESC closes it", async ({ page }) => {
    await stubGraphApi(page);
    await openTaskGraph(page);
    await page.waitForSelector("svg circle.node", { timeout: 20_000 });

    // SVG paints in document order, so the full-size transparent
    // .graph-background rect must be painted BEFORE the node group — appended
    // after, it covers the whole graph and every node click lands on it
    // instead. Checked structurally because a hit-test at a node's centre
    // depends on the node landing inside the viewport.
    const backgroundIsBehind = await page.evaluate(() => {
      const svg = document.querySelector("svg.task-graph-svg") ?? document.querySelector("svg");
      if (!svg) return null;
      const children = [...svg.children];
      const bg = children.findIndex((el) => el.classList.contains("graph-background"));
      const main = children.findIndex((el) => el.classList.contains("main-group"));
      return bg !== -1 && main !== -1 && bg < main;
    });
    expect(backgroundIsBehind).toBe(true);

    // No force: Playwright refuses a click that another element intercepts,
    // so this also fails if anything covers the node.
    await page.locator("svg circle.node").first().click();

    const modal = page.locator("div.fixed.inset-0.z-50");
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("Node Information");

    await page.keyboard.press("Escape");
    await expect(modal).toHaveCount(0);
  });

  test("shows an empty state when no task has a checkpoint", async ({ page }) => {
    await stubGraphApi(page, GRAPH, { tasks: [], count: 0 });
    await openTaskGraph(page);

    await expect(page.getByTestId("task-graph-empty")).toBeVisible();
    await expect(page.locator("svg circle.node")).toHaveCount(0);
  });

  test("live API: /api/tasks/graphs answers with the documented shape", async ({ request }) => {
    // No stub here on purpose — this is the contract check against the running
    // backend, so a server-side rename cannot pass unnoticed behind the stubs.
    const res = await request.get("/v1/console/api/tasks/graphs");
    expect(res.status()).toBe(200);

    const body = await res.json();
    expect(Array.isArray(body.tasks)).toBe(true);
    expect(typeof body.count).toBe("number");

    if (body.tasks.length > 0) {
      const task = body.tasks[0];
      for (const key of ["task_id", "checkpoint_id", "iteration_num", "timestamp", "phase"]) {
        expect(task).toHaveProperty(key);
      }

      const graphRes = await request.get(
        `/v1/console/api/tasks/${encodeURIComponent(task.task_id)}/graph`
      );
      expect(graphRes.status()).toBe(200);
      const graph = await graphRes.json();

      // The shape the frontend normalizes against.
      expect(Array.isArray(graph.nodes)).toBe(true);
      expect(Array.isArray(graph.edges)).toBe(true);

      // Every edge endpoint must exist as a node, or the graph cannot render.
      const ids = new Set(graph.nodes.map((n: { id: string }) => n.id));
      for (const edge of graph.edges) {
        expect(ids.has(edge.from_id)).toBe(true);
        expect(ids.has(edge.to_id)).toBe(true);
      }
    }
  });
});
