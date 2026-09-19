/**
 * Shared Playwright fixtures for Console Panel E2E testing.
 *
 * Provides:
 * - Panel navigation helpers (goto, assertLoaded, waitForElement)
 * - Common locators (title, breadcrumb, mainContent)
 * - Reusable test presets (forms, tables, modals)
 * - Performance baseline recording
 *
 * Usage:
 *   import { test, expect } from '../fixtures/panel-fixtures';
 *
 *   test('panel test', async ({ page, panelNav }) => {
 *     await panelNav.goto('dashboard');
 *     const title = panelNav.getTitle();
 *     await expect(title).toContainText('Dashboard');
 *   });
 */

import { test as base, expect, Page } from '@playwright/test';
import { setupMockApis } from './mock-api';

/**
 * Navigation utilities for panels.
 */
export class PanelNavigator {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  /**
   * Navigate to a panel by slug.
   * E.g., goto('dashboard') → /console/app/dashboard
   */
  async goto(panelSlug: string) {
    const url = `${this.baseUrl}/app/${panelSlug}`;
    await this.page.goto(url, { waitUntil: 'load', timeout: 15000 });
    // Wait for React to settle (nav links, content loaded)
    await this.page.waitForLoadState('networkidle');
  }

  /**
   * Assert the panel is loaded and responsive.
   */
  async assertLoaded(panelSlug: string) {
    // Check URL
    const currentUrl = this.page.url();
    expect(currentUrl).toContain(`/app/${panelSlug}`);

    // Check for common panel structure (layout should be present)
    const mainContent = this.page.locator('main');
    await expect(mainContent).toBeVisible({ timeout: 5000 });
  }

  /**
   * Get the main content area.
   */
  getMainContent() {
    return this.page.locator('main, [role="main"], .panel-content, .panel-main');
  }

  /**
   * Get the page title/heading.
   */
  getTitle() {
    return this.page.locator('h1, .panel-title, [data-testid="panel-title"]');
  }

  /**
   * Get breadcrumb navigation.
   */
  getBreadcrumb() {
    return this.page.locator('[aria-label="Breadcrumb"], .breadcrumb, nav[role="navigation"]');
  }

  /**
   * Wait for a specific element to be visible (useful for panels with async loading).
   */
  async waitForElement(selector: string, timeout = 10000) {
    await this.page.locator(selector).waitFor({ state: 'visible', timeout });
  }

  /**
   * Take a screenshot for visual regression testing.
   */
  async takeSnapshot(name: string) {
    await this.page.screenshot({ path: `tests/e2e/snapshots/${name}.png` });
  }

  /**
   * Record performance metrics (load time, render time).
   */
  async recordMetrics(panelSlug: string) {
    const metrics = await this.page.evaluate(() => {
      const perfData = (window as any).__PANEL_PERF || {};
      return {
        navigationStart: perfData.navigationStart || Date.now(),
        contentfulPaint: perfData.contentfulPaint || null,
        largestContentfulPaint: perfData.largestContentfulPaint || null,
      };
    });
    return metrics;
  }
}

/**
 * Presets for common panel test patterns.
 */
export const panelTestPresets = {
  /**
   * Test basic panel navigation and structure.
   */
  basicNavigation: async (page: Page, panelSlug: string, panelNav: PanelNavigator) => {
    await panelNav.goto(panelSlug);
    await panelNav.assertLoaded(panelSlug);
    const mainContent = panelNav.getMainContent();
    await expect(mainContent).toBeVisible();
  },

  /**
   * Test panel title is present and correct.
   */
  assertTitle: async (panelNav: PanelNavigator, expectedText: string) => {
    const title = panelNav.getTitle();
    await expect(title).toContainText(expectedText);
  },

  /**
   * Test form submission in a panel.
   */
  testFormSubmit: async (page: Page, formSelector: string, submitButtonText = 'Save') => {
    const form = page.locator(formSelector);
    await expect(form).toBeVisible();

    const button = form.locator(`button:has-text("${submitButtonText}")`);
    await expect(button).toBeVisible();

    // Intercept form submission
    let formSubmitted = false;
    page.on('response', (response) => {
      if (response.request().method() === 'POST' || response.request().method() === 'PUT') {
        formSubmitted = true;
      }
    });

    await button.click();
    // Wait a bit for potential submission
    await page.waitForTimeout(1000);

    return formSubmitted;
  },

  /**
   * Test a data table in the panel.
   */
  testDataTable: async (page: Page, tableSelector: string) => {
    const table = page.locator(tableSelector);
    await expect(table).toBeVisible();

    const rows = table.locator('tbody tr, [role="row"]');
    const count = await rows.count();

    return {
      rowCount: count,
      hasRows: count > 0,
      firstRow: rows.first(),
    };
  },

  /**
   * Test a modal or dialog in the panel.
   */
  testModal: async (page: Page, triggerSelector: string, modalSelector = '[role="dialog"]') => {
    const trigger = page.locator(triggerSelector);
    await trigger.click();

    const modal = page.locator(modalSelector);
    await expect(modal).toBeVisible();

    return modal;
  },

  /**
   * Test a collapsible section.
   */
  testCollapsible: async (page: Page, headerSelector: string) => {
    const header = page.locator(headerSelector);
    const content = header.locator('.. [role="region"], .. [aria-expanded]');

    // Check initial state
    const initialState = await content.isVisible();

    // Click to toggle
    await header.click();
    await page.waitForTimeout(300); // Wait for animation

    // Check new state
    const newState = await content.isVisible();

    return {
      toggled: initialState !== newState,
      nowVisible: newState,
    };
  },
};

/**
 * Custom test function with panel-specific fixtures.
 */
export const test = base.extend<{
  panelNav: PanelNavigator;
}>({
  panelNav: async ({ page, baseURL }, use) => {
    // Setup mock APIs
    await setupMockApis(page);

    // Create navigator instance
    const navigator = new PanelNavigator(page, baseURL || 'http://127.0.0.1:8765/console');

    // Provide to test
    await use(navigator);
  },
});

export { expect };
