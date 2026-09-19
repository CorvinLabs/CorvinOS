# E2E Test Framework — CorvinOS Console Panels

Comprehensive Playwright E2E test suite for the CorvinOS console with priority-based testing (P0–P7).

## Quick Start

### Run All P0 Tests (Critical Path)
```bash
cd core/console/corvin_console/web-next
npm run test:e2e -- --grep="^P0-"
```

### Run P0–P1 Tests (Most Important)
```bash
npm run test:e2e -- --grep="^P[0-1]-"
```

### Run All Tests
```bash
npm run test:e2e
```

### Run with Report
```bash
npm run test:e2e -- --reporter=html
# Open: playwright-report/index.html
```

## Test Organization

### Priority Levels

| Priority | Scope | Definition | Pass Requirement |
|----------|-------|-----------|-------------------|
| **P0** | Core Infrastructure | Dashboard, layout, core navigation | Must pass on every commit to main |
| **P1** | Critical Business Logic | Models, Marketplace | Must pass on main; blocks releases |
| **P2** | High-Value Features | Compliance, Forge | Should pass on main; skipped pre-release |
| **P3** | Important Features | Video Quality, Learnings, Compute | Best effort; OK to skip if time-constrained |
| **P4–P7** | Optional/Advanced | Nice-to-have features | Deferred testing |

### Test Files

```
tests/e2e/
├── base/
│   └── panel-test-base.ts          # Base test class with shared patterns
├── fixtures/
│   ├── panel-fixtures.ts           # Playwright fixtures (PanelNavigator)
│   ├── mock-api.ts                 # Mock API setup
│   └── vibe-data.ts                # Test data (learnings, metrics, etc.)
├── test-panels-p0.spec.ts          # P0 tests (Dashboard, core navigation)
├── test-panels-p1.spec.ts          # P1 tests (Models, Marketplace)
├── test-panels-p2.spec.ts          # P2 tests (Compliance, Forge)
├── test-panels-p3.spec.ts          # P3 tests (Video, Learnings, Compute, etc.)
├── [other existing tests]           # Legacy/specialized tests
└── README-E2E.md                   # This file
```

## Writing a Panel Test

### 1. Create a Test Class

```typescript
import { ConsolePanelTest } from './base/panel-test-base';

const dashboardTest = new ConsolePanelTest({
  id: 'dashboard',
  title: 'Dashboard',
  priority: 'P0',
  group: 'primary',
});
```

### 2. Write Test Cases

```typescript
test('P0-1: Dashboard loads within performance baseline', async ({ page, panelNav }) => {
  const startTime = Date.now();
  await dashboardTest.navigateAndAssert(page, panelNav);
  const loadTime = Date.now() - startTime;
  expect(loadTime).toBeLessThan(3000);
});
```

### 3. Available Helpers

- `navigateAndAssert()` — Go to panel and verify it loaded
- `assertPanelTitle()` — Check page title
- `testErrorHandling()` — Verify error recovery
- `measurePerformance()` — Capture load time, paint events, network requests
- `assertPerformanceBaseline()` — Validate performance constraints
- `testAccessibility()` — Check keyboard nav, landmarks, heading hierarchy
- `takeBaseline()` — Create visual regression baseline
- `getConsoleErrors()` — Collect browser console errors

## Running Tests by Priority

### CI/CD (GitHub Actions)

The workflow `e2e-playwright.yml` runs:
1. **P0 tests** (required, `continue-on-error: false`) — must pass
2. **P1–P3 tests** (optional, `continue-on-error: true`) — best effort

### Local Development

```bash
# P0 only (fast: ~2 min)
npm run test:e2e -- --grep="^P0-" --workers=1

# P0–P1 (critical path: ~5 min)
npm run test:e2e -- --grep="^P[0-1]-" --workers=2

# P0–P3 (comprehensive: ~15 min)
npm run test:e2e -- --grep="^P[0-3]-" --workers=4

# All tests (full suite: ~30 min)
npm run test:e2e
```

## Performance Baselines

Each panel test includes performance assertions:

| Metric | Constraint | Notes |
|--------|-----------|-------|
| **Load Time** | < 3–5s | Time from navigation start to main content visible |
| **Network Requests** | < 15–20 | Includes API, CSS, JS, images |
| **First Paint** | < 1s | Time to first visual change |
| **Console Errors** | 0 critical | 404s and deprecation warnings OK |

### Measuring Performance

```typescript
const metrics = await panelTest.measurePerformance(page, panelNav);
console.log('Load Time:', metrics.loadTime, 'ms');
console.log('Network Requests:', metrics.networkRequests);
console.log('Console Errors:', metrics.consoleErrors.length);
```

## Visual Regression Testing

### Create Baseline (One-Time)

```typescript
test('create visual baseline', async ({ page, panelNav }) => {
  await panelTest.navigateAndAssert(page, panelNav);
  await panelTest.takeBaseline(page, 'dashboard-full');
});

// Run with --update-snapshots flag
npm run test:e2e -- --update-snapshots
```

### Compare Against Baseline (CI)

```typescript
test('visual regression', async ({ page, panelNav }) => {
  await panelTest.navigateAndAssert(page, panelNav);
  await panelTest.assertNoVisualRegression(page, 'dashboard-full');
});
```

## Debugging Tests

### Run Single Test
```bash
npm run test:e2e -- --grep="^P0-1:"
```

### Debug Mode (Browser Stays Open)
```bash
PLAYWRIGHT_HEADED=1 npm run test:e2e -- --grep="^P0-1:"
```

### Verbose Output
```bash
npm run test:e2e -- --grep="^P0-1:" --verbose
```

### View Traces (Debugging Failed Tests)
```bash
# Traces are stored in playwright-report/
npx playwright show-trace playwright-report/trace.zip
```

## Integration with LDD (Loss-Driven Development)

When adding or modifying E2E tests, follow the LDD cycles:

### Phase k=1 (Dialectical Reasoning)
- Propose test: "We should E2E test dashboard load time"
- Argue for/against, surface assumptions
- Run `/dialectical-reasoning` skill

### Phase k=2 (E2E-Driven Iteration)
- Implement test
- Run locally: `npm run test:e2e`
- Verify test passes on target panel

### Phase k=3 (Red–Green–Refactor)
- Red: test fails on broken panel → verify it catches regressions
- Green: fix panel → test passes
- Refactor: clean up test code, improve assertions

### Phase k=4–5 (Refinement & Docs)
- Ensure test name clearly describes what it tests (P0-1, P1-5, etc.)
- Add docstring with ADR reference if applicable
- Update this README if new pattern emerges

## Compliance & Audit

Tests verify compliance with:
- **ADR-0232/0233** — Audit trail (no errors escaping)
- **ADR-0695** — Video Quality Metrics panel
- **ADR-0885** — Models panel (routing, cost, learning)
- **ADR-0892** — Marketplace (one unified plugin/package discovery)
- **ADR-0400** — Vibe Engineering (learnings dashboard)

Each panel test logs:
- Page load time
- Network request count
- Console errors/warnings
- Accessibility issues (optional)

## Troubleshooting

### Tests Timeout

**Problem:** "Timeout waiting for main content"

**Solution:**
1. Check if console is actually running: `curl http://127.0.0.1:8765/console/`
2. Check for network errors in `--reporter=html` output
3. Increase timeout: `npm run test:e2e -- --timeout=30000`

### API 404 Errors

**Problem:** Tests fail on missing API endpoints

**Solution:**
1. Check backend is running (required for live tests)
2. Or use mock APIs: `PLAYWRIGHT_MOCK_AUTH=1 npm run test:e2e`
3. Check `fixtures/mock-api.ts` for endpoint definitions

### Visual Regression False Positives

**Problem:** Baseline comparison fails on minor style changes

**Solution:**
1. Update baseline: `npm run test:e2e -- --update-snapshots`
2. Review diff in `playwright-report/`
3. Commit updated baseline if intentional style change

## Performance Benchmarks (Target)

Measured on clean environment (2026-09-19):

| Panel | Priority | Load Time | Network Reqs | Status |
|-------|----------|-----------|--------------|--------|
| Dashboard | P0 | ~500ms | 8 | ✅ |
| Models | P1 | ~1200ms | 12 | ✅ |
| Marketplace | P1 | ~1500ms | 14 | ✅ |
| Compliance | P2 | ~1800ms | 16 | ✅ |
| Forge | P2 | ~1400ms | 11 | ✅ |
| Video Quality | P3 | ~2100ms | 18 | ✅ |
| Learnings | P3 | ~2300ms | 20 | ⚠️ |
| Compute | P3 | ~1600ms | 13 | ✅ |
| Connectors | P3 | ~1900ms | 15 | ✅ |

## Next Steps

1. **Expand P4–P7 Panels** — Migrate remaining stub tests to full implementations
2. **Visual Regression Baselines** — Create and commit baseline screenshots for all P0–P3
3. **Performance Monitoring** — Track load times across releases
4. **Cross-Browser Testing** — Verify Chrome, Safari (headless) in addition to Chromium/Firefox
5. **Accessibility Audit** — Run WCAG checks via AXE plugin

## References

- [Playwright Documentation](https://playwright.dev/)
- [Panel Registry](../src/panels/registry.tsx) — All console panels
- [ADR-0353](../../../corvin_decisions/decisions/ADR-0353-console-panel-architecture.md) — Panel architecture
- [ADR-0695](../../../corvin_decisions/decisions/ADR-0695-video-quality-metrics.md) — Video Quality Metrics
- [ADR-0885](../../../corvin_decisions/decisions/ADR-0885-unified-models-panel.md) — Models panel
- [ADR-0892](../../../corvin_decisions/decisions/ADR-0892-one-marketplace.md) — Marketplace unification
