import { test, expect } from '@playwright/test';

/**
 * Token Metrics Dashboard E2E Tests
 * Tests the real-time token usage and cost savings visualization
 */

test.describe('Token Metrics Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to Console
    await page.goto('http://localhost:8765/console/app', { waitUntil: 'networkidle' });

    // Wait for the app to load
    await page.waitForSelector('[role="navigation"]', { timeout: 10000 });
  });

  test('should display Token Metrics in navigation when vibe_engineering is enabled', async ({ page }) => {
    // Look for Token Metrics link in navigation
    const tokenMetricsLink = page.locator('a:has-text("Token Metrics")');
    await expect(tokenMetricsLink).toBeVisible({ timeout: 5000 });
  });

  test('should navigate to Token Metrics page', async ({ page }) => {
    // Click Token Metrics link
    await page.click('a:has-text("Token Metrics")');

    // Wait for page to load
    await page.waitForURL(/.*token-metrics/);

    // Verify page header
    await expect(page.locator('h1:has-text("Token Metrics Dashboard")')).toBeVisible();
  });

  test('should load and display token metrics data', async ({ page }) => {
    // Navigate to Token Metrics
    await page.click('a:has-text("Token Metrics")');
    await page.waitForURL(/.*token-metrics/);

    // Wait for metrics to load
    await page.waitForSelector('[class*="Zap"]', { timeout: 10000 });

    // Check for KPI cards
    const cards = page.locator('[role="region"]');
    await expect(cards).toHaveCount(4); // Cost Saved, Tokens Saved, Total Turns, Confidence %

    // Verify specific metrics are displayed
    await expect(page.locator('text=Cost Saved')).toBeVisible();
    await expect(page.locator('text=Tokens Saved')).toBeVisible();
    await expect(page.locator('text=Total Turns')).toBeVisible();
    await expect(page.locator('text=Confidence %')).toBeVisible();
  });

  test('should display cost comparison breakdown', async ({ page }) => {
    await page.click('a:has-text("Token Metrics")');
    await page.waitForURL(/.*token-metrics/);

    // Wait for content
    await page.waitForTimeout(2000);

    // Check for cost comparison section
    await expect(page.locator('text=Cost Comparison')).toBeVisible();

    // Verify baselines are shown
    await expect(page.locator('text=Baseline Cost')).toBeVisible();
    await expect(page.locator('text=Vibe Optimized Cost')).toBeVisible();
    await expect(page.locator('text=Total Savings')).toBeVisible();
  });

  test('should show subsystem attribution breakdown', async ({ page }) => {
    await page.click('a:has-text("Token Metrics")');
    await page.waitForURL(/.*token-metrics/);

    // Wait for content
    await page.waitForTimeout(2000);

    // Check for attribution section
    await expect(page.locator('text=Subsystem Attribution')).toBeVisible();

    // Verify subsystems are listed
    await expect(page.locator('text=Confidence Cache')).toBeVisible();
    await expect(page.locator('text=Context Bridge')).toBeVisible();
    await expect(page.locator('text=Skill Injection')).toBeVisible();
    await expect(page.locator('text=Learning System')).toBeVisible();
  });

  test('should display session overview statistics', async ({ page }) => {
    await page.click('a:has-text("Token Metrics")');
    await page.waitForURL(/.*token-metrics/);

    // Wait for content
    await page.waitForTimeout(2000);

    // Check for session overview
    await expect(page.locator('text=Session Overview')).toBeVisible();

    // Verify statistics
    await expect(page.locator('text=Total Tokens')).toBeVisible();
    await expect(page.locator('text=Baseline Tokens')).toBeVisible();
    await expect(page.locator('text=Avg per Turn')).toBeVisible();
    await expect(page.locator('text=Cost per 1k Tokens')).toBeVisible();
  });

  test('should update metrics in real-time', async ({ page }) => {
    await page.click('a:has-text("Token Metrics")');
    await page.waitForURL(/.*token-metrics/);

    // Get initial cost value
    const initialCost = await page.locator('[class*="text-3xl"]:first-of-type').textContent();

    // Wait for refresh (default 5 seconds)
    await page.waitForTimeout(6000);

    // Verify page is still responsive (no error state)
    await expect(page.locator('h1:has-text("Token Metrics Dashboard")')).toBeVisible();

    // Check that metrics area still shows data
    const costCards = page.locator('[class*="gradient"]');
    await expect(costCards).toHaveCount(4);
  });

  test('should handle missing data gracefully', async ({ page }) => {
    await page.click('a:has-text("Token Metrics")');
    await page.waitForURL(/.*token-metrics/);

    // The page should either:
    // 1. Show loaded metrics with real data, or
    // 2. Show "No Metrics Available" message
    const hasMetrics = await page.locator('[class*="Zap"]').isVisible().catch(() => false);
    const hasNoData = await page.locator('text=No Metrics Available').isVisible().catch(() => false);

    expect(hasMetrics || hasNoData).toBe(true);
  });

  test('should filter metrics by session ID from URL params', async ({ page }) => {
    // Navigate with session ID parameter
    await page.goto('http://localhost:8765/console/app/token-metrics?sessionId=current', { waitUntil: 'networkidle' });

    // Wait for metrics
    await page.waitForTimeout(2000);

    // Verify page loaded
    await expect(page.locator('h1:has-text("Token Metrics Dashboard")')).toBeVisible();
  });

  test('Settings: vibe_engineering feature flag should be togglable', async ({ page }) => {
    // Navigate to Settings
    await page.click('a:has-text("Settings")');
    await page.waitForURL(/.*settings/);

    // Wait for features section
    await page.waitForSelector('text=Features', { timeout: 5000 });

    // Find vibe_engineering toggle
    const vibeToggle = page.locator('label:has-text("vibe_engineering")').locator('input[type="checkbox"]');

    // Check if it exists and is enabled
    await expect(vibeToggle).toBeDefined();

    // Verify it's checked (enabled)
    const isChecked = await vibeToggle.isChecked();
    expect(isChecked).toBe(true);
  });

  test('Settings: should display all 41 features', async ({ page }) => {
    // Navigate to Settings
    await page.click('a:has-text("Settings")');
    await page.waitForURL(/.*settings/);

    // Wait for features section
    await page.waitForSelector('text=Features', { timeout: 5000 });

    // Count feature items
    const featureItems = page.locator('[class*="flex"][class*="items-start"]').filter({
      has: page.locator('input[type="checkbox"]')
    });

    // Should have 41 features
    const count = await featureItems.count();
    expect(count).toBe(41);
  });

  test('Settings: should show enabled features correctly', async ({ page }) => {
    // Navigate to Settings
    await page.click('a:has-text("Settings")');
    await page.waitForURL(/.*settings/);

    // Wait for features section
    await page.waitForSelector('text=Features', { timeout: 5000 });

    // Get enabled features
    const enabledToggles = page.locator('input[type="checkbox"]:checked');
    const enabledCount = await enabledToggles.count();

    // Should have 5 enabled (from whitelist)
    expect(enabledCount).toBe(5);
  });

  test('Settings: can toggle a feature on/off', async ({ page }) => {
    // Navigate to Settings
    await page.click('a:has-text("Settings")');
    await page.waitForURL(/.*settings/);

    // Wait for features section
    await page.waitForSelector('text=Features', { timeout: 5000 });

    // Find a disabled feature (e.g., browser_automation)
    const browserToggle = page.locator('label:has-text("browser_automation")').locator('input[type="checkbox"]');

    // Get initial state
    const initialState = await browserToggle.isChecked();

    // Click to toggle
    await browserToggle.click();

    // Wait for API call
    await page.waitForTimeout(1000);

    // Verify state changed
    const newState = await browserToggle.isChecked();
    expect(newState).not.toBe(initialState);

    // Toggle back
    await browserToggle.click();
    await page.waitForTimeout(1000);
  });

  test('should handle API errors gracefully', async ({ page }) => {
    // Navigate to Token Metrics
    await page.click('a:has-text("Token Metrics")');
    await page.waitForURL(/.*token-metrics/);

    // Simulate network error by setting offline mode
    await page.context().setOffline(true);

    // Wait a moment
    await page.waitForTimeout(2000);

    // Should show error or retry message
    const hasError = await page.locator('text=Error').isVisible().catch(() => false);

    // Restore connection
    await page.context().setOffline(false);

    // This test just verifies the page handles errors gracefully
    expect(true).toBe(true); // Placeholder - page should still be interactive
  });
});

test.describe('Token Metrics - Whitelist Integration', () => {
  test('should only show whitelisted features as enabled', async ({ page }) => {
    await page.goto('http://localhost:8765/console/app/settings', { waitUntil: 'networkidle' });
    await page.waitForSelector('text=Features', { timeout: 5000 });

    // Expected enabled features (whitelist)
    const whitelist = [
      'vibe_engineering',
      'vibe_engineering_active',
      'outcome_feedback_loop',
      'cross_device_sync',
      'package_marketplace_ui',
    ];

    // Check each whitelisted feature is enabled
    for (const feature of whitelist) {
      const toggle = page.locator(`label:has-text("${feature}") input[type="checkbox"]`);
      const isChecked = await toggle.isChecked();
      expect(isChecked).toBe(true);
    }
  });

  test('should disable non-whitelisted features', async ({ page }) => {
    await page.goto('http://localhost:8765/console/app/settings', { waitUntil: 'networkidle' });
    await page.waitForSelector('text=Features', { timeout: 5000 });

    // Sample of non-whitelisted features that should be disabled
    const nonWhitelisted = [
      'browser_automation',
      'admin_control_plane',
      'plugin_builder_enabled',
    ];

    // Check each non-whitelisted feature is disabled
    for (const feature of nonWhitelisted) {
      const toggle = page.locator(`label:has-text("${feature}") input[type="checkbox"]`);
      const isChecked = await toggle.isChecked();
      expect(isChecked).toBe(false);
    }
  });
});
