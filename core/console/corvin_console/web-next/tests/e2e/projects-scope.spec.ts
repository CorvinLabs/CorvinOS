/**
 * Scope of the Tasks board and the Knowledge Graph (T-0360, ADR-2316), on the served bundle with the endpoints
 * stubbed at the network boundary. The stubs answer exactly as the real routes do for ?scope= and ?project=
 * (CorvinOS tests/e2e/test_project_scope_api_e2e.py proves the real answers). What this proves:
 *   - the Tasks board asks for the chosen knowledge base and shows only its cards; the scope survives a view change;
 *   - the Knowledge Graph "All knowledge bases" view joins both knowledge bases, qualifies a project's labels, and
 *     two equal local ids (T-0001 in each) stay distinct.
 * The card move (/kb-transition) is the existing board path; its server side is proven in test_project_kb_writes_e2e.py.
 */
import { test, expect, type Page } from "@playwright/test";

const PROJECTS = { projects_root: "/home/u/projects", projects: [
  { id: "alpha", path: "/home/u/projects/alpha", template: "video-engineering@1", state: "ok", title: "Plumbline", kb: true, active: ["core", "kb"] },
  { id: "demo", path: "/home/u/projects/demo", template: "blank@1", state: "ok", kb: false, features: { kb: false }, active: ["core"] },
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
    // the real route counts the summary over the scoped list (test_project_graph_scope_e2e.py)
    const summary = { open: items.length, in_progress: 0, blocked: 0, complete: 0, archived: 0, total: items.length, overdue: 0,
      approvals_pending: 0, done_7d: 0, initiatives_active: 0 };
    return r.fulfill({ json: { items, server_time: new Date().toISOString(), summary, import_available: false } });
  });
  await page.route(/\/v1\/console\/task-tracking\/kb\/status/, (r) => r.fulfill({ json: { state: "ok", enabled: true, projects: {} } }));
  await page.route(/\/v1\/console\/plugins\/corvin-knowledge\/config/, (r) => r.fulfill({ json: { repo_path: "/k", remote_url: "", auto_sync_on_query: true, consistency_level: "warn" } }));
  await page.route(/\/v1\/console\/plugins\/corvin-knowledge\/graph(\?|$)/, (r) => {
    const project = new URL(r.request().url()).searchParams.get("project");
    const entity = project
      ? { id: "uid-alpha", label: "T-0001", file: "a.md", type: "decision", title: "Project decision", status: "accepted", tags: [] }
      : { id: "uid-bound", label: "T-0001", file: "b.md", type: "decision", title: "Bound decision", status: "accepted", tags: [] };
    return r.fulfill({ json: { entities: [entity], relations: [], duplicate_rows: 0, graph_state: "ok", graph_error: null,
      repo_path: project ? "/home/u/projects/alpha/docs" : "/k" } });
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

test("the Knowledge Graph 'All knowledge bases' view joins both knowledge bases with qualified labels", async ({ page }) => {
  await stub(page);
  await page.goto("/console/app/corvin-knowledge");
  await page.getByTestId("scope-select").selectOption("all");
  await expect(page).toHaveURL(/scope=all/);
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

test("a project without a knowledge base is listed disabled with its reason, on both pages", async ({ page }) => {
  await stub(page);
  for (const url of ["/console/app/initiatives", "/console/app/corvin-knowledge"]) {
    await page.goto(url);
    const demo = page.getByTestId("scope-select").locator('option[value="project:demo"]');
    await expect(demo).toHaveCount(1);
    await expect(demo).toHaveAttribute("disabled", "");
    await expect(demo).toHaveText("demo (no knowledge base - switch it on in Features)");
    await expect(page.getByTestId("scope-unavailable")).toContainText("demo");
    await expect(page.getByTestId("scope-unavailable").getByRole("link", { name: "demo" })).toHaveAttribute("href", /\/app\/projects\/demo\?tab=features/);
    await expect(page.getByTestId("scope-select").locator("option")).toHaveText(["All knowledge bases", "Corvin", "Plumbline", "demo (no knowledge base - switch it on in Features)"]);
  }
});

test("an unknown or unavailable ?scope= falls back to the page default and says why", async ({ page }) => {
  await stub(page);
  await page.goto("/console/app/initiatives?scope=project:ghost");
  await expect(page.getByTestId("scope-note")).toHaveText('There is no project "ghost". Showing All knowledge bases instead.');
  await expect(page.getByTestId("scope-select")).toHaveValue("all");
  await page.goto("/console/app/corvin-knowledge?scope=project:demo");
  await expect(page.getByTestId("scope-note")).toContainText("demo cannot be shown: no knowledge base");
  await expect(page.getByTestId("scope-select")).toHaveValue("bound");
});

test("the counters and the repository line name the chosen knowledge base", async ({ page }) => {
  await stub(page);
  await page.goto("/console/app/initiatives?scope=project:alpha");
  await expect(page.getByTestId("tasks-scope-counts")).toHaveText("Plumbline: 1 items · 0 active initiatives");
  await expect(page.getByTestId("kpi-scope-note")).toContainText("Counts for Plumbline");
  await expect(page.getByTestId("kpi-open")).toContainText("1");
  await page.getByTestId("scope-select").selectOption("all");
  await expect(page.getByTestId("tasks-scope-counts")).toHaveText("All knowledge bases: 2 items · 0 active initiatives");
  await page.goto("/console/app/corvin-knowledge?scope=project:alpha");
  await expect(page.getByTestId("knowledge-repo")).toContainText("Plumbline · Repository: /home/u/projects/alpha/docs");
  await page.reload();
  await expect(page.getByTestId("knowledge-repo")).toContainText("/home/u/projects/alpha/docs");
  await page.getByTestId("scope-select").selectOption("bound");
  await expect(page.getByTestId("knowledge-repo")).toContainText("Corvin · Repository: /k");
});

test("a project in the Trash is listed in the switcher as unavailable: In Trash, with a link to the Trash (T-0405)", async ({ page }) => {
  await stub(page);
  await page.unroute(/\/v1\/console\/projects$/);
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: { ...PROJECTS, trash_count: 1,
    trashed: [{ id: "gone", path: "", template: "blank@1", state: "trashed", storage: "trashed", title: "Old study", reason: "In Trash" }] } }));
  await page.goto("/console/app/initiatives");
  const opt = page.locator('[data-testid="scope-select"] option[value="project:gone"]');
  await expect(opt).toHaveText("Old study (In Trash)");
  await expect(opt).toHaveAttribute("disabled", "");                         // toBeDisabled() reads an <option> as enabled
  const line = page.getByTestId("scope-unavailable");
  await expect(line).toContainText("Old study (In Trash)");
  await expect(line.getByRole("link", { name: "Old study" })).toHaveAttribute("href", "/console/app/projects?view=trash");
  await page.goto("/console/app/initiatives?scope=project:gone");
  await expect(page.getByTestId("scope-note")).toHaveText("Old study cannot be shown: In Trash. Showing All knowledge bases instead.");
});

test("the graph names its own repository when it is not Corvin's, and lists Corvin on its own (REVIEW-0067 M4)", async ({ page }) => {
  await stub(page);
  const CK = "/home/u/projects/Corvin-Knowledge";
  const corvin = { id: "corvin", path: CK, display_path: CK, template: "builtin@1", state: "ok", title: "Corvin", kb: true, builtin: true, storage: "builtin",
    active: ["core", "kb", "tasks", "graph"] };
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: { ...PROJECTS, projects: [corvin, ...PROJECTS.projects] } }));
  await page.route(/\/v1\/console\/plugins\/corvin-knowledge\/config/, (r) => r.fulfill({ json: { repo_path: "/home/u/projects/LLM_lie_detector/docs",
    remote_url: "", auto_sync_on_query: true, consistency_level: "warn" } }));
  const asked: (string | null)[] = [];
  await page.route(/\/v1\/console\/plugins\/corvin-knowledge\/graph(\?|$)/, (r) => {
    const project = new URL(r.request().url()).searchParams.get("project");
    asked.push(project);
    const repo = project === "corvin" ? CK : project ? "/home/u/projects/alpha/docs" : "/home/u/projects/LLM_lie_detector/docs";
    const title = project === "corvin" ? "Corvin decision" : project ? "Project decision" : "Lie detector decision";
    return r.fulfill({ json: { entities: [{ id: `uid-${project ?? "bound"}`, label: "ADR-0001", file: "a.md", type: "decision", title, status: "accepted", tags: [] }],
      relations: [], duplicate_rows: 0, graph_state: "ok", graph_error: null, repo_path: repo } });
  });
  await page.goto("/console/app/corvin-knowledge");
  await expect(page.getByTestId("scope-select").locator("option")).toHaveText(
    ["All knowledge bases", "LLM_lie_detector/docs", "Corvin", "Plumbline", "demo (no knowledge base - switch it on in Features)"]);
  await expect(page.getByTestId("knowledge-repo")).toContainText("LLM_lie_detector/docs");
  await expect(page.getByTestId("knowledge-repo")).not.toContainText("Corvin ·");
  await expect(page.getByTestId("scope-bound-note")).toContainText("not Corvin's knowledge base");
  // the built-in project's own graph: the board's knowledge base, by its own request
  await page.goto("/console/app/corvin-knowledge?scope=project:corvin");
  await expect(page.getByTestId("scope-select")).toHaveValue("project:corvin");
  await expect(page.getByTestId("knowledge-repo")).toContainText("Corvin");
  await expect(page.getByTestId("knowledge-repo")).toContainText(CK);
  await expect(page.getByTestId("scope-bound-note")).toHaveCount(0);
  expect(asked).toContain("corvin");
});

test("when the graph reads Corvin's own knowledge base, Corvin is the default and listed once", async ({ page }) => {
  await stub(page);
  const CK = "/home/u/projects/Corvin-Knowledge";
  const corvin = { id: "corvin", path: CK, display_path: CK, template: "builtin@1", state: "ok", title: "Corvin", kb: true, builtin: true, storage: "builtin",
    active: ["core", "kb", "tasks", "graph"] };
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: { ...PROJECTS, projects: [corvin, ...PROJECTS.projects] } }));
  await page.route(/\/v1\/console\/plugins\/corvin-knowledge\/config/, (r) => r.fulfill({ json: { repo_path: CK, remote_url: "", auto_sync_on_query: true, consistency_level: "warn" } }));
  await page.goto("/console/app/corvin-knowledge");
  await expect(page.getByTestId("scope-select").locator("option")).toHaveText(
    ["All knowledge bases", "Corvin", "Plumbline", "demo (no knowledge base - switch it on in Features)"]);
  await expect(page.getByTestId("scope-bound-note")).toHaveCount(0);
});

test("in the joined view a copy's document and its link list open the copy's nodes, not the original's (REVIEW-0071, REVIEW-0072 N7)", async ({ page }) => {
  // a duplicated project carries its original's knowledge base verbatim: same uids, same ids
  const two = { projects_root: "/home/u/projects", projects: [
    { id: "orig", path: "/home/u/projects/orig", template: "blank@1", state: "ok", title: "Original", kb: true, active: ["core", "kb"] },
    { id: "copy", path: "/home/u/projects/copy", template: "blank@1", state: "ok", title: "Original (copy)", kb: true, active: ["core", "kb"] },
  ] };
  await stub(page);
  await page.unroute(/\/v1\/console\/projects$/);
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: two }));
  const ent = (uid: string, label: string) => ({ id: uid, label, file: `${label}.md`, type: "decision", title: label, status: "accepted", tags: [] });
  await page.unroute(/\/v1\/console\/plugins\/corvin-knowledge\/graph(\?|$)/);
  await page.route(/\/v1\/console\/plugins\/corvin-knowledge\/graph(\?|$)/, (r) => {
    const project = new URL(r.request().url()).searchParams.get("project");
    const entities = project ? [ent("U1", "ADR-0001"), ent("U2", "ADR-0002")] : [];
    return r.fulfill({ json: { entities, relations: project ? [{ from_id: "U1", to_id: "U2", type: "related" }] : [], duplicate_rows: 0,
      graph_state: "ok", graph_error: null, repo_path: project ? `/home/u/projects/${project}/docs` : "/k" } });
  });
  // the real /doc route answers with the knowledge base's own raw uids, the project named by ?project=
  await page.route(/\/v1\/console\/plugins\/corvin-knowledge\/doc\//, (r) => {
    const u = new URL(r.request().url());
    const uid = decodeURIComponent(u.pathname.split("/").pop() ?? "");
    const project = u.searchParams.get("project") ?? "bound";
    const label = uid === "U1" ? "ADR-0001" : "ADR-0002";
    const ref = { id: "U2", label: "ADR-0002", title: "ADR-0002", type: "decision", relation: "related" };
    return r.fulfill({ json: { entity: { id: uid, label, type: "decision", title: label, status: "accepted", realization: "" }, frontmatter: {},
      markdown: uid === "U1" ? `# ${project} ADR-0001\n\nsee ADR-0002` : `# ${project} ADR-0002`, outgoing: uid === "U1" ? [ref] : [], incoming: [] } });
  });
  await page.goto(`/console/app/corvin-knowledge?scope=all&node=${encodeURIComponent("U1@copy")}`);
  const body = page.getByTestId("knowledge-doc-body");
  await expect(body).toContainText("copy ADR-0001");
  // the link in the text
  await body.getByRole("link", { name: "ADR-0002" }).click();
  await expect(page).toHaveURL(/node=U2%40copy/);
  await expect(body).toContainText("copy ADR-0002");
  // the "Links" list
  await page.goBack();
  await expect(body).toContainText("copy ADR-0001");
  await page.getByTestId("knowledge-doc-outgoing").getByRole("button", { name: "ADR-0002" }).click();
  await expect(page).toHaveURL(/node=U2%40copy/);
  await expect(body).toContainText("copy ADR-0002");
  // and the original stays the original
  await page.goto(`/console/app/corvin-knowledge?scope=all&node=U1`);
  await expect(body).toContainText("orig ADR-0001");
  await page.getByTestId("knowledge-doc-outgoing").getByRole("button", { name: "ADR-0002" }).click();
  await expect(body).toContainText("orig ADR-0002");
});

test("in one project's view a hand-written link with a human id opens that project's node, and a click keeps the view", async ({ page }) => {
  await stub(page);
  await page.route(/\/v1\/console\/plugins\/corvin-knowledge\/doc\//, (r) => {
    const u = new URL(r.request().url());
    return r.fulfill({ json: { entity: { id: decodeURIComponent(u.pathname.split("/").pop() ?? ""), label: "T-0001", type: "decision", title: "Project decision",
      status: "accepted", realization: "" }, frontmatter: {}, markdown: `# from ${u.searchParams.get("project") ?? "bound"}`, outgoing: [], incoming: [] } });
  });
  // the project's labels are qualified ("alpha/T-0001") inside the page; the human id of the project's own knowledge base still resolves
  await page.goto("/console/app/corvin-knowledge?scope=project:alpha&node=T-0001");
  await expect(page.getByTestId("knowledge-doc-body")).toContainText("from alpha");
  await expect(page.getByTestId("knowledge-unknown-node")).toHaveCount(0);
  await page.getByTestId("knowledge-search-input").fill("decision");
  await page.getByTestId("knowledge-search-hits").getByRole("button").first().click();
  await expect(page).toHaveURL(/scope=project(%3A|:)alpha/);
});

test("switching the board's knowledge base closes an item that belongs to the old one", async ({ page }) => {
  await stub(page);
  await page.goto("/console/app/initiatives?scope=project:alpha&item=a-1");
  await page.getByTestId("scope-select").selectOption("bound");
  await expect(page).not.toHaveURL(/item=/);
});
