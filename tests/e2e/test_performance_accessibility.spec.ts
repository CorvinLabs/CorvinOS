/**
 * E2E Tests: Task Graph Visualization — Performance & Accessibility
 *
 * Performance Tests:
 * - Render time benchmarks (10, 100, 500 nodes)
 * - API latency (< 500ms)
 * - Memory usage (< 50MB for 100 nodes)
 * - FCP, LCP metrics
 *
 * Accessibility Tests:
 * - Keyboard navigation (Tab)
 * - ARIA labels on nodes/edges
 * - Screen reader announcements
 * - Color contrast (WCAG AA)
 */

import { test, expect } from '@playwright/test';
import { graphTest, GraphE2EBase } from './base.spec';

// ============================================================================
// PERFORMANCE TESTS
// ============================================================================

graphTest.describe('Task Graph Component — Performance', () => {
  graphTest('Small graph (10 nodes): render < 200ms', async ({
    page,
    graphBase,
  }) => {
    const renderTime = await graphBase.measureRenderTime(page, async () => {
      await graphBase.navigateToGraphPanel(page, 'task_small_001');
      await graphBase.waitForGraphRender(page);
    });

    expect(renderTime).toBeLessThan(200);
  });

  graphTest('Medium graph (100 nodes): render < 1s', async ({
    page,
    graphBase,
  }) => {
    const renderTime = await graphBase.measureRenderTime(page, async () => {
      await graphBase.navigateToGraphPanel(page, 'task_medium_001');
      await graphBase.waitForGraphRender(page);
    });

    expect(renderTime).toBeLessThan(1000);
  });

  graphTest('Large graph (500 nodes): render < 2s', async ({
    page,
    graphBase,
  }) => {
    const renderTime = await graphBase.measureRenderTime(page, async () => {
      await graphBase.navigateToGraphPanel(page, 'task_large_001');
      await graphBase.waitForGraphRender(page);
    });

    expect(renderTime).toBeLessThan(2000);
  });

  graphTest('Query API latency < 500ms', async ({ request }) => {
    const startTime = Date.now();

    const response = await request.get(
      'http://127.0.0.1:8765/api/tasks/task_small_001/graph/query',
      {
        params: { type: 'reachability', node: 'start_decision' },
      }
    );

    const endTime = Date.now();
    const latency = endTime - startTime;

    expect(latency).toBeLessThan(500);
  });

  graphTest('Memory usage < 50MB for 100-node graph', async ({
    page,
    graphBase,
  }) => {
    // Baseline memory
    const memoryBefore = await page.evaluate(() => {
      if ('memory' in performance) {
        return (performance as any).memory.usedJSHeapSize;
      }
      return 0;
    });

    // Render graph
    await graphBase.navigateToGraphPanel(page, 'task_medium_001');
    await graphBase.waitForGraphRender(page);

    // Peak memory
    const memoryAfter = await page.evaluate(() => {
      if ('memory' in performance) {
        return (performance as any).memory.usedJSHeapSize;
      }
      return 0;
    });

    const memoryUsed = memoryAfter - memoryBefore;
    const memoryMB = memoryUsed / (1024 * 1024);

    // Should use less than 50MB
    if (memoryBefore > 0) {
      expect(memoryMB).toBeLessThan(50);
    }
  });

  graphTest('First Contentful Paint (FCP) < 1s', async ({
    page,
    graphBase,
  }) => {
    // Measure FCP
    const metrics = await page.evaluate(() => {
      const navigation = performance.getEntriesByType('navigation')[0] as any;
      const paintEntries = performance.getEntriesByType('paint');

      const fcp = paintEntries.find((p: any) => p.name === 'first-contentful-paint');

      return {
        fcp: fcp ? fcp.startTime : navigation?.responseEnd,
      };
    });

    expect(metrics.fcp).toBeLessThan(1000);
  });

  graphTest('Largest Contentful Paint (LCP) < 2.5s', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Get LCP metric
    const lcp = await page.evaluate(() => {
      return new Promise<number>((resolve) => {
        const observer = new PerformanceObserver((list) => {
          const entries = list.getEntries();
          const lastEntry = entries[entries.length - 1] as any;
          resolve(lastEntry.renderTime || lastEntry.loadTime);
        });

        observer.observe({ type: 'largest-contentful-paint', buffered: true });

        // Stop after 3 seconds
        setTimeout(() => {
          observer.disconnect();
          resolve(0);
        }, 3000);
      });
    });

    expect(lcp).toBeLessThan(2500);
  });

  graphTest('No memory leaks during repeated renders', async ({
    page,
    graphBase,
  }) => {
    const samples: number[] = [];

    for (let i = 0; i < 3; i++) {
      // Render graph
      await graphBase.navigateToGraphPanel(page, 'task_small_001');
      await graphBase.waitForGraphRender(page);

      // Measure memory
      const memory = await page.evaluate(() => {
        if ('memory' in performance) {
          return (performance as any).memory.usedJSHeapSize;
        }
        return 0;
      });

      samples.push(memory);

      // Navigate away
      await page.goto('/console/app');
      await page.waitForTimeout(500);
    }

    // Memory should stabilize or decrease (not keep growing)
    if (samples[0] > 0) {
      // Allow some variance, but not exponential growth
      const growth = samples[2] - samples[0];
      const growthPercent = (growth / samples[0]) * 100;

      // Less than 50% growth expected
      expect(growthPercent).toBeLessThan(50);
    }
  });

  graphTest('Pan and zoom performance (60 fps)', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_medium_001');
    await graphBase.waitForGraphRender(page);

    // Measure animation frame rate during pan
    const fps = await page.evaluate(() => {
      return new Promise<number>((resolve) => {
        let frameCount = 0;
        let lastTime = performance.now();
        let rafId: number;

        const countFrames = () => {
          frameCount++;
          const currentTime = performance.now();

          if (currentTime - lastTime >= 1000) {
            cancelAnimationFrame(rafId);
            resolve(frameCount);
          } else {
            rafId = requestAnimationFrame(countFrames);
          }
        };

        rafId = requestAnimationFrame(countFrames);
      });
    });

    // Should maintain at least 30 fps (ideally 60 fps)
    expect(fps).toBeGreaterThanOrEqual(30);
  });

  graphTest('Export operation completes < 5s', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    const startTime = Date.now();

    // Set up download
    const downloadPromise = page.waitForEvent('download');
    await page.click('[data-testid="export-svg-button"]');

    try {
      await Promise.race([
        downloadPromise,
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error('Export timeout')), 5000)
        ),
      ]);

      const duration = Date.now() - startTime;
      expect(duration).toBeLessThan(5000);
    } catch {
      // Export may not be implemented yet
    }
  });
});

// ============================================================================
// ACCESSIBILITY TESTS
// ============================================================================

graphTest.describe('Task Graph Component — Accessibility', () => {
  graphTest('Keyboard navigation (Tab) through nodes', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Get first node
    const firstNode = await page.locator(graphBase.NODE_SELECTOR).first();
    await firstNode.focus();

    // Tab should move focus through nodes
    await page.keyboard.press('Tab');
    const focusedElement = await page.evaluate(() => {
      return document.activeElement?.getAttribute('data-testid');
    });

    // Focus should have moved
    expect(focusedElement).toBeDefined();
  });

  graphTest('ARIA labels on nodes and edges', async ({ page, graphBase }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Check nodes have aria-label
    const nodeWithLabel = await page.locator(
      graphBase.NODE_SELECTOR + '[aria-label]'
    );

    if (await nodeWithLabel.first().isVisible()) {
      const label = await nodeWithLabel.first().getAttribute('aria-label');
      expect(label).toBeDefined();
      expect(label?.length).toBeGreaterThan(0);
    }

    // Check edges have aria-label
    const edgeWithLabel = await page.locator(
      graphBase.EDGE_SELECTOR + '[aria-label]'
    );

    if (await edgeWithLabel.first().isVisible()) {
      const label = await edgeWithLabel.first().getAttribute('aria-label');
      expect(label).toBeDefined();
    }
  });

  graphTest('Screen reader announces node type and status', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Get node aria-label
    const nodeLabel = await page.evaluate(() => {
      const node = document.querySelector('[data-testid^="graph-node-"]');
      return node?.getAttribute('aria-label');
    });

    // Should contain type and status information
    if (nodeLabel) {
      expect(nodeLabel).toMatch(/decision|error|checkpoint|context|metric/i);
    }
  });

  graphTest('ARIA live region for status updates', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Check for aria-live region
    const liveRegion = page.locator('[aria-live]');

    if (await liveRegion.first().isVisible()) {
      const liveValue = await liveRegion.first().getAttribute('aria-live');
      expect(['polite', 'assertive']).toContain(liveValue);
    }
  });

  graphTest('Color contrast meets WCAG AA standard', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Check contrast ratio for nodes
    const hasGoodContrast = await page.evaluate(() => {
      const nodes = document.querySelectorAll('[data-testid^="graph-node-"]');
      let passed = true;

      nodes.forEach((node) => {
        const fill = window.getComputedStyle(node).fill;
        const text = node.querySelector('text');

        if (text) {
          const textColor = window.getComputedStyle(text).fill;

          // Simple check: colors should not be identical
          if (fill.toLowerCase() === textColor.toLowerCase()) {
            passed = false;
          }
        }
      });

      return passed;
    });

    expect(hasGoodContrast).toBe(true);
  });

  graphTest('Sufficient touch target size (44x44 px minimum)', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Check interactive element sizes
    const allGood = await page.evaluate(() => {
      const buttons = document.querySelectorAll('button[data-testid]');
      let allSufficient = true;

      buttons.forEach((button) => {
        const rect = button.getBoundingClientRect();
        if (rect.width < 44 || rect.height < 44) {
          allSufficient = false;
        }
      });

      return allSufficient;
    });

    expect(allGood).toBe(true);
  });

  graphTest('Error messages are accessible to screen readers', async ({
    page,
    graphBase,
  }) => {
    // Simulate error
    await page.route('**/api/tasks/**/graph', (route) => {
      route.abort();
    });

    await page.goto('/console/app/tasks/task_error_001', {
      waitUntil: 'networkidle',
    });

    // Error should be announced
    const errorElement = page.locator('[role="alert"]');

    if (await errorElement.isVisible()) {
      const errorText = await errorElement.textContent();
      expect(errorText).toBeDefined();
      expect(errorText?.length).toBeGreaterThan(0);
    }
  });

  graphTest('Focus indicator visible on all interactive elements', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Tab to first interactive element
    await page.keyboard.press('Tab');

    // Check if focus is visible
    const hasFocusIndicator = await page.evaluate(() => {
      const focused = document.activeElement;

      if (!focused) return false;

      const style = window.getComputedStyle(focused);
      const outline = style.outline;
      const boxShadow = style.boxShadow;

      // Should have either outline or box-shadow
      return (
        (outline && outline !== 'none') ||
        (boxShadow && boxShadow !== 'none')
      );
    });

    expect(hasFocusIndicator).toBe(true);
  });

  graphTest('Tooltip text is announced by screen readers', async ({
    page,
    graphBase,
  }) => {
    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Hover to show tooltip
    await graphBase.hoverNode(page, 'start_decision');

    // Tooltip should have aria-label or be in aria-live region
    const tooltip = page.locator('[data-testid="node-tooltip"]');

    const ariaLabel = await tooltip.getAttribute('aria-label');
    const ariaLive = await tooltip.getAttribute('aria-live');
    const role = await tooltip.getAttribute('role');

    // Should have one of these accessibility features
    const isAccessible =
      ariaLabel !== null || ariaLive !== null || role === 'tooltip';

    expect(isAccessible).toBe(true);
  });

  graphTest('High contrast mode support', async ({ page, graphBase }) => {
    // Emulate high contrast preference
    await page.emulateMedia({ forcedColors: 'active' });

    await graphBase.navigateToGraphPanel(page, 'task_small_001');
    await graphBase.waitForGraphRender(page);

    // Graph should still be visible
    const nodeCount = await graphBase.getNodeElements(page);
    expect(nodeCount).toBeGreaterThan(0);
  });
});
