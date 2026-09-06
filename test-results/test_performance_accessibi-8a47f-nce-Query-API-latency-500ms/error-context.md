# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: test_performance_accessibility.spec.ts >> Task Graph Component — Performance >> Query API latency < 500ms
- Location: tests/e2e/test_performance_accessibility.spec.ts:61:12

# Error details

```
Error: expect(received).toBeLessThan(expected)

Expected: < 500
Received:   18425
```

# Test source

```ts
  1   | /**
  2   |  * E2E Tests: Task Graph Visualization — Performance & Accessibility
  3   |  *
  4   |  * Performance Tests:
  5   |  * - Render time benchmarks (10, 100, 500 nodes)
  6   |  * - API latency (< 500ms)
  7   |  * - Memory usage (< 50MB for 100 nodes)
  8   |  * - FCP, LCP metrics
  9   |  *
  10  |  * Accessibility Tests:
  11  |  * - Keyboard navigation (Tab)
  12  |  * - ARIA labels on nodes/edges
  13  |  * - Screen reader announcements
  14  |  * - Color contrast (WCAG AA)
  15  |  */
  16  | 
  17  | import { test, expect } from '@playwright/test';
  18  | import { graphTest, GraphE2EBase } from './base';
  19  | 
  20  | // ============================================================================
  21  | // PERFORMANCE TESTS
  22  | // ============================================================================
  23  | 
  24  | graphTest.describe('Task Graph Component — Performance', () => {
  25  |   graphTest('Small graph (10 nodes): render < 200ms', async ({
  26  |     page,
  27  |     graphBase,
  28  |   }) => {
  29  |     const renderTime = await graphBase.measureRenderTime(page, async () => {
  30  |       await graphBase.navigateToGraphPanel(page, 'task_small_001');
  31  |       await graphBase.waitForGraphRender(page);
  32  |     });
  33  | 
  34  |     expect(renderTime).toBeLessThan(200);
  35  |   });
  36  | 
  37  |   graphTest('Medium graph (100 nodes): render < 1s', async ({
  38  |     page,
  39  |     graphBase,
  40  |   }) => {
  41  |     const renderTime = await graphBase.measureRenderTime(page, async () => {
  42  |       await graphBase.navigateToGraphPanel(page, 'task_medium_001');
  43  |       await graphBase.waitForGraphRender(page);
  44  |     });
  45  | 
  46  |     expect(renderTime).toBeLessThan(1000);
  47  |   });
  48  | 
  49  |   graphTest('Large graph (500 nodes): render < 2s', async ({
  50  |     page,
  51  |     graphBase,
  52  |   }) => {
  53  |     const renderTime = await graphBase.measureRenderTime(page, async () => {
  54  |       await graphBase.navigateToGraphPanel(page, 'task_large_001');
  55  |       await graphBase.waitForGraphRender(page);
  56  |     });
  57  | 
  58  |     expect(renderTime).toBeLessThan(2000);
  59  |   });
  60  | 
  61  |   graphTest('Query API latency < 500ms', async ({ request }) => {
  62  |     const startTime = Date.now();
  63  | 
  64  |     const response = await request.get(
  65  |       'http://127.0.0.1:8765/api/tasks/task_small_001/graph/query',
  66  |       {
  67  |         params: { type: 'reachability', node: 'start_decision' },
  68  |       }
  69  |     );
  70  | 
  71  |     const endTime = Date.now();
  72  |     const latency = endTime - startTime;
  73  | 
> 74  |     expect(latency).toBeLessThan(500);
      |                     ^ Error: expect(received).toBeLessThan(expected)
  75  |   });
  76  | 
  77  |   graphTest('Memory usage < 50MB for 100-node graph', async ({
  78  |     page,
  79  |     graphBase,
  80  |   }) => {
  81  |     // Baseline memory
  82  |     const memoryBefore = await page.evaluate(() => {
  83  |       if ('memory' in performance) {
  84  |         return (performance as any).memory.usedJSHeapSize;
  85  |       }
  86  |       return 0;
  87  |     });
  88  | 
  89  |     // Render graph
  90  |     await graphBase.navigateToGraphPanel(page, 'task_medium_001');
  91  |     await graphBase.waitForGraphRender(page);
  92  | 
  93  |     // Peak memory
  94  |     const memoryAfter = await page.evaluate(() => {
  95  |       if ('memory' in performance) {
  96  |         return (performance as any).memory.usedJSHeapSize;
  97  |       }
  98  |       return 0;
  99  |     });
  100 | 
  101 |     const memoryUsed = memoryAfter - memoryBefore;
  102 |     const memoryMB = memoryUsed / (1024 * 1024);
  103 | 
  104 |     // Should use less than 50MB
  105 |     if (memoryBefore > 0) {
  106 |       expect(memoryMB).toBeLessThan(50);
  107 |     }
  108 |   });
  109 | 
  110 |   graphTest('First Contentful Paint (FCP) < 1s', async ({
  111 |     page,
  112 |     graphBase,
  113 |   }) => {
  114 |     // Measure FCP
  115 |     const metrics = await page.evaluate(() => {
  116 |       const navigation = performance.getEntriesByType('navigation')[0] as any;
  117 |       const paintEntries = performance.getEntriesByType('paint');
  118 | 
  119 |       const fcp = paintEntries.find((p: any) => p.name === 'first-contentful-paint');
  120 | 
  121 |       return {
  122 |         fcp: fcp ? fcp.startTime : navigation?.responseEnd,
  123 |       };
  124 |     });
  125 | 
  126 |     expect(metrics.fcp).toBeLessThan(1000);
  127 |   });
  128 | 
  129 |   graphTest('Largest Contentful Paint (LCP) < 2.5s', async ({
  130 |     page,
  131 |     graphBase,
  132 |   }) => {
  133 |     await graphBase.navigateToGraphPanel(page, 'task_small_001');
  134 |     await graphBase.waitForGraphRender(page);
  135 | 
  136 |     // Get LCP metric
  137 |     const lcp = await page.evaluate(() => {
  138 |       return new Promise<number>((resolve) => {
  139 |         const observer = new PerformanceObserver((list) => {
  140 |           const entries = list.getEntries();
  141 |           const lastEntry = entries[entries.length - 1] as any;
  142 |           resolve(lastEntry.renderTime || lastEntry.loadTime);
  143 |         });
  144 | 
  145 |         observer.observe({ type: 'largest-contentful-paint', buffered: true });
  146 | 
  147 |         // Stop after 3 seconds
  148 |         setTimeout(() => {
  149 |           observer.disconnect();
  150 |           resolve(0);
  151 |         }, 3000);
  152 |       });
  153 |     });
  154 | 
  155 |     expect(lcp).toBeLessThan(2500);
  156 |   });
  157 | 
  158 |   graphTest('No memory leaks during repeated renders', async ({
  159 |     page,
  160 |     graphBase,
  161 |   }) => {
  162 |     const samples: number[] = [];
  163 | 
  164 |     for (let i = 0; i < 3; i++) {
  165 |       // Render graph
  166 |       await graphBase.navigateToGraphPanel(page, 'task_small_001');
  167 |       await graphBase.waitForGraphRender(page);
  168 | 
  169 |       // Measure memory
  170 |       const memory = await page.evaluate(() => {
  171 |         if ('memory' in performance) {
  172 |           return (performance as any).memory.usedJSHeapSize;
  173 |         }
  174 |         return 0;
```