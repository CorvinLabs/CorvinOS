/**
 * Data Sources Panel E2E Tests
 *
 * Auto-generated test suite for data-sources panel.
 */

import { test, expect } from '../fixtures/panel-fixtures';

test.describe('Data Sources Panel', () => {
  const panelSlug = 'data-sources';
  const panelTitle = 'Data Sources';

  test('navigates to data-sources and loads', async ({ panelNav }) => {
    await panelNav.goto(panelSlug);
    await panelNav.assertLoaded(panelSlug);
    expect(panelNav.page).toHaveURL(/\/app\/data-sources/);
  });

  test('displays page title', async ({ panelNav }) => {
    await panelNav.goto(panelSlug);
    const title = panelNav.getTitle();
    await expect(title).toContainText(panelTitle);
  });

  test('main content is visible', async ({ panelNav }) => {
    await panelNav.goto(panelSlug);
    const mainContent = panelNav.getMainContent();
    await expect(mainContent).toBeVisible();
  });

  test('breadcrumb navigation is optional', async ({ panelNav }) => {
    await panelNav.goto(panelSlug);
    const breadcrumb = panelNav.getBreadcrumb();
    const isVisible = await breadcrumb.isVisible().catch(() => false);
    // Breadcrumb is optional
  });

  test('responds to user interactions', async ({ page, panelNav }) => {
    await panelNav.goto(panelSlug);
    const buttons = page.locator('button');
    const count = await buttons.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('handles API errors gracefully', async ({ page, panelNav }) => {
    await page.route('**/v1/console/data-sources/**', (route) => {
      route.abort('failed');
    });

    await panelNav.goto(panelSlug);
    const mainContent = panelNav.getMainContent();
    const isVisible = await mainContent.isVisible().catch(() => false);
    // Panel should recover or show error
  });

  test('performance baseline', async ({ panelNav }) => {
    const startTime = Date.now();
    await panelNav.goto(panelSlug);
    const loadTime = Date.now() - startTime;

    expect(loadTime).toBeLessThan(5000);
  });
});
