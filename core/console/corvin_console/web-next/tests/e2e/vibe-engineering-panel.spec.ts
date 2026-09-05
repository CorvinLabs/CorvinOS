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
 * Learning Hub · Session Explorer) were retired from NAV_GROUPS, PANELS and the
 * backend capability manifest, and the panel itself was cut down to the
 * Learning view — the Graph View / Inspector / Timeline tabs over the audit
 * chain went with them. The route id stays `vibe-engineering`; the visible name
 * is "Learning Dashboard".
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
  // NOT networkidle: the shell polls, so the network never goes idle and every
  // wait would burn the timeout.
  await page.waitForLoadState('domcontentloaded');
  // The shell renders "Loading session…" until whoami resolves. On a console
  // that just booted that can take longer than an assertion's 5s timeout, which
  // made every panel assertion flaky. Wait for the auth gate to clear first.
  await page
    .locator('text=Loading session…')
    .waitFor({ state: 'detached', timeout: 30000 })
    .catch(() => {});
}

/** A React render crash leaves the route error boundary, not the panel. */
async function expectNoCrash(page: Page) {
  await expect(
    page.locator('text=/something went wrong|application error/i'),
  ).toHaveCount(0);
}

test.describe('Vibe Engineering group', () => {
  test('sidebar lists exactly the one current panel, named Learning Dashboard', async ({ page }) => {
    await goto(page, 'vibe-engineering');

    const nav = page.locator('aside, nav').first();
    const entry = nav.locator('a[href$="/app/vibe-engineering"]');
    await expect(entry, 'sidebar entry for the Learning Dashboard').toHaveCount(1);
    await expect(entry).toContainText(/learning dashboard/i);
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

  test('the route renders the Learning Dashboard', async ({ page }) => {
    await goto(page, 'vibe-engineering');
    await expect(
      page.getByRole('heading', { name: /^learning dashboard$/i }),
    ).toBeVisible();
    await expect(page.locator('text=/learning score/i').first()).toBeVisible();
    await expectNoCrash(page);
  });

  test('the retired audit tabs are gone', async ({ page }) => {
    await goto(page, 'vibe-engineering');
    await expect(page.locator('[role="tablist"]')).toHaveCount(0);
    for (const gone of ['Graph View', 'Inspector', 'Timeline']) {
      await expect(
        page.getByRole('tab', { name: new RegExp(gone, 'i') }),
        `retired tab ${gone}`,
      ).toHaveCount(0);
    }
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
