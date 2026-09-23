/**
 * Playwright E2E Tests for Admin Panels (Phase 2)
 * Coverage: Panel render, data binding, chart display, interactions
 * Total: 29 tests (8 per Stream 1–3 + 5 for unified dashboard)
 */

import { test, expect } from '@playwright/test';

const BASE_URL = process.env.PLAYWRIGHT_TEST_BASE_URL || 'http://localhost:8765';

/**
 * Stream 1: Workflow Optimizer Admin Panel (8 tests)
 */
test.describe('Workflow Optimizer Admin Panel', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE_URL}/console/skill-settings/workflow-optimizer`);
  });

  test('should render panel with all sections', async ({ page }) => {
    // Check header
    await expect(page.locator('h1')).toContainText('Workflow Optimizer Admin');

    // Check main sections
    await expect(page.locator('text=Confidence Timeline')).toBeVisible();
    await expect(page.locator('text=Agent Routing Distribution')).toBeVisible();
    await expect(page.locator('text=Learned Weights')).toBeVisible();
    await expect(page.locator('text=Configuration Versioning')).toBeVisible();
  });

  test('should display confidence score in header', async ({ page }) => {
    const confidenceText = page.locator('text=Current confidence:');
    await expect(confidenceText).toBeVisible();

    // Verify it shows a percentage
    const percentage = page.locator('span:has-text("%")').first();
    await expect(percentage).toBeVisible();
  });

  test('should render confidence timeline chart', async ({ page }) => {
    // Chart should be visible
    const chart = page.locator('svg').first();
    await expect(chart).toBeVisible();

    // Should have axis labels
    await expect(page.locator('text=Confidence')).toBeVisible();
  });

  test('should render routing distribution pie chart', async ({ page }) => {
    // Pie chart should show agent options
    await expect(page.locator('text=haiku')).toBeVisible();
    await expect(page.locator('text=sonnet')).toBeVisible();
    await expect(page.locator('text=opus')).toBeVisible();

    // Should show percentages
    const percentages = page.locator('text=%');
    await expect(percentages.first()).toBeVisible();
  });

  test('should display learned weights table', async ({ page }) => {
    const table = page.locator('table');
    await expect(table).toBeVisible();

    // Check table headers
    await expect(page.locator('th:has-text("Task Type")')).toBeVisible();
    await expect(page.locator('th:has-text("Haiku")')).toBeVisible();
    await expect(page.locator('th:has-text("Sonnet")')).toBeVisible();
    await expect(page.locator('th:has-text("Opus")')).toBeVisible();

    // Check for data rows
    const rows = page.locator('tbody tr');
    const count = await rows.count();
    expect(count).toBeGreaterThan(0);
  });

  test('should allow version selection', async ({ page }) => {
    const versionSelect = page.locator('select').last();
    await expect(versionSelect).toBeVisible();

    // Should have version options
    const options = page.locator('option');
    const count = await options.count();
    expect(count).toBeGreaterThan(1);
  });

  test('should display export button', async ({ page }) => {
    const exportBtn = page.locator('button:has-text("Export Weights")');
    await expect(exportBtn).toBeVisible();
    await expect(exportBtn).toBeEnabled();
  });

  test('should display refresh button', async ({ page }) => {
    const refreshBtn = page.locator('button:has-text("Refresh")');
    await expect(refreshBtn).toBeVisible();
    await expect(refreshBtn).toBeEnabled();
  });
});

/**
 * Stream 2: Security Orchestrator Admin Panel (8 tests)
 */
test.describe('Security Orchestrator Admin Panel', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE_URL}/console/skill-settings/security-orchestrator`);
  });

  test('should render security panel with all sections', async ({ page }) => {
    await expect(page.locator('h1')).toContainText('Security Orchestrator Admin');

    // Check metric cards
    await expect(page.locator('text=True Positive Rate')).toBeVisible();
    await expect(page.locator('text=False Positive Rate')).toBeVisible();
    await expect(page.locator('text=P95 Latency')).toBeVisible();
  });

  test('should display key metrics as cards', async ({ page }) => {
    // Find metric cards with percentage values
    const metricValues = page.locator('text=%');
    const count = await metricValues.count();
    expect(count).toBeGreaterThan(2);
  });

  test('should display threat detection bar chart', async ({ page }) => {
    await expect(page.locator('text=Threat Detection Breakdown')).toBeVisible();

    // Chart should be visible
    const chart = page.locator('svg').first();
    await expect(chart).toBeVisible();
  });

  test('should display threat timeline list', async ({ page }) => {
    await expect(page.locator('text=Recent Threats')).toBeVisible();

    // Should show threat items
    const threatItems = page.locator('[class*="border"][class*="rounded"]').filter({
      has: page.locator('text=' + /SQL|XSS|CSRF|threat/i),
    });
    const count = await threatItems.count();
    expect(count).toBeGreaterThan(0);
  });

  test('should expand threat details on click', async ({ page }) => {
    // Find first threat item
    const threatItem = page.locator('[class*="border"][class*="rounded"]').first();
    await threatItem.click();

    // Details should expand
    await expect(page.locator('text=Policy Applied:')).toBeVisible();
  });

  test('should display false positives table', async ({ page }) => {
    await expect(page.locator('text=False Positives')).toBeVisible();

    const table = page.locator('table');
    await expect(table).toBeVisible();

    // Check headers
    await expect(page.locator('th:has-text("Threat Type")')).toBeVisible();
    await expect(page.locator('th:has-text("Detected")')).toBeVisible();
  });

  test('should display canary rollout progress', async ({ page }) => {
    await expect(page.locator('text=Canary Rollout Status')).toBeVisible();

    // Progress bar should be visible
    const progressBar = page.locator('[class*="bg-amber"] [style*="width"]');
    await expect(progressBar).toBeVisible();
  });

  test('should display refresh button', async ({ page }) => {
    const refreshBtn = page.locator('button:has-text("Refresh")');
    await expect(refreshBtn).toBeVisible();
    await expect(refreshBtn).toBeEnabled();
  });
});

/**
 * Stream 3: Flow Guard Admin Panel (8 tests)
 */
test.describe('Flow Guard Admin Panel', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE_URL}/console/skill-settings/flow-guard`);
  });

  test('should render flow guard panel with all sections', async ({ page }) => {
    await expect(page.locator('h1')).toContainText('Flow Guard Admin');

    // Check main sections
    await expect(page.locator('text=Classification Accuracy')).toBeVisible();
    await expect(page.locator('text=Policy Matrix')).toBeVisible();
    await expect(page.locator('text=Threshold Configuration')).toBeVisible();
  });

  test('should display classification accuracy bar chart', async ({ page }) => {
    await expect(page.locator('text=Classification Accuracy by Data Class')).toBeVisible();

    const chart = page.locator('svg').first();
    await expect(chart).toBeVisible();
  });

  test('should display policy matrix table', async ({ page }) => {
    await expect(page.locator('text=Policy Matrix')).toBeVisible();

    const table = page.locator('table');
    await expect(table).toBeVisible();

    // Should have data class rows
    const rows = page.locator('tbody tr');
    const count = await rows.count();
    expect(count).toBeGreaterThan(0);
  });

  test('should show color-coded confidence in matrix cells', async ({ page }) => {
    // Should have green, yellow, or red cells
    const coloredCells = page.locator('[class*="bg-green"], [class*="bg-yellow"], [class*="bg-red"]');
    const count = await coloredCells.count();
    expect(count).toBeGreaterThan(0);
  });

  test('should display threshold configuration sliders', async ({ page }) => {
    await expect(page.locator('text=Threshold Configuration')).toBeVisible();

    // Should have range sliders
    const sliders = page.locator('input[type="range"]');
    const count = await sliders.count();
    expect(count).toBeGreaterThan(0);
  });

  test('should display override requests queue', async ({ page }) => {
    await expect(page.locator('text=Override Requests')).toBeVisible();

    // Should show request count
    const requestCount = page.locator('text=Override Requests');
    await expect(requestCount).toBeVisible();
  });

  test('should show approve/deny buttons for pending requests', async ({ page }) => {
    // Look for pending status badges
    const pendingBadges = page.locator('text=PENDING');
    const pendingCount = await pendingBadges.count();

    if (pendingCount > 0) {
      // Should have approve/deny buttons
      const approveBtn = page.locator('button:has-text("Approve")').first();
      const denyBtn = page.locator('button:has-text("Deny")').first();

      await expect(approveBtn).toBeVisible();
      await expect(denyBtn).toBeVisible();
    }
  });

  test('should display policy versioning section', async ({ page }) => {
    await expect(page.locator('text=Policy Versioning')).toBeVisible();

    const versionSelect = page.locator('select').last();
    await expect(versionSelect).toBeVisible();
  });
});

/**
 * Unified Learning Dashboard (5 tests)
 */
test.describe('Unified Learning Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE_URL}/console/skills/unified-learning-dashboard`);
  });

  test('should render dashboard with header and key metrics', async ({ page }) => {
    await expect(page.locator('h1')).toContainText('Learning Dashboard');

    // Should show key metrics
    await expect(page.locator('text=Total Feedback')).toBeVisible();
    await expect(page.locator('text=Active Skills')).toBeVisible();
    await expect(page.locator('text=Avg Confidence')).toBeVisible();
  });

  test('should display feedback volume stacked bar chart', async ({ page }) => {
    await expect(page.locator('text=Feedback Volume Over Time')).toBeVisible();

    const chart = page.locator('svg').first();
    await expect(chart).toBeVisible();

    // Should show legend for all three streams
    await expect(page.locator('text=Workflow Optimizer')).toBeVisible();
    await expect(page.locator('text=Security Orchestrator')).toBeVisible();
    await expect(page.locator('text=Flow Guard')).toBeVisible();
  });

  test('should display confidence trends multi-line chart', async ({ page }) => {
    await expect(page.locator('text=Confidence Trends')).toBeVisible();

    const charts = page.locator('svg');
    const count = await charts.count();
    expect(count).toBeGreaterThan(1);
  });

  test('should have date range filter controls', async ({ page }) => {
    // Should have start and end date inputs
    const dateInputs = page.locator('input[type="date"]');
    const count = await dateInputs.count();
    expect(count).toEqual(2);

    // Should have apply button
    const applyBtn = page.locator('button:has-text("Apply Filter")');
    await expect(applyBtn).toBeVisible();
  });

  test('should display quick links to skill panels', async ({ page }) => {
    // Should have links to all three skill admin panels
    const workflowLink = page.locator('a[href*="workflow-optimizer"]');
    const securityLink = page.locator('a[href*="security-orchestrator"]');
    const flowLink = page.locator('a[href*="flow-guard"]');

    await expect(workflowLink).toBeVisible();
    await expect(securityLink).toBeVisible();
    await expect(flowLink).toBeVisible();
  });
});

/**
 * Cross-Panel Integration Tests (2 tests)
 */
test.describe('Admin Panels Integration', () => {
  test('should have consistent styling across all panels', async ({ browser }) => {
    const context = await browser.newContext();
    const page1 = await context.newPage();
    const page2 = await context.newPage();
    const page3 = await context.newPage();

    // Load all three panels
    await page1.goto(`${BASE_URL}/console/skill-settings/workflow-optimizer`);
    await page2.goto(`${BASE_URL}/console/skill-settings/security-orchestrator`);
    await page3.goto(`${BASE_URL}/console/skill-settings/flow-guard`);

    // All should use consistent styling (dark mode, rounded borders, etc.)
    const page1BgColor = await page1.locator('body').evaluate((el) =>
      window.getComputedStyle(el).backgroundColor
    );
    const page2BgColor = await page2.locator('body').evaluate((el) =>
      window.getComputedStyle(el).backgroundColor
    );
    const page3BgColor = await page3.locator('body').evaluate((el) =>
      window.getComputedStyle(el).backgroundColor
    );

    // Background colors should match (same theme)
    expect(page1BgColor).toBe(page2BgColor);
    expect(page2BgColor).toBe(page3BgColor);

    await context.close();
  });

  test('should navigate between panels via dashboard quick links', async ({ page }) => {
    await page.goto(`${BASE_URL}/console/skills/unified-learning-dashboard`);

    // Click workflow optimizer link
    const workflowLink = page.locator('a[href*="workflow-optimizer"]');
    await workflowLink.click();

    // Should navigate to workflow optimizer panel
    await expect(page).toHaveURL(/workflow-optimizer/);
    await expect(page.locator('h1')).toContainText('Workflow Optimizer Admin');
  });
});
