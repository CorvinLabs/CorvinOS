/**
 * Role templates, protection badges, the list filter and the protection settings, on the served bundle (CONCEPT-0127).
 * The project endpoints are stubbed at the network boundary; the pages, grouping, filter and the confirmation flow are real.
 * The server's own guarantees are proven by tests/e2e/test_projects_roles_e2e.py over real HTTP.
 */
import { test, expect, type Page } from "@playwright/test";

const T = (id: string, title: string, role: string, cls: string, acc: string, outcome: string) => ({
  id, version: 1, title, description: "d", features: ["core", "kb"], offered: ["core", "kb", "tasks"], docs_mode: "nested",
  role, classification: cls, assistant_access: acc, outcome });
const TEMPLATES = {
  available: true, projects_root: "/home/u/projects", features: [
    { id: "core", title: "Project", description: "", requires: [], suggests: [], plugins: [] },
    { id: "kb", title: "kb", description: "", requires: ["core"], suggests: [], plugins: [] },
    { id: "tasks", title: "tasks", description: "", requires: ["kb"], suggests: [], plugins: [] }],
  templates: [
    T("software-development", "Software engineering", "software", "internal", "write", "code, ADRs, tests"),
    T("data-science", "Data science", "data_science", "confidential", "write", "experiments, models, reports"),
    T("marketing-campaign", "Marketing", "marketing", "internal", "write", "briefs, content calendar, copy"),
    T("sales-account", "Sales", "sales", "confidential", "read", "account plans, pipeline notes"),
    T("legal-compliance", "Legal and compliance", "legal", "confidential", "read", "matters, clause library, reviews"),
    T("knowledge-only", "Knowledge only", "knowledge", "internal", "read", "notes, concepts, decisions")],
};
const P = (id: string, role: string, cls: string, acc: string) => ({ id, path: `/home/u/projects/${id}`, template: "x@1", state: "ok", title: id.toUpperCase(),
  docs_mode: "nested", role, classification: cls, assistant_access: acc, active: ["core", "kb"], features: { core: true, kb: true } });
const LIST = { projects_root: "/home/u/projects", projects: [P("alpha", "software", "internal", "write"), P("beta", "legal", "confidential", "read"), P("gamma", "software", "confidential", "read")] };

async function stubList(page: Page) {
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: TEMPLATES }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: LIST }));
}

test("the wizard shows the roles in three families with their outcome, class and access, and the choice carries into the plan request", async ({ page }) => {
  await stubList(page);
  let planBody: Record<string, unknown> | null = null;
  await page.route(/\/v1\/console\/projects\/plan$/, (r) => { planBody = r.request().postDataJSON(); return r.fulfill({ json: {
    plan_id: "p1", project_id: "legal-x", title: "Legal X", template: "legal-compliance@1", docs_mode: "nested", location: "/home/u/projects/legal-x",
    features: ["core", "kb"], pulled: [], files: [], actions: [], warnings: [], conflicts: [], plugins: [], suggestions: [], ready: true, expires_in_s: 600,
    role: "legal", classification: "confidential", assistant_access: "write" } }); });
  await page.goto("/console/app/projects");
  await page.getByTestId("new-project-button").click();
  const fams = await page.getByTestId("template-group").evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.family));
  expect(fams).toEqual(["build", "grow", "govern"]);
  const legal = page.locator('[data-testid="template-option"][data-template="legal-compliance"]');
  await expect(legal.getByTestId("template-outcome")).toHaveText("matters, clause library, reviews");
  await expect(legal.getByTestId("class-badge")).toHaveAttribute("data-class", "confidential");
  await expect(legal.getByTestId("access-badge")).toHaveAttribute("data-access", "read");
  await legal.click();
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByTestId("wizard-title").fill("Legal X");
  await page.getByRole("button", { name: "Next" }).click();
  // step 3 starts from the role's proposal; the user changes the access
  await expect(page.locator('[data-testid="wizard-class"][data-class="confidential"]')).toBeChecked();
  await expect(page.locator('[data-testid="wizard-access"][data-access="read"]')).toBeChecked();
  await page.locator('[data-testid="wizard-access"][data-access="write"]').check();
  await page.getByRole("button", { name: "Review" }).click();
  await expect(page.getByTestId("review-protection")).toContainText("Confidential");
  expect(planBody).toMatchObject({ template: "legal-compliance", classification: "confidential", assistant_access: "write" });
});

test("the list shows role, class and access on every card, filters by role and class, and says when nothing matches", async ({ page }) => {
  await stubList(page);
  await page.goto("/console/app/projects");
  const card = (id: string) => page.locator(`[data-testid="project-card"][data-project-id="${id}"]`);
  await expect(card("beta").getByTestId("project-role")).toHaveText("Legal and compliance");
  await expect(card("beta").getByTestId("class-badge")).toHaveAttribute("data-class", "confidential");
  await expect(card("alpha").getByTestId("access-badge")).toHaveAttribute("data-access", "write");
  await expect(page.getByTestId("filter-count")).toHaveText("3 of 3");
  await page.getByTestId("filter-role").selectOption("software");
  await expect(page.getByTestId("project-card")).toHaveCount(2);
  await page.getByTestId("filter-class").selectOption("confidential");
  await expect(page.getByTestId("project-card")).toHaveCount(1);
  await expect(card("gamma")).toBeVisible();
  await page.getByTestId("filter-role").selectOption("legal");
  await expect(page.getByTestId("project-card")).toHaveCount(1);                       // beta: legal AND confidential
  await page.getByTestId("filter-class").selectOption("internal");
  await expect(page.getByTestId("filter-empty")).toContainText("No project matches");
  await page.getByTestId("filter-empty").getByRole("button", { name: "Show all" }).click();
  await expect(page.getByTestId("project-card")).toHaveCount(3);
});

test("raising the class saves at once; lowering it asks first and only then sends confirm", async ({ page }) => {
  await stubList(page);
  const detail = { ...P("alpha", "software", "confidential", "read"), catalogue: { available: true, features: [] }, directory_peers: [] };
  await page.route(/\/v1\/console\/projects\/alpha$/, (r) => r.fulfill({ json: detail }));
  await page.route(/\/v1\/console\/projects\/alpha\/health$/, (r) => r.fulfill({ json: { project_id: "alpha", ok: true, checks: [] } }));
  const bodies: Record<string, unknown>[] = [];
  await page.route(/\/v1\/console\/projects\/alpha\/settings$/, (r) => {
    const b = r.request().postDataJSON(); bodies.push(b);
    if (!b.confirm && (b.classification === "internal" || b.assistant_access === "write")) {
      return r.fulfill({ status: 409, json: { detail: { message: "needs confirmation", reason_class: "confirmation_required" } } });
    }
    return r.fulfill({ json: { ...detail, classification: b.classification ?? detail.classification, assistant_access: b.assistant_access ?? detail.assistant_access } });
  });
  await page.goto("/console/app/projects/alpha?tab=settings");
  await expect(page.getByTestId("protection-save")).toBeDisabled();                    // nothing changed yet
  await page.locator('[data-testid="settings-class"][data-class="secret"]').check();   // raising: free
  await page.getByTestId("protection-save").click();
  await expect(page.getByTestId("protection-note")).toContainText("Saved");
  expect(bodies.at(-1)).toMatchObject({ classification: "secret", confirm: false });
  await expect(page.getByTestId("protection-confirm")).toHaveCount(0);
  await page.locator('[data-testid="settings-class"][data-class="internal"]').check(); // lowering: asks
  await page.getByTestId("protection-save").click();
  await expect(page.getByTestId("protection-confirm")).toBeVisible();
  expect(bodies.at(-1)).toMatchObject({ classification: "internal", confirm: false });
  await page.getByTestId("protection-confirm-yes").click();
  await expect.poll(() => bodies.at(-1)?.confirm).toBe(true);
  await expect(page.getByTestId("protection-note")).toContainText("Saved");
});
