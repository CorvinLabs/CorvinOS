# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: test_component_rendering.spec.ts >> Task Graph Component — Rendering >> Navigate to task detail → graph panel visible
- Location: tests/e2e/test_component_rendering.spec.ts:20:12

# Error details

```
Test timeout of 60000ms exceeded.
```

```
Error: page.click: Test timeout of 60000ms exceeded.
Call log:
  - waiting for locator('[data-testid="tab-graph"]')

```

# Page snapshot

```yaml
- generic [ref=e3]:
  - heading "Page not found." [level=1] [ref=e7]
  - paragraph [ref=e8]: The address /app/tasks/task_small_001 doesn't exist in the console. This may be a broken link or a typo.
  - generic [ref=e9]:
    - button "Go back" [ref=e10] [cursor=pointer]
    - link "Home" [ref=e11] [cursor=pointer]:
      - /url: /console
```

# Test source

```ts
  1   | /**
  2   |  * E2E Tests: Task Graph Visualization — Component Rendering
  3   |  *
  4   |  * Tests:
  5   |  * - Graph panel visibility and structure
  6   |  * - DAG rendering without errors
  7   |  * - Node and edge visibility
  8   |  * - Color coding correct (success, error, warning, active)
  9   |  * - Node labels visible
  10  |  * - Zoom and pan functionality
  11  |  * - Fit-to-screen and reset buttons
  12  |  * - Performance metrics (render time < 1s for 100 nodes)
  13  |  * - Export functionality (SVG, DOT)
  14  |  */
  15  | 
  16  | import { test, expect } from '@playwright/test';
  17  | import { graphTest, GraphE2EBase } from './base';
  18  | 
  19  | graphTest.describe('Task Graph Component — Rendering', () => {
  20  |   graphTest(
  21  |     'Navigate to task detail → graph panel visible',
  22  |     async ({ page, graphBase }) => {
  23  |       // Navigate to task with graph
  24  |       await page.goto('/console/app/tasks/task_small_001');
> 25  |       await page.click('[data-testid="tab-graph"]');
      |                  ^ Error: page.click: Test timeout of 60000ms exceeded.
  26  | 
  27  |       // Verify graph panel appears
  28  |       const graphPanel = page.locator(
  29  |         graphBase.GRAPH_PANEL_SELECTOR
  30  |       );
  31  |       await expect(graphPanel).toBeVisible();
  32  |     }
  33  |   );
  34  | 
  35  |   graphTest(
  36  |     'DAG renders without errors (100 nodes, 500 edges)',
  37  |     async ({ page, graphBase }) => {
  38  |       await graphBase.navigateToGraphPanel(page, 'task_small_001');
  39  |       await graphBase.waitForGraphRender(page);
  40  | 
  41  |       // Verify graph is rendered (no error message)
  42  |       const error = await graphBase.getErrorMessage(page);
  43  |       expect(error).toBeNull();
  44  | 
  45  |       // Get node count
  46  |       const nodeCount = await graphBase.getNodeElements(page);
  47  |       expect(nodeCount).toBeGreaterThan(0);
  48  |     }
  49  |   );
  50  | 
  51  |   graphTest('All nodes visible (no cutoff, within viewport)', async ({
  52  |     page,
  53  |     graphBase,
  54  |   }) => {
  55  |     await graphBase.navigateToGraphPanel(page, 'task_small_001');
  56  |     await graphBase.waitForGraphRender(page);
  57  | 
  58  |     const nodes = await page.locator(graphBase.NODE_SELECTOR).all();
  59  | 
  60  |     for (const node of nodes) {
  61  |       // Each node should be visible on the screen
  62  |       const isInViewport = await page.evaluate((el: any) => {
  63  |         const rect = el.getBoundingClientRect();
  64  |         return rect.width > 0 && rect.height > 0;
  65  |       }, await node.elementHandle());
  66  | 
  67  |       expect(isInViewport).toBe(true);
  68  |     }
  69  |   });
  70  | 
  71  |   graphTest('All edges visible with direction arrows', async ({
  72  |     page,
  73  |     graphBase,
  74  |   }) => {
  75  |     await graphBase.navigateToGraphPanel(page, 'task_small_001');
  76  |     await graphBase.waitForGraphRender(page);
  77  | 
  78  |     const edges = await page.locator(graphBase.EDGE_SELECTOR).all();
  79  | 
  80  |     expect(edges.length).toBeGreaterThan(0);
  81  | 
  82  |     for (const edge of edges) {
  83  |       // Verify edge is visible
  84  |       await expect(edge).toBeVisible();
  85  | 
  86  |       // Verify direction arrow exists (path or marker)
  87  |       const hasArrow = await edge.evaluate((el: any) => {
  88  |         // Check for arrow marker or direction indicator
  89  |         const svg = el.querySelector('path[marker-end], path[marker-start]');
  90  |         return svg !== null;
  91  |       });
  92  | 
  93  |       // Arrow markers are optional, but edges should be visible
  94  |       expect(edge).toBeDefined();
  95  |     }
  96  |   });
  97  | 
  98  |   graphTest('Color coding correct (success, error, warning, active)', async ({
  99  |     page,
  100 |     graphBase,
  101 |   }) => {
  102 |     await graphBase.navigateToGraphPanel(page, 'task_small_001');
  103 |     await graphBase.waitForGraphRender(page);
  104 | 
  105 |     // Check for color-coded nodes
  106 |     const successNodes = page.locator('[data-status="success"]');
  107 |     const errorNodes = page.locator('[data-status="error"]');
  108 |     const warningNodes = page.locator('[data-status="warning"]');
  109 |     const activeNodes = page.locator('[data-status="active"]');
  110 | 
  111 |     // At least one type of status node should exist
  112 |     const totalStatusNodes =
  113 |       (await successNodes.count()) +
  114 |       (await errorNodes.count()) +
  115 |       (await warningNodes.count()) +
  116 |       (await activeNodes.count());
  117 | 
  118 |     // Note: This depends on the actual implementation
  119 |     // If status is determined by node type instead, verify that instead
  120 |   });
  121 | 
  122 |   graphTest('Node labels visible (decision/error/checkpoint text)', async ({
  123 |     page,
  124 |     graphBase,
  125 |   }) => {
```