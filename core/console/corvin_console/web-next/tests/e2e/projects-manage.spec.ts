/**
 * Adopt, rename and move on the served bundle (T-0362). The project endpoints are stubbed at the
 * network boundary; the forms, their guards and the request bodies they send are the real ones.
 * The server-side guarantees (one file written, only the title changes, nothing overwritten) are
 * proven by tests/e2e/test_projects_ops_e2e.py over real HTTP.
 */
import { test, expect, type Page } from "@playwright/test";

const ID = "plumbline";
const DETAIL = {
  id: ID, path: "/home/u/projects/plumbline", template: "video-engineering@1", state: "ok", title: "Plumbline explainer",
  docs_mode: "nested", kb: true, active: ["core", "kb"], features: {}, directory_peers: [],
  catalogue: { available: true, features: [{ id: "core", title: "Project", description: "", active: true, requires: [], suggests: [], dependants: [], plugins: [] }] },
};

async function base(page: Page) {
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: { available: true, templates: [], features: [] } }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: { projects_root: "/home/u/projects", projects: [] } }));
  await page.route(new RegExp(`/v1/console/projects/${ID}$`), (r) => r.fulfill({ json: DETAIL }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/health$`), (r) => r.fulfill({ json: { project_id: ID, ok: true, checks: [] } }));
}

test("Add existing folder sends the path and the optional title, and is disabled while the path is empty", async ({ page }) => {
  await base(page);
  let body: { location?: string; title?: string } | null = null;
  await page.route(/\/v1\/console\/projects\/adopt$/, (r) => { body = r.request().postDataJSON(); return r.fulfill({ status: 201, json: { ...DETAIL, id: "lie-copy" } }); });
  await page.goto("/console/app/projects");
  await page.getByTestId("adopt-start").click();
  await expect(page.getByTestId("adopt-submit")).toBeDisabled();
  await page.getByTestId("adopt-location").fill("/home/u/projects/lie-copy");
  await page.getByTestId("adopt-title").fill("Lie detector");
  await page.getByTestId("adopt-submit").click();
  await expect.poll(() => body?.location).toBe("/home/u/projects/lie-copy");
  expect(body?.title).toBe("Lie detector");
  await expect(page.getByTestId("adopt-note")).toContainText("lie-copy");
});

test("a refused adopt shows the server's reason instead of success", async ({ page }) => {
  await base(page);
  await page.route(/\/v1\/console\/projects\/adopt$/, (r) => r.fulfill({ status: 422, json: { detail: { message: "the location must be inside this tenant's projects root", reason_class: "path_rule" } } }));
  await page.goto("/console/app/projects");
  await page.getByTestId("adopt-start").click();
  await page.getByTestId("adopt-location").fill("/etc");
  await page.getByTestId("adopt-submit").click();
  await expect(page.getByTestId("adopt-note")).toContainText("projects root");
});

test("Rename sends only the title; Move needs a second confirming click and then sends the location", async ({ page }) => {
  await base(page);
  let renamed: { title?: string } | null = null;
  let moved: { location?: string } | null = null;
  await page.route(new RegExp(`/v1/console/projects/${ID}/title$`), (r) => { renamed = r.request().postDataJSON(); return r.fulfill({ json: { ...DETAIL, title: "A better name" } }); });
  await page.route(new RegExp(`/v1/console/projects/${ID}/move$`), (r) => { moved = r.request().postDataJSON(); return r.fulfill({ json: { ...DETAIL, path: "/home/u/projects/new", repaired_files: 2 } }); });
  await page.goto(`/console/app/projects/${ID}?tab=settings`);
  await expect(page.getByTestId("rename-submit")).toBeDisabled();             // unchanged title
  await page.getByTestId("rename-input").fill("A better name");
  await page.getByTestId("rename-submit").click();
  await expect.poll(() => renamed?.title).toBe("A better name");
  await expect(page.getByTestId("manage-note")).toContainText("Only project.yaml changed");

  await page.getByTestId("move-input").fill("/home/u/projects/new");
  await page.getByTestId("move-start").click();
  expect(moved).toBeNull();                                                    // not sent before the confirmation
  await page.getByTestId("move-confirm").click();
  await expect.poll(() => moved?.location).toBe("/home/u/projects/new");
  await expect(page.getByTestId("manage-note")).toContainText("2 machine-local file(s) regenerated");
});
