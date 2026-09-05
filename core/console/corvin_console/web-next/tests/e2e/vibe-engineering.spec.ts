/**
 * VibeDashboard E2E — the tabbed Vibe Engineering panel (ADR-0564 Phase 5).
 *
 * The dashboard's tabs are Graph View · Inspector · Timeline · Learning.
 * The five-tab shape this file used to assert (Dashboard / Brain Monitor /
 * Context Intelligence / Learning Hub / Session Explorer) is gone: those four
 * secondary panels were retired on 2026-09-05 and the sidebar now carries the
 * dashboard alone.
 */
import { test, expect } from '@playwright/test';
import { setupMockApis } from '../fixtures/mock-api';

const TABS = ['Graph View', 'Inspector', 'Timeline', 'Learning'] as const;

test.describe('VibeDashboard (tabbed unified view)', () => {
  test.beforeEach(async ({ page }) => {
    await setupMockApis(page);
    await page.goto('/console/app/vibe-engineering', { waitUntil: 'networkidle' });
    await page.waitForSelector('h1', { timeout: 5000 });
  });

  test('page loads with correct header and description', async ({ page }) => {
    await expect(page.locator('h1:has-text("Vibe Engineering")')).toBeVisible({ timeout: 3000 });
    await expect(
      page.locator('text=Immutable audit trail visualization'),
    ).toBeVisible({ timeout: 3000 });
  });

  test('all four tabs are present and enabled', async ({ page }) => {
    for (const label of TABS) {
      const tab = page.getByRole('tab', { name: new RegExp(label, 'i') });
      await expect(tab).toBeVisible({ timeout: 3000 });
      await expect(tab).toBeEnabled();
    }
  });

  test('no retired tab is still rendered', async ({ page }) => {
    for (const gone of ['Brain Monitor', 'Context Intelligence', 'Learning Hub', 'Session Explorer']) {
      await expect(
        page.getByRole('tab', { name: new RegExp(gone, 'i') }),
        `retired tab ${gone}`,
      ).toHaveCount(0);
    }
  });

  test('every tab activates and renders content', async ({ page }) => {
    for (const label of TABS) {
      const tab = page.getByRole('tab', { name: new RegExp(label, 'i') });
      await tab.click();
      await expect(tab).toHaveAttribute('aria-selected', 'true');
      await expect(page.locator('[role="tabpanel"][data-state="active"]')).toBeVisible();
    }
  });

  test('Learning tab renders the learning dashboard (ADR-0321)', async ({ page }) => {
    await page.getByRole('tab', { name: /Learning/i }).click();
    await expect(page.locator('text=/learning score/i').first()).toBeVisible({ timeout: 5000 });
  });

  test('tab state persists in the URL query param', async ({ page }) => {
    await page.getByRole('tab', { name: /Learning/i }).click();
    await page.waitForURL(/\?tab=learning/, { timeout: 3000 });
    expect(page.url()).toContain('tab=learning');
  });

  test('direct URL navigation to a specific tab works', async ({ page }) => {
    await page.goto('/console/app/vibe-engineering?tab=timeline', { waitUntil: 'networkidle' });
    await expect(
      page.getByRole('tab', { name: /Timeline/i }),
    ).toHaveAttribute('aria-selected', 'true');
  });

  test('responsive tab layout on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await expect(page.getByRole('tab', { name: /Graph View/i })).toBeVisible({ timeout: 3000 });
    const gridClass = await page.locator('[role="tablist"]').getAttribute('class');
    expect(gridClass).toContain('grid');
  });

  test('no console errors on page load and tab switch', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
    page.on('pageerror', (err) => errors.push(err.message));

    await page.goto('/console/app/vibe-engineering', { waitUntil: 'networkidle' });
    await page.getByRole('tab', { name: /Learning/i }).click();
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

  // The dashboard syncs the tab with setSearchParams({ replace: true }) ON
  // PURPOSE: pushing a history entry per tab click buried the page the operator
  // came from under a stack of tab states. So switching tabs must NOT add a
  // history entry — Back leaves the panel.
  test('switching tabs replaces the history entry instead of pushing one', async ({ page }) => {
    await page.goto('/console/app/dashboard', { waitUntil: 'networkidle' });
    await page.goto('/console/app/vibe-engineering?tab=graph', { waitUntil: 'networkidle' });
    await page.getByRole('tab', { name: /Learning/i }).click();
    await page.waitForURL(/\?tab=learning/);

    await page.goBack();
    await page.waitForURL(/\/app\/dashboard$/);
  });
});
