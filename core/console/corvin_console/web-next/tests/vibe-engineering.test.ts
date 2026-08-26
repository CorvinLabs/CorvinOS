import { test, expect } from '@playwright/test';

test.describe('Vibe Engineering Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate with cache-busting query
    await page.goto('http://localhost:8765/console/', { waitUntil: 'networkidle' });
    // Wait for app to render
    await page.waitForSelector('[class*="space-y"]', { timeout: 5000 });
  });

  test('Dashboard page loads without errors', async ({ page }) => {
    // Check no 404 or error messages in console
    const errors: string[] = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    page.on('pageerror', err => errors.push(err.message));

    await page.waitForLoadState('networkidle');
    expect(errors.filter(e => !e.includes('Failed to load resource'))).toHaveLength(0);
  });

  test('all three columns render in dashboard view', async ({ page }) => {
    // Click dashboard tab if needed
    const dashboardTab = page.locator('text=Dashboard').first();
    await dashboardTab.click();

    // Check BrainStatus column (left)
    await expect(page.locator('text=Active Task')).toBeVisible({ timeout: 3000 });
    await expect(page.locator('text=Workers')).toBeVisible({ timeout: 3000 });

    // Check ContextIntelligence column (center)
    await expect(page.locator('text=Original Context')).toBeVisible({ timeout: 3000 });
    await expect(page.locator('text=Pipeline Context')).toBeVisible({ timeout: 3000 });

    // Check LearningHub column (right)
    await expect(page.locator('text=Talent Score')).toBeVisible({ timeout: 3000 });
    await expect(page.locator('text=Learning Events')).toBeVisible({ timeout: 3000 });
  });

  test('debug panel exists and is collapsible', async ({ page }) => {
    // Scroll to bottom to find debug panel
    await page.locator('text=DEBUG').first().scrollIntoViewIfNeeded();

    // Debug panel should exist
    const debugCard = page.locator('text=Real Data Inspector').first();
    await expect(debugCard).toBeVisible({ timeout: 3000 });

    // Click to expand
    await debugCard.click();

    // Events list should appear
    await expect(page.locator('text=Latest Event')).toBeVisible({ timeout: 3000 });
    await expect(page.locator('text=Total Events')).toBeVisible({ timeout: 3000 });
  });

  test('navigation tabs switch between views', async ({ page }) => {
    const tabs = ['dashboard', 'brain', 'context', 'learning', 'sessions'];

    for (const tab of tabs) {
      const tabButton = page.locator(`text=${tab.charAt(0).toUpperCase() + tab.slice(1)}`).first();
      await tabButton.click();

      // Verify tab is active
      const activeTab = page.locator(`text=${tab.charAt(0).toUpperCase() + tab.slice(1)}`).first();
      const tabStyle = await activeTab.evaluate(el => {
        return window.getComputedStyle(el.closest('button')!).borderBottomColor;
      });

      // Active tab should have different color
      expect(tabStyle).not.toBe('rgb(0, 0, 0)');
    }
  });

  test('real data from backend displays', async ({ page }) => {
    // Intercept API call to verify real data endpoint is hit
    let apiCalled = false;
    page.on('response', response => {
      if (response.url().includes('/vibe-engineering/state')) {
        apiCalled = true;
      }
    });

    await page.goto('http://localhost:8765/console/', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1000);

    expect(apiCalled).toBe(true);
  });

  test('data updates every ~5 seconds', async ({ page }) => {
    // Track API calls
    const apiCalls: number[] = [];
    page.on('response', response => {
      if (response.url().includes('/vibe-engineering/state')) {
        apiCalls.push(Date.now());
      }
    });

    await page.goto('http://localhost:8765/console/', { waitUntil: 'networkidle' });

    // Wait for multiple polls
    await page.waitForTimeout(12000); // > 2 poll cycles

    // Should have at least 2 API calls (initial + 1 poll)
    expect(apiCalls.length).toBeGreaterThanOrEqual(2);

    // Interval should be ~5s
    if (apiCalls.length >= 2) {
      const interval = apiCalls[1] - apiCalls[0];
      expect(interval).toBeGreaterThan(4000); // At least 4s
      expect(interval).toBeLessThan(6000);   // Less than 6s
    }
  });

  test('responsive layout on mobile', async ({ page }) => {
    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });

    await page.goto('http://localhost:8765/console/', { waitUntil: 'networkidle' });

    // On mobile, columns should stack (1 column layout)
    const gridContainer = page.locator('[class*="grid"]').first();
    const computedClass = await gridContainer.getAttribute('class');

    // Should be 1 column on mobile (grid-cols-1)
    expect(computedClass).toContain('grid-cols-1');
  });

  test('responsive layout on tablet', async ({ page }) => {
    // Set tablet viewport
    await page.setViewportSize({ width: 768, height: 1024 });

    await page.goto('http://localhost:8765/console/', { waitUntil: 'networkidle' });

    // On tablet, should be 2-3 columns (md:grid-cols-2 or similar)
    const gridContainer = page.locator('[class*="grid"]').first();
    const computedClass = await gridContainer.getAttribute('class');

    expect(computedClass).toContain('md:grid-cols-2');
  });

  test('no console errors on page load', async ({ page, context }) => {
    const errors: Array<{ type: string; message: string }> = [];

    page.on('console', msg => {
      if (msg.type() === 'error') {
        errors.push({ type: 'console', message: msg.text() });
      }
    });

    page.on('pageerror', err => {
      errors.push({ type: 'pageerror', message: err.message });
    });

    await page.goto('http://localhost:8765/console/', { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);

    // Filter out known safe errors
    const criticalErrors = errors.filter(e =>
      !e.message.includes('Failed to load resource') &&
      !e.message.includes('404') &&
      !e.message.includes('net::ERR')
    );

    expect(criticalErrors).toHaveLength(0);
  });
});
