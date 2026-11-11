/**
 * From a project tile to its details, in the browser on the served bundle. The project endpoints are stubbed at the network boundary; the page,
 * router, bundle and renderer are real. What this proves: a click ANYWHERE on a tile opens the project (not only on its title), the controls
 * inside a tile keep their own job (the selection checkbox does not navigate), a tile says that it opens, the detail page shows its numbers
 * at once - each from an endpoint of its own, with a loading state and an honest error instead of a zero - and the way back leads to the list.
 * Nothing is written to disk by this spec.
 */
import { test, expect, type Page } from "@playwright/test";

const ID = "plumbline";
const LIST_ITEM = { id: ID, path: "/home/u/projects/plumbline", template: "video-engineering@1", state: "ok", title: "Plumbline explainer",
  docs_mode: "nested", kb: true, active: ["core", "kb", "tasks", "graph", "videos"], role: "video", classification: "internal", assistant_access: "write",
  storage: "own", display_path: "~/projects/plumbline" };
const MISSING = { id: "old-study", path: "/home/u/projects/old-study", template: "research-compute@1", state: "missing", title: "Old study",
  reason: "project.yaml is missing or unreadable", storage: "own", display_path: "~/projects/old-study" };
const DETAIL = { ...LIST_ITEM, features: { core: true, kb: true, tasks: true, graph: true, videos: true }, catalogue: { available: true, suggestions: [], features: [] } };
const SUMMARY = { open: 4, in_progress: 2, blocked: 1, complete: 9, archived: 0, total: 16, overdue: 0, approvals_pending: 0, done_7d: 3, initiatives_active: 1 };
const STRUCTURE = { project_id: ID, entries: [], truncated: false, kb_counts: { tasks: 7, decisions: 2, concepts: 1 }, videos: [
  { name: "intro", de: true, en: true, folder: "videos/intro", state: "ok", folder_exists: true },
  { name: "deep-dive", de: true, en: false, folder: "videos/deep-dive", state: "ok", folder_exists: true }], videos_root: "videos" };
const CHANGES = { project_id: ID, commit: null, files: [{ path: "a.md", status: "modified", additions: 1, deletions: 0, area: "docs", links: [] },
  { path: "b.md", status: "untracked", additions: 3, deletions: 0, area: "docs", links: [] }], commits: [], has_repository: true, truncated: false };

async function stub(page: Page, over: { tasks?: "ok" | "fail" | "slow"; list?: unknown[] } = {}) {
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: { projects_root: "/home/u/projects", default_storage: "managed",
    projects: over.list ?? [LIST_ITEM, MISSING] } }));
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: { available: true, templates: [], features: [] } }));
  await page.route(new RegExp(`/v1/console/projects/${ID}$`), (r) => r.fulfill({ json: DETAIL }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/health$`), (r) => r.fulfill({ json: { project_id: ID, ok: true, checks: [
    { id: "path_exists", ok: true, detail: "the project directory exists", at: "x" }] } }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/structure$`), (r) => r.fulfill({ json: STRUCTURE }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/changes`), (r) => r.fulfill({ json: CHANGES }));
  await page.route(/\/v1\/console\/task-tracking\/items/, async (r) => {
    if (over.tasks === "fail") return r.fulfill({ status: 500, json: { detail: "boom" } });
    if (over.tasks === "slow") await new Promise((res) => setTimeout(res, 1500));
    return r.fulfill({ json: { server_time: "2026-11-11T10:00:00Z", items: [], summary: SUMMARY, import_available: false } });
  });
}

test("a click anywhere on a tile opens the project, and the tile says so", async ({ page }) => {
  await stub(page);
  await page.goto("/console/app/projects");
  const card = page.locator(`[data-testid="project-card"][data-project-id="${ID}"]`);
  await expect(card).toBeVisible();
  await expect(card.getByTestId("project-open-hint")).toHaveText("Open project");
  // the cursor says "clickable" over the whole tile
  expect(await card.evaluate((el) => getComputedStyle(el).cursor)).toBe("pointer");
  // a click on the tile's lower area - nowhere near the title link - still opens the project (the title link stretches over the tile, so the
  // click lands on that link: Playwright refuses to click the covered path line itself, which is exactly the point)
  const box = (await card.boundingBox())!;
  await card.click({ position: { x: box.width / 2, y: box.height - 24 } });
  await expect(page).toHaveURL(new RegExp(`/app/projects/${ID}$`));
  await expect(page.getByTestId("inspector-title")).toHaveText("Plumbline explainer");
});

test("the controls inside a tile keep their own job", async ({ page }) => {
  await stub(page);
  await page.goto("/console/app/projects");
  // selection mode shows a checkbox per tile; ticking it must NOT open the project
  await expect(page.locator(`[data-testid="project-card"][data-project-id="${ID}"]`)).toBeVisible();
  const tick = page.locator(`[data-testid="project-card"][data-project-id="${ID}"]`).getByTestId("project-select");
  await expect(tick).toBeVisible();
  await tick.click();
  await expect(tick).toBeChecked();
  await expect(page).toHaveURL(/\/app\/projects$/);                  // ticking selected the project; it did not open it
});

test("a project that cannot be opened says what a click leads to, and the locate link still works", async ({ page }) => {
  await stub(page);
  await page.route(new RegExp("/v1/console/projects/old-study$"), (r) => r.fulfill({ json: { ...MISSING, features: {}, active: [] } }));
  await page.goto("/console/app/projects");
  const card = page.locator('[data-testid="project-card"][data-project-id="old-study"]');
  await expect(card.getByTestId("project-open-hint")).toHaveText("Open details");
  await card.getByTestId("project-locate").click();
  await expect(page).toHaveURL(/\/app\/projects\/old-study\?tab=settings$/);
});

test("the detail page shows its numbers at once, each leading to its tab, and the way back works", async ({ page }) => {
  await stub(page);
  await page.goto(`/console/app/projects/${ID}`);
  await expect(page.getByTestId("inspector-title")).toHaveText("Plumbline explainer");
  const glance = page.getByTestId("project-glance");
  const tile = (id: string) => glance.locator(`[data-testid="glance-tile"][data-tile="${id}"]`);
  await expect(tile("tasks").getByTestId("glance-value")).toHaveText("4");
  await expect(tile("tasks").getByTestId("glance-detail")).toHaveText("open · 2 in progress · 1 blocked");
  await expect(tile("knowledge").getByTestId("glance-value")).toHaveText("10");
  await expect(tile("knowledge").getByTestId("glance-detail")).toHaveText("7 tasks · 2 decisions · 1 concept");
  await expect(tile("videos").getByTestId("glance-value")).toHaveText("2");
  await expect(tile("videos").getByTestId("glance-detail")).toHaveText("1 with German and English");
  await expect(tile("changes").getByTestId("glance-value")).toHaveText("2");
  await expect(tile("changes").getByTestId("glance-detail")).toHaveText("files not committed yet");
  // a number leads to its tab; the changes tile only informs
  await expect(tile("changes").getByTestId("glance-link")).toHaveCount(0);
  await tile("tasks").getByTestId("glance-link").click();
  await expect(page).toHaveURL(/\/app\/projects\/plumbline\?tab=tasks$/);
  // and the way back is named for what it does
  await expect(page.getByTestId("inspector-back")).toHaveText("All projects");
  await page.getByTestId("inspector-back").click();
  await expect(page).toHaveURL(/\/app\/projects$/);
  await expect(page.locator(`[data-testid="project-card"][data-project-id="${ID}"]`)).toBeVisible();
});

test("a number that is still loading says so, and one that cannot be loaded says that instead of a zero", async ({ page }) => {
  await stub(page, { tasks: "slow" });
  await page.goto(`/console/app/projects/${ID}`);
  const tasks = page.locator('[data-testid="glance-tile"][data-tile="tasks"]');
  await expect(tasks.getByTestId("glance-loading")).toBeVisible();
  await expect(tasks.getByTestId("glance-value")).toHaveText("4", { timeout: 8000 });

  await page.unrouteAll({ behavior: "ignoreErrors" });
  await stub(page, { tasks: "fail" });
  await page.goto(`/console/app/projects/${ID}`);
  const failed = page.locator('[data-testid="glance-tile"][data-tile="tasks"]');
  await expect(failed.getByTestId("glance-error")).toContainText("Could not load");
  await expect(failed.getByTestId("glance-value")).toHaveCount(0);
  // the other tiles are not held up by it
  await expect(page.locator('[data-testid="glance-tile"][data-tile="knowledge"]').getByTestId("glance-value")).toHaveText("10");
});

test("a project without a task board or videos draws no tile for them", async ({ page }) => {
  await stub(page);
  await page.route(new RegExp(`/v1/console/projects/${ID}$`), (r) => r.fulfill({ json: { ...DETAIL, kb: false, active: ["core"], features: { core: true } } }));
  await page.goto(`/console/app/projects/${ID}`);
  await expect(page.getByTestId("inspector-title")).toBeVisible();
  const ids = await page.locator('[data-testid="glance-tile"]').evaluateAll((els) => els.map((e) => e.getAttribute("data-tile")));
  expect(ids).toEqual(["changes"]);
});

test("a failing check is named in plain words and points to the place where it is repaired", async ({ page }) => {
  await stub(page);
  await page.route(new RegExp(`/v1/console/projects/${ID}/health$`), (r) => r.fulfill({ json: { project_id: ID, ok: false, checks: [
    { id: "path_under_base", ok: true, detail: "the folder lies where its storage kind says", at: "x" },
    { id: "pending_import", ok: true, detail: "no unfinished import is recorded", at: "x" },
    { id: "generated_files_in_sync", ok: false, detail: "out of sync (repair fixes): ['.gitignore']", at: "x" }] } }));
  await page.goto(`/console/app/projects/${ID}`);
  await expect(page.getByTestId("health-headline")).toContainText("1 of 3 checks need attention.");
  // no raw identifier is shown as a title
  const titles = await page.locator('[data-testid="health-check"] .font-medium').allInnerTexts();
  expect(titles).toContain("Folder is where it belongs");
  expect(titles).toContain("No import left half-done");
  expect(titles.some((t) => /_/.test(t))).toBe(false);
  await page.getByTestId("health-fix-link").click();
  await expect(page).toHaveURL(new RegExp(`/app/projects/${ID}\\?tab=settings$`));
});

test("when every check passes there is nothing to repair and no repair link", async ({ page }) => {
  await stub(page);
  await page.goto(`/console/app/projects/${ID}`);
  await expect(page.getByTestId("health-headline")).toHaveText("Every check passes.");
  await expect(page.getByTestId("health-fix-link")).toHaveCount(0);
});

test("a project.yaml that names another project says what to put back and offers no repair it cannot do", async ({ page }) => {
  await stub(page);
  await page.route(new RegExp(`/v1/console/projects/${ID}/health$`), (r) => r.fulfill({ json: { project_id: ID, ok: false, checks: [
    { id: "path_exists", ok: true, detail: "the project directory exists", at: "x" },
    { id: "manifest", ok: false, detail: `project.yaml names the id 'beta', the list has '${ID}' - put 'id: ${ID}' back into project.yaml`, at: "x" }] } }));
  await page.goto(`/console/app/projects/${ID}`);
  await expect(page.getByTestId("health-headline")).toContainText("1 of 2 checks need attention.");
  await expect(page.getByText(`put 'id: ${ID}' back into project.yaml`)).toBeVisible();
  await expect(page.getByTestId("health-fix-link")).toHaveCount(0);
});

test("a project that cannot be opened lists every check on its page, and Settings offers no link to a tab it does not have (REVIEW-0078 N21)", async ({ page }) => {
  await stub(page);
  // what the server answers for a project.yaml that names another id: state invalid, two failed checks
  await page.route(new RegExp(`/v1/console/projects/${ID}$`), (r) => r.fulfill({ json: { ...DETAIL, state: "invalid", reason: "project.yaml names another project" } }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/health$`), (r) => r.fulfill({ json: { project_id: ID, ok: false, checks: [
    { id: "path_exists", ok: true, detail: "the project directory exists", at: "x" },
    { id: "manifest", ok: false, detail: `project.yaml names the id 'beta', the list has '${ID}' - put 'id: ${ID}' back into project.yaml`, at: "x" },
    { id: "generated_files_in_sync", ok: false, detail: "out of sync (repair fixes): ['CLAUDE.md']", at: "x" }] } }));
  await page.goto(`/console/app/projects/${ID}?tab=settings`);
  await expect(page.getByTestId("inspector-not-ready")).toBeVisible();
  const checks = page.getByTestId("not-ready-checks");
  await expect(checks.getByTestId("health-headline")).toContainText("2 of 3 checks need attention.");
  await expect(checks.locator('[data-testid="health-check"][data-ok="false"]')).toHaveCount(2);
  await expect(checks).toContainText(`put 'id: ${ID}' back into project.yaml`);
  await expect(page.getByTestId("doctor-open")).toHaveCount(0);
});
