/**
 * Project templates on a FRESH install (ADR-2318): boot alone installs AND enables the first-party
 * `project_templates` plugin, so the Projects panel can create a project with no operator step.
 *
 *   boot (empty CORVIN_HOME, `git archive HEAD` backend, plugin source from the marketplace) ->
 *   default_plugins.yaml provisioned in the background -> project_templates installed + enabled ->
 *   GET /projects/templates available:true with templates -> "New project" enabled on the page.
 *
 * Run (network + built SPA):  npx playwright test -c playwright.fresh-install.config.ts project-templates-default
 *
 * Nothing in this file installs anything. A missing plugin is the bug under test.
 */
import { expect, test, type Page } from "@playwright/test";

const API = "/v1/console";
const LIST = `${API}/api/v1/marketplace/default-plugins`;
const PT = "plugin:buildin-knowledge_management-project_templates";

async function login(page: Page): Promise<void> {
  const r = await page.request.get(`${API}/auth/local-login`, { maxRedirects: 0 });
  expect([200, 302]).toContain(r.status());
  await expect.poll(async () => (await page.request.get(`${API}/auth/whoami`)).status(), { timeout: 20_000 }).toBe(200);
}

const pt = async (page: Page) =>
  ((await (await page.request.get(LIST)).json()).plugins as any[]).find((p) => p.index_id === PT);

test.describe.serial("project templates on a fresh install", () => {
  test("boot alone offers, installs and enables project_templates", async ({ page }) => {
    await login(page);
    await expect.poll(async () => JSON.stringify((await pt(page)) ?? null), { timeout: 100_000, intervals: [2_000] })
      .toContain('"installed":true');
    const e = await pt(page);
    expect(e, JSON.stringify(e)).toMatchObject({ registry_id: "project_templates", offered: true, installed: true });
    await expect.poll(async () => (await pt(page)).enabled, { timeout: 30_000, intervals: [1_000] }).toBe(true);
  });

  test("the templates endpoint is available and lists templates", async ({ page }) => {
    await login(page);
    const r = await page.request.get(`${API}/projects/templates`);
    expect(r.status(), await r.text()).toBe(200);
    const t = await r.json();
    expect(t.available, JSON.stringify(t)).toBe(true);
    expect((t.templates as any[]).map((x) => x.id)).toContain("video-engineering");
  });

  test("the Projects page shows New project enabled", async ({ page }) => {
    await login(page);
    await page.goto("/console/app/projects", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("new-project-button")).toBeEnabled({ timeout: 45_000 });
  });
});
