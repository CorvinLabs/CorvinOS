/**
 * Phase 1 of the managed workspace on the RUNNING console, nothing stubbed (T-0387, T-0388, T-0389): the default-place sentence,
 * a wizard create with no path that lands in the Corvin workspace, the seven Settings sections, rename + Undo observed through the
 * API, Remove from the list in two clicks with the folder still there (proven by adopting it again), screenshots in both themes.
 *
 * It creates ONE throwaway project, `p1-live-check`, in the tenant's managed workspace, and leaves nothing behind: the entry is
 * removed from the list, and the one folder it created (exactly `<managed_root>/p1-live-check`, holding that project.yaml) is
 * deleted. No other project or folder is touched.
 *
 * Run: CORVIN_E2E_ALLOW_LIVE=1 CONSOLE_BASE_URL=http://127.0.0.1:8765/console \
 *      npx playwright test -c playwright.config.ts projects-settings-live --project=chromium
 */
import fs from "node:fs";
import path from "node:path";
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

const ID = "p1-live-check";
const TITLE = "P1 live check";
const OUT = process.env.SHOTS_DIR ?? "/tmp/phase1-shots";

test.describe.configure({ mode: "serial" });

async function csrf(req: APIRequestContext): Promise<string> {
  const r = await req.get("/v1/console/auth/whoami");
  expect(r.ok()).toBeTruthy();
  return (await r.json()).csrf_token as string;
}

async function list(req: APIRequestContext) {
  const r = await req.get("/v1/console/projects");
  expect(r.ok()).toBeTruthy();
  return r.json() as Promise<{ default_storage: string; managed_root: string; projects: { id: string; title?: string; path: string; storage?: string }[] }>;
}

/** Removes the throwaway entry and deletes exactly the one folder this spec created. */
async function cleanUp(req: APIRequestContext, managedRoot: string) {
  const h = { "X-CSRF-Token": await csrf(req) };
  const l = await list(req);
  if (l.projects.some((p) => p.id === ID)) await req.delete(`/v1/console/projects/${ID}`, { headers: h });
  const dir = path.join(managedRoot, ID);
  const yaml = path.join(dir, "project.yaml");
  if (path.dirname(dir) === managedRoot && fs.existsSync(yaml) && /\bid:\s*p1-live-check\b/.test(fs.readFileSync(yaml, "utf8"))) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

async function shot(page: Page, name: string) {
  fs.mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
}

test("phase 1 on the running console: create in the workspace, settings sections, rename Undo, remove keeps the folder", async ({ page }) => {
  test.setTimeout(240_000);
  const req = page.request;
  const before = await list(req);
  expect(before.projects.some((p) => p.id === ID), `a project ${ID} already exists - remove it first`).toBe(false);
  const mr = before.managed_root;
  expect(fs.existsSync(path.join(mr, ID)), "the throwaway folder already exists").toBe(false);
  const others = before.projects.map((p) => p.id).sort();
  try {
    // the list: the default place is named
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/console/app/projects", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("default-place")).toContainText(
      before.default_storage === "managed" ? "New projects go to the Corvin workspace." : "New projects go to", { timeout: 45_000 });

    // the wizard: no path, the Corvin workspace by default
    await page.getByTestId("new-project-button").click();
    const wiz = page.getByTestId("new-project-wizard");
    await wiz.locator('[data-testid="template-option"][data-template="knowledge-only"]').click();
    await wiz.getByTestId("wizard-next").click();
    await wiz.getByTestId("wizard-title").fill(TITLE);
    await expect(wiz.getByTestId("wizard-place-managed")).toBeChecked();
    await wiz.getByTestId("wizard-next").click();
    await wiz.getByTestId("wizard-next").click();
    await expect(wiz.getByTestId("review-storage")).toHaveText("Corvin workspace", { timeout: 30_000 });
    await expect(wiz.getByTestId("review-location")).toHaveText(path.join(mr, ID));
    await wiz.getByTestId("wizard-create").click();
    await expect(page.getByTestId("new-project-wizard")).toHaveCount(0, { timeout: 60_000 });
    const card = page.locator(`[data-testid="project-card"][data-project-id="${ID}"]`);
    await expect(card.getByTestId("project-where")).toHaveText(`Corvin workspace › ${ID}`, { timeout: 30_000 });
    await expect(card.getByTestId("project-where")).toHaveAttribute("data-storage", "managed");
    await shot(page, "live-list-light-1280");

    // the Settings tab: seven sections
    await page.goto(`/console/app/projects/${ID}?tab=settings`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("settings-section").locator("h2"))
      .toHaveText(["General", "Location", "Protection", "Use in a chat", "Maintenance", "Share", "Danger zone"], { timeout: 30_000 });
    await expect(page.getByTestId("location-path")).toHaveText(`Corvin workspace › ${ID}`);
    await expect(page.getByTestId("delete-row")).toContainText("goes to Trash for 30 days");
    await expect(page.getByTestId("delete-files")).toBeEnabled();
    await expect(page.getByTestId("doctor-summary")).toContainText(/\d+ checks, /, { timeout: 60_000 });
    await shot(page, "live-settings-light-1280");
    await page.setViewportSize({ width: 375, height: 812 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    await shot(page, "live-settings-light-375");
    await page.setViewportSize({ width: 1280, height: 900 });

    // rename, then Undo: the API answers the old title again
    await page.getByTestId("rename-input").fill("P1 live renamed");
    await page.getByTestId("rename-submit").click();
    await expect.poll(async () => (await (await req.get(`/v1/console/projects/${ID}`)).json()).title, { timeout: 20_000 }).toBe("P1 live renamed");
    await page.getByTestId("rename-undo").click();
    await expect.poll(async () => (await (await req.get(`/v1/console/projects/${ID}`)).json()).title, { timeout: 20_000 }).toBe(TITLE);

    // remove from the list: two clicks, the folder stays (adopting it again succeeds), then off the list again
    await page.getByTestId("remove-start").click();
    expect((await list(req)).projects.some((p) => p.id === ID)).toBe(true);
    await page.getByTestId("remove-confirm").click();
    await expect(page.getByTestId("remove-message")).toContainText("every file stay", { timeout: 20_000 });
    expect((await list(req)).projects.some((p) => p.id === ID)).toBe(false);
    expect(fs.existsSync(path.join(mr, ID, "project.yaml"))).toBe(true);
    const h = { "X-CSRF-Token": await csrf(req) };
    const back = await req.post("/v1/console/projects/adopt", { headers: h, data: { location: path.join(mr, ID) } });
    expect(back.status(), await back.text()).toBeLessThan(300);
    expect((await back.json()).id).toBe(ID);
    const again = (await list(req)).projects.find((p) => p.id === ID);
    expect(again?.storage).toBe("managed");
  } finally {
    await cleanUp(req, mr);
  }
  const after = await list(req);
  expect(after.projects.map((p) => p.id).sort()).toEqual(others);
  expect(fs.existsSync(path.join(mr, ID))).toBe(false);
});

for (const t of ["light", "dark"] as const) {
  for (const s of [{ name: "1280", width: 1280, height: 900 }, { name: "375", width: 375, height: 812 }]) {
    test(`the live list, ${t}, ${s.name}`, async ({ page }) => {
      await page.addInitScript((v) => { try { localStorage.setItem("corvin-theme", v); localStorage.setItem("corvin-dark-default-v2", "1"); } catch { /* ignore */ } }, t);
      await page.setViewportSize({ width: s.width, height: s.height });
      await page.goto("/console/app/projects", { waitUntil: "domcontentloaded" });
      await expect(page.locator('[data-testid="project-card"][data-project-id="corvin"]')).toBeVisible({ timeout: 45_000 });
      await expect(page.locator("html")).toHaveAttribute("data-theme", t);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      await shot(page, `live-list-${t}-${s.name}`);
    });
  }
}
