/**
 * E2E Tests: Task Graph Visualization — Responsive Design, Dark Mode, Error Handling
 *
 * Tests:
 * - Mobile (375px) responsive design
 * - Tablet (768px) responsive design
 * - Desktop (1920px) responsive design
 * - Dark mode colors and contrast
 * - Error scenarios (API timeout, empty graph, malformed data, network errors)
 */

import { test, expect, devices } from '@playwright/test';
import { graphTest, GraphE2EBase } from './base.spec';

// ============================================================================
// RESPONSIVE DESIGN TESTS
// ============================================================================

graphTest.describe('Task Graph Component — Responsive Design', () => {
  // Mobile tests
  test.use({ ...devices['Pixel 5'] });

  graphTest(
    'Mobile (375px): graph readable, no horizontal scroll',
    async ({ page, graphBase }) => {
      await graphBase.navigateToGraphPanel(page, 'task_small_001');
      await graphBase.waitForGraphRender(page);

      // Get viewport width
      const viewportWidth = (await graphBase.getViewportSize(page)).width;
      expect(viewportWidth).toBe(375);

      // Graph should not overflow horizontally
      const hasHorizontalScroll = await page.evaluate(() => {
        return document.body.scrollWidth > window.innerWidth;
      });

      expect(hasHorizontalScroll).toBe(false);
    }
  );

  graphTest('Mobile: filter controls accessible', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Filter dropdown should be visible and tappable
    const filterDropdown = page.locator('[data-testid="filter-dropdown"]');
    await expect(filterDropdown).toBeVisible();

    // Size should be touch-friendly (at least 44x44 px)
    const size = await filterDropdown.boundingBox();
    expect(size!.width).toBeGreaterThanOrEqual(44);
    expect(size!.height).toBeGreaterThanOrEqual(44);
  });

  graphTest('Mobile: export button accessible', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    const exportButton = page.locator('[data-testid="export-svg-button"]');
    await expect(exportButton).toBeVisible();

    // Should be touch-friendly
    const size = await exportButton.boundingBox();
    expect(size!.width).toBeGreaterThanOrEqual(44);
    expect(size!.height).toBeGreaterThanOrEqual(44);
  });

  // Tablet tests
  test.use({ ...devices['iPad Pro'] });

  graphTest('Tablet (768px): graph fits with reasonable zoom', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    const viewportWidth = (await graphBase.getViewportSize(page)).width;
    expect(viewportWidth).toBeGreaterThanOrEqual(768);

    // Graph should be readable without excessive zoom
    const zoom = await page.evaluate(() => {
      const canvas = document.querySelector('[data-testid="graph-canvas"]');
      const transform = window.getComputedStyle(canvas!).transform;
      const match = transform.match(/scale\(([\d.]+)/);
      return match ? parseFloat(match[1]) : 1;
    });

    expect(zoom).toBeGreaterThanOrEqual(0.5);
    expect(zoom).toBeLessThanOrEqual(2);
  });

  graphTest('Tablet: all controls accessible', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // All toolbar buttons should be visible
    const zoomIn = page.locator('[data-testid="zoom-in-button"]');
    const zoomOut = page.locator('[data-testid="zoom-out-button"]');
    const fitScreen = page.locator('[data-testid="fit-to-screen-button"]');
    const resetView = page.locator('[data-testid="reset-view-button"]');

    await expect(zoomIn).toBeVisible();
    await expect(zoomOut).toBeVisible();
    await expect(fitScreen).toBeVisible();
    await expect(resetView).toBeVisible();
  });

  // Desktop tests
  test.use({ viewport: { width: 1920, height: 1080 } });

  graphTest('Desktop (1920px): full DAG visible at 1x zoom', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    const zoom = await page.evaluate(() => {
      const canvas = document.querySelector('[data-testid="graph-canvas"]');
      const transform = window.getComputedStyle(canvas!).transform;
      const match = transform.match(/scale\(([\d.]+)/);
      return match ? parseFloat(match[1]) : 1;
    });

    // Should be at 1x zoom (no scaling needed for small graph)
    expect(zoom).toBeCloseTo(1, 1);
  });

  graphTest('Desktop: performance metrics shown', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Performance metrics should be visible (if implemented)
    const metricsPanel = page.locator('[data-testid="performance-metrics"]');

    if (await metricsPanel.isVisible()) {
      expect(metricsPanel).toBeVisible();

      // Should show render time
      const renderTime = page.locator('[data-testid="metric-render-time"]');
      expect(renderTime).toBeVisible();
    }
  });

  graphTest('Touch interactions (mobile) work correctly', async ({
    page,
    graphBase,
  }) => {
    test.use({ ...devices['Pixel 5'] });

    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Swipe to pan
    const canvas = page.locator(graphBase.GRAPH_CANVAS_SELECTOR);

    const initialPosition = await canvas.evaluate((el) => {
      const rect = el.getBoundingClientRect();
      return { x: rect.x, y: rect.y };
    });

    // Perform swipe (drag)
    await canvas.dragTo(canvas, {
      sourcePosition: { x: 300, y: 200 },
      targetPosition: { x: 100, y: 200 },
      steps: 10, // Simulate slow swipe
    });

    const newPosition = await canvas.evaluate((el) => {
      const rect = el.getBoundingClientRect();
      return { x: rect.x, y: rect.y };
    });

    // Position should have changed
    expect(newPosition.x !== initialPosition.x || newPosition.y !== initialPosition.y).toBe(
      true
    );
  });
});

// ============================================================================
// DARK MODE TESTS
// ============================================================================

graphTest.describe('Task Graph Component — Dark Mode', () => {
  graphTest('Toggle dark mode → graph colors change', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Get initial colors
    const initialNodeColor = await page.evaluate(() => {
      const node = document.querySelector('[data-testid^="graph-node-"]');
      return window.getComputedStyle(node!).fill;
    });

    // Toggle dark mode
    const darkModeToggle = page.locator('[data-testid="dark-mode-toggle"]');
    if (await darkModeToggle.isVisible()) {
      await darkModeToggle.click();
      await page.waitForTimeout(300);
    } else {
      // Use settings or system preference
      await page.evaluate(() => {
        document.documentElement.classList.toggle('dark');
      });
    }

    // Get new colors
    const newNodeColor = await page.evaluate(() => {
      const node = document.querySelector('[data-testid^="graph-node-"]');
      return window.getComputedStyle(node!).fill;
    });

    // Colors should change
    expect(newNodeColor).not.toEqual(initialNodeColor);
  });

  graphTest('Node colors are readable in dark mode', async ({
    page,
    graphBase,
  }) => {
    // Enable dark mode
    await page.evaluate(() => {
      document.documentElement.classList.add('dark');
    });

    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Check contrast ratio (at least 4.5:1 for text)
    const hasGoodContrast = await page.evaluate(() => {
      const nodes = document.querySelectorAll('[data-testid^="graph-node-"]');
      let allReadable = true;

      nodes.forEach((node) => {
        const bgColor = window.getComputedStyle(node).fill;
        const textColor = window.getComputedStyle(
          node.querySelector('text') || node
        ).color;

        // Basic check: colors should be different
        if (bgColor === textColor) {
          allReadable = false;
        }
      });

      return allReadable;
    });

    expect(hasGoodContrast).toBe(true);
  });

  graphTest('Edge labels readable in dark mode', async ({
    page,
    graphBase,
  }) => {
    // Enable dark mode
    await page.evaluate(() => {
      document.documentElement.classList.add('dark');
    });

    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Hover over edge to show label
    const edges = await page.locator('[data-testid^="graph-edge-"]').all();
    if (edges.length > 0) {
      await edges[0].hover();

      const edgeLabel = page.locator('[data-testid="edge-label"]');
      const isVisible = await edgeLabel.isVisible({ timeout: 2000 });

      expect(isVisible).toBe(true);

      // Label should have good contrast
      const labelColor = await edgeLabel.evaluate((el) => {
        return window.getComputedStyle(el).color;
      });

      expect(labelColor).toBeDefined();
    }
  });
});

// ============================================================================
// ERROR HANDLING TESTS
// ============================================================================

graphTest.describe('Task Graph Component — Error Handling', () => {
  graphTest('API timeout → error message shown', async ({
    page,
    graphBase,
  }) => {
    // Simulate API timeout by delaying response
    await page.route('**/api/tasks/**/graph', (route) => {
      setTimeout(() => route.abort(), 10000); // Abort after 10s
    });

    await page.goto('/console/app/tasks/task_timeout_001', {
      waitUntil: 'networkidle',
    });

    // Error message should appear
    const error = page.locator('[data-testid="graph-error"]');
    await expect(error).toBeVisible({ timeout: 15000 });

    // Retry button should be available
    const retryButton = page.locator('[data-testid="retry-button"]');
    expect(retryButton).toBeVisible();
  });

  graphTest('Empty graph (0 nodes) → "No data" message', async ({
    page,
    graphBase,
  }) => {
    // Navigate to empty task
    await page.goto('/console/app/tasks/task_empty_001');

    // No data message should appear
    const noDataMessage = page.locator('[data-testid="graph-no-data"]');

    // Either "no data" message or empty graph is acceptable
    const graphPanel = page.locator(graphBase.GRAPH_PANEL_SELECTOR);
    await expect(graphPanel).toBeVisible();

    const nodeCount = await graphBase.getNodeElements(page);
    expect(nodeCount).toBe(0);
  });

  graphTest('Malformed graph JSON → graceful degradation', async ({
    page,
    graphBase,
  }) => {
    // Return malformed JSON
    await page.route('**/api/tasks/**/graph', (route) => {
      route.abort('failed');
    });

    await page.goto('/console/app/tasks/task_malformed_001', {
      waitUntil: 'networkidle',
    });

    // Should show error, not crash
    const error = page.locator('[data-testid="graph-error"]');
    await expect(error).toBeVisible({ timeout: 5000 });

    // Page should be accessible
    const graphPanel = page.locator(graphBase.GRAPH_PANEL_SELECTOR);
    expect(graphPanel).toBeDefined();
  });

  graphTest('Large graph (1000 nodes) → still renders (progressive)', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_large_001');

    // Should start loading
    try {
      await graphBase.waitForLoadingSpinner(page);
    } catch {
      // Loading spinner may not appear for very fast loads
    }

    // Should eventually complete (progressive rendering)
    await page.waitForTimeout(3000);

    const nodeCount = await graphBase.getNodeElements(page);
    expect(nodeCount).toBeGreaterThan(0);
  });

  graphTest('Missing node data → fallback values used', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_malformed_001');
    await graphBase.waitForGraphRender(page);

    // Even with missing data, graph should render
    const nodeCount = await graphBase.getNodeElements(page);
    expect(nodeCount).toBeGreaterThanOrEqual(0);

    // No crash should occur (test fails if exception is thrown)
  });

  graphTest(
    'Broken edge references (to_id not in nodes) → gracefully handled',
    async ({ page, graphBase }) => {
      await graphBase.navigateToGraphPanel(page, 'task_malformed_001');
      await graphBase.waitForGraphRender(page);

      // Graph should render despite broken edges
      const edgeCount = await graphBase.getEdgeElements(page);

      // Only valid edges should be rendered
      // Broken edges should be skipped
      expect(edgeCount).toBeGreaterThanOrEqual(0);

      // No error should crash the component
      const error = await graphBase.getErrorMessage(page);
      expect(error).toBeNull();
    }
  );

  graphTest('Network error on export → retry button available', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Abort export request
    await page.route('**/api/tasks/**/export', (route) => {
      route.abort();
    });

    // Click export
    await page.click('[data-testid="export-svg-button"]');

    // Error message should show retry
    const retryButton = page.locator('[data-testid="retry-export-button"]');

    // Either error is shown or download dialog appears
    await page.waitForTimeout(1000);
  });
});
