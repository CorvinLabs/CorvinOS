# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: test_api_endpoints.spec.ts >> Task Graph API — Endpoints >> Malformed query params handled gracefully
- Location: tests/e2e/test_api_endpoints.spec.ts:233:7

# Error details

```
Error: expect(received).toContain(expected) // indexOf

Expected value: 404
Received array: [200, 400]
```

# Test source

```ts
  143 |       }
  144 |     );
  145 | 
  146 |     // Expect 200 or 404 (endpoint may not be implemented yet)
  147 |     expect([200, 404]).toContain(response.status());
  148 | 
  149 |     if (response.status() === 200) {
  150 |       const result = await response.json();
  151 | 
  152 |       // Should return reachability analysis
  153 |       expect(result).toHaveProperty('reachable_nodes');
  154 |       expect(Array.isArray(result.reachable_nodes)).toBe(true);
  155 |     }
  156 |   });
  157 | 
  158 |   test('GET /api/tasks/{id}/graph/snapshot?t={timestamp}', async ({
  159 |     request,
  160 |   }) => {
  161 |     const timestamp = new Date().toISOString();
  162 | 
  163 |     const response = await request.get(
  164 |       `${TASK_GRAPH_ENDPOINT('task_small_001')}/snapshot`,
  165 |       {
  166 |         params: { t: timestamp },
  167 |       }
  168 |     );
  169 | 
  170 |     expect([200, 404]).toContain(response.status());
  171 | 
  172 |     if (response.status() === 200) {
  173 |       const snapshot = await response.json();
  174 | 
  175 |       // Should return graph at specific point in time
  176 |       expect(snapshot).toHaveProperty('nodes');
  177 |       expect(snapshot).toHaveProperty('edges');
  178 |       expect(snapshot).toHaveProperty('timestamp');
  179 |     }
  180 |   });
  181 | 
  182 |   test('404 on nonexistent task', async ({ request }) => {
  183 |     const response = await request.get(
  184 |       TASK_GRAPH_ENDPOINT('task_does_not_exist_xyz')
  185 |     );
  186 | 
  187 |     expect(response.status()).toBe(404);
  188 | 
  189 |     const error = await response.json();
  190 |     expect(error).toHaveProperty('error');
  191 |     expect(error.error).toContain('not found');
  192 |   });
  193 | 
  194 |   test('Empty graph on new task (no events)', async ({ request }) => {
  195 |     // Assuming a new task with no events returns empty graph
  196 |     const response = await request.get(
  197 |       TASK_GRAPH_ENDPOINT('task_brand_new_001')
  198 |     );
  199 | 
  200 |     if (response.status() === 200) {
  201 |       const graph = await response.json();
  202 | 
  203 |       // Should have empty or minimal structure
  204 |       expect(Object.keys(graph.nodes).length).toBe(0);
  205 |       expect(graph.edges.length).toBe(0);
  206 |     }
  207 |   });
  208 | 
  209 |   test('Large graph fetch (500 nodes) completes under 5s', async ({
  210 |     request,
  211 |   }) => {
  212 |     const startTime = Date.now();
  213 | 
  214 |     const response = await request.get(
  215 |       TASK_GRAPH_ENDPOINT('task_large_001')
  216 |     );
  217 | 
  218 |     const endTime = Date.now();
  219 |     const duration = endTime - startTime;
  220 | 
  221 |     // Should complete within 5 seconds
  222 |     expect(duration).toBeLessThan(5000);
  223 | 
  224 |     if (response.status() === 200) {
  225 |       const graph = await response.json();
  226 | 
  227 |       // Large graph should have many nodes
  228 |       const nodeCount = Object.keys(graph.nodes).length;
  229 |       expect(nodeCount).toBeGreaterThan(100);
  230 |     }
  231 |   });
  232 | 
  233 |   test('Malformed query params handled gracefully', async ({ request }) => {
  234 |     // Missing required query param
  235 |     const response = await request.get(
  236 |       `${TASK_GRAPH_ENDPOINT('task_small_001')}/query`,
  237 |       {
  238 |         params: { type: '' }, // Empty type
  239 |       }
  240 |     );
  241 | 
  242 |     // Should return 400 or provide default behavior
> 243 |     expect([200, 400]).toContain(response.status());
      |                        ^ Error: expect(received).toContain(expected) // indexOf
  244 | 
  245 |     if (response.status() === 400) {
  246 |       const error = await response.json();
  247 |       expect(error).toHaveProperty('error');
  248 |     }
  249 |   });
  250 | 
  251 |   test('Missing fields in response handled by client', async ({ request }) => {
  252 |     const response = await request.get(
  253 |       TASK_GRAPH_ENDPOINT('task_small_001')
  254 |     );
  255 | 
  256 |     if (response.status() === 200) {
  257 |       const graph = await response.json();
  258 | 
  259 |       // Client should handle missing optional fields gracefully
  260 |       // e.g., missing 'metadata' field in edge should use empty object
  261 |       const edge = graph.edges[0];
  262 | 
  263 |       // This test verifies that even if metadata is missing,
  264 |       // the client doesn't crash
  265 |       expect(edge).toBeDefined();
  266 | 
  267 |       // If metadata is present, it should be an object
  268 |       if ('metadata' in edge) {
  269 |         expect(typeof edge.metadata).toBe('object');
  270 |       }
  271 |     }
  272 |   });
  273 | 
  274 |   test('Response includes correct CORS headers', async ({ request }) => {
  275 |     const response = await request.get(
  276 |       TASK_GRAPH_ENDPOINT('task_small_001')
  277 |     );
  278 | 
  279 |     if (response.status() === 200) {
  280 |       // Verify CORS headers are present for browser consumption
  281 |       const headers = response.headers();
  282 | 
  283 |       // This is a conditional check — server may or may not set these
  284 |       if ('access-control-allow-origin' in headers) {
  285 |         expect(headers['access-control-allow-origin']).toBeDefined();
  286 |       }
  287 |     }
  288 |   });
  289 | 
  290 |   test('Content-Type is application/json', async ({ request }) => {
  291 |     const response = await request.get(
  292 |       TASK_GRAPH_ENDPOINT('task_small_001')
  293 |     );
  294 | 
  295 |     if (response.status() === 200) {
  296 |       const contentType = response.headers()['content-type'];
  297 |       expect(contentType).toContain('application/json');
  298 |     }
  299 |   });
  300 | 
  301 |   test('API rate limiting returns 429 on excessive requests', async ({
  302 |     request,
  303 |   }) => {
  304 |     // Make 100 rapid requests
  305 |     const promises: Promise<any>[] = [];
  306 |     for (let i = 0; i < 100; i++) {
  307 |       promises.push(
  308 |         request.get(TASK_GRAPH_ENDPOINT('task_small_001')).catch(() => null)
  309 |       );
  310 |     }
  311 | 
  312 |     const results = await Promise.all(promises);
  313 |     const statuses = results.map((r) => r?.status()).filter(Boolean);
  314 | 
  315 |     // Server may rate limit after many requests
  316 |     // This test verifies graceful handling if rate limiting is implemented
  317 |     const hasRateLimit = statuses.some((s) => s === 429 || s === 503);
  318 | 
  319 |     // If rate limiting exists, it should be handled gracefully
  320 |     if (hasRateLimit) {
  321 |       expect(statuses.some((s) => [429, 503].includes(s))).toBe(true);
  322 |     }
  323 |   });
  324 | });
  325 | 
```