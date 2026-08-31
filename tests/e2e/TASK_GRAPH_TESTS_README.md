# Task Graph Visualization E2E Test Suite

Comprehensive Playwright end-to-end tests for the Task Graph Visualization component. Tests cover API endpoints, component rendering, user interactions, responsive design, dark mode, error handling, performance, and accessibility.

## Test Structure

### Files

```
tests/e2e/
├── playwright.config.ts           # Playwright configuration (multi-browser, multi-device)
├── fixtures.ts                    # Test fixtures: sample graphs, API utilities, test data
├── base.spec.ts                   # Base test class with common utilities
├── test_api_endpoints.spec.ts     # API level tests (10 tests)
├── test_component_rendering.spec.ts   # Component rendering (15 tests)
├── test_interactions.spec.ts      # User interactions (20 tests)
├── test_responsive_dark_error.spec.ts # Responsive, dark mode, errors (20 tests)
├── test_performance_accessibility.spec.ts # Performance & accessibility (20 tests)
└── TASK_GRAPH_TESTS_README.md     # This file
```

### Test Suites

#### Suite A: API Level (`test_api_endpoints.spec.ts`) — 10 tests
- `GET /api/tasks/{id}/graph` returns valid JSON
- Node structure validation (id, type, timestamp, data)
- Edge structure validation (from_id, to_id, edge_type, label)
- Query endpoints: reachability, snapshots
- 404 handling for nonexistent tasks
- Empty graphs (0 nodes)
- Large graph performance (500 nodes < 5s)
- Malformed query params handling
- Missing fields graceful handling
- CORS headers and Content-Type validation
- Rate limiting handling (429 responses)

#### Suite B: Component Rendering (`test_component_rendering.spec.ts`) — 15 tests
- Graph panel visibility and navigation
- DAG rendering without errors (100+ nodes)
- All nodes and edges visible
- Color coding (success, error, warning, active)
- Node labels visible
- Zoom functionality (1x → 2x → 0.5x)
- Pan functionality (drag and drag)
- Fit-to-screen button
- Reset view button
- SVG elements render correctly
- No console errors during render
- Render performance (< 1s for 100 nodes)
- Export SVG functionality
- Export DOT (GraphViz) functionality
- Memory usage for large graphs (< 50MB for 500 nodes)

#### Suite C: User Interactions (`test_interactions.spec.ts`) — 20 tests
- Click decision node → tooltip with decision text
- Tooltip disappears on mouseleave
- Click error node → modal with error details
- Click checkpoint node → modal with state snapshot
- Hover edge → edge label visible
- Filter by node type (decision, error, checkpoint, etc.)
- Multiple filters combinable
- Reset filters
- Search and highlight nodes by ID
- Highlighted node styling
- Double-click drill-down to subgraph (if supported)
- Breadcrumb navigation
- Modal close button
- Modal ESC key handling
- Modal click-outside-to-close
- Keyboard shortcuts: "+" zooms in
- Keyboard shortcuts: "-" zooms out
- Keyboard shortcuts: "Home" resets view

#### Suite D: Responsive & Dark & Errors (`test_responsive_dark_error.spec.ts`) — 20 tests

**Responsive Design:**
- Mobile (375px): readable, no horizontal scroll
- Mobile: filter controls accessible
- Mobile: export button accessible
- Tablet (768px): reasonable zoom
- Tablet: all controls accessible
- Desktop (1920px): full DAG visible at 1x
- Desktop: performance metrics shown
- Touch interactions on mobile

**Dark Mode:**
- Toggle dark mode → colors change
- Node colors readable in dark mode
- Edge labels readable in dark mode

**Error Handling:**
- API timeout → error message with retry
- Empty graph → "No data" message
- Malformed JSON → graceful error
- Large graphs (1000 nodes) → progressive rendering
- Missing node data → fallback values
- Broken edge references → graceful handling
- Network errors on export → retry button

#### Suite E: Performance & Accessibility (`test_performance_accessibility.spec.ts`) — 20 tests

**Performance:**
- Small graph (10 nodes): render < 200ms
- Medium graph (100 nodes): render < 1s
- Large graph (500 nodes): render < 2s
- Query API latency < 500ms
- Memory usage < 50MB for 100 nodes
- First Contentful Paint (FCP) < 1s
- Largest Contentful Paint (LCP) < 2.5s
- No memory leaks on repeated renders
- Pan/zoom maintains 60 fps (or 30 fps minimum)
- Export completes < 5s

**Accessibility:**
- Keyboard navigation (Tab) through nodes
- ARIA labels on nodes and edges
- Screen reader announces node type and status
- ARIA live regions for status updates
- Color contrast meets WCAG AA
- Touch target size >= 44x44 px
- Error messages announced to screen readers
- Focus indicator visible
- Tooltips announced by screen readers
- High contrast mode support

## Test Fixtures

### Graph Samples

```typescript
// Small graph: 10 nodes, 12 edges
smallGraph

// Medium graph: 50 nodes, 120 edges (procedurally generated)
mediumGraph

// Large graph: 500 nodes, 1200 edges (for performance testing)
largeGraph

// Empty graph: 0 nodes
emptyGraph

// Malformed graph: broken edge references
malformedGraph

// Cyclic graph: contains cycles (invalid DAG)
cyclicGraph
```

### API Utilities

```typescript
// Fetch graph data
await taskGraphAPI.fetchGraph(taskId)

// Query graph (reachability, snapshots, etc.)
await taskGraphAPI.queryGraph(taskId, queryType)

// Get graph snapshot at specific timestamp
await taskGraphAPI.getSnapshot(taskId, timestamp)
```

## Running Tests

### Run all tests
```bash
npx playwright test tests/e2e/test_*.spec.ts
```

### Run specific test suite
```bash
npx playwright test tests/e2e/test_api_endpoints.spec.ts
npx playwright test tests/e2e/test_component_rendering.spec.ts
npx playwright test tests/e2e/test_interactions.spec.ts
npx playwright test tests/e2e/test_responsive_dark_error.spec.ts
npx playwright test tests/e2e/test_performance_accessibility.spec.ts
```

### Run specific test
```bash
npx playwright test -g "GET /api/tasks/{id}/graph returns valid"
```

### Run with specific browser
```bash
npx playwright test --project=chromium
npx playwright test --project=firefox
npx playwright test --project=webkit
npx playwright test --project=mobile-chrome
npx playwright test --project=tablet
```

### Run in debug mode
```bash
npx playwright test --debug tests/e2e/test_api_endpoints.spec.ts
```

### Run with UI mode
```bash
npx playwright test --ui tests/e2e/test_*.spec.ts
```

### Run headed (show browser)
```bash
npx playwright test --headed tests/e2e/test_api_endpoints.spec.ts
```

### Generate HTML report
```bash
npx playwright test tests/e2e/
npx playwright show-report
```

## Test Data & Mocking

### API Mocking
Tests can mock API responses using Playwright's route interception:

```typescript
await page.route('**/api/tasks/**/graph', (route) => {
  route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(smallGraph),
  });
});
```

### Test Fixtures
Large graphs are generated procedurally to avoid massive JSON files:

```typescript
const largeGraph = generateGraph('task_large_001', 500, 1200);
```

## Performance Benchmarks

| Graph Size | Expected Render Time | Memory Usage | Nodes | Edges |
|---|---|---|---|---|
| Small | < 200ms | N/A | 10 | 12 |
| Medium | < 1s | < 50MB | 100 | 120 |
| Large | < 2s | < 50MB | 500 | 1200 |

## CI/CD Integration

### GitHub Actions
Add to `.github/workflows/test.yml`:

```yaml
- name: Run Graph Visualization E2E Tests
  run: npx playwright test tests/e2e/test_*.spec.ts
  
- name: Upload Test Results
  if: always()
  uses: actions/upload-artifact@v3
  with:
    name: playwright-report
    path: playwright-report/
```

### Key Configurations
- `workers: 1` on CI (serial execution)
- `retries: 2` on CI (retry flaky tests)
- `headless: true` on CI
- Screenshots on failure
- Video on failure
- HTML report generation

## Test Coverage

### Covered Scenarios
✅ API endpoints (CRUD, query, errors)
✅ Component rendering (SVG, performance)
✅ User interactions (click, hover, keyboard)
✅ Responsive design (mobile, tablet, desktop)
✅ Dark mode support
✅ Error handling (timeouts, malformed data, network errors)
✅ Performance (render time, memory, FCP/LCP)
✅ Accessibility (keyboard, screen reader, WCAG AA)
✅ Touch interactions
✅ Large graph handling (progressive rendering)

### Known Limitations
- Some features (drill-down, breadcrumbs) are optional/future implementations
- Rate limiting tests assume 429 responses (may not be implemented)
- Some performance metrics depend on CI environment

## Troubleshooting

### Flaky Tests
**Symptom:** Tests pass locally but fail on CI
**Solutions:**
- Increase timeouts in CI (`workers: 1` serializes tests)
- Add explicit waits for async operations
- Use `waitForFunction` instead of fixed `waitForTimeout`

### Slow Tests
**Symptom:** Tests timeout on slow hardware
**Solutions:**
- Run medium/large graph tests separately
- Increase timeout in `playwright.config.ts`
- Disable video recording on CI

### Missing Selectors
**Symptom:** `Locator.click() timeout`
**Solutions:**
- Verify component implements `data-testid` attributes
- Use `getByRole` / `getByLabel` as fallback
- Check for shadow DOM or iframes

### API Mocking Issues
**Symptom:** Real API calls despite mocking
**Solutions:**
- Ensure route pattern matches exactly
- Use `page.route()` before navigation
- Check for XHR vs fetch differences

## Maintenance

### Adding New Tests
1. Determine test suite (API, rendering, interactions, etc.)
2. Add test to appropriate file
3. Use existing fixtures and utilities
4. Document expected behavior in test name
5. Verify test passes locally in multiple browsers

### Updating Fixtures
1. Keep fixture graphs realistic (based on actual data)
2. Document node/edge counts in comments
3. Test with fixture-update workflow
4. Regenerate large graphs if schema changes

### Performance Baselines
Update benchmark thresholds if:
- Component optimization reduces render time
- Target hardware changes
- New features impact memory/performance
- Browser performance improves

## References

- TaskGraph ADR-0400 (see Corvin-ADR repo for graph-native-task-execution-model)
- [Playwright Documentation](https://playwright.dev)
- [WCAG 2.1 Accessibility Guidelines](https://www.w3.org/WAI/WCAG21/quickref/)

---

**Last Updated:** 2026-08-24  
**Test Count:** 85+ tests across 5 suites  
**Status:** Production-grade E2E test suite
