/**
 * E2E Base Test Class for Task Graph Visualization
 *
 * Provides common setup/teardown and helper methods
 */

import { test, expect, Page } from '@playwright/test';

/**
 * Common setup and cleanup for all graph E2E tests
 */
export class GraphE2EBase {
  static readonly GRAPH_PANEL_SELECTOR = '[data-testid="task-graph-panel"]';
  static readonly GRAPH_CANVAS_SELECTOR = '[data-testid="graph-canvas"]';
  static readonly NODE_SELECTOR = '[data-testid^="graph-node-"]';
  static readonly EDGE_SELECTOR = '[data-testid^="graph-edge-"]';

  static async navigateToGraphPanel(
    page: Page,
    taskId: string
  ): Promise<void> {
    await page.goto(`/console/app/tasks/${taskId}/graph`, {
      waitUntil: 'networkidle',
    });
    await page.waitForSelector(this.GRAPH_PANEL_SELECTOR, { timeout: 5000 });
  }

  static async waitForGraphRender(
    page: Page,
    expectedNodeCount?: number
  ): Promise<void> {
    // Wait for SVG to be rendered
    await page.waitForSelector(this.GRAPH_CANVAS_SELECTOR, { timeout: 5000 });

    // Optionally wait for specific node count
    if (expectedNodeCount !== undefined) {
      await page.waitForFunction(
        (count) => {
          const nodes = document.querySelectorAll('[data-testid^="graph-node-"]');
          return nodes.length === count;
        },
        expectedNodeCount,
        { timeout: 10000 }
      );
    }
  }

  static async getNodeElements(page: Page): Promise<number> {
    const nodes = await page.locator(this.NODE_SELECTOR).count();
    return nodes;
  }

  static async getEdgeElements(page: Page): Promise<number> {
    const edges = await page.locator(this.EDGE_SELECTOR).count();
    return edges;
  }

  static async getNodeById(page: Page, nodeId: string) {
    return page.locator(`[data-testid="graph-node-${nodeId}"]`);
  }

  static async clickNode(page: Page, nodeId: string): Promise<void> {
    const node = await this.getNodeById(page, nodeId);
    await node.click();
  }

  static async hoverNode(page: Page, nodeId: string): Promise<void> {
    const node = await this.getNodeById(page, nodeId);
    await node.hover();
  }

  static async getNodeText(page: Page, nodeId: string): Promise<string> {
    const node = await this.getNodeById(page, nodeId);
    return node.textContent();
  }

  static async waitForLoadingSpinner(page: Page): Promise<void> {
    await page.waitForSelector('[data-testid="graph-loading"]', {
      timeout: 1000,
    });
  }

  static async waitForLoadingComplete(page: Page): Promise<void> {
    try {
      await this.waitForLoadingSpinner(page);
    } catch {
      // Loading spinner may not appear for fast loads
    }

    await page.waitForSelector('[data-testid="graph-loading"]', {
      state: 'hidden',
      timeout: 5000,
    });
  }

  static async getErrorMessage(page: Page): Promise<string | null> {
    const error = page.locator('[data-testid="graph-error"]');
    if (await error.isVisible()) {
      return error.textContent();
    }
    return null;
  }

  static async measureRenderTime(
    page: Page,
    action: () => Promise<void>
  ): Promise<number> {
    const startTime = Date.now();
    await action();
    const endTime = Date.now();
    return endTime - startTime;
  }

  static async getGraphStats(page: Page) {
    const stats = await page.evaluate(() => {
      const nodeCount = document.querySelectorAll('[data-testid^="graph-node-"]')
        .length;
      const edgeCount = document.querySelectorAll('[data-testid^="graph-edge-"]')
        .length;
      return { nodeCount, edgeCount };
    });
    return stats;
  }

  static async getViewportSize(page: Page) {
    return page.viewportSize() || { width: 1280, height: 720 };
  }
}

/**
 * Extend Playwright test with base class methods
 */
export const graphTest = test.extend<{
  graphBase: typeof GraphE2EBase;
}>({
  graphBase: async ({}, use) => {
    await use(GraphE2EBase);
  },
});
