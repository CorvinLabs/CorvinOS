/**
 * Panel E2E Test Base Class — shared test patterns for all panels
 *
 * Provides:
 * - Standardized panel navigation and validation
 * - Performance measurement (load time, interaction time)
 * - Error handling and recovery testing
 * - Visual regression baseline support
 * - Common assertions for UI/UX
 *
 * Usage:
 *   class TestPanelDashboard extends ConsolePanel Test {
 *     constructor() {
 *       super({
 *         id: 'dashboard',
 *         title: 'Dashboard',
 *         priority: 'P0',
 *       });
 *     }
 *
 *     async testHappyPath(page, panelNav) {
 *       await panelNav.goto(this.id);
 *       await this.assertPanelLoaded(page);
 *       // Panel-specific tests
 *     }
 *   }
 */

import { Page, expect } from '@playwright/test';
import type { PanelNavigator } from '../fixtures/panel-fixtures';

export interface PanelTestConfig {
  id: string; // Panel route ID (e.g., 'dashboard')
  title: string; // Panel display name
  priority: 'P0' | 'P1' | 'P2' | 'P3' | 'P4' | 'P5' | 'P6' | 'P7'; // Test priority
  requiredFlag?: string; // Optional feature flag requirement
  group?: string; // Navigation group
}

export interface PerformanceMetrics {
  navigationStart: number;
  navigationEnd: number;
  loadTime: number;
  firstPaint?: number;
  firstContentfulPaint?: number;
  largestContentfulPaint?: number;
  networkRequests: number;
  consoleErrors: string[];
  consoleWarnings: string[];
}

/**
 * Base test class for console panels.
 * Extends this to implement panel-specific tests.
 */
export class ConsolePanelTest {
  readonly config: PanelTestConfig;

  constructor(config: PanelTestConfig) {
    this.config = config;
  }

  /**
   * Navigate to the panel and assert it loaded successfully.
   */
  async navigateAndAssert(page: Page, panelNav: PanelNavigator): Promise<void> {
    const startTime = Date.now();
    await panelNav.goto(this.config.id);
    const navigationTime = Date.now() - startTime;

    // Verify URL
    expect(page.url()).toContain(`/app/${this.config.id}`);

    // Verify main content is visible
    const mainContent = panelNav.getMainContent();
    await expect(mainContent).toBeVisible({ timeout: 5000 });

    // Verify performance baseline
    expect(navigationTime).toBeLessThan(5000);
  }

  /**
   * Assert the panel is fully loaded and responsive.
   */
  async assertPanelLoaded(page: Page, panelNav?: PanelNavigator): Promise<void> {
    // Check for common panel indicators
    const mainContent = panelNav?.getMainContent() || page.locator('main');
    await expect(mainContent).toBeVisible({ timeout: 5000 });

    // Wait for network to settle
    await page.waitForLoadState('networkidle');

    // Verify no critical errors
    const errors = await this.getConsoleErrors(page);
    const criticalErrors = errors.filter(
      (err) =>
        !err.includes('404') &&
        !err.includes('Deprecation') &&
        !err.includes('experiment')
    );
    expect(criticalErrors).toHaveLength(0);
  }

  /**
   * Assert panel title is present and matches expected value.
   */
  async assertPanelTitle(
    page: Page,
    expectedTitle: string,
    panelNav?: PanelNavigator
  ): Promise<void> {
    const title = panelNav?.getTitle() || page.locator('h1, .panel-title');
    await expect(title).toContainText(expectedTitle, { timeout: 3000 });
  }

  /**
   * Test error handling: simulate API failure and verify recovery.
   */
  async testErrorHandling(
    page: Page,
    panelNav: PanelNavigator,
    apiPattern: string = '**/v1/console/**'
  ): Promise<void> {
    // Intercept API calls and simulate failure
    let failedRequestCount = 0;
    await page.route(apiPattern, (route) => {
      if (failedRequestCount < 1) {
        failedRequestCount++;
        route.abort('failed');
      } else {
        route.continue();
      }
    });

    // Navigate to panel
    await panelNav.goto(this.config.id);

    // Panel should either recover or show graceful error
    const mainContent = panelNav.getMainContent();
    const isVisible = await mainContent.isVisible().catch(() => false);
    const errorMessage = page.locator('[role="alert"]');
    const hasError = await errorMessage.isVisible().catch(() => false);

    expect(isVisible || hasError).toBeTruthy();
  }

  /**
   * Measure panel performance (load time, paint events, network requests).
   */
  async measurePerformance(
    page: Page,
    panelNav: PanelNavigator
  ): Promise<PerformanceMetrics> {
    const consoleErrors: string[] = [];
    const consoleWarnings: string[] = [];

    // Collect console output
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        consoleErrors.push(msg.text());
      } else if (msg.type() === 'warning') {
        consoleWarnings.push(msg.text());
      }
    });

    const startTime = Date.now();

    // Navigate and measure
    await panelNav.goto(this.config.id);

    // Get performance timing
    const perfData = await page.evaluate(() => {
      const perf = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming;
      const paints = performance.getEntriesByType('paint');
      return {
        navigationStart: perf?.navigationStart || 0,
        navigationEnd: perf?.loadEventEnd || 0,
        firstPaint: paints.find((p) => p.name === 'first-paint')?.startTime,
        firstContentfulPaint: paints.find((p) => p.name === 'first-contentful-paint')?.startTime,
        largestContentfulPaint: (
          performance.getEntriesByType('largest-contentful-paint') as PerformanceEntry[]
        )
          .pop()
          ?.startTime,
      };
    });

    // Count network requests
    let networkRequests = 0;
    const requestListener = () => networkRequests++;
    page.on('request', requestListener);
    await page.waitForLoadState('networkidle');
    page.off('request', requestListener);

    const endTime = Date.now();

    return {
      navigationStart: perfData.navigationStart,
      navigationEnd: perfData.navigationEnd,
      loadTime: endTime - startTime,
      firstPaint: perfData.firstPaint,
      firstContentfulPaint: perfData.firstContentfulPaint,
      largestContentfulPaint: perfData.largestContentfulPaint,
      networkRequests,
      consoleErrors,
      consoleWarnings,
    };
  }

  /**
   * Assert performance baseline constraints.
   */
  async assertPerformanceBaseline(
    metrics: PerformanceMetrics,
    constraints: Partial<PerformanceMetrics> = {}
  ): Promise<void> {
    const defaults = {
      loadTime: 3000, // Panel should load in < 3s
      networkRequests: 15, // < 15 network requests
    };

    const checks = { ...defaults, ...constraints };

    expect(metrics.loadTime).toBeLessThan(checks.loadTime);
    expect(metrics.networkRequests).toBeLessThan(checks.networkRequests);
  }

  /**
   * Test user interactions (clicks, form submissions, etc.).
   */
  async testUserInteraction(
    page: Page,
    interactionFn: (page: Page) => Promise<void>
  ): Promise<void> {
    await interactionFn(page);
    // Verify page is still responsive
    await expect(page.locator('main')).toBeVisible({ timeout: 3000 });
  }

  /**
   * Take a visual regression baseline screenshot.
   */
  async takeBaseline(page: Page, name: string = this.config.id): Promise<void> {
    // Wait for animations to settle
    await page.waitForTimeout(500);
    await expect(page).toHaveScreenshot(`baseline-${name}.png`);
  }

  /**
   * Assert visual regression (compare against baseline).
   */
  async assertNoVisualRegression(
    page: Page,
    name: string = this.config.id
  ): Promise<void> {
    await page.waitForTimeout(500);
    await expect(page).toHaveScreenshot(`current-${name}.png`);
  }

  /**
   * Helper: collect console errors during page load.
   */
  async getConsoleErrors(page: Page): Promise<string[]> {
    return new Promise((resolve) => {
      const errors: string[] = [];
      const handler = (msg: any) => {
        if (msg.type() === 'error') {
          errors.push(msg.text());
        }
      };
      page.on('console', handler);
      setTimeout(() => {
        page.off('console', handler);
        resolve(errors);
      }, 1000);
    });
  }

  /**
   * Test panel navigation from sidebar or breadcrumb.
   */
  async testPanelNavigation(page: Page, panelNav: PanelNavigator): Promise<void> {
    // Test navigation to this panel
    const navLink = page.locator(`a[href*="${this.config.id}"]`).first();
    if (await navLink.isVisible().catch(() => false)) {
      await navLink.click();
      await page.waitForURL(`**/${this.config.id}`);
    }
  }

  /**
   * Verify panel is accessible (keyboard navigation, screen reader labels).
   */
  async testAccessibility(page: Page, panelNav: PanelNavigator): Promise<void> {
    // Check for main landmark
    const main = page.locator('main');
    await expect(main).toBeVisible();

    // Check for heading hierarchy
    const headings = page.locator('h1, h2, h3');
    const headingCount = await headings.count();
    expect(headingCount).toBeGreaterThan(0);

    // Check for keyboard navigation
    const focusableElements = page.locator(
      'button, a, input, [tabindex="0"]'
    );
    const focusableCount = await focusableElements.count();
    expect(focusableCount).toBeGreaterThan(0);
  }
}

/**
 * Parametrized test helper for running tests across multiple panels.
 */
export function createPanelTestSuite(
  panels: ConsolePanelTest[],
  options: {
    skipP4Plus?: boolean; // Skip P4–P7 panels by default
    onlyPriority?: string; // Run only tests with this priority
  } = {}
): ConsolePanelTest[] {
  return panels.filter((panel) => {
    // Skip lower-priority panels if requested
    if (options.skipP4Plus) {
      const priority = parseInt(panel.config.priority[1]);
      if (priority >= 4) return false;
    }

    // Filter by priority if specified
    if (options.onlyPriority && panel.config.priority !== options.onlyPriority) {
      return false;
    }

    return true;
  });
}
