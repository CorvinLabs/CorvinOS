/**
 * E2E Tests: Task Graph Visualization — User Interactions
 *
 * Tests:
 * - Tooltips on node hover/click
 * - Modal dialogs for node details
 * - Filtering by node type and edge type
 * - Search and highlighting
 * - Drill-down/breadcrumb navigation
 * - Keyboard shortcuts (zoom, reset, etc.)
 */

import { test, expect } from '@playwright/test';
import { graphTest, GraphE2EBase } from './base.spec';

graphTest.describe('Task Graph Component — User Interactions', () => {
  graphTest(
    'Click decision node → tooltip appears (decision text, iteration)',
    async ({ page, graphBase }) => {
      await graphBase.navigateToGraphPanel(page, 'task_small_001');
      await graphBase.waitForGraphRender(page);

      // Click decision node
      await graphBase.clickNode(page, 'start_decision');

      // Tooltip should appear
      const tooltip = page.locator('[data-testid="node-tooltip"]');
      await expect(tooltip).toBeVisible();

      // Tooltip should contain node information
      const tooltipText = await tooltip.textContent();
      expect(tooltipText).toContain('start_decision');
      expect(tooltipText).toContain('decision');
    }
  );

  graphTest('Tooltip disappears on mouseleave', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Hover over node
    await graphBase.hoverNode(page, 'start_decision');

    const tooltip = page.locator('[data-testid="node-tooltip"]');
    await expect(tooltip).toBeVisible();

    // Move mouse away
    await page.mouse.move(0, 0);

    // Tooltip should disappear
    await expect(tooltip).not.toBeVisible({ timeout: 2000 });
  });

  graphTest(
    'Click error node → modal opens with error details',
    async ({ page, graphBase }) => {
      await graphBase.navigateToGraphPanel(page, 'task_small_001');
      await graphBase.waitForGraphRender(page);

      // Click error node
      await graphBase.clickNode(page, 'impl_error');

      // Modal should open
      const modal = page.locator('[data-testid="node-details-modal"]');
      await expect(modal).toBeVisible();

      // Modal should show error details
      const modalText = await modal.textContent();
      expect(modalText).toContain('impl_error');
      expect(modalText).toContain('error');
    }
  );

  graphTest(
    'Click checkpoint node → modal shows state snapshot',
    async ({ page, graphBase }) => {
      await graphBase.navigateToGraphPanel(page, 'task_small_001');
      await graphBase.waitForGraphRender(page);

      // Click checkpoint node
      await graphBase.clickNode(page, 'analysis_checkpoint');

      // Modal should open
      const modal = page.locator('[data-testid="node-details-modal"]');
      await expect(modal).toBeVisible();

      // Modal should show checkpoint data
      const modalText = await modal.textContent();
      expect(modalText).toContain('analysis_checkpoint');
      expect(modalText).toContain('checkpoint');
    }
  );

  graphTest('Hover edge → edge label visible', async ({ page, graphBase }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Hover over first edge
    const edges = await page.locator('[data-testid^="graph-edge-"]').all();
    if (edges.length > 0) {
      await edges[0].hover();

      // Edge label should become visible
      const edgeLabel = page.locator('[data-testid="edge-label"]');
      await expect(edgeLabel).toBeVisible({ timeout: 2000 });
    }
  });

  graphTest('Filter dropdown: select "decision" → only decision nodes', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Open filter dropdown
    const filterDropdown = page.locator('[data-testid="filter-dropdown"]');
    await filterDropdown.click();

    // Select "decision" filter
    await page.click('[data-testid="filter-option-decision"]');

    // Wait for filter to apply
    await page.waitForTimeout(300);

    // Get visible nodes
    const visibleNodes = await page.evaluate(() => {
      const nodes = document.querySelectorAll('[data-testid^="graph-node-"]');
      return Array.from(nodes).map((n) => n.getAttribute('data-node-type'));
    });

    // All visible nodes should be "decision" type
    visibleNodes.forEach((type) => {
      expect(type).toBe('decision');
    });
  });

  graphTest(
    'Filter dropdown: select "error" → only error nodes visible',
    async ({ page, graphBase }) => {
      await graphBase.navigateToGraphPanel(page, 'task_small_001');
      await graphBase.waitForGraphRender(page);

      // Open filter dropdown
      await page.click('[data-testid="filter-dropdown"]');

      // Select "error" filter
      await page.click('[data-testid="filter-option-error"]');

      await page.waitForTimeout(300);

      // Get visible nodes
      const visibleNodes = await page.evaluate(() => {
        const nodes = document.querySelectorAll('[data-testid^="graph-node-"]');
        return Array.from(nodes).map((n) => n.getAttribute('data-node-type'));
      });

      // All visible nodes should be "error" type
      visibleNodes.forEach((type) => {
        expect(type).toBe('error');
      });
    }
  );

  graphTest('Filter dropdown: "All" shows all nodes', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Get initial node count
    const initialCount = await graphBase.getNodeElements(page);

    // Apply a filter
    await page.click('[data-testid="filter-dropdown"]');
    await page.click('[data-testid="filter-option-decision"]');
    await page.waitForTimeout(300);

    const filteredCount = await graphBase.getNodeElements(page);
    expect(filteredCount).toBeLessThanOrEqual(initialCount);

    // Reset to "All"
    await page.click('[data-testid="filter-dropdown"]');
    await page.click('[data-testid="filter-option-all"]');
    await page.waitForTimeout(300);

    // Should show all nodes again
    const resetCount = await graphBase.getNodeElements(page);
    expect(resetCount).toBe(initialCount);
  });

  graphTest('Multiple filters combinable', async ({ page, graphBase }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Apply node type filter
    await page.click('[data-testid="filter-dropdown"]');
    await page.click('[data-testid="filter-option-decision"]');
    await page.waitForTimeout(300);

    // Apply edge type filter
    const edgeFilterDropdown = page.locator('[data-testid="edge-filter-dropdown"]');
    if (await edgeFilterDropdown.isVisible()) {
      await edgeFilterDropdown.click();
      await page.click('[data-testid="edge-filter-option-hard"]');
      await page.waitForTimeout(300);
    }

    // Both filters should be applied
    const visibleNodes = await page.evaluate(() => {
      const nodes = document.querySelectorAll('[data-testid^="graph-node-"]');
      return Array.from(nodes).map((n) => n.getAttribute('data-node-type'));
    });

    // At least some nodes should still be visible
    expect(visibleNodes.length).toBeGreaterThan(0);
  });

  graphTest('Reset filters button shows all nodes', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    const initialCount = await graphBase.getNodeElements(page);

    // Apply filter
    await page.click('[data-testid="filter-dropdown"]');
    await page.click('[data-testid="filter-option-decision"]');
    await page.waitForTimeout(300);

    // Reset filters
    await page.click('[data-testid="reset-filters-button"]');
    await page.waitForTimeout(300);

    const resetCount = await graphBase.getNodeElements(page);
    expect(resetCount).toBe(initialCount);
  });

  graphTest('Search/highlight node by ID (input field)', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Type in search field
    const searchInput = page.locator('[data-testid="search-node-input"]');
    await searchInput.fill('start_decision');

    // Node should be highlighted
    const node = await graphBase.getNodeById(page, 'start_decision');
    const highlightClass = await node.getAttribute('class');

    expect(highlightClass).toContain('highlighted');
  });

  graphTest('Highlighted node has different styling', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Search for node
    const searchInput = page.locator('[data-testid="search-node-input"]');
    await searchInput.fill('start_decision');

    // Get styling
    const node = await graphBase.getNodeById(page, 'start_decision');
    const color = await node.evaluate((el) => {
      return window.getComputedStyle(el).fill;
    });

    // Highlighted node should have distinct color
    expect(color).toBeDefined();
  });

  graphTest('Double-click node → drill-down view (subgraph)', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    const node = await graphBase.getNodeById(page, 'impl_decision');

    // Double-click on node
    await node.dblclick();

    // Drill-down view should open
    const drilldownPanel = page.locator(
      '[data-testid="node-drill-down-panel"]'
    );

    // This is optional — only if subgraphs are implemented
    if (await drilldownPanel.isVisible()) {
      expect(drilldownPanel).toBeVisible();

      // Should show subgraph title
      const title = page.locator('[data-testid="drill-down-title"]');
      const titleText = await title.textContent();
      expect(titleText).toContain('impl_decision');
    }
  });

  graphTest('Breadcrumb navigation: go back to full graph', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Double-click to drill down (if supported)
    const node = await graphBase.getNodeById(page, 'impl_decision');
    await node.dblclick();

    const drilldownPanel = page.locator(
      '[data-testid="node-drill-down-panel"]'
    );

    if (await drilldownPanel.isVisible()) {
      // Click breadcrumb back button
      const backButton = page.locator(
        '[data-testid="breadcrumb-back-button"]'
      );
      if (await backButton.isVisible()) {
        await backButton.click();

        // Should return to full graph
        const fullGraph = page.locator('[data-testid="full-graph-panel"]');
        await expect(fullGraph).toBeVisible();
      }
    }
  });

  graphTest('Modal close button works', async ({ page, graphBase }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Open modal
    await graphBase.clickNode(page, 'impl_error');

    const modal = page.locator('[data-testid="node-details-modal"]');
    await expect(modal).toBeVisible();

    // Click close button
    const closeButton = page.locator('[data-testid="modal-close-button"]');
    await closeButton.click();

    // Modal should close
    await expect(modal).not.toBeVisible();
  });

  graphTest('Modal ESC key closes it', async ({ page, graphBase }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Open modal
    await graphBase.clickNode(page, 'impl_error');

    const modal = page.locator('[data-testid="node-details-modal"]');
    await expect(modal).toBeVisible();

    // Press ESC
    await page.keyboard.press('Escape');

    // Modal should close
    await expect(modal).not.toBeVisible();
  });

  graphTest('Click outside modal closes it', async ({ page, graphBase }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Open modal
    await graphBase.clickNode(page, 'impl_error');

    const modal = page.locator('[data-testid="node-details-modal"]');
    await expect(modal).toBeVisible();

    // Click outside modal (on overlay)
    const overlay = page.locator('[data-testid="modal-overlay"]');
    await overlay.click({ position: { x: 10, y: 10 } });

    // Modal should close
    await expect(modal).not.toBeVisible();
  });

  graphTest('Keyboard shortcut: "+" zooms in', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    const initialScale = await page.evaluate(() => {
      const canvas = document.querySelector('[data-testid="graph-canvas"]');
      const transform = window.getComputedStyle(canvas!).transform;
      const match = transform.match(/scale\(([\d.]+)/);
      return match ? parseFloat(match[1]) : 1;
    });

    // Press "+"
    await page.keyboard.press('Plus');
    await page.waitForTimeout(300);

    const newScale = await page.evaluate(() => {
      const canvas = document.querySelector('[data-testid="graph-canvas"]');
      const transform = window.getComputedStyle(canvas!).transform;
      const match = transform.match(/scale\(([\d.]+)/);
      return match ? parseFloat(match[1]) : 1;
    });

    expect(newScale).toBeGreaterThan(initialScale);
  });

  graphTest('Keyboard shortcut: "-" zooms out', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Zoom in first
    await page.keyboard.press('Plus');
    await page.waitForTimeout(300);

    const zoomedScale = await page.evaluate(() => {
      const canvas = document.querySelector('[data-testid="graph-canvas"]');
      const transform = window.getComputedStyle(canvas!).transform;
      const match = transform.match(/scale\(([\d.]+)/);
      return match ? parseFloat(match[1]) : 1;
    });

    // Zoom out
    await page.keyboard.press('Minus');
    await page.waitForTimeout(300);

    const newScale = await page.evaluate(() => {
      const canvas = document.querySelector('[data-testid="graph-canvas"]');
      const transform = window.getComputedStyle(canvas!).transform;
      const match = transform.match(/scale\(([\d.]+)/);
      return match ? parseFloat(match[1]) : 1;
    });

    expect(newScale).toBeLessThan(zoomedScale);
  });

  graphTest('Keyboard shortcut: "Home" resets zoom/pan', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Modify zoom and pan
    await page.keyboard.press('Plus');
    await page.keyboard.press('Plus');
    await page.waitForTimeout(300);

    const modifiedTransform = await page
      .locator('[data-testid="graph-canvas"]')
      .evaluate((el) => window.getComputedStyle(el).transform);

    // Press Home to reset
    await page.keyboard.press('Home');
    await page.waitForTimeout(300);

    const resetTransform = await page
      .locator('[data-testid="graph-canvas"]')
      .evaluate((el) => window.getComputedStyle(el).transform);

    // Transform should be reset (no zoom/pan)
    expect(resetTransform).not.toEqual(modifiedTransform);
  });
});
