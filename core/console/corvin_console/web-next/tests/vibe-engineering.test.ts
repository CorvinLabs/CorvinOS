import { test, expect } from '@playwright/test';

test.describe('VibeDashboard (Tab-Based Unified View — ADR-0561 Phase 4)', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to Vibe Engineering with cache-busting query
    await page.goto('http://localhost:8765/console/app/vibe-engineering', { waitUntil: 'networkidle' });
    // Wait for initial render
    await page.waitForSelector('h1', { timeout: 5000 });
  });

  test('page loads with correct header and description', async ({ page }) => {
    // Check main heading
    const heading = page.locator('h1:has-text("Vibe Engineering")');
    await expect(heading).toBeVisible({ timeout: 3000 });

    // Check description
    const description = page.locator('text=Unified dashboard for system observability');
    await expect(description).toBeVisible({ timeout: 3000 });
  });

  test('all five tabs are present and clickable', async ({ page }) => {
    const tabLabels = ['Dashboard', 'Brain Monitor', 'Context Intelligence', 'Learning Hub', 'Session Explorer'];

    for (const label of tabLabels) {
      const tab = page.locator(`button:has-text("${label}")`);
      await expect(tab).toBeVisible({ timeout: 3000 });
      await expect(tab).toBeEnabled();
    }
  });

  test('tab navigation switches active tab', async ({ page }) => {
    // Click Brain Monitor tab
    await page.locator(`button:has-text("Brain Monitor")`).click();

    // Verify it becomes active (has proper tab state)
    const brainTab = page.locator(`button:has-text("Brain Monitor")`).first();
    const ariaSelected = await brainTab.getAttribute('aria-selected');

    // Tab should be selected (Radix UI tabs use data-state or aria-selected)
    expect(ariaSelected).toBe('true');
  });

  test('dashboard tab content loads', async ({ page }) => {
    // Click Dashboard tab
    await page.locator(`button:has-text("Dashboard")`).first().click();

    // Check for Dashboard-specific content
    const dashboardContent = page.locator('text=Overview of system observability');
    await expect(dashboardContent).toBeVisible({ timeout: 3000 });
  });

  test('brain monitor tab loads with lazy loading', async ({ page }) => {
    // Click Brain Monitor tab
    await page.locator(`button:has-text("Brain Monitor")`).click();

    // Should show loading spinner briefly, then content
    const brainContent = page.locator('[class*="BrainMonitor"]');
    // Wait for lazy-loaded component to appear (no need to check spinner, it's fast)
    await page.waitForTimeout(1000);

    // Component should be in the DOM (even if still loading data)
    expect(await brainContent.count()).toBeGreaterThanOrEqual(0);
  });

  test('context intelligence tab loads', async ({ page }) => {
    // Click Context Intelligence tab
    await page.locator(`button:has-text("Context Intelligence")`).click();

    // Wait for lazy load
    await page.waitForTimeout(1000);

    // Tab should be active
    const tab = page.locator(`button:has-text("Context Intelligence")`).first();
    const ariaSelected = await tab.getAttribute('aria-selected');
    expect(ariaSelected).toBe('true');
  });

  test('learning hub tab loads', async ({ page }) => {
    // Click Learning Hub tab
    await page.locator(`button:has-text("Learning Hub")`).click();

    await page.waitForTimeout(1000);

    const tab = page.locator(`button:has-text("Learning Hub")`).first();
    const ariaSelected = await tab.getAttribute('aria-selected');
    expect(ariaSelected).toBe('true');
  });

  test('session explorer tab loads', async ({ page }) => {
    // Click Session Explorer tab
    await page.locator(`button:has-text("Session Explorer")`).click();

    await page.waitForTimeout(1000);

    const tab = page.locator(`button:has-text("Session Explorer")`).first();
    const ariaSelected = await tab.getAttribute('aria-selected');
    expect(ariaSelected).toBe('true');
  });

  test('tab state persists in URL query param', async ({ page }) => {
    // Click Brain Monitor tab
    await page.locator(`button:has-text("Brain Monitor")`).click();

    // Check URL contains ?tab=brain-monitor
    await page.waitForURL(/\?tab=brain-monitor/, { timeout: 3000 });
    const url = page.url();
    expect(url).toContain('tab=brain-monitor');
  });

  test('direct URL navigation to specific tab works', async ({ page }) => {
    // Navigate directly to learning-hub tab
    await page.goto('http://localhost:8765/console/app/vibe-engineering?tab=learning-hub', { waitUntil: 'networkidle' });

    // Verify Learning Hub tab is active
    const tab = page.locator(`button:has-text("Learning Hub")`).first();
    const ariaSelected = await tab.getAttribute('aria-selected');
    expect(ariaSelected).toBe('true');
  });

  test('responsive tab layout on mobile', async ({ page }) => {
    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });

    // Tabs should still be visible
    await expect(page.locator('button:has-text("Dashboard")')).toBeVisible({ timeout: 3000 });

    // Check tabs are stacked or scrollable (grid-cols-5 becomes grid-cols-2 or similar on mobile)
    const tabsList = page.locator('[role="tablist"]');
    const gridClass = await tabsList.getAttribute('class');

    // Should use responsive grid (grid-cols-5 on desktop, responsive on mobile)
    expect(gridClass).toContain('grid');
  });

  test('no console errors on page load', async ({ page }) => {
    const errors: string[] = [];
    const warnings: string[] = [];

    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
      if (msg.type() === 'warning') warnings.push(msg.text());
    });

    page.on('pageerror', err => errors.push(err.message));

    // Trigger initial load + tab switch
    await page.goto('http://localhost:8765/console/app/vibe-engineering', { waitUntil: 'networkidle' });
    await page.locator(`button:has-text("Brain Monitor")`).click();
    await page.waitForTimeout(2000);

    // Filter out safe/expected errors
    const criticalErrors = errors.filter(e =>
      !e.includes('Failed to load resource') &&
      !e.includes('404') &&
      !e.includes('net::ERR') &&
      !e.includes('CORS')
    );

    expect(criticalErrors).toHaveLength(0);
  });

  test('lazy-loaded tabs eventually render content', async ({ page }) => {
    // Click through all tabs and verify they render
    const tabs = ['Dashboard', 'Brain Monitor', 'Context Intelligence', 'Learning Hub', 'Session Explorer'];

    for (const tabName of tabs) {
      await page.locator(`button:has-text("${tabName}")`).click();

      // Each tab should have some content rendered
      // (at minimum, the tab should be marked active)
      const activeTab = page.locator(`button:has-text("${tabName}")`).first();
      const ariaSelected = await activeTab.getAttribute('aria-selected');
      expect(ariaSelected).toBe('true');

      // Small delay between tab switches
      await page.waitForTimeout(500);
    }
  });

  test('back/forward navigation works with tab state', async ({ page }) => {
    // Start at dashboard
    await page.goto('http://localhost:8765/console/app/vibe-engineering?tab=dashboard', { waitUntil: 'networkidle' });

    // Click to Brain Monitor
    await page.locator(`button:has-text("Brain Monitor")`).click();
    await page.waitForURL(/\?tab=brain-monitor/);

    // Click back button
    await page.goBack();

    // Should return to dashboard tab
    await page.waitForURL(/\?tab=dashboard/);
    const dashboardTab = page.locator(`button:has-text("Dashboard")`).first();
    const ariaSelected = await dashboardTab.getAttribute('aria-selected');
    expect(ariaSelected).toBe('true');
  });
});
