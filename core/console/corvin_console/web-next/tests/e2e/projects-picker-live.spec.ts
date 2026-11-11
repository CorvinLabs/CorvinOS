/**
 * The folder picker on the RUNNING console, nothing stubbed (T-0392, T-0393, T-0394): the real browse routes, the real location
 * rule and the real mutations. It opens the picker from "Add existing folder", walks into a throwaway folder, sees the refusals
 * for /etc, for Corvin's own data (CORVIN_HOME) and for a folder under /tmp (a system prefix), checks the verdict BEFORE it
 * confirms, adopts the folder, moves it through the picker (two clicks), renames it by hand on disk and relinks it with
 * "Locate folder…", and checks the hint-word warning on an own folder. Screenshots in both themes at 1280 and 375 px.
 *
 * It creates exactly: ~/corvin-picker-live-<random>/ (with pkl-alpha/ and forge-lab/ inside) and /tmp/corvin-picker-live-<random>/,
 * and the project `pkl-alpha`. The `finally` removes the project from the list, deletes exactly those two folders, and puts the
 * tenant's picker.json back as it was (deleted when it did not exist before) - the moves record recent places there - and
 * removes the empty project-pkl-alpha.lock the adopt created. The
 * operator's other projects are asserted unchanged.
 *
 * Run: CORVIN_E2E_ALLOW_LIVE=1 CONSOLE_BASE_URL=http://127.0.0.1:8765/console \
 *      npx playwright test -c playwright.config.ts projects-picker-live --project=chromium
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../../../..");
const CORVIN_HOME = path.join(REPO, ".corvin");
const PICKER_JSON = path.join(CORVIN_HOME, "tenants", "_default", "projects", "picker.json");
const PICKER_LOCK = path.join(CORVIN_HOME, "tenants", "_default", "projects", "picker.lock");
const PROJECT_LOCK = path.join(CORVIN_HOME, "tenants", "_default", "projects", "project-pkl-alpha.lock");
const TAG = `corvin-picker-live-${Math.random().toString(36).slice(2, 8)}`;
const BASE = path.join(os.homedir(), TAG);
const TMP = path.join("/tmp", TAG);
const ID = "pkl-alpha";
const OUT = process.env.SHOTS_DIR ?? "/tmp/phase2-shots";

test.describe.configure({ mode: "serial" });

async function csrf(req: APIRequestContext): Promise<string> {
  const r = await req.get("/v1/console/auth/whoami");
  expect(r.ok()).toBeTruthy();
  return (await r.json()).csrf_token as string;
}

async function list(req: APIRequestContext) {
  const r = await req.get("/v1/console/projects");
  expect(r.ok()).toBeTruthy();
  return r.json() as Promise<{ projects: { id: string; path: string; state: string; title?: string; storage?: string }[] }>;
}

async function shot(page: Page, name: string) {
  fs.mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
}

async function theme(page: Page, t: "light" | "dark") {
  await page.addInitScript((v) => { try { localStorage.setItem("corvin-theme", v); localStorage.setItem("corvin-dark-default-v2", "1"); } catch { /* ignore */ } }, t);
}

async function typePath(page: Page, testid: string, p: string) {
  const picker = page.getByTestId("folder-picker");
  const link = picker.getByTestId("picker-path-link");
  if (await link.isVisible()) await link.click();
  await picker.getByTestId(testid).fill(p);
  await picker.getByTestId(testid).press("Enter");
}

test("the picker on the running console: refusals, verdict before confirm, adopt, move, locate, hint warning", async ({ page }) => {
  test.setTimeout(240_000);
  const req = page.request;
  const before = await list(req);
  expect(before.projects.some((p) => p.id === ID), `a project ${ID} already exists - remove it first`).toBe(false);
  const othersBefore = JSON.stringify(before.projects.map((p) => [p.id, p.path, p.title]).sort());
  const pickerBefore = fs.existsSync(PICKER_JSON) ? fs.readFileSync(PICKER_JSON) : null;
  const lockBefore = fs.existsSync(PICKER_LOCK);
  const projectLockBefore = fs.existsSync(PROJECT_LOCK);
  fs.mkdirSync(path.join(BASE, ID), { recursive: true });
  fs.mkdirSync(path.join(BASE, "forge-lab"), { recursive: true });
  fs.mkdirSync(TMP, { recursive: true });
  try {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/console/app/projects", { waitUntil: "domcontentloaded" });
    await page.getByTestId("adopt-start").click({ timeout: 45_000 });
    await expect(page.getByTestId("adopt-location")).toHaveCount(0);
    await page.getByTestId("adopt-folder-choose").click();
    const picker = page.getByTestId("folder-picker");
    await expect(picker.getByTestId("picker-place").first()).toBeVisible({ timeout: 20_000 });

    // Home -> the throwaway folder -> pkl-alpha, by clicking
    await picker.locator('[data-testid="picker-place"][data-kind="home"]').click();
    await expect(picker.getByTestId("picker-verdict-text")).toContainText("Top-level folders and home folders cannot be chosen", { timeout: 20_000 });
    await expect(picker.getByTestId("picker-choose")).toBeDisabled();
    await picker.locator(`[data-testid="picker-row"][data-name="${TAG}"]`).click();
    await expect(picker.getByTestId("picker-crumb-current")).toHaveText(TAG);
    await expect(picker.locator(`[data-testid="picker-row"][data-name="${ID}"]`)).toBeVisible();

    // refusals by path: /etc, Corvin's own data, a folder under /tmp
    await typePath(page, "adopt-location", "/etc");
    await expect(picker.getByTestId("picker-verdict-text")).toHaveText("This is a system folder; projects cannot live here.");
    await expect(picker.getByTestId("picker-blocked")).toHaveText("This folder cannot be opened here.");
    await expect(picker.getByTestId("picker-choose")).toBeDisabled();
    await shot(page, "live-picker-etc-1280");
    await typePath(page, "adopt-location", CORVIN_HOME);
    await expect(picker.getByTestId("picker-verdict-text")).toHaveText("This is Corvin's own data; projects cannot live here.");
    await expect(picker.getByTestId("picker-choose")).toBeDisabled();
    await typePath(page, "adopt-location", TMP);
    await expect(picker.getByTestId("picker-verdict-text")).toHaveText("This is a system folder; projects cannot live here.");
    await expect(picker.getByTestId("picker-choose")).toBeDisabled();

    // the throwaway folder: verdict first, then Choose
    await typePath(page, "adopt-location", path.join(BASE, ID));
    await expect(picker.getByTestId("picker-verdict-text")).toHaveText("Can be used");
    await expect(picker.getByTestId("picker-target")).toHaveText(path.join(BASE, ID));
    await expect(picker.getByTestId("picker-choose")).toBeEnabled();
    expect((await list(req)).projects.some((p) => p.id === ID)).toBe(false);          // nothing happened before confirm
    await shot(page, "live-picker-verdict-1280");
    await picker.getByTestId("picker-choose").click();
    await expect(page.getByTestId("adopt-folder-value")).toHaveText(path.join(BASE, ID));
    await page.getByTestId("adopt-title").fill("Picker live check");
    await page.getByTestId("adopt-submit").click();
    await expect(page.getByTestId("adopt-note")).toContainText(`Added ${ID}.`, { timeout: 30_000 });

    // hint word on an own folder: a warning, Choose stays on (nothing is adopted)
    await expect(page.getByTestId("adopt-folder-value")).toHaveCount(0);
    await page.getByTestId("adopt-folder-choose").click();
    await typePath(page, "adopt-location", path.join(BASE, "forge-lab"));
    await expect(picker.getByTestId("picker-warning")).toHaveText("The assistant may refuse some shell commands in a folder whose path contains 'forge'.");
    await expect(picker.getByTestId("picker-choose")).toBeEnabled();
    await shot(page, "live-picker-hint-1280");
    await picker.getByTestId("picker-cancel").click();
    await expect(page.getByTestId("adopt-folder-value")).toHaveCount(0);       // the form was cleared by the adopt; Cancel kept it empty

    // Move through the picker: a new folder next to it, two clicks
    await page.goto(`/console/app/projects/${ID}?tab=settings`, { waitUntil: "domcontentloaded" });
    await page.getByTestId("move-open").click({ timeout: 30_000 });
    await expect(picker.getByTestId("picker-title")).toHaveText('Choose a folder for "Picker live check"');
    await expect(picker.getByTestId("picker-crumb-current")).toHaveText(TAG, { timeout: 20_000 });
    await picker.getByTestId("picker-new-name").fill(`${ID}-moved`);
    await expect(picker.getByTestId("picker-verdict-text")).toHaveText("Can be used — a new folder will be created when you continue.", { timeout: 20_000 });
    await shot(page, "live-move-picker-1280");
    await picker.getByTestId("picker-choose").click();
    await page.getByTestId("move-start").click();
    expect((await list(req)).projects.find((p) => p.id === ID)?.path).toBe(path.join(BASE, ID));   // not before the second click
    await page.getByTestId("move-confirm").click();
    await expect(page.getByTestId("move-note")).toContainText("Moved.", { timeout: 60_000 });
    await expect.poll(async () => (await list(req)).projects.find((p) => p.id === ID)?.path, { timeout: 20_000 }).toBe(path.join(BASE, `${ID}-moved`));
    await expect(page.getByTestId("location-path")).toContainText(`${ID}-moved`, { timeout: 20_000 });
    expect(fs.existsSync(path.join(BASE, `${ID}-moved`, "project.yaml"))).toBe(true);

    // renamed by hand -> Folder missing -> Locate folder through the picker
    fs.renameSync(path.join(BASE, `${ID}-moved`), path.join(BASE, `${ID}-renamed`));
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("locate-box")).toBeVisible({ timeout: 45_000 });
    await page.getByTestId("locate-open").click();
    await expect(picker.getByTestId("picker-crumb-current")).toHaveText(TAG, { timeout: 20_000 });
    await picker.locator(`[data-testid="picker-row"][data-name="${ID}-renamed"]`).click();
    await expect(picker.getByTestId("picker-verdict-text")).toHaveText("Can be used", { timeout: 20_000 });
    await shot(page, "live-locate-picker-1280");
    await picker.getByTestId("picker-choose").click();
    await expect(page.getByTestId("locate-note")).toContainText("Found.", { timeout: 60_000 });
    await expect.poll(async () => (await list(req)).projects.find((p) => p.id === ID), { timeout: 20_000 })
      .toMatchObject({ path: path.join(BASE, `${ID}-renamed`), state: "ok" });

    // the mobile sheet on the real routes
    await page.setViewportSize({ width: 375, height: 812 });
    await page.getByTestId("move-open").click();
    await expect(picker.getByTestId("picker-crumb-current")).toHaveText(TAG, { timeout: 20_000 });
    await expect(picker.getByTestId("picker-choose")).toBeInViewport();
    await shot(page, "live-move-picker-375");
    await picker.getByTestId("picker-cancel").click();
  } finally {
    const h = { "X-CSRF-Token": await csrf(req) };
    if ((await list(req)).projects.some((p) => p.id === ID)) await req.delete(`/v1/console/projects/${ID}`, { headers: h });
    if (path.dirname(BASE) === os.homedir() && path.basename(BASE).startsWith("corvin-picker-live-")) fs.rmSync(BASE, { recursive: true, force: true });
    if (TMP.startsWith("/tmp/corvin-picker-live-")) fs.rmSync(TMP, { recursive: true, force: true });
    if (pickerBefore === null) fs.rmSync(PICKER_JSON, { force: true }); else fs.writeFileSync(PICKER_JSON, pickerBefore, { mode: 0o600 });
    if (!lockBefore) fs.rmSync(PICKER_LOCK, { force: true });
    if (!projectLockBefore) fs.rmSync(PROJECT_LOCK, { force: true });       // the empty per-project lock the adopt created
  }
  const after = await list(req);
  expect(JSON.stringify(after.projects.map((p) => [p.id, p.path, p.title]).sort())).toBe(othersBefore);
  expect(fs.existsSync(BASE)).toBe(false);
  expect(fs.existsSync(TMP)).toBe(false);
});

for (const t of ["light", "dark"] as const) {
  for (const s of [{ name: "1280", width: 1280, height: 900 }, { name: "375", width: 375, height: 812 }]) {
    test(`the live picker at home, ${t}, ${s.name}`, async ({ page }) => {
      await theme(page, t);
      await page.setViewportSize({ width: s.width, height: s.height });
      await page.goto("/console/app/projects", { waitUntil: "domcontentloaded" });
      await page.getByTestId("adopt-start").click({ timeout: 45_000 });
      await page.getByTestId("adopt-folder-choose").click();
      const picker = page.getByTestId("folder-picker");
      await expect(page.locator("html")).toHaveAttribute("data-theme", t);
      await picker.locator('[data-testid="picker-place"][data-kind="home"]').click({ timeout: 20_000 });
      await expect(picker.getByTestId("picker-row").first()).toBeVisible({ timeout: 20_000 });
      await shot(page, `live-picker-home-${t}-${s.name}`);
      await picker.getByTestId("picker-cancel").click();
    });
  }
}
