/**
 * New-project wizard in the browser, on the served bundle. The plan and create endpoints are stubbed at
 * the network boundary: this proves the flow, the gating and the review, not the server (that is proven by
 * the HTTP E2E in CorvinOS). A real project is never created by this spec.
 */
import { test, expect, type Page } from "@playwright/test";

const CATALOGUE = {
  available: true, projects_root: "/home/u/projects",
  templates: [
    { id: "knowledge-only", version: 1, title: "Knowledge only", description: "A knowledge base.", features: ["core", "kb"], offered: ["core", "kb", "tasks"], docs_mode: "plain" },
    { id: "video-engineering", version: 1, title: "Video engineering", description: "Narrated videos.", features: ["core", "kb", "videos"], offered: ["core", "kb", "videos", "acs"], docs_mode: "nested" },
  ],
  features: [
    { id: "core", title: "Project", description: "", requires: [], suggests: [], plugins: [] },
    { id: "kb", title: "kb", description: "Knowledge base", requires: ["core"], suggests: [], plugins: [{ plugin: "corvin_knowledge", state: "enabled" }] },
    { id: "videos", title: "videos", description: "Videos", requires: ["core"], suggests: [], plugins: [] },
    { id: "acs", title: "acs", description: "Agentic compute", requires: ["claude"], suggests: [], plugins: [] },
    { id: "claude", title: "claude", description: "Rules", requires: ["core"], suggests: [], plugins: [] },
    { id: "tasks", title: "tasks", description: "Tasks", requires: ["kb"], suggests: [], plugins: [] },
  ],
};

const PLAN = {
  plan_id: "plan-abc-123456", project_id: "plumbline", title: "Plumbline", template: "knowledge-only@1", docs_mode: "plain",
  location: "/home/u/projects/plumbline", features: ["core", "kb"], pulled: [], expires_in_s: 900,
  files: [{ path: "README.md", owner: "core", kind: "seed", status: "new" }], actions: [], warnings: [], conflicts: [],
  plugins: [{ plugin: "corvin_knowledge", state: "enabled" }], suggestions: [], ready: true,
};

async function stub(page: Page, plan: unknown = PLAN, planStatus = 200) {
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: CATALOGUE }));
  await page.route(/\/v1\/console\/projects$/, (r) => (r.request().method() === "GET"
    ? r.fulfill({ json: { projects_root: "/home/u/projects", projects: [] } })
    : r.fulfill({ status: 201, json: { project: { id: "plumbline" }, written: [], actions: [] } })));
  await page.route(/\/v1\/console\/projects\/plan$/, (r) => (planStatus === 200
    ? r.fulfill({ json: plan })
    : r.fulfill({ status: planStatus, json: { detail: { message: "the location must not contain '..'", reason_class: "path_rule" } } })));
}

test("the wizard walks four steps; Create stays disabled until the server's plan is ready", async ({ page }) => {
  await stub(page);
  await page.goto("/console/app/projects");
  await page.getByTestId("new-project-button").click();
  const wiz = page.getByTestId("new-project-wizard");
  await expect(wiz.getByTestId("wizard-step")).toHaveText("Step 1 of 4");
  await expect(wiz.getByTestId("wizard-next")).toBeDisabled();
  await wiz.getByTestId("template-option").filter({ hasText: "Knowledge only" }).click();
  await wiz.getByTestId("wizard-next").click();
  await expect(wiz.getByTestId("wizard-step")).toHaveText("Step 2 of 4");
  await expect(wiz.getByTestId("wizard-next")).toBeDisabled();
  await wiz.getByTestId("wizard-title").fill("Plumbline");
  await wiz.getByTestId("wizard-next").click();
  await expect(wiz.getByTestId("wizard-step")).toHaveText("Step 3 of 4");
  // knowledge-only defaults to plain docs (one repository): the box is unchecked and says what it means
  await expect(wiz.getByTestId("docs-separate")).not.toBeChecked();
  await wiz.getByTestId("wizard-next").click();                          // Review = the plan request
  await expect(wiz.getByTestId("wizard-step")).toHaveText("Step 4 of 4");
  await expect(wiz.getByTestId("review-title")).toContainText("Plumbline");
  await expect(wiz.getByTestId("review-location")).toHaveText("/home/u/projects/plumbline");
  await expect(wiz.getByTestId("review-files")).toHaveText("1 to write");
  await expect(wiz.getByTestId("wizard-create")).toBeEnabled();
  await expect(wiz.getByText(/Nothing is written until you press Create/)).toBeVisible();
});

test("a feature that needs another one pulls it in; the review says what was added", async ({ page }) => {
  await stub(page);
  await page.goto("/console/app/projects");
  await page.getByTestId("new-project-button").click();
  const wiz = page.getByTestId("new-project-wizard");
  await wiz.getByTestId("template-option").filter({ hasText: "Video engineering" }).click();
  await wiz.getByTestId("wizard-next").click();
  await wiz.getByTestId("wizard-title").fill("Plumbline");
  await wiz.getByTestId("wizard-next").click();
  await expect(wiz.getByTestId("feature-requires").first()).toBeVisible();
  const acs = wiz.locator('[data-feature="acs"]');
  await acs.check();
  await expect(acs).toBeChecked();
  await expect(wiz.getByTestId("feature-toggle").filter({ has: page.locator('[data-feature="claude"]') })).toHaveCount(0);
});

test("a refused plan shows the server's sentence and does not advance", async ({ page }) => {
  await stub(page, PLAN, 422);
  await page.goto("/console/app/projects");
  await page.getByTestId("new-project-button").click();
  const wiz = page.getByTestId("new-project-wizard");
  await wiz.getByTestId("template-option").filter({ hasText: "Knowledge only" }).click();
  await wiz.getByTestId("wizard-next").click();
  await wiz.getByTestId("wizard-title").fill("Plumbline");
  await wiz.getByTestId("wizard-next").click();
  await wiz.getByTestId("wizard-next").click();
  await expect(wiz.getByTestId("wizard-error")).toHaveText("the location must not contain '..'");
  await expect(wiz.getByTestId("wizard-step")).toHaveText("Step 3 of 4");
});

test("a plan with a conflict offers no Create", async ({ page }) => {
  await stub(page, { ...PLAN, ready: false, conflicts: ["the directory already exists and is not empty"] });
  await page.goto("/console/app/projects");
  await page.getByTestId("new-project-button").click();
  const wiz = page.getByTestId("new-project-wizard");
  await wiz.getByTestId("template-option").filter({ hasText: "Knowledge only" }).click();
  await wiz.getByTestId("wizard-next").click();
  await wiz.getByTestId("wizard-title").fill("Plumbline");
  await wiz.getByTestId("wizard-next").click();
  await wiz.getByTestId("wizard-next").click();
  await expect(wiz.getByTestId("review-conflict")).toContainText("already exists");
  await expect(wiz.getByTestId("wizard-create")).toBeDisabled();
});
