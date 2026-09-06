# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: test_component_rendering.spec.ts >> Task Graph Component — Rendering >> All edges visible with direction arrows
- Location: tests/e2e/test_component_rendering.spec.ts:71:12

# Error details

```
TimeoutError: page.waitForSelector: Timeout 5000ms exceeded.
Call log:
  - waiting for locator('[data-testid="task-graph-panel"]') to be visible

```

# Page snapshot

```yaml
- generic [ref=e3]:
  - heading "Page not found." [level=1] [ref=e7]
  - paragraph [ref=e8]: The address /app/tasks/task_small_001/graph doesn't exist in the console. This may be a broken link or a typo.
  - generic [ref=e9]:
    - button "Go back" [ref=e10] [cursor=pointer]
    - link "Home" [ref=e11] [cursor=pointer]:
      - /url: /console
```

# Test source

```ts
  1   | /**
  2   |  * E2E Base Test Class for Task Graph Visualization
  3   |  *
  4   |  * Provides common setup/teardown and helper methods
  5   |  */
  6   | 
  7   | import { test, expect, Page } from '@playwright/test';
  8   | 
  9   | /**
  10  |  * Common setup and cleanup for all graph E2E tests
  11  |  */
  12  | export class GraphE2EBase {
  13  |   static readonly GRAPH_PANEL_SELECTOR = '[data-testid="task-graph-panel"]';
  14  |   static readonly GRAPH_CANVAS_SELECTOR = '[data-testid="graph-canvas"]';
  15  |   static readonly NODE_SELECTOR = '[data-testid^="graph-node-"]';
  16  |   static readonly EDGE_SELECTOR = '[data-testid^="graph-edge-"]';
  17  | 
  18  |   static async navigateToGraphPanel(
  19  |     page: Page,
  20  |     taskId: string
  21  |   ): Promise<void> {
  22  |     await page.goto(`/console/app/tasks/${taskId}/graph`, {
  23  |       waitUntil: 'networkidle',
  24  |     });
> 25  |     await page.waitForSelector(this.GRAPH_PANEL_SELECTOR, { timeout: 5000 });
      |                ^ TimeoutError: page.waitForSelector: Timeout 5000ms exceeded.
  26  |   }
  27  | 
  28  |   static async waitForGraphRender(
  29  |     page: Page,
  30  |     expectedNodeCount?: number
  31  |   ): Promise<void> {
  32  |     // Wait for SVG to be rendered
  33  |     await page.waitForSelector(this.GRAPH_CANVAS_SELECTOR, { timeout: 5000 });
  34  | 
  35  |     // Optionally wait for specific node count
  36  |     if (expectedNodeCount !== undefined) {
  37  |       await page.waitForFunction(
  38  |         (count) => {
  39  |           const nodes = document.querySelectorAll('[data-testid^="graph-node-"]');
  40  |           return nodes.length === count;
  41  |         },
  42  |         expectedNodeCount,
  43  |         { timeout: 10000 }
  44  |       );
  45  |     }
  46  |   }
  47  | 
  48  |   static async getNodeElements(page: Page): Promise<number> {
  49  |     const nodes = await page.locator(this.NODE_SELECTOR).count();
  50  |     return nodes;
  51  |   }
  52  | 
  53  |   static async getEdgeElements(page: Page): Promise<number> {
  54  |     const edges = await page.locator(this.EDGE_SELECTOR).count();
  55  |     return edges;
  56  |   }
  57  | 
  58  |   static async getNodeById(page: Page, nodeId: string) {
  59  |     return page.locator(`[data-testid="graph-node-${nodeId}"]`);
  60  |   }
  61  | 
  62  |   static async clickNode(page: Page, nodeId: string): Promise<void> {
  63  |     const node = await this.getNodeById(page, nodeId);
  64  |     await node.click();
  65  |   }
  66  | 
  67  |   static async hoverNode(page: Page, nodeId: string): Promise<void> {
  68  |     const node = await this.getNodeById(page, nodeId);
  69  |     await node.hover();
  70  |   }
  71  | 
  72  |   static async getNodeText(page: Page, nodeId: string): Promise<string> {
  73  |     const node = await this.getNodeById(page, nodeId);
  74  |     return node.textContent();
  75  |   }
  76  | 
  77  |   static async waitForLoadingSpinner(page: Page): Promise<void> {
  78  |     await page.waitForSelector('[data-testid="graph-loading"]', {
  79  |       timeout: 1000,
  80  |     });
  81  |   }
  82  | 
  83  |   static async waitForLoadingComplete(page: Page): Promise<void> {
  84  |     try {
  85  |       await this.waitForLoadingSpinner(page);
  86  |     } catch {
  87  |       // Loading spinner may not appear for fast loads
  88  |     }
  89  | 
  90  |     await page.waitForSelector('[data-testid="graph-loading"]', {
  91  |       state: 'hidden',
  92  |       timeout: 5000,
  93  |     });
  94  |   }
  95  | 
  96  |   static async getErrorMessage(page: Page): Promise<string | null> {
  97  |     const error = page.locator('[data-testid="graph-error"]');
  98  |     if (await error.isVisible()) {
  99  |       return error.textContent();
  100 |     }
  101 |     return null;
  102 |   }
  103 | 
  104 |   static async measureRenderTime(
  105 |     page: Page,
  106 |     action: () => Promise<void>
  107 |   ): Promise<number> {
  108 |     const startTime = Date.now();
  109 |     await action();
  110 |     const endTime = Date.now();
  111 |     return endTime - startTime;
  112 |   }
  113 | 
  114 |   static async getGraphStats(page: Page) {
  115 |     const stats = await page.evaluate(() => {
  116 |       const nodeCount = document.querySelectorAll('[data-testid^="graph-node-"]')
  117 |         .length;
  118 |       const edgeCount = document.querySelectorAll('[data-testid^="graph-edge-"]')
  119 |         .length;
  120 |       return { nodeCount, edgeCount };
  121 |     });
  122 |     return stats;
  123 |   }
  124 | 
  125 |   static async getViewportSize(page: Page) {
```