/**
 * Vibe Engineering group E2E — the group is ONE panel.
 *
 * Runs against the LIVE console (playwright.config baseURL =
 * http://127.0.0.1:8765/console) with the shared session from global-setup, so
 * every assertion crosses the real HTTP + router + bundle boundary. That is the
 * point: the panels these tests cover were source-correct and unreachable for
 * weeks because pages/vibe-engineering.tsx shadowed pages/vibe-engineering/ and
 * nothing exercised the mounted route.
 *
 * On 2026-09-05 the four secondary panels (Brain Monitor · Context Intelligence ·
 * Learning Hub · Session Explorer) were retired — their content is reachable as
 * tabs of the dashboard, and the duplicate sidebar entries were removed from
 * NAV_GROUPS, PANELS and the backend capability manifest.
 */
import { test, expect, type Page } from '@playwright/test';

const RETIRED = [
  'vibe-overview', 'talent', 'learning', 'learning-objectives',
  'multi-instance', 'task-graph', 'brain-status', 'debug-panel',
  // Retired 2026-09-05 — folded into the dashboard's tabs.
  'brain-monitor', 'context-intelligence', 'learning-hub', 'session-explorer',
];

async function goto(page: Page, route: string) {
  // baseURL already ends in /console, and an ABSOLUTE path would discard that
  // prefix and hit the gateway's own 404 instead of the SPA.
  await page.goto(`/console/app/${route}`);
  // NOT networkidle: the dashboard polls the audit query, so the network never
  // goes idle and every wait would burn the timeout.
  await page.waitForLoadState('domcontentloaded');
}

/** A React render crash leaves the route error boundary, not the panel. */
async function expectNoCrash(page: Page) {
  await expect(
    page.locator('text=/something went wrong|application error/i'),
  ).toHaveCount(0);
}

test.describe('Vibe Engineering group', () => {
  test('sidebar lists exactly the one current panel', async ({ page }) => {
    await goto(page, 'vibe-engineering');

    const nav = page.locator('aside, nav').first();
    await expect(
      nav.locator('a[href$="/app/vibe-engineering"]'),
      'sidebar entry for the Vibe Dashboard',
    ).toHaveCount(1);
  });

  test('no retired panel is still linked from the sidebar', async ({ page }) => {
    await goto(page, 'vibe-engineering');
    for (const route of RETIRED) {
      await expect(
        page.locator(`a[href$="/app/${route}"]`),
        `retired route ${route} must not be linked`,
      ).toHaveCount(0);
    }
  });

  test('Dashboard route renders the tabbed dashboard', async ({ page }) => {
    await goto(page, 'vibe-engineering');
    await expect(
      page.getByRole('heading', { name: /^vibe engineering$/i }),
    ).toBeVisible();
    for (const label of ['Graph View', 'Inspector', 'Timeline', 'Learning']) {
      await expect(
        page.getByRole('tab', { name: new RegExp(label, 'i') }),
        `tab ${label}`,
      ).toBeVisible();
    }
    await expectNoCrash(page);
  });

  test('Learning tab renders the learning dashboard', async ({ page }) => {
    await goto(page, 'vibe-engineering?tab=learning');
    await expect(page.locator('text=/learning score/i').first()).toBeVisible();
    await expectNoCrash(page);
  });

  test('retired routes resolve to the 404 page, not a stale panel', async ({ page }) => {
    for (const route of RETIRED) {
      await goto(page, route);
      await expect(
        page.getByRole('heading', { name: /page not found/i }),
        `${route} should 404`,
      ).toBeVisible();
    }
  });
});
