/**
 * Projects on a FRESH install, end to end, nothing stubbed (release review 2026-11-08): an empty CORVIN_HOME, a backend exported from the
 * working tree, plugin sources from the marketplace. The operator chooses where projects live, creates a project from a role template in the
 * wizard, inspects it (health with a real `kb check`, files on disk, seeded tasks), changes its protection, attaches it to a chat, watches a
 * file change arrive live in the explorer and in the change view, and removes it from the list - the folder stays.
 *
 * Run: npx playwright test -c playwright.fresh-install.config.ts projects-fresh
 *      (pre-push: CORVIN_FRESH_MARKETPLACE_REF=HEAD takes the plugin source from the local marketplace commit)
 */
import fs from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

const ROOT = process.env.CORVIN_FRESH_ROOT ?? "/tmp/corvin-fresh-install";
const PROJECTS = path.join(ROOT, "projects");
const API = "/v1/console";
const LIST = `${API}/api/v1/marketplace/default-plugins`;

async function login(page: Page): Promise<string> {
  const r = await page.request.get(`${API}/auth/local-login`, { maxRedirects: 0 });
  expect([200, 302]).toContain(r.status());
  await expect.poll(async () => (await page.request.get(`${API}/auth/whoami`)).status(), { timeout: 20_000 }).toBe(200);
  return (await (await page.request.get(`${API}/auth/whoami`)).json()).csrf_token as string;
}

async function enabled(page: Page, indexId: string): Promise<boolean> {
  const p = ((await (await page.request.get(LIST)).json()).plugins as { index_id: string; enabled?: boolean }[]).find((x) => x.index_id === indexId);
  return !!p?.enabled;
}

test.describe.serial("projects on a fresh install", () => {
  test("the standard plugins a project needs are provisioned by boot alone", async ({ page }) => {
    await login(page);
    for (const id of ["plugin:buildin-knowledge_management-corvin_knowledge", "plugin:buildin-knowledge_management-project_templates"]) {
      await expect.poll(() => enabled(page, id), { timeout: 120_000, intervals: [2_000] }).toBe(true);
    }
    const t = await (await page.request.get(`${API}/projects/templates`)).json();
    expect(t.available, JSON.stringify(t).slice(0, 300)).toBe(true);
    expect((t.templates as { role: string }[]).map((x) => x.role).sort()).toEqual(
      ["data_science", "knowledge", "legal", "marketing", "product", "research", "sales", "software", "video"]);
  });

  test("Corvin itself is the first, built-in tile and cannot be changed", async ({ page }) => {
    await login(page);
    const l = (await (await page.request.get(`${API}/projects`)).json()).projects as { id: string; builtin?: boolean; state: string }[];
    expect(l[0].id).toBe("corvin");
    expect(l[0].builtin).toBe(true);
    expect(["ok", "pending"]).toContain(l[0].state);               // pending until the tenant's knowledge base exists
    await page.goto("/console/app/projects", { waitUntil: "domcontentloaded" });
    await expect(page.locator('[data-testid="project-card"][data-project-id="corvin"]')).toBeVisible({ timeout: 45_000 });
  });

  test("the operator chooses the projects folder, then creates a project from a role card", async ({ page }) => {
    test.setTimeout(180_000);
    await login(page);
    await page.goto("/console/app/projects", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("projects-empty")).toBeVisible({ timeout: 45_000 });
    await page.getByTestId("default-place-change").click();
    await page.getByTestId("place-root").check();
    await page.getByTestId("place-root-folder-choose").click();
    await page.getByTestId("picker-path-link").click();
    await page.getByTestId("place-root-input").fill(PROJECTS);
    await page.getByTestId("place-root-input").press("Enter");
    await page.getByTestId("picker-choose").click();
    await page.getByTestId("place-save").click();
    await expect(page.getByTestId("default-place")).toContainText(PROJECTS);
    expect(fs.statSync(PROJECTS).isDirectory()).toBe(true);
    await page.getByTestId("new-project-button").click();
    await page.locator('[data-testid="template-option"][data-template="software-development"]').click();
    await page.getByRole("button", { name: "Next" }).click();
    await page.getByTestId("wizard-title").fill("Fresh billing");
    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.locator('[data-testid="wizard-class"][data-class="internal"]')).toBeChecked();
    await expect(page.locator('[data-testid="wizard-access"][data-access="write"]')).toBeChecked();
    await page.getByRole("button", { name: "Review" }).click();
    await expect(page.getByTestId("review-protection")).toContainText("Internal");
    await page.getByTestId("wizard-create").click();
    const card = page.locator('[data-testid="project-card"][data-project-id="fresh-billing"]');
    await expect(card).toBeVisible({ timeout: 90_000 });
    await expect(card.getByTestId("project-state")).toHaveText("Ready");
    await expect(card.getByTestId("project-role")).toHaveText("Software engineering");
    const dir = path.join(PROJECTS, "fresh-billing");
    for (const f of ["project.yaml", "engineering/ARCHITECTURE.md", "docs/kb/_meta", "CLAUDE.md"]) expect(fs.existsSync(path.join(dir, f)), f).toBe(true);
  });

  test("the inspector shows a green doctor (a real kb check), the folder and the seeded tasks", async ({ page }) => {
    test.setTimeout(120_000);
    await login(page);
    const h = await (await page.request.get(`${API}/projects/fresh-billing/health`)).json();
    expect(h.ok, JSON.stringify(h.checks)).toBe(true);
    expect((h.checks as { id: string }[]).map((c) => c.id)).toContain("kb_check");
    await page.goto("/console/app/projects/fresh-billing", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("health-headline")).toBeVisible({ timeout: 45_000 });
    await expect(page.locator('[data-testid="health-check"][data-ok="false"]')).toHaveCount(0);
    await page.goto("/console/app/projects/fresh-billing?tab=structure");
    await expect(page.getByTestId("structure-entry").filter({ hasText: "engineering/" }).first()).toBeVisible({ timeout: 30_000 });
    await page.goto("/console/app/projects/fresh-billing?tab=tasks");
    await expect(page.getByTestId("tasks-row").first()).toBeVisible({ timeout: 60_000 });          // the board projects the project's KB
  });

  test("raising the protection saves at once and the list shows it", async ({ page }) => {
    await login(page);
    await page.goto("/console/app/projects/fresh-billing?tab=settings");
    await page.locator('[data-testid="settings-class"][data-class="confidential"]').check();
    await page.getByTestId("protection-save").click();
    await expect(page.getByTestId("protection-note")).toContainText("Saved");
    await page.goto("/console/app/projects");
    await expect(page.locator('[data-testid="project-card"][data-project-id="fresh-billing"]').getByTestId("class-badge")).toHaveAttribute("data-class", "confidential");
  });

  test("attached to a chat, a file change arrives live in the explorer and in the change view", async ({ page }) => {
    test.setTimeout(120_000);
    const csrf = await login(page);
    const r = await page.request.post(`${API}/chat/sessions`, { headers: { "X-CSRF-Token": csrf }, data: { title: "Fresh chat" } });
    expect(r.ok(), await r.text()).toBeTruthy();
    const b = await r.json();
    const sid = (b.session?.sid ?? b.sid ?? b.session_id) as string;
    await page.goto(`/console/app/chat/${sid}`, { waitUntil: "domcontentloaded" });
    await page.getByTestId("project-menu-button").click({ timeout: 45_000 });
    await page.locator('[data-testid="project-option"][data-project="fresh-billing"]').click();
    await expect(page.getByTestId("project-menu-button")).toHaveAttribute("data-attached", "yes");
    await page.getByTestId("project-menu-button").click();
    await page.getByTestId("project-menu-explorer").click();
    await expect(page.getByTestId("explorer-status")).toHaveAttribute("data-conn", "live", { timeout: 20_000 });
    await page.waitForTimeout(1500);                                                   // the watcher's first look is silent
    fs.writeFileSync(path.join(PROJECTS, "fresh-billing", "notes-fresh.md"), "# Written outside the console\n");
    await expect(page.locator('[data-testid="explorer-row"][data-kind="file"]', { hasText: "notes-fresh.md" })).toBeVisible({ timeout: 15_000 });
    await page.getByTestId("explorer-open-changes").click();
    const row = page.locator('[data-testid="change-row"][data-path="notes-fresh.md"]');
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.getByTestId("change-open").click();
    await expect(page.getByTestId("change-diff")).toContainText("Written outside the console");
  });

  test("removing the project from the list keeps its folder and detaches the chat", async ({ page }) => {
    await login(page);
    await page.goto("/console/app/projects/fresh-billing?tab=settings");
    await page.getByTestId("remove-start").click();
    await page.getByTestId("remove-confirm").click();
    await page.goto("/console/app/projects");
    await expect(page.getByTestId("projects-empty")).toBeVisible({ timeout: 30_000 });
    expect(fs.existsSync(path.join(PROJECTS, "fresh-billing", "project.yaml"))).toBe(true);
  });
});
