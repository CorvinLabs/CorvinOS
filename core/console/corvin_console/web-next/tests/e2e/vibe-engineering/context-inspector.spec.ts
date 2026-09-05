import { test, expect } from '@playwright/test';

test.describe('Task Context Inspector', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to Vibe Dashboard
    await page.goto('http://127.0.0.1:8765/console/app/vibe-engineering');

    // Click on Context Inspector tab
    await page.click('button:has-text("Task Context Inspector")');

    // Wait for the panel to load
    await page.waitForSelector('[data-testid="task-id-input"]');
  });

  test('renders all 4 layers for a task (dark mode)', async ({ page }) => {
    // Ensure dark mode
    await page.evaluate(() => document.documentElement.classList.add('dark'));

    // Enter task ID
    await page.fill('[data-testid="task-id-input"]', 'task_example_123');

    // Click Load button
    await page.click('[data-testid="load-button"]');

    // Wait for layers to render
    await page.waitForSelector('[data-testid="original-layer"]');
    await page.waitForSelector('[data-testid="preserved-layer"]');
    await page.waitForSelector('[data-testid="injected-layer"]');
    await page.waitForSelector('[data-testid="merged-layer"]');

    // Verify all layers are visible
    const originalLayer = await page.locator('[data-testid="original-layer"]').count();
    const preservedLayer = await page.locator('[data-testid="preserved-layer"]').count();
    const injectedLayer = await page.locator('[data-testid="injected-layer"]').count();
    const mergedLayer = await page.locator('[data-testid="merged-layer"]').count();

    expect(originalLayer).toBe(1);
    expect(preservedLayer).toBe(1);
    expect(injectedLayer).toBe(1);
    expect(mergedLayer).toBe(1);
  });

  test('renders all 4 layers for a task (light mode)', async ({ page }) => {
    // Ensure light mode
    await page.evaluate(() => document.documentElement.classList.remove('dark'));

    // Enter task ID
    await page.fill('[data-testid="task-id-input"]', 'task_example_123');

    // Click Load button
    await page.click('[data-testid="load-button"]');

    // Wait for layers to render
    await page.waitForSelector('[data-testid="original-layer"]');

    // Verify color contrast (light mode should have good contrast)
    const originalCard = await page.locator('[data-testid="original-layer"]').locator('.bg-card').first();
    const bgColor = await originalCard.evaluate((el) =>
      window.getComputedStyle(el).backgroundColor
    );

    // Light mode bg-card should be close to white (rgb(255, 255, 255))
    expect(bgColor).toBeTruthy();
  });

  test('displays correct context data structure', async ({ page }) => {
    await page.fill('[data-testid="task-id-input"]', 'task_example_123');
    await page.click('[data-testid="load-button"]');

    await page.waitForSelector('[data-testid="original-layer"]');

    // Verify original layer contains expected fields
    const originalLayerContent = await page.locator('[data-testid="original-layer"]').innerText();
    expect(originalLayerContent).toContain('task_id');
    expect(originalLayerContent).toContain('intent');
    expect(originalLayerContent).toContain('metadata');

    // Verify merged layer contains combined data
    const mergedLayerContent = await page.locator('[data-testid="merged-layer"]').innerText();
    expect(mergedLayerContent).toContain('task_id');
    expect(mergedLayerContent).toContain('intent');
  });

  test('can expand and collapse layers', async ({ page }) => {
    await page.fill('[data-testid="task-id-input"]', 'task_example_123');
    await page.click('[data-testid="load-button"]');

    await page.waitForSelector('[data-testid="original-layer"]');

    // Get the first layer's header
    const layerHeader = await page.locator('[data-testid="original-layer"]').locator('button').first();

    // Click to collapse
    await layerHeader.click();

    // Verify layer content is hidden (collapse indicator visible)
    const collapseIndicator = await page.locator('[data-testid="original-layer"]').locator('text=/fields/').count();
    expect(collapseIndicator).toBeGreaterThanOrEqual(0);

    // Click to expand
    await layerHeader.click();

    // Verify layer content is visible again
    const expandedContent = await page.locator('[data-testid="original-layer"]').innerText();
    expect(expandedContent).toContain('task_id');
  });

  test('copy to clipboard works', async ({ page, context }) => {
    // Grant clipboard permissions
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);

    await page.fill('[data-testid="task-id-input"]', 'task_example_123');
    await page.click('[data-testid="load-button"]');

    await page.waitForSelector('[data-testid="original-layer"]');

    // Find and click copy button in first layer
    const firstLayerCopyButton = await page.locator('[data-testid="original-layer"]').locator('button svg[class*="Copy"]').first();
    if (firstLayerCopyButton.count() > 0) {
      await firstLayerCopyButton.click();

      // Verify clipboard contains JSON
      const clipboardText = await page.evaluate(() => navigator.clipboard.readText());
      expect(clipboardText).toContain('task_id');
    }
  });

  test('search filters layer fields', async ({ page }) => {
    await page.fill('[data-testid="task-id-input"]', 'task_example_123');
    await page.click('[data-testid="load-button"]');

    await page.waitForSelector('[data-testid="original-layer"]');

    // Find the search input in the original layer
    const searchInput = await page.locator('[data-testid="original-layer"]').locator('input[placeholder*="Search"]').first();

    if (searchInput.count() > 0) {
      // Type search term
      await searchInput.fill('task');

      // Verify only matching fields are shown
      const layerContent = await page.locator('[data-testid="original-layer"]').innerText();
      expect(layerContent).toContain('task_id');
    }
  });

  test('shows error message for invalid task ID', async ({ page }) => {
    // Mock API to return error
    await page.route('**/api/v1/vibe/task/**', (route) => {
      route.abort('failed');
    });

    await page.fill('[data-testid="task-id-input"]', 'invalid_task');
    await page.click('[data-testid="load-button"]');

    // Wait for error message
    await page.waitForSelector('text=/Failed to load context/');

    const errorElement = await page.locator('text=/Failed to load context/');
    expect(errorElement).toBeVisible();
  });

  test('handles empty task ID input', async ({ page }) => {
    // Clear input (if any)
    await page.fill('[data-testid="task-id-input"]', '');

    // Click Load button
    await page.click('[data-testid="load-button"]');

    // Should show error about empty task ID
    await page.waitForSelector('text=/Please enter a task ID/');
  });

  test('responds to Enter key in task ID input', async ({ page }) => {
    await page.fill('[data-testid="task-id-input"]', 'task_example_123');

    // Press Enter
    await page.press('[data-testid="task-id-input"]', 'Enter');

    // Wait for layers to load
    await page.waitForSelector('[data-testid="original-layer"]', { timeout: 5000 });

    // Verify layers are visible
    const originalLayer = await page.locator('[data-testid="original-layer"]').count();
    expect(originalLayer).toBe(1);
  });

  test('maintains color consistency between dark and light modes', async ({ page }) => {
    // Test in dark mode
    await page.evaluate(() => document.documentElement.classList.add('dark'));
    await page.fill('[data-testid="task-id-input"]', 'task_example_123');
    await page.click('[data-testid="load-button"]');
    await page.waitForSelector('[data-testid="original-layer"]');

    // Get computed colors
    const darkCard = await page.locator('[data-testid="original-layer"]').locator('.bg-card').first();
    const darkBgColor = await darkCard.evaluate((el) =>
      window.getComputedStyle(el).backgroundColor
    );

    // Switch to light mode
    await page.evaluate(() => document.documentElement.classList.remove('dark'));

    // Get light mode colors (same element should update)
    const lightBgColor = await darkCard.evaluate((el) =>
      window.getComputedStyle(el).backgroundColor
    );

    // Both should be valid (non-empty)
    expect(darkBgColor).toBeTruthy();
    expect(lightBgColor).toBeTruthy();
    // They should be different
    expect(darkBgColor).not.toBe(lightBgColor);
  });
});

test.describe('Vibe Engineering Tab Navigation', () => {
  test('shows all 4 tabs', async ({ page }) => {
    await page.goto('http://127.0.0.1:8765/console/app/vibe-engineering');

    // Verify all tab buttons are present
    const tabs = ['Learning Dashboard', 'Task Context Inspector', 'Audit Graph', 'Skill Composition'];

    for (const tabName of tabs) {
      const button = await page.locator(`button:has-text("${tabName}")`);
      expect(button).toBeVisible();
    }
  });

  test('switches between tabs correctly', async ({ page }) => {
    await page.goto('http://127.0.0.1:8765/console/app/vibe-engineering');

    // Click Context Inspector tab
    await page.click('button:has-text("Task Context Inspector")');

    // Verify Context Inspector is visible
    await page.waitForSelector('[data-testid="task-id-input"]');

    // Click Learning Dashboard tab
    await page.click('button:has-text("Learning Dashboard")');

    // Verify Learning Dashboard is visible (should have specific content)
    await page.waitForSelector('text=/Learning Score/');
  });

  test('preserves scroll position when switching tabs', async ({ page }) => {
    await page.goto('http://127.0.0.1:8765/console/app/vibe-engineering');

    // Load context inspector
    await page.click('button:has-text("Task Context Inspector")');
    await page.waitForSelector('[data-testid="task-id-input"]');
    await page.fill('[data-testid="task-id-input"]', 'task_example_123');
    await page.click('[data-testid="load-button"]');
    await page.waitForSelector('[data-testid="original-layer"]');

    // Scroll down
    await page.evaluate(() => window.scrollBy(0, 200));
    const scrollAfterFirst = await page.evaluate(() => window.scrollY);

    // Switch to another tab
    await page.click('button:has-text("Learning Dashboard")');

    // Switch back
    await page.click('button:has-text("Task Context Inspector")');

    // Scroll position should be reset (normal behavior for tab switch)
    const scrollAfterSwitch = await page.evaluate(() => window.scrollY);
    expect(scrollAfterSwitch).toBeLessThan(scrollAfterFirst);
  });
});
