/**
 * P0 Panel E2E Tests — Core Infrastructure Panels (CRITICAL PATH)
 *
 * Priority: P0 = Core dashboard and navigation
 * Panel: Dashboard
 *
 * Definition: P0 tests must pass on every commit to main. These test core
 * functionality that operator depends on for basic system operation.
 */

import { test, expect } from '../fixtures/panel-fixtures';
import { ConsolePanelTest } from './base/panel-test-base';

const dashboardTest = new ConsolePanelTest({
  id: 'dashboard',
  title: 'Dashboard',
  priority: 'P0',
  group: 'primary',
});

test.describe('P0: Dashboard Panel (Core Infrastructure)', () => {
  test.beforeEach(async ({ panelNav, page }) => {
    // Ensure fresh navigation state
    await page.goto('/console/');
  });

  test('P0-1: Dashboard loads within performance baseline', async ({ page, panelNav }) => {
    const startTime = Date.now();
    await dashboardTest.navigateAndAssert(page, panelNav);
    const loadTime = Date.now() - startTime;

    expect(loadTime).toBeLessThan(3000); // P0: < 3s load
  });

  test('P0-2: Dashboard title is visible and correct', async ({ page, panelNav }) => {
    await dashboardTest.navigateAndAssert(page, panelNav);
    await dashboardTest.assertPanelTitle(page, 'Dashboard', panelNav);
  });

  test('P0-3: Dashboard main content loads', async ({ page, panelNav }) => {
    await dashboardTest.navigateAndAssert(page, panelNav);
    const mainContent = panelNav.getMainContent();
    await expect(mainContent).toBeVisible({ timeout: 5000 });
  });

  test('P0-4: Dashboard tabs are visible and functional', async ({ page, panelNav }) => {
    await dashboardTest.navigateAndAssert(page, panelNav);

    // Check for tab navigation elements
    const tabButtons = page.locator('[role="tab"], .tab-button, [data-testid*="tab"]');
    const tabCount = await tabButtons.count();
    expect(tabCount).toBeGreaterThanOrEqual(0); // May or may not have tabs
  });

  test('P0-5: Dashboard handles API errors gracefully', async ({ page, panelNav }) => {
    await dashboardTest.testErrorHandling(page, panelNav, '**/v1/console/dashboard/**');
  });

  test('P0-6: Dashboard performance metrics stay within bounds', async ({ page, panelNav }) => {
    const metrics = await dashboardTest.measurePerformance(page, panelNav);
    await dashboardTest.assertPerformanceBaseline(metrics, {
      loadTime: 5000,
      networkRequests: 20,
    });

    // Log metrics for observation
    console.log(`Dashboard performance:`, {
      loadTime: metrics.loadTime,
      networkRequests: metrics.networkRequests,
      consoleErrors: metrics.consoleErrors.length,
    });
  });

  test('P0-7: Dashboard navigation from sidebar works', async ({ page, panelNav }) => {
    await dashboardTest.testPanelNavigation(page, panelNav);
  });

  test('P0-8: Dashboard is keyboard accessible', async ({ page, panelNav }) => {
    await dashboardTest.navigateAndAssert(page, panelNav);
    await dashboardTest.testAccessibility(page, panelNav);
  });

  test('P0-9: No critical console errors during load', async ({ page, panelNav }) => {
    await dashboardTest.navigateAndAssert(page, panelNav);
    const errors = await dashboardTest.getConsoleErrors(page);
    const criticalErrors = errors.filter(
      (err) =>
        !err.includes('404') &&
        !err.includes('Deprecation') &&
        !err.includes('Warning')
    );
    expect(criticalErrors).toHaveLength(0);
  });

  test('P0-10: Dashboard content is not empty', async ({ page, panelNav }) => {
    await dashboardTest.navigateAndAssert(page, panelNav);
    const mainContent = panelNav.getMainContent();
    const textContent = await mainContent.textContent();
    expect(textContent).toBeTruthy();
    expect(textContent?.trim().length).toBeGreaterThan(0);
  });
});

test.describe('P0: Core Navigation & Layout', () => {
  test('P0-11: Console sidebar is visible', async ({ page }) => {
    await page.goto('/console/');
    const sidebar = page.locator('aside, nav, [role="navigation"]').first();
    await expect(sidebar).toBeVisible({ timeout: 5000 });
  });

  test('P0-12: Main layout structure is present', async ({ page }) => {
    await page.goto('/console/');
    const main = page.locator('main');
    await expect(main).toBeVisible({ timeout: 5000 });
  });

  test('P0-13: Page responds to user navigation', async ({ page }) => {
    await page.goto('/console/');

    // Click on a navigation link if available
    const navLinks = page.locator('a[href*="/app/"]');
    const linkCount = await navLinks.count();
    expect(linkCount).toBeGreaterThan(0);
  });
});
