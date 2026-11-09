/**
 * The built-in Corvin project on the served bundle (CONCEPT-0129): first in the list, marked built in, never offered for removal or
 * reconfiguration, present in the chat menu, and not a second entry in the Tasks scope. Network stubbed at the boundary; the server's own
 * guarantees (409 builtin, the tenant's own knowledge base only) are proven by tests/e2e/test_projects_builtin_e2e.py over real HTTP.
 */
import { test, expect, type Page } from "@playwright/test";

const CORVIN = { id: "corvin", path: "/home/u/Corvin-Knowledge", template: "builtin@1", state: "ok", title: "Corvin", builtin: true, kb: true, docs_mode: "plain",
  classification: "internal", assistant_access: "read", active: ["core", "kb", "tasks", "graph"], features: { core: true, kb: true, tasks: true, graph: true } };
const MINE = { id: "alpha", path: "/home/u/projects/alpha", template: "x@1", state: "ok", title: "ALPHA", role: "software", classification: "internal", assistant_access: "write",
  active: ["core", "kb"], features: { core: true, kb: true } };

async function stub(page: Page, projects: unknown[]) {
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: { available: true, templates: [], features: [] } }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: { projects_root: "/home/u/projects", projects } }));
  await page.route(/\/v1\/console\/projects\/corvin$/, (r) => r.fulfill({ json: { ...CORVIN, directory_peers: [], catalogue: { available: false, reason: "Corvin is built in; its features are fixed" } } }));
  await page.route(/\/v1\/console\/projects\/corvin\/health$/, (r) => r.fulfill({ json: { project_id: "corvin", ok: true, checks: [
    { id: "path_exists", ok: true, detail: "Corvin's knowledge base exists", at: "x" }, { id: "kb_check", ok: true, detail: "kb check: blocking 0", at: "x" }] } }));
  await page.route(/\/v1\/console\/projects\/chat-attachment/, (r) => r.fulfill({ json: { session_id: "s", project_id: null, title: null, withheld: null } }));
}

test("Corvin is the first card, marked built in, even before the user has a project of their own", async ({ page }) => {
  await stub(page, [CORVIN]);
  await page.goto("/console/app/projects");
  const card = page.locator('[data-testid="project-card"][data-project-id="corvin"]');
  await expect(card).toBeVisible();
  await expect(card.getByTestId("project-builtin")).toHaveText("Built in");
  await expect(card.getByTestId("class-badge")).toHaveAttribute("data-class", "internal");
  await expect(card.getByTestId("access-badge")).toHaveAttribute("data-access", "read");
  await expect(page.getByTestId("projects-empty")).toBeVisible();                        // "no project of your own yet" is still said
  await expect(page.getByTestId("projects-filter")).toHaveCount(0);
});

test("with projects of their own, Corvin stays first", async ({ page }) => {
  await stub(page, [MINE, CORVIN]);
  await page.goto("/console/app/projects");
  await expect(page.getByTestId("project-card")).toHaveCount(2);
  const ids = await page.getByTestId("project-card").evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.projectId));
  expect(ids).toEqual(["corvin", "alpha"]);
  await expect(page.getByTestId("projects-empty")).toHaveCount(0);
});

test("the inspector of Corvin offers looking and attaching, never removing or reconfiguring", async ({ page }) => {
  await stub(page, [CORVIN, MINE]);
  await page.goto("/console/app/projects/corvin");
  await expect(page.getByTestId("inspector-builtin")).toHaveText("Built in");
  const tabs = await page.getByRole("tab").evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.testid));
  expect(tabs).toEqual(["tab-overview", "tab-structure", "tab-docs", "tab-knowledge", "tab-tasks", "tab-settings"]);
  await expect(page.getByTestId("health-headline")).toBeVisible();
  await page.getByTestId("tab-settings").click();
  await expect(page.getByTestId("builtin-note")).toContainText("built in");
  await expect(page.getByTestId("attach-section")).toBeVisible();
  for (const id of ["remove-start", "rename-input", "move-input", "protection-save", "repair-preview"]) await expect(page.getByTestId(id)).toHaveCount(0);
});

test("the chat menu lists Corvin first, labelled built in", async ({ page }) => {
  await stub(page, [CORVIN, MINE]);
  const sid = (await (await page.request.get("/v1/console/chat/sessions")).json()).sessions?.[0]?.sid;
  test.skip(!sid, "needs at least one chat session on the console under test");
  await page.goto(`/console/app/chat/${sid}`);
  await page.getByTestId("project-menu-button").click();
  const opts = await page.getByTestId("project-option").evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.project));
  expect(opts).toEqual(["", "corvin", "alpha"]);
  await expect(page.locator('[data-testid="project-option"][data-project="corvin"]').locator("xpath=ancestor::label")).toContainText("Built in");
});
