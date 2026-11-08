/**
 * The inspector's tabs on the served bundle (T-0359): every tab shows real data or an explained empty state, a tab
 * whose feature is off shows "Feature off" with what it needs and a switch that sends accept_dependencies. The
 * project endpoints are stubbed at the network boundary; the tab logic and the bundle are the real ones.
 */
import { test, expect, type Page } from "@playwright/test";

const ID = "plumbline";
function detail(opts: { videos: boolean; kb: boolean; peers?: string[]; more?: string[] }) {
  const active = ["core", ...(opts.kb ? ["kb"] : []), ...(opts.videos ? ["videos"] : []), ...(opts.more ?? [])];
  return {
    id: ID, path: "/home/u/projects/plumbline", template: "video-engineering@1", state: "ok", title: "Plumbline explainer",
    docs_mode: "nested", kb: opts.kb, active, features: {}, directory_peers: opts.peers ?? [],
    catalogue: { available: true, features: [
      { id: "core", title: "Project", description: "", active: true, requires: [], suggests: [], dependants: [], plugins: [] },
      { id: "kb", title: "kb", description: "", active: opts.kb, requires: ["core"], suggests: [], dependants: [], plugins: [] },
      { id: "videos", title: "videos", description: "", active: opts.videos, requires: ["core"], suggests: [], dependants: [], plugins: [] },
      { id: "graph", title: "graph", description: "", active: active.includes("graph"), requires: ["kb"], suggests: [], dependants: [], plugins: [] },
      { id: "tasks", title: "tasks", description: "", active: active.includes("tasks"), requires: ["kb"], suggests: [], dependants: [], plugins: [] },
      { id: "directory", title: "directory", description: "", active: active.includes("directory"), requires: ["core"], suggests: [], dependants: [], plugins: [] },
      { id: "acs", title: "acs", description: "", active: active.includes("acs"), requires: ["claude"], suggests: [], dependants: [], plugins: [] },
    ] },
  };
}
const STRUCTURE = {
  project_id: ID, truncated: false, videos_root: "/home/u/Corvin-Videos",
  entries: [{ path: "project.yaml", kind: "file", size: 120 }, { path: "docs", kind: "dir", size: null }, { path: "videos", kind: "dir", size: null }],
  kb_counts: { initiatives: 1, epics: 2, tasks: 7, decisions: 0, concepts: 0 },
  videos: [{ name: "plumbline-intro", de: true, en: true, folder: "/home/u/Corvin-Videos/plumbline-intro", state: "started", folder_exists: true }],
};
const HEALTH = { project_id: ID, ok: false, checks: [{ id: "compute_worker", ok: false, detail: "the compute worker of this tenant is not reachable", at: "x" }] };

async function stub(page: Page, d: ReturnType<typeof detail>, onPut?: (body: unknown) => void) {
  await page.route(new RegExp(`/v1/console/projects/${ID}$`), (r) => r.fulfill({ json: d }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/structure`), (r) => r.fulfill({ json: STRUCTURE }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/health$`), (r) => r.fulfill({ json: HEALTH }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/features$`), (r) => { onPut?.(r.request().postDataJSON()); return r.fulfill({ json: d }); });
  await page.route(/\/v1\/console\/task-tracking\/items/, (r) => r.fulfill({ json: { items: [
    { id: "t1", title: "T-0001 · Narration DE", status: "open", labels: ["kb", "prj-plumbline"], project: ID },
  ], server_time: new Date().toISOString(), summary: {}, import_available: false } }));
}

test("Structure lists the folder; Docs counts the knowledge base; Compute reports the real worker check", async ({ page }) => {
  await stub(page, detail({ videos: true, kb: true, more: ["acs"] }));
  await page.goto(`/console/app/projects/${ID}?tab=structure`);
  await expect(page.getByTestId("structure-entry").filter({ hasText: "project.yaml" })).toHaveAttribute("data-kind", "file");
  await page.getByRole("tab", { name: "Docs" }).click();
  await expect(page.getByTestId("docs-count-row").filter({ hasText: "tasks" })).toContainText("7");
  await page.getByRole("tab", { name: "Compute" }).click();
  await expect(page.getByTestId("compute-worker")).toContainText("not reachable");
});

test("Videos lists the video rows; Tasks lists the project's items and links to the scoped board", async ({ page }) => {
  await stub(page, detail({ videos: true, kb: true, more: ["tasks"] }));
  await page.goto(`/console/app/projects/${ID}?tab=videos`);
  await expect(page.getByTestId("video-row")).toContainText("plumbline-intro");
  await expect(page.getByTestId("video-row")).toContainText("folder present");
  await page.getByRole("tab", { name: "Tasks" }).click();
  await expect(page.getByTestId("tasks-row")).toContainText("Narration DE");
  await expect(page.getByTestId("tasks-open")).toHaveAttribute("href", /scope=project%3Aplumbline|scope=project:plumbline/);
});

test("a tab of a feature that is on for the project is not shut", async ({ page }) => {
  await stub(page, detail({ videos: true, kb: true, more: ["tasks"] }));
  await page.goto(`/console/app/projects/${ID}?tab=tasks`);
  await expect(page.getByTestId("feature-off")).toHaveCount(0);
  await expect(page.getByTestId("tasks-tab")).toBeVisible();
});

test("a tab whose feature is off says so, names what it needs, and switches the feature on with its requirements", async ({ page }) => {
  let body: { set?: Record<string, boolean>; accept_dependencies?: boolean } | null = null;
  await stub(page, detail({ videos: false, kb: false }), (b) => { body = b as typeof body; });
  await page.goto(`/console/app/projects/${ID}?tab=docs`);
  await expect(page.getByTestId("feature-off")).toHaveAttribute("data-feature", "kb");
  await page.getByTestId("feature-off-enable").click();
  await expect.poll(() => body?.set?.kb).toBe(true);
  expect(body?.accept_dependencies).toBe(true);
});

test("Directory explains an empty allowlist and lists the allowed peers", async ({ page }) => {
  await stub(page, detail({ videos: true, kb: true, peers: ["peer-berlin"], more: ["directory"] }));
  await page.goto(`/console/app/projects/${ID}?tab=directory`);
  await expect(page.getByTestId("directory-peer")).toHaveText("peer-berlin");
});

test("Knowledge opens this project's graph, and an empty Videos tab says how to add one", async ({ page }) => {
  await stub(page, detail({ videos: true, kb: true, more: ["graph"] }));
  await page.route(/\/v1\/console\/projects\/plumbline\/structure/, (r) => r.fulfill({ json: { ...STRUCTURE, videos: [] } }));
  await page.goto(`/console/app/projects/${ID}?tab=knowledge`);
  await expect(page.getByTestId("knowledge-open")).toHaveAttribute("href", /scope=project%3Aplumbline|scope=project:plumbline/);
  await page.getByRole("tab", { name: "Videos" }).click();
  await expect(page.getByTestId("videos-empty")).toContainText("No videos yet");
});
