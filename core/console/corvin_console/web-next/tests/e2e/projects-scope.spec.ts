/**
 * Scope of the Tasks board and the Knowledge Graph (T-0360, ADR-2316), on the served bundle with the endpoints
 * stubbed at the network boundary. The stubs answer exactly as the real routes do for ?scope= and ?project=
 * (CorvinOS tests/e2e/test_project_scope_api_e2e.py proves the real answers). What this proves:
 *   - the Tasks board asks for the chosen knowledge base and shows only its cards; the scope survives a view change;
 *   - the Knowledge Graph "Corvin + projects" view joins both knowledge bases, qualifies a project's labels, and
 *     two equal local ids (T-0001 in each) stay distinct.
 * The card move (/kb-transition) is the existing board path; its server side is proven in test_project_kb_writes_e2e.py.
 */
import { test, expect, type Page } from "@playwright/test";

const PROJECTS = { projects_root: "/home/u/projects", projects: [
  { id: "alpha", path: "/home/u/projects/alpha", template: "video-engineering@1", state: "ok", title: "Plumbline", kb: true, active: ["core", "kb"] },
] };

function item(id: string, title: string, project: string | null) {
  return { id, kind: "task", parent_id: null, title, description: null, status: "open", status_reason: null, status_changed_at: null,
    priority: "medium", owner: null, assignee: null, start_at: null, deadline: null, progress: null, work_estimate: null,
    work_actual: null, target_milestone: null, category: "kb", labels: project ? ["kb", `prj-${project}`] : ["kb"], approval_state: "none",
    external_ref: `kb:${id}`, sort_key: 0, created_at: "2026-11-08T00:00:00Z", created_by: "sync:kb", updated_at: "2026-11-08T00:00:00Z",
    completed_at: null, deleted_at: null, deleted_by: null, cascading_delete_id: null, restored_at: null, version: 1, overdue: false,
    depends_on: [], waiting_on: [], child_ids: [], run_count: 0, rollup: { progress: 0, descendants: 0, counts: {}, overdue: 0 },
    evidence: null, claim_conflict: false, live_runs: 0, running_runs: 0, live_run_titles: [], project };
}
const BOUND = item("b-1", "T-0001 · bound task", null);
const ALPHA = item("a-1", "T-0001 · alpha task", "alpha");

async function stub(page: Page) {
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: PROJECTS }));
  await page.route(/\/v1\/console\/task-tracking\/items/, (r) => {
    const scope = new URL(r.request().url()).searchParams.get("scope");
    const items = scope === "project:alpha" ? [ALPHA] : scope === "bound" ? [BOUND] : [BOUND, ALPHA];
    return r.fulfill({ json: { items, server_time: new Date().toISOString(), summary: {}, import_available: false } });
  });
  await page.route(/\/v1\/console\/task-tracking\/kb\/status/, (r) => r.fulfill({ json: { state: "ok", enabled: true, projects: {} } }));
  await page.route(/\/v1\/console\/plugins\/corvin-knowledge\/config/, (r) => r.fulfill({ json: { repo_path: "/k", remote_url: "", auto_sync_on_query: true, consistency_level: "warn" } }));
  await page.route(/\/v1\/console\/plugins\/corvin-knowledge\/graph(\?|$)/, (r) => {
    const project = new URL(r.request().url()).searchParams.get("project");
    const entity = project
      ? { id: "uid-alpha", label: "T-0001", file: "a.md", type: "decision", title: "Project decision", status: "accepted", tags: [] }
      : { id: "uid-bound", label: "T-0001", file: "b.md", type: "decision", title: "Bound decision", status: "accepted", tags: [] };
    return r.fulfill({ json: { entities: [entity], relations: [], duplicate_rows: 0 } });
  });
}

test("the Tasks board shows the chosen knowledge base and keeps the scope when the view changes", async ({ page }) => {
  await stub(page);
  const requests: string[] = [];
  page.on("request", (req) => { if (req.url().includes("/task-tracking/items")) requests.push(new URL(req.url()).search); });
  await page.goto("/console/app/initiatives");
  await expect(page.getByText("alpha task")).toBeVisible();
  await expect(page.getByText("bound task")).toBeVisible();
  await page.getByTestId("scope-select").selectOption("project:alpha");
  await expect(page.getByText("alpha task")).toBeVisible();
  await expect(page.getByText("bound task")).toHaveCount(0);
  await expect(page).toHaveURL(/scope=project%3Aalpha|scope=project:alpha/);
  expect(requests.some((q) => q.includes("scope=project%3Aalpha") || q.includes("scope=project:alpha"))).toBe(true);
  // the scope survives a change of view (setQuery rebuilds the query string)
  await page.getByRole("tab", { name: "Table" }).click();
  await expect(page).toHaveURL(/scope=project/);
  await expect(page.getByText("bound task")).toHaveCount(0);
});

test("the Knowledge Graph 'Corvin + projects' view joins both knowledge bases with qualified labels", async ({ page }) => {
  await stub(page);
  await page.goto("/console/app/corvin-knowledge");
  await page.getByTestId("scope-select").selectOption("both");
  await expect(page).toHaveURL(/scope=both/);
  await page.getByTestId("knowledge-search-input").fill("decision");
  const hits = page.getByTestId("knowledge-search-hits");
  await expect(hits).toContainText("alpha/T-0001");
  await expect(hits).toContainText("T-0001");
  // two equal local ids stay two nodes
  await expect(hits.getByRole("button").filter({ hasText: "alpha/T-0001" })).toHaveCount(1);
  await expect(hits.getByRole("button").filter({ hasText: /^T-0001/ })).toHaveCount(1);
});

test("a direct link to the joined view loads both knowledge bases, even when the project list arrives later", async ({ page }) => {
  await stub(page);
  // the project list answers only after the graph has been asked for: the first render cannot know the projects
  await page.route(/\/v1\/console\/projects$/, async (r) => { await new Promise((res) => setTimeout(res, 600)); return r.fulfill({ json: PROJECTS }); });
  await page.goto("/console/app/corvin-knowledge?scope=both");
  await page.getByTestId("knowledge-search-input").fill("decision");
  await expect(page.getByTestId("knowledge-search-hits")).toContainText("alpha/T-0001");
  await expect(page.getByTestId("knowledge-search-hits")).toContainText("T-0001");
});
