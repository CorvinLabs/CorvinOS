/**
 * Project inspector, in the browser on the served bundle (T-0359). The project endpoints are stubbed at the
 * network boundary; the page, router, bundle and renderer are real. What this proves: the overview shows the
 * server's checks and never "all good" over a red one; a switch-off that would take dependants away asks first
 * and then sends the cascade; a feature whose plugin is not enabled cannot be switched on and says why; repair
 * is previewed before it is applied; removing a project asks first and says the files stay. Nothing is written
 * to disk by this spec.
 */
import { test, expect, type Page } from "@playwright/test";

const ID = "plumbline";
const DETAIL = {
  id: ID, path: "/home/u/projects/plumbline", template: "video-engineering@1", state: "ok", title: "Plumbline explainer",
  docs_mode: "nested", kb: true, active: ["core", "kb", "tasks", "videos"],
  features: { core: true, kb: true, tasks: true, videos: true, acs: false, video_producer: false },
  catalogue: { available: true, suggestions: [], features: [
    { id: "core", title: "Project", description: "", active: true, requires: [], suggests: [], dependants: [], plugins: [] },
    { id: "kb", title: "kb", description: "Knowledge base", active: true, requires: ["core"], suggests: [], dependants: ["tasks"], plugins: [{ plugin: "corvin_knowledge", state: "enabled" }] },
    { id: "tasks", title: "tasks", description: "Tasks", active: true, requires: ["kb"], suggests: [], dependants: [], plugins: [] },
    { id: "videos", title: "videos", description: "Videos", active: true, requires: ["core"], suggests: [], dependants: [], plugins: [] },
    { id: "claude", title: "claude", description: "Rules", active: false, requires: ["core"], suggests: [], dependants: [], plugins: [] },
    { id: "acs", title: "acs", description: "Agentic compute", active: false, requires: ["claude"], suggests: [], dependants: [], plugins: [] },
    { id: "video_producer", title: "video_producer", description: "Video Producer", active: false, requires: ["videos"], suggests: [], dependants: [], plugins: [{ plugin: "video_producer", state: "absent" }] },
  ] },
};
const CHECKS = { project_id: ID, ok: false, checks: [
  { id: "path_exists", ok: true, detail: "the project directory exists", at: "x" },
  { id: "kb_check", ok: false, detail: "kb check: blocking 2", at: "x" },
] };

async function stub(page: Page) {
  await page.route(new RegExp(`/v1/console/projects/${ID}$`), (r) => (r.request().method() === "DELETE"
    ? r.fulfill({ json: { removed: ID, files_deleted: false } }) : r.fulfill({ json: DETAIL })));
  await page.route(new RegExp(`/v1/console/projects/${ID}/health$`), (r) => r.fulfill({ json: CHECKS }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/features$`), (r) => r.fulfill({ json: DETAIL }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/repair$`), (r) => r.fulfill({ json: { pending: [{ path: "CLAUDE.md", status: "changed", diff: "--- a/CLAUDE.md\n+++ b/CLAUDE.md\n-## Core (old)\n+## Core" }], dry_run: true } }));
}

test("the overview shows every check and never claims all is well over a red one", async ({ page }) => {
  await stub(page);
  await page.goto(`/console/app/projects/${ID}`);
  await expect(page.getByTestId("inspector-title")).toHaveText("Plumbline explainer");
  await expect(page.getByTestId("health-headline")).toHaveText("1 of 2 checks need attention.");
  await expect(page.getByTestId("health-check").filter({ hasText: "kb check: blocking 2" })).toHaveAttribute("data-ok", "false");
  await expect(page.getByTestId("health-check").filter({ hasText: "the project directory exists" })).toHaveAttribute("data-ok", "true");
});

test("switching off a feature with dependants asks first, then sends the cascade", async ({ page }) => {
  await stub(page);
  let body: unknown = null;
  await page.route(new RegExp(`/v1/console/projects/${ID}/features$`), async (r) => {
    body = r.request().postDataJSON();
    if (!(body as { cascade?: boolean }).cascade) {
      return r.fulfill({ status: 409, json: { detail: { message: "kb is required by: tasks", reason_class: "has_dependants", feature: "kb", dependants: ["tasks"] } } });
    }
    return r.fulfill({ json: DETAIL });
  });
  await page.goto(`/console/app/projects/${ID}?tab=features`);
  await page.locator('[data-feature-row="kb"], [data-testid="feature-row"][data-feature="kb"]').getByTestId("feature-action").click();
  await expect(page.getByTestId("conflict-dependants")).toContainText("also switches off: Tasks");
  await expect(page.getByTestId("conflict-dependants")).toContainText("Their files stay");
  await page.getByTestId("conflict-confirm").click();
  await expect.poll(() => (body as { cascade?: boolean } | null)?.cascade).toBe(true);
});

test("a feature whose plugin is not enabled cannot be switched on, and the reason is shown", async ({ page }) => {
  await stub(page);
  await page.goto(`/console/app/projects/${ID}?tab=features`);
  const row = page.locator('[data-feature="video_producer"]');
  await expect(row.getByTestId("feature-status")).toHaveText("Feature off");
  await expect(row.getByTestId("feature-action")).toBeDisabled();
  await expect(row.getByTestId("feature-blocked")).toContainText("video producer plugin, which is not enabled");
});

test("repair is previewed before it is applied", async ({ page }) => {
  await stub(page);
  await page.goto(`/console/app/projects/${ID}?tab=settings`);
  await page.getByTestId("repair-preview").click();
  await expect(page.getByTestId("repair-pending")).toContainText("changed: CLAUDE.md");
  await expect(page.getByTestId("repair-diff")).toBeVisible();
  await expect(page.getByTestId("repair-apply")).toBeVisible();
});

test("removing a project asks first and says its files stay", async ({ page }) => {
  await stub(page);
  let deleted = false;
  await page.route(new RegExp(`/v1/console/projects/${ID}$`), (r) => {
    if (r.request().method() === "DELETE") { deleted = true; return r.fulfill({ json: { removed: ID, files_deleted: false } }); }
    return r.fulfill({ json: DETAIL });
  });
  await page.goto(`/console/app/projects/${ID}?tab=settings`);
  await page.getByTestId("remove-start").click();
  await expect(page.getByTestId("remove-confirm")).toBeVisible();
  await page.getByTestId("remove-confirm").click();
  await expect(page.getByTestId("remove-message")).toContainText("Its folder and every file stay where they are");
  expect(deleted).toBe(true);
});

test("a project that is not in the list says so and touches nothing", async ({ page }) => {
  await page.route(new RegExp(`/v1/console/projects/nope$`), (r) => r.fulfill({ status: 404, json: { detail: "no such project" } }));
  await page.goto("/console/app/projects/nope");
  await expect(page.getByTestId("inspector-missing")).toContainText("not in your list");
});
