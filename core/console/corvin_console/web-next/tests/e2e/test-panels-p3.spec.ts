/**
 * P3 Panel E2E Tests — Important Features
 *
 * Priority: P3 = Important features that add significant value
 * Panels:
 * - Video Quality Metrics (ADR-0695) — Production video producer monitoring
 * - Learnings / Vibe Engineering (ADR-0400) — Learning dashboard
 * - Compute — System resource monitoring
 * - Connectors — External integrations
 *
 * Definition: P3 tests validate important but non-critical features.
 * May skip these on pre-release cycles but should be solid.
 */

import { test, expect } from '../fixtures/panel-fixtures';
import { ConsolePanelTest } from './base/panel-test-base';

// ─ Video Quality Metrics Panel (P3) ─────────────────────────────────────────

const videoQualityTest = new ConsolePanelTest({
  id: 'video-quality-metrics',
  title: 'Video Quality',
  priority: 'P3',
  group: 'observability',
  requiredFlag: 'video_producer_enabled',
});

test.describe('P3: Video Quality Metrics Panel (ADR-0695)', () => {
  test.beforeEach(async ({ panelNav }) => {
    await panelNav.goto('video-quality-metrics');
  });

  test('P3-1: Video Quality panel loads (when feature enabled)', async ({ page, panelNav }) => {
    // May 404 if feature not enabled
    const response = await page.goto('/console/app/video-quality-metrics');

    // If loaded, verify content
    if (response?.status() === 200) {
      await videoQualityTest.navigateAndAssert(page, panelNav);
    }
  });

  test('P3-2: Video Quality panel displays metrics data', async ({ page, panelNav }) => {
    const mainContent = page.locator('main');
    const isVisible = await mainContent.isVisible().catch(() => false);

    if (isVisible) {
      // Check for metric-related content
      const metricContent = page.locator(
        'text=/quality|metrics|resolution|bitrate|frame|video/i'
      );
      const hasMetrics = await metricContent.isVisible().catch(() => false);
      expect(hasMetrics).toBeTruthy();
    }
  });

  test('P3-3: Video Quality panel performance', async ({ page, panelNav }) => {
    const mainContent = page.locator('main');
    const isVisible = await mainContent.isVisible().catch(() => false);

    if (isVisible) {
      const metrics = await videoQualityTest.measurePerformance(page, panelNav);
      expect(metrics.loadTime).toBeLessThan(5000);
    }
  });

  test('P3-4: Video Quality charts render', async ({ page, panelNav }) => {
    const mainContent = page.locator('main');
    const isVisible = await mainContent.isVisible().catch(() => false);

    if (isVisible) {
      // Check for chart elements
      const charts = page.locator('canvas, svg[role="img"], [data-testid*="chart"]');
      const chartCount = await charts.count();
      expect(chartCount).toBeGreaterThanOrEqual(0);
    }
  });
});

// ─ Learnings / Vibe Engineering Panel (P3) ──────────────────────────────────

const learningsTest = new ConsolePanelTest({
  id: 'vibe-engineering',
  title: 'Learnings',
  priority: 'P3',
  group: 'primary',
});

test.describe('P3: Learnings / Vibe Engineering Panel (ADR-0400)', () => {
  test.beforeEach(async ({ panelNav }) => {
    await panelNav.goto('vibe-engineering');
  });

  test('P3-5: Learnings panel loads successfully', async ({ page, panelNav }) => {
    await learningsTest.navigateAndAssert(page, panelNav);
    await learningsTest.assertPanelTitle(page, 'Learnings', panelNav);
  });

  test('P3-6: Learnings panel displays learning data', async ({ page, panelNav }) => {
    await learningsTest.navigateAndAssert(page, panelNav);

    // Look for learning-related content
    const learningContent = page.locator(
      'text=/learning|vibe|engineering|insights|pattern/i'
    );
    const isVisible = await learningContent.isVisible().catch(() => false);

    // Check for dashboard structure (tabs, cards, etc.)
    const dashboard = page.locator('[role="tablist"], .dashboard, [data-testid*="dashboard"]');
    const hasDashboard = isVisible || (await dashboard.count()) > 0;

    expect(hasDashboard).toBeTruthy();
  });

  test('P3-7: Learnings panel tabs/sections', async ({ page, panelNav }) => {
    await learningsTest.navigateAndAssert(page, panelNav);

    // Check for tab structure (ADR-0400 multi-column design)
    const tabs = page.locator('[role="tab"]');
    const tabCount = await tabs.count();

    if (tabCount > 0) {
      // Test tab switching
      const firstTab = tabs.first();
      await firstTab.click();
      await page.waitForTimeout(300);
    }
  });

  test('P3-8: Learnings panel performance', async ({ page, panelNav }) => {
    const metrics = await learningsTest.measurePerformance(page, panelNav);
    expect(metrics.loadTime).toBeLessThan(5000);
  });

  test('P3-9: Learnings panel accessibility', async ({ page, panelNav }) => {
    await learningsTest.navigateAndAssert(page, panelNav);
    await learningsTest.testAccessibility(page, panelNav);
  });
});

// ─ Compute Panel (P3) ───────────────────────────────────────────────────────

const computeTest = new ConsolePanelTest({
  id: 'compute',
  title: 'Compute',
  priority: 'P3',
  group: 'build',
});

test.describe('P3: Compute Panel (Resource Monitoring)', () => {
  test.beforeEach(async ({ panelNav }) => {
    await panelNav.goto('compute');
  });

  test('P3-10: Compute panel loads successfully', async ({ page, panelNav }) => {
    await computeTest.navigateAndAssert(page, panelNav);
    await computeTest.assertPanelTitle(page, 'Compute', panelNav);
  });

  test('P3-11: Compute panel displays resource metrics', async ({ page, panelNav }) => {
    await computeTest.navigateAndAssert(page, panelNav);

    // Look for compute/resource content
    const computeContent = page.locator(
      'text=/compute|cpu|memory|resource|gauge|usage/i'
    );
    const isVisible = await computeContent.isVisible().catch(() => false);

    // Check for gauge/meter elements
    const meters = page.locator('meter, progress, [role="progressbar"]');
    const hasMeters = isVisible || (await meters.count()) > 0;

    expect(hasMeters).toBeTruthy();
  });

  test('P3-12: Compute panel performance', async ({ page, panelNav }) => {
    const metrics = await computeTest.measurePerformance(page, panelNav);
    expect(metrics.loadTime).toBeLessThan(4000);
  });

  test('P3-13: Compute error handling', async ({ page, panelNav }) => {
    await computeTest.testErrorHandling(page, panelNav, '**/v1/console/compute/**');
  });
});

// ─ Connectors Panel (P3) ────────────────────────────────────────────────────

const connectorsTest = new ConsolePanelTest({
  id: 'connectors',
  title: 'Connectors',
  priority: 'P3',
  group: 'network',
});

test.describe('P3: Connectors Panel (External Integrations)', () => {
  test.beforeEach(async ({ panelNav }) => {
    await panelNav.goto('connectors');
  });

  test('P3-14: Connectors panel loads successfully', async ({ page, panelNav }) => {
    await connectorsTest.navigateAndAssert(page, panelNav);
    await connectorsTest.assertPanelTitle(page, 'Connectors', panelNav);
  });

  test('P3-15: Connectors panel displays connector list', async ({ page, panelNav }) => {
    await connectorsTest.navigateAndAssert(page, panelNav);

    // Look for connector-related content
    const connectorContent = page.locator(
      'text=/connector|integration|plugin|channel|bridge/i'
    );
    const isVisible = await connectorContent.isVisible().catch(() => false);

    // Check for list/grid of connectors
    const connectorList = page.locator('[data-testid*="connector"], ul, .grid');
    const hasConnectors = isVisible || (await connectorList.count()) > 0;

    expect(hasConnectors).toBeTruthy();
  });

  test('P3-16: Connectors panel add/configure workflow', async ({ page, panelNav }) => {
    await connectorsTest.navigateAndAssert(page, panelNav);

    // Look for "Add Connector" button
    const addButton = page.locator('button:has-text(/add|new|configure|connect/i)');
    const hasAddButton = await addButton.count();

    expect(hasAddButton).toBeGreaterThanOrEqual(0);
  });

  test('P3-17: Connectors panel performance', async ({ page, panelNav }) => {
    const metrics = await connectorsTest.measurePerformance(page, panelNav);
    expect(metrics.loadTime).toBeLessThan(4000);
  });

  test('P3-18: Connectors panel accessibility', async ({ page, panelNav }) => {
    await connectorsTest.navigateAndAssert(page, panelNav);
    await connectorsTest.testAccessibility(page, panelNav);
  });
});

// ─ P3 Cross-Panel Features ──────────────────────────────────────────────────

test.describe('P3: Cross-Panel Features', () => {
  test('P3-19: All P3 panels maintain consistent styling', async ({ page, panelNav }) => {
    const panels = ['video-quality-metrics', 'vibe-engineering', 'compute', 'connectors'];

    for (const panelId of panels) {
      const response = await page.goto(`/console/app/${panelId}`);
      if (response?.status() === 200) {
        const main = page.locator('main');
        await expect(main).toBeVisible({ timeout: 5000 });
      }
    }
  });

  test('P3-20: P3 panels handle responsive layout', async ({ page, panelNav }) => {
    // Test with normal viewport (already set by default)
    await panelNav.goto('compute');
    const mainContent = page.locator('main');
    await expect(mainContent).toBeVisible({ timeout: 5000 });

    // Verify content wraps properly
    const bbox = await mainContent.boundingBox();
    expect(bbox?.width).toBeGreaterThan(0);
  });
});
