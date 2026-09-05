/**
 * Learning Dashboard E2E (mocked transport) — /app/vibe-engineering.
 *
 * The panel is the Learning view alone since 2026-09-05. Everything this file
 * used to assert (five tabs, then four) is gone; what it guards now is that the
 * page renders the learning content and NOT a tab bar, because both retired
 * shapes have been "restored" by a later edit before.
 */
import { test, expect } from '@playwright/test';
import { setupMockApis } from '../fixtures/mock-api';

test.describe('Learning Dashboard panel', () => {
  test.beforeEach(async ({ page }) => {
    await setupMockApis(page);
    // baseURL already ends in /console — an absolute /app/... would discard
    // that prefix and hit the gateway's own 404.
    await page.goto('/console/app/vibe-engineering', { waitUntil: 'networkidle' });
    await page.waitForSelector('h1', { timeout: 5000 });
  });

  test('renders the Learning Dashboard heading', async ({ page }) => {
    await expect(
      page.getByRole('heading', { name: /^learning dashboard$/i }),
    ).toBeVisible({ timeout: 3000 });
  });

  test('renders the learning content', async ({ page }) => {
    await expect(page.locator('text=/learning score/i').first()).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=5-System Confidence Profile')).toBeVisible();
    await expect(page.locator('text=System Health Heatmap')).toBeVisible();
  });

  test('has no tab bar and no retired view', async ({ page }) => {
    await expect(page.locator('[role="tablist"]')).toHaveCount(0);
    for (const gone of ['Graph View', 'Inspector', 'Timeline',
                        'Brain Monitor', 'Context Intelligence',
                        'Learning Hub', 'Session Explorer']) {
      await expect(
        page.getByRole('tab', { name: new RegExp(gone, 'i') }),
        `retired view ${gone}`,
      ).toHaveCount(0);
    }
  });

  test('responsive layout on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await expect(
      page.getByRole('heading', { name: /^learning dashboard$/i }),
    ).toBeVisible({ timeout: 3000 });
  });

  test('no console errors on load', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
    page.on('pageerror', (err) => errors.push(err.message));

    await page.goto('/console/app/vibe-engineering', { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);

    const criticalErrors = errors.filter((e) =>
      !e.includes('Failed to load resource') &&
      !e.includes('404') &&
      !e.includes('net::ERR') &&
      !e.includes('CORS') &&
      // The mock fixture answers the settings SSE stream with application/json,
      // so the browser aborts the EventSource. An artefact of the mock, not of
      // the page — the live-console spec covers the real stream.
      !e.includes("EventSource's response has a MIME type"),
    );
    expect(criticalErrors).toHaveLength(0);
  });
});
