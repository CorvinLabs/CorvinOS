/**
 * Vibe Engineering group E2E — the five-panel navigation
 * (CONSOLE_REDESIGN_UNIFIED_CONCEPT, replaces the eleven-entry legacy group).
 *
 * Runs against the LIVE console (playwright.config baseURL =
 * http://127.0.0.1:8765/console) with the shared session from global-setup, so
 * every assertion crosses the real HTTP + router + bundle boundary. That is the
 * point: the panels these tests cover were source-correct and unreachable for
 * weeks because pages/vibe-engineering.tsx shadowed pages/vibe-engineering/ and
 * nothing exercised the mounted route.
 *
 * Group: Dashboard · Brain Monitor · Context Intelligence · Learning Hub ·
 *        Session Explorer.
 */
import { test, expect, type Page } from '@playwright/test';

const RETIRED = [
  'vibe-overview', 'talent', 'learning', 'learning-objectives',
  'multi-instance', 'task-graph', 'brain-status', 'debug-panel',
];

async function goto(page: Page, route: string) {
  // baseURL already ends in /console, and an ABSOLUTE path would discard that
  // prefix and hit the gateway's own 404 instead of the SPA.
  await page.goto(`/console/app/${route}`);
  // NOT networkidle: these panels poll (/state every 5s, the vibe adapter every
  // 15s), so the network never goes idle and every wait would burn the timeout.
  await page.waitForLoadState('domcontentloaded');
}

/** A React render crash leaves the route error boundary, not the panel. */
async function expectNoCrash(page: Page) {
  await expect(
    page.locator('text=/something went wrong|application error/i'),
  ).toHaveCount(0);
}

test.describe('Vibe Engineering group', () => {
  test('sidebar lists exactly the five current panels', async ({ page }) => {
    await goto(page, 'vibe-engineering');

    const nav = page.locator('aside, nav').first();
    for (const [route, label] of [
      ['vibe-engineering', 'Dashboard'],
      ['brain-monitor', 'Brain Monitor'],
      ['context-intelligence', 'Context Intelligence'],
      ['learning-hub', 'Learning Hub'],
      ['session-explorer', 'Session Explorer'],
    ] as const) {
      await expect(
        nav.locator(`a[href$="/app/${route}"]`),
        `sidebar entry for ${label}`,
      ).toHaveCount(1);
    }
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

  test('Dashboard route renders the unified 3-column dashboard', async ({ page }) => {
    await goto(page, 'vibe-engineering');
    // The directory index (Dashboard.tsx), not the retired Context Pipeline page.
    await expect(page.getByTestId('vibe-dashboard')).toBeVisible();
    await expect(
      page.getByRole('heading', { name: /vibe engineering dashboard/i }),
    ).toBeVisible();
    await expectNoCrash(page);
  });

  test('Brain Monitor renders real pipeline telemetry', async ({ page }) => {
    await goto(page, 'brain-monitor');
    await expect(page.getByTestId('brain-monitor')).toBeVisible();
    await expect(
      page.getByRole('heading', { name: /brain monitor/i }),
    ).toBeVisible();
    // The configured-pipeline card is rendered from GET /pipeline, always.
    await expect(page.locator('text=/configured pipeline/i')).toBeVisible();
    await expectNoCrash(page);
  });

  test('Session Explorer renders the turn history', async ({ page }) => {
    await goto(page, 'session-explorer');
    await expect(page.getByTestId('session-explorer')).toBeVisible();
    await expect(
      page.getByRole('heading', { name: /session explorer/i }),
    ).toBeVisible();
    // Either sessions or the honest empty state — never a crash.
    await expectNoCrash(page);
  });

  test('Context Intelligence and Learning Hub still render', async ({ page }) => {
    // Assert a card these panels always render once /state resolves — an empty
    // `main` is just the loading spinner, which would pass a truthiness check
    // and prove nothing.
    await goto(page, 'context-intelligence');
    await expect(page.locator('text=Original Context').first()).toBeVisible();
    await expectNoCrash(page);

    await goto(page, 'learning-hub');
    await expect(page.locator('text=Talent Score').first()).toBeVisible();
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
