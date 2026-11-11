/**
 * "New video" card of the project inspector, on the served bundle (T-0361). The endpoints are stubbed at the network
 * boundary; the real folder and KB work is proven by CorvinOS tests/e2e/test_projects_videos_e2e.py. Nothing here
 * creates a folder. What this proves: a bad name is refused in the page before any request, the answer names the
 * video, its epic and its tasks, a project without a knowledge base is told so, and a project with the videos
 * feature off is told why instead of offered a form.
 */
import { test, expect, type Page } from "@playwright/test";

const ID = "plumbline";
function detail(videos: boolean) {
  return {
    id: ID, path: "/home/u/projects/plumbline", template: "video-engineering@1", state: "ok", title: "Plumbline explainer",
    docs_mode: "nested", kb: true, active: videos ? ["core", "kb", "videos"] : ["core", "kb"],
    features: { core: true, kb: true, videos },
    catalogue: { available: true, features: [
      { id: "core", title: "Project", description: "", active: true, requires: [], suggests: [], dependants: [], plugins: [] },
      { id: "kb", title: "kb", description: "", active: true, requires: ["core"], suggests: [], dependants: [], plugins: [] },
      { id: "videos", title: "videos", description: "", active: videos, requires: ["core"], suggests: [], dependants: [], plugins: [] },
    ] },
  };
}
const HEALTH = { project_id: ID, ok: true, checks: [{ id: "path_exists", ok: true, detail: "exists", at: "x" }] };

async function stub(page: Page, opts: { videos: boolean; post?: (r: import("@playwright/test").Route) => unknown }) {
  await page.route(new RegExp(`/v1/console/projects/${ID}$`), (r) => r.fulfill({ json: detail(opts.videos) }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/health$`), (r) => r.fulfill({ json: HEALTH }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/videos$`), (r) => (opts.post ? opts.post(r) : r.fulfill({ status: 201, json: {} })));
}

test("a bad name is refused in the page, before any request", async ({ page }) => {
  let posted = false;
  await stub(page, { videos: true, post: (r) => { posted = true; return r.fulfill({ status: 201, json: {} }); } });
  await page.goto(`/console/app/projects/${ID}`);
  await page.getByTestId("new-video-name").fill("Bad Name");
  await expect(page.getByTestId("new-video-problem")).toContainText("lowercase");
  await expect(page.getByTestId("new-video-create")).toBeDisabled();
  expect(posted).toBe(false);
});

test("a created video names its folder, its epic and its tasks", async ({ page }) => {
  await stub(page, { videos: true, post: (r) => r.fulfill({ status: 201, json: {
    video: { name: "plumbline-intro", path: "/home/u/Corvin-Videos/plumbline-intro", languages: ["de", "en"], kb: true, epic: "E-002", tasks: ["T-0005", "T-0006", "T-0007", "T-0008", "T-0009", "T-0010"] },
    note: "" } }) });
  await page.goto(`/console/app/projects/${ID}`);
  await page.getByTestId("new-video-name").fill("plumbline-intro");
  await page.getByTestId("new-video-create").click();
  await expect(page.getByTestId("new-video-result")).toContainText("Created plumbline-intro at /home/u/Corvin-Videos/plumbline-intro");
  await expect(page.getByTestId("new-video-result")).toContainText("Epic E-002 with 6 tasks");
});

test("a project without a knowledge base is told that no epic or tasks were made", async ({ page }) => {
  await stub(page, { videos: true, post: (r) => r.fulfill({ status: 201, json: {
    video: { name: "bare", path: "/v/bare", languages: ["de", "en"], kb: false, epic: null, tasks: [] },
    note: "No knowledge base on this project: the folder and sources.md were created; no epic or tasks." } }) });
  await page.goto(`/console/app/projects/${ID}`);
  await page.getByTestId("new-video-name").fill("bare");
  await page.getByTestId("new-video-create").click();
  await expect(page.getByTestId("new-video-result")).toContainText("No knowledge base on this project");
  await expect(page.getByTestId("new-video-result")).not.toContainText("Epic");
});

test("with the videos feature off the Overview has no New video card and starts with the checks (REVIEW-0067 M5)", async ({ page }) => {
  await stub(page, { videos: false });
  await page.goto(`/console/app/projects/${ID}`);
  await expect(page.getByTestId("health-headline")).toBeVisible();
  await expect(page.getByTestId("new-video-card")).toHaveCount(0);
  await expect(page.getByTestId("new-video-name")).toHaveCount(0);
  // the first thing in the Overview tab is the checks' headline, not a card about another feature
  await expect(page.getByTestId("overview-tab").locator("> *").first()).toContainText(/check/i);
});
