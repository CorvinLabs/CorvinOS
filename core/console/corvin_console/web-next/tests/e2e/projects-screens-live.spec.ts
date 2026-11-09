/**
 * The projects surfaces on the RUNNING console, nothing stubbed: the page with the built-in Corvin tile and the role cards, the chat's project
 * menu, the change view and the explorer - each in both themes at 1280 and 375 px, saved as screenshots (T-0371, T-0372, T-0373, T-0377).
 * The usability gate says "look at it": the assertions only prove the surface rendered; the screenshots are for a human to look at.
 *
 * Needs a project attached to a chat (see projects-explorer-live): EXPLORER_DEMO_SID. Output: SHOTS_DIR (default /tmp/projects-screens).
 * Run: CORVIN_E2E_ALLOW_LIVE=1 CONSOLE_BASE_URL=http://127.0.0.1:8765/console EXPLORER_DEMO_SID=<chat sid> \
 *      npx playwright test -c playwright.config.ts projects-screens-live --project=chromium
 */
import fs from "node:fs";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";

const SID = process.env.EXPLORER_DEMO_SID ?? "";
const OUT = process.env.SHOTS_DIR ?? "/tmp/projects-screens";
test.skip(!SID, "needs EXPLORER_DEMO_SID");

const SIZES = [{ name: "1280", width: 1280, height: 800 }, { name: "375", width: 375, height: 812 }];

async function theme(page: Page, t: "light" | "dark") {
  await page.addInitScript((v) => { try { localStorage.setItem("corvin-theme", v); localStorage.setItem("corvin-dark-default-v2", "1"); } catch { /* ignore */ } }, t);
}

async function shot(page: Page, name: string, t: string, size: string) {
  fs.mkdirSync(OUT, { recursive: true });
  expect(await page.locator("html").getAttribute("data-theme")).toBe(t);
  await page.screenshot({ path: path.join(OUT, `${name}-${t}-${size}.png`), fullPage: false });
}

for (const t of ["light", "dark"] as const) {
  for (const s of SIZES) {
    test(`projects surfaces, ${t}, ${s.name}`, async ({ page }) => {
      test.setTimeout(120_000);
      await theme(page, t);
      await page.setViewportSize({ width: s.width, height: s.height });

      // T-0373: the page - built-in Corvin tile first, role cards in the wizard
      await page.goto("/console/app/projects", { waitUntil: "domcontentloaded" });
      await expect(page.locator('[data-testid="project-card"][data-project-id="corvin"]')).toBeVisible({ timeout: 45_000 });
      await shot(page, "projects-page", t, s.name);
      await page.getByTestId("new-project-button").click();
      await expect(page.locator('[data-testid="template-option"]').first()).toBeVisible({ timeout: 15_000 });
      expect(await page.locator('[data-testid="template-option"]').count()).toBeGreaterThanOrEqual(9);
      await shot(page, "wizard-roles", t, s.name);

      // T-0371: the chat's project menu
      await page.goto(`/console/app/chat/${SID}`, { waitUntil: "domcontentloaded" });
      await page.getByTestId("project-menu-button").click();
      await expect(page.getByTestId("project-menu-panel")).toBeVisible({ timeout: 15_000 });
      await shot(page, "chat-project-menu", t, s.name);

      // T-0372: the change view
      await page.getByTestId("project-menu-changes").click();
      await expect(page.getByTestId("changes-panel")).toBeVisible({ timeout: 15_000 });
      await expect(page.locator('[data-testid="changes-loading"]')).toHaveCount(0, { timeout: 30_000 });
      await shot(page, "changes-panel", t, s.name);
      await page.getByTestId("changes-close").click();

      // T-0377: the explorer
      await page.getByTestId("project-menu-button").click();
      await page.getByTestId("project-menu-explorer").click();
      await expect(page.getByTestId("explorer-panel")).toBeVisible({ timeout: 15_000 });
      await expect(page.getByTestId("explorer-status")).toHaveAttribute("data-conn", "live", { timeout: 20_000 });
      await shot(page, "explorer", t, s.name);
    });
  }
}
