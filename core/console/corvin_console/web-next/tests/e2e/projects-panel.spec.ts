/**
 * Projects panel, in the real browser on the bundle the console serves (T-0357). The two project
 * endpoints are stubbed at the network boundary (page.route); the page, bundle, registry, sidebar,
 * router and renderer are the real ones. Read-only: nothing here writes to the console.
 *
 * What must hold: the sidebar entry reaches the page (the PANELS + NAV_GROUPS pair), a list shows every
 * project with its state, a missing folder is shown as missing, the empty state names what is missing,
 * no ADR id or plugin index id is rendered, and the page fits a 375 px phone in both themes.
 */
import { test, expect, type Page } from "@playwright/test";

const LIST = {
  projects_root: "/home/u/projects",
  projects: [
    { id: "plumbline", path: "/home/u/projects/plumbline", template: "video-engineering@1", state: "ok", title: "Plumbline explainer",
      docs_mode: "nested", active: ["core", "kb", "tasks", "graph", "gates", "claude", "videos", "video_producer"],
      features: { core: true, kb: true, tasks: true, graph: true, gates: true, claude: true, videos: true, video_producer: true, acs: false, experiments: false } },
    { id: "old-study", path: "/home/u/projects/old-study", template: "research-compute@1", state: "missing", reason: "project.yaml is missing or unreadable" },
  ],
};

async function stub(page: Page, opts: { projects: unknown; available?: boolean; reason?: string }) {
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({
    json: { available: opts.available ?? true, reason: opts.reason, templates: [], features: [] } }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: opts.projects }));
}

async function noIds(page: Page) {
  const text = await page.getByTestId("projects-page").innerText();
  expect(text).not.toMatch(/ADR-\d|CONCEPT-\d|plugin:buildin|plugin:contributor/);
}

test("the sidebar entry reaches the Projects page and the list shows every project with its state", async ({ page }) => {
  await stub(page, { projects: LIST });
  await page.goto("/console/app/dashboard");
  await page.getByRole("link", { name: "Projects", exact: true }).first().click();
  await expect(page).toHaveURL(/\/console\/app\/projects$/);
  const cards = page.getByTestId("project-card");
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0)).toHaveAttribute("data-project-id", "old-study");                 // needs attention: first
  await expect(cards.nth(0).getByTestId("project-state")).toHaveText("Directory missing");
  await expect(cards.nth(0).getByTestId("project-reason")).toContainText("project.yaml is missing");
  await expect(cards.nth(1).getByTestId("project-title")).toHaveText("Plumbline explainer");
  await expect(cards.nth(1).getByTestId("project-state")).toHaveText("Ready");
  const chips = cards.nth(1).getByTestId("project-features");
  for (const label of ["Knowledge base", "Tasks", "Knowledge graph", "Quality gates", "Claude Code rules", "Videos", "Video Producer"]) {
    await expect(chips).toContainText(label);
  }
  await expect(cards.nth(1).getByTestId("project-off")).toContainText("Off: Agentic compute, Experiments");
  await expect(cards.nth(1).getByTestId("project-path")).toHaveText("/home/u/projects/plumbline");
  await expect(page.getByTestId("projects-root")).toContainText("/home/u/projects");
  await noIds(page);
});

test("an install without projects explains what a project is and offers no dead button", async ({ page }) => {
  await stub(page, { projects: { projects_root: "/home/u/projects", projects: [] } });
  await page.goto("/console/app/projects");
  await expect(page.getByTestId("projects-empty-headline")).toHaveText("No projects yet");
  await expect(page.getByTestId("projects-empty-body")).toContainText("never deletes a file");
  await expect(page.getByTestId("projects-empty").getByRole("button")).toHaveCount(0);
  await expect(page.getByTestId("projects-empty").getByRole("link")).toHaveCount(0);
  await noIds(page);
});

test("a disabled template plugin is named, with the way to enable it", async ({ page }) => {
  await stub(page, { projects: { projects_root: "/p", projects: [] }, available: false,
    reason: "the project templates plugin is absent for this tenant - enable it in the Marketplace" });
  await page.goto("/console/app/projects");
  await expect(page.getByTestId("projects-empty-headline")).toHaveText("Project templates are not enabled");
  await expect(page.getByTestId("projects-empty-body")).toContainText("enable it in the Marketplace");
  await page.getByRole("link", { name: /Open the Marketplace/ }).click();
  await expect(page).toHaveURL(/\/console\/app\/marketplace/);
});

test("a failed load is an error, not an empty list", async ({ page }) => {
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: { available: true, templates: [], features: [] } }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ status: 500, json: { detail: "boom" } }));
  await page.goto("/console/app/projects");
  await expect(page.getByTestId("projects-error")).toBeVisible();
  await expect(page.getByTestId("projects-empty")).toHaveCount(0);
});

for (const theme of ["light", "dark"] as const) {
  test(`the list fits a 375 px phone without sideways scrolling (${theme})`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    // the console is data-theme driven (NOT prefers-color-scheme): set the stored choice the page reads at boot
    await page.addInitScript((t) => { try { localStorage.setItem("corvin-theme", t); localStorage.setItem("corvin-dark-default-v2", "1"); } catch { /* ignore */ } }, theme);
    await stub(page, { projects: LIST });
    await page.goto("/console/app/projects");
    await expect(page.getByTestId("project-card")).toHaveCount(2);
    // positive control: the theme under test is the one actually rendered (a check that renders one theme twice proves nothing)
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `test-results/projects-${theme}-375.png`, fullPage: true });
  });
}
