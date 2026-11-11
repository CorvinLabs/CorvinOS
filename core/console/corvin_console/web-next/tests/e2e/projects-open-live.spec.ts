/**
 * From a project tile to its details on the RUNNING console, nothing stubbed: the operator's real projects. A click on the body of a tile (not its
 * title) opens the project, the detail page shows its numbers, and the way back leads to the list - in both themes at 1280 and 375 px, with
 * screenshots for the Console Usability Gate. Read-only: nothing is created, changed or removed.
 * Run: CORVIN_E2E_ALLOW_LIVE=1 CONSOLE_BASE_URL=http://127.0.0.1:8765/console npx playwright test -c playwright.config.ts projects-open-live --project=chromium
 * Output: SHOTS_DIR (default /tmp/projects-open).
 */
import fs from "node:fs";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";

const OUT = process.env.SHOTS_DIR ?? "/tmp/projects-open";
const SIZES = [{ name: "1280", width: 1280, height: 800 }, { name: "375", width: 375, height: 812 }];

async function theme(page: Page, t: "light" | "dark") {
  await page.addInitScript((v) => { try { localStorage.setItem("corvin-theme", v); localStorage.setItem("corvin-dark-default-v2", "1"); } catch { /* ignore */ } }, t);
}
async function shot(page: Page, name: string, t: string, size: string) {
  fs.mkdirSync(OUT, { recursive: true });
  expect(await page.locator("html").getAttribute("data-theme")).toBe(t);
  await page.screenshot({ path: path.join(OUT, `${name}-${t}-${size}.png`) });
}

for (const t of ["light", "dark"] as const) {
  for (const s of SIZES) {
    test(`tile to details and back on the live console, ${t}, ${s.name}`, async ({ page }) => {
      test.setTimeout(90_000);
      await theme(page, t);
      await page.setViewportSize({ width: s.width, height: s.height });
      await page.goto("/console/app/projects", { waitUntil: "domcontentloaded" });
      const cards = page.getByTestId("project-card");
      await expect(cards.first()).toBeVisible({ timeout: 45_000 });
      await shot(page, "list", t, s.name);

      // the first project of the user's own that is usable (not the built-in Corvin tile, which has its own check below)
      // prefer a project with a task board (the richest detail page); fall back to any usable one
      const usable = page.locator('[data-testid="project-card"][data-state="ok"]:not(:has([data-testid="project-builtin"]))');
      const withTasks = usable.filter({ has: page.locator('[data-testid="project-features"]', { hasText: "Tasks" }) });
      const own = (await withTasks.count()) > 0 ? withTasks.first() : usable.first();
      await expect(own).toBeVisible();
      const id = (await own.getAttribute("data-project-id"))!;
      await expect(own.getByTestId("project-open-hint")).toHaveText("Open project");
      const box = (await own.boundingBox())!;
      await own.click({ position: { x: box.width / 2, y: box.height - 20 } });              // the lower area, far from the title
      await expect(page).toHaveURL(new RegExp(`/app/projects/${id}$`));
      await expect(page.getByTestId("project-inspector")).toBeVisible();
      const tiles = page.locator('[data-testid="glance-tile"]');
      await expect(tiles.first()).toBeVisible();
      // every tile settles on a number or an honest error - never stays a skeleton
      await expect(page.getByTestId("glance-loading")).toHaveCount(0, { timeout: 30_000 });
      expect(await page.getByTestId("glance-value").count() + await page.getByTestId("glance-error").count()).toBe(await tiles.count());
      await shot(page, "detail", t, s.name);

      await page.getByTestId("inspector-back").click();
      await expect(page).toHaveURL(/\/app\/projects$/);
      await expect(page.locator(`[data-testid="project-card"][data-project-id="${id}"]`)).toBeVisible();

      // Corvin itself: opens, shows its task and knowledge numbers, no "changes" tile
      await page.locator('[data-testid="project-card"][data-project-id="corvin"]').click({ position: { x: 40, y: 30 } });
      await expect(page).toHaveURL(/\/app\/projects\/corvin$/);
      await expect(page.getByTestId("project-inspector")).toBeVisible({ timeout: 30_000 });      // the page itself, not its loading skeleton
      await expect(page.locator('[data-testid="glance-tile"]').first()).toBeVisible();
      await expect(page.getByTestId("glance-loading")).toHaveCount(0, { timeout: 30_000 });
      const corvinTiles = await page.locator('[data-testid="glance-tile"]').evaluateAll((els) => els.map((e) => e.getAttribute("data-tile")));
      expect(corvinTiles).toEqual(["tasks", "knowledge"]);                                       // and no "changes" tile for Corvin itself
      await shot(page, "detail-corvin", t, s.name);
    });
  }
}
