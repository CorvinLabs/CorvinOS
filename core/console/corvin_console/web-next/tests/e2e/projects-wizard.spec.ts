/**
 * New-project wizard in the browser, on the served bundle. The plan and create endpoints are stubbed at
 * the network boundary: this proves the flow, the gating and the review, not the server (that is proven by
 * the HTTP E2E in CorvinOS). A real project is never created by this spec.
 */
import { test, expect, type Page } from "@playwright/test";
import { enterPath, stubPicker } from "./projects-picker-stub";

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

// ── step 2: where the project goes (T-0388, CONCEPT-0131 § C3) ──────────────────────────────────────

const MR = "/home/u/.local/share/corvin/projects/w-0123456789";

async function stubPlace(page: Page, opts: { rootSet?: boolean; refuse?: { status: number; reason: string; message: string } } = {}) {
  const st = { plans: [] as Record<string, unknown>[], created: false };
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: CATALOGUE }));
  await page.route(/\/v1\/console\/projects$/, (r) => {
    if (r.request().method() !== "GET") { st.created = true; return r.fulfill({ status: 201, json: { project: { id: "plumbline" }, written: [], actions: [] } }); }
    return r.fulfill({ json: { projects_root: "/home/u/work", projects_root_set: !!opts.rootSet, default_storage: "managed", managed_root: MR, workspace: "ok",
      projects: st.created ? [{ id: "plumbline", path: `${MR}/plumbline`, template: "knowledge-only@1", state: "ok", title: "Plumbline",
        active: ["core", "kb"], features: { core: true, kb: true }, storage: "managed", display_path: "Corvin workspace › plumbline" }] : [] } });
  });
  await page.route(/\/v1\/console\/projects\/plan$/, (r) => {
    const b = r.request().postDataJSON() as Record<string, unknown>;
    st.plans.push(b);
    if (opts.refuse && st.plans.length === 1) {
      return r.fulfill({ status: opts.refuse.status, json: { detail: { message: opts.refuse.message, reason_class: opts.refuse.reason } } });
    }
    const managed = !b.location && b.storage !== "root";
    const loc = typeof b.location === "string" ? b.location : managed ? `${MR}/plumbline` : "/home/u/work/plumbline";
    return r.fulfill({ json: { ...PLAN, location: loc, storage: managed ? "managed" : "own" } });
  });
  return st;
}

async function toStep2(page: Page) {
  await page.goto("/console/app/projects");
  await page.getByTestId("new-project-button").click();
  const wiz = page.getByTestId("new-project-wizard");
  await wiz.getByTestId("template-option").filter({ hasText: "Knowledge only" }).click();
  await wiz.getByTestId("wizard-next").click();
  await wiz.getByTestId("wizard-title").fill("Plumbline");
  return wiz;
}

test("step 2 defaults to the Corvin workspace; a create with no path lands there and the card says so", async ({ page }) => {
  const st = await stubPlace(page);
  const wiz = await toStep2(page);
  await expect(wiz.getByTestId("wizard-place-managed")).toBeChecked();
  await expect(wiz.getByTestId("wizard-place-root")).toHaveCount(0);                   // no projects folder chosen: not offered
  await expect(wiz.getByTestId("wizard-location")).toHaveCount(0);
  await wiz.getByTestId("wizard-next").click();
  await wiz.getByTestId("wizard-next").click();
  await expect(wiz.getByTestId("review-storage")).toHaveText("Corvin workspace");
  await expect(wiz.getByTestId("review-location")).toHaveText(`${MR}/plumbline`);
  expect(st.plans[0]).toMatchObject({ storage: "managed" });
  expect(st.plans[0]).not.toHaveProperty("location");
  await wiz.getByTestId("wizard-create").click();
  await expect(page.getByTestId("new-project-wizard")).toHaveCount(0);
  await expect(page.locator('[data-testid="project-card"][data-project-id="plumbline"]').getByTestId("project-where"))
    .toHaveText("Corvin workspace › plumbline");
});

test("the projects folder is offered once one is set; Choose a folder opens the picker and sends only the chosen path", async ({ page }) => {
  const st = await stubPlace(page, { rootSet: true });
  await stubPicker(page);
  const wiz = await toStep2(page);
  await expect(wiz.getByTestId("wizard-place-root")).toBeVisible();
  await expect(wiz.getByTestId("wizard-place-root-label")).toContainText("/home/u/work");
  await wiz.getByTestId("wizard-place-custom").check();
  await expect(wiz.getByTestId("wizard-next")).toBeDisabled();                         // a folder of my own needs the folder
  await expect(wiz.getByTestId("wizard-location")).toHaveCount(0);                     // no bare path field before the picker opens
  // Cancel keeps the wizard as it was: still step 2, the title kept, no folder
  await wiz.getByTestId("wizard-folder-choose").click();
  await page.getByTestId("picker-cancel").click();
  await expect(page.getByTestId("folder-picker")).toHaveCount(0);
  await expect(wiz.getByTestId("wizard-step")).toHaveText("Step 2 of 4");
  await expect(wiz.getByTestId("wizard-title")).toHaveValue("Plumbline");
  await expect(wiz.getByTestId("wizard-folder-value")).toHaveCount(0);
  await wiz.getByTestId("wizard-folder-choose").click();
  await enterPath(page, "wizard-location", "/home/u/code/plumbline");
  await page.getByTestId("picker-choose").click();
  await expect(wiz.getByTestId("wizard-folder-value")).toHaveText("/home/u/code/plumbline");
  // choosing again and cancelling restores the chosen folder
  await wiz.getByTestId("wizard-folder-choose").click();
  await page.getByTestId("picker-place").filter({ hasText: "Home" }).click();
  await page.getByTestId("picker-cancel").click();
  await expect(wiz.getByTestId("wizard-folder-value")).toHaveText("/home/u/code/plumbline");
  await wiz.getByTestId("wizard-next").click();
  await wiz.getByTestId("wizard-next").click();
  await expect(wiz.getByTestId("review-storage")).toHaveText("Own folder");
  expect(st.plans[0]).toMatchObject({ location: "/home/u/code/plumbline" });
  expect(st.plans[0]).not.toHaveProperty("storage");
});

test("a managed id the safety check would refuse brings the wizard back to the id field with the sentence", async ({ page }) => {
  const message = "Commands that name this folder would be refused by the assistant's safety check; choose another id.";
  const st = await stubPlace(page, { refuse: { status: 422, reason: "hint_word", message } });
  const wiz = await toStep2(page);
  await wiz.getByTestId("wizard-id").fill("forge-lab");
  await wiz.getByTestId("wizard-next").click();
  await wiz.getByTestId("wizard-next").click();
  await expect(wiz.getByTestId("wizard-step")).toHaveText("Step 2 of 4");
  await expect(wiz.getByTestId("wizard-id-error")).toHaveText(message);
  expect(st.plans[0]).toMatchObject({ project_id: "forge-lab", storage: "managed" });
  await wiz.getByTestId("wizard-id").fill("lab-one");
  await expect(wiz.getByTestId("wizard-id-error")).toHaveCount(0);
  await wiz.getByTestId("wizard-next").click();
  await wiz.getByTestId("wizard-next").click();
  await expect(wiz.getByTestId("wizard-step")).toHaveText("Step 4 of 4");
});

for (const theme of ["light", "dark"] as const) {
  test(`the wizard's place step at 375 px (${theme})`, async ({ page }) => {
    const out = process.env.SHOTS_DIR ?? "/tmp/phase2-shots";
    await page.setViewportSize({ width: 375, height: 812 });
    await page.addInitScript((t) => { try { localStorage.setItem("corvin-theme", t); localStorage.setItem("corvin-dark-default-v2", "1"); } catch { /* ignore */ } }, theme);
    await stubPlace(page, { rootSet: true });
    await stubPicker(page);
    const wiz = await toStep2(page);
    await wiz.getByTestId("wizard-place-custom").check();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    await page.screenshot({ path: `${out}/wizard-place-${theme}-375.png` });
    await wiz.getByTestId("wizard-folder-choose").click();
    await enterPath(page, "wizard-location", "/home/u/code/plumbline");
    await expect(page.getByTestId("picker-choose")).toBeEnabled();
    await page.screenshot({ path: `${out}/wizard-picker-${theme}-375.png` });
    await page.getByTestId("picker-choose").click();
    await page.screenshot({ path: `${out}/wizard-place-chosen-${theme}-375.png` });
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.screenshot({ path: `${out}/wizard-place-${theme}-1280.png` });
    await wiz.getByTestId("wizard-folder-choose").click();
    await expect(page.getByTestId("picker-choose")).toBeEnabled();
    await page.screenshot({ path: `${out}/wizard-picker-${theme}-1280.png` });
  });
}
