# E2E Testing Guide — Playwright Console Tests

**Status:** Phase 2 Stream 1 (2026-09-19)  
**Coverage:** 33 Console Panels (200+ test cases)  
**Browsers:** Chrome, Firefox  
**Execution:** Parallel (4-10 workers)

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Test Structure](#test-structure)
3. [Running Tests](#running-tests)
4. [Writing New Tests](#writing-new-tests)
5. [Debugging Failed Tests](#debugging-failed-tests)
6. [CI/CD Integration](#cicd-integration)
7. [Performance Baselines](#performance-baselines)
8. [Troubleshooting](#troubleshooting)

---

## Quick Start

### Prerequisites

```bash
# Node.js 18+ required
node --version

# Install dependencies
npm install

# Install Playwright browsers
npx playwright install
```

### Run All E2E Tests

```bash
# Default: runs on Chrome + Firefox with 4 workers
npm run test:e2e

# Run only Chrome
npm run test:e2e -- --project=chromium

# Run specific panel tests
npm run test:e2e -- --grep "Dashboard|Settings"

# Run with UI mode (interactive)
npm run test:e2e -- --ui

# Run single test file
npm run test:e2e -- tests/e2e/panel-dashboard.spec.ts
```

### View Test Results

```bash
# Open HTML report
npx playwright show-report

# Or navigate to
open playwright-report/index.html
```

---

## Test Structure

### Directory Layout

```
tests/e2e/
├── panel-<name>.spec.ts           # Individual panel test suites (33 files)
├── panel-suite-master.spec.ts     # Master orchestrator
├── fixtures/
│   ├── panel-fixtures.ts          # Shared PanelNavigator + test helpers
│   ├── mock-api.ts                # API mocking setup
│   ├── mock-pages.tsx             # React component mocks
│   └── server.ts                  # Test server config
├── global-setup-adr0124.ts        # Global auth setup (login once)
├── auth-state.json                # Cached session (auto-generated)
└── snapshots/                     # Screenshot baselines
```

### Fixture Architecture

**PanelNavigator** — shared navigation utilities:

```typescript
import { test, expect } from '../fixtures/panel-fixtures';

test('basic flow', async ({ page, panelNav }) => {
  // Navigate to panel
  await panelNav.goto('dashboard');

  // Assert loaded
  await panelNav.assertLoaded('dashboard');

  // Get common elements
  const title = panelNav.getTitle();
  const mainContent = panelNav.getMainContent();

  // Wait for async elements
  await panelNav.waitForElement('[data-testid="chart"]');

  // Assert
  await expect(mainContent).toBeVisible();
});
```

**panelTestPresets** — reusable test patterns:

```typescript
// Test form submission
const formSubmitted = await panelTestPresets.testFormSubmit(
  page,
  '[data-testid="my-form"]',
  'Save'
);

// Test data table
const tableInfo = await panelTestPresets.testDataTable(
  page,
  'table[data-testid="my-table"]'
);
console.log(`Table has ${tableInfo.rowCount} rows`);

// Test modal
const modal = await panelTestPresets.testModal(
  page,
  'button[data-testid="open-modal"]'
);
```

---

## Running Tests

### Basic Commands

```bash
# Run all tests
npm run test:e2e

# Run single browser
npm run test:e2e -- --project=chromium
npm run test:e2e -- --project=firefox

# Run with grep filter
npm run test:e2e -- --grep "Dashboard"
npm run test:e2e -- --grep "Settings|Plugins"
npm run test:e2e -- --grep "form"

# Run specific test file
npm run test:e2e -- tests/e2e/panel-dashboard.spec.ts

# Run with 1 worker (debug mode)
npm run test:e2e -- --workers=1

# Run with UI mode (interactive browser)
npm run test:e2e -- --ui

# Run headed mode (see browser window)
npm run test:e2e -- --headed
```

### Debug Mode

```bash
# Step through tests interactively
npm run test:e2e -- --debug

# Inspect specific test
npx playwright test tests/e2e/panel-dashboard.spec.ts --debug
```

### Headed Mode (Watch Tests Run)

```bash
# Run and watch the browser
npm run test:e2e -- --headed

# Only chrome, headed
npm run test:e2e -- --project=chromium --headed
```

---

## Writing New Tests

### Template: Basic Panel Test

```typescript
import { test, expect } from '../fixtures/panel-fixtures';

test.describe('My Panel', () => {
  const panelSlug = 'my-panel';
  const panelTitle = 'My Panel';

  test.beforeEach(async ({ page }) => {
    // Mock API responses
    await page.route('**/v1/console/my-panel/**', (route) => {
      route.fulfill({
        status: 200,
        body: JSON.stringify({
          data: { /* your mock data */ },
          success: true,
        }),
      });
    });
  });

  test('navigates and loads', async ({ panelNav }) => {
    await panelNav.goto(panelSlug);
    await panelNav.assertLoaded(panelSlug);
    expect(panelNav.page).toHaveURL(/\/app\/my-panel/);
  });

  test('displays title', async ({ panelNav }) => {
    await panelNav.goto(panelSlug);
    await panelTestPresets.assertTitle(panelNav, panelTitle);
  });

  test('content is visible', async ({ panelNav }) => {
    await panelNav.goto(panelSlug);
    const content = panelNav.getMainContent();
    await expect(content).toBeVisible();
  });
});
```

### Best Practices

1. **Use test-specific API mocks** — each test should mock its own endpoints
2. **Wait for elements explicitly** — use `waitForElement()` for async content
3. **Name data-testid attributes clearly** — `data-testid="panel-title"`, not `data-testid="title"`
4. **Test critical user flows** — navigation, forms, data display, errors
5. **Keep tests isolated** — no test should depend on another test's state
6. **Use meaningful assertions** — `expect(text).toContainText('Expected')` not `expect(true).toBe(true)`
7. **Add performance assertions** — `expect(loadTime).toBeLessThan(5000)`

### Common Test Patterns

**Test navigation:**
```typescript
test('navigates correctly', async ({ panelNav }) => {
  await panelNav.goto('dashboard');
  expect(panelNav.page).toHaveURL(/\/app\/dashboard/);
});
```

**Test data loading:**
```typescript
test('loads data', async ({ page, panelNav }) => {
  await panelNav.goto('dashboard');
  await panelNav.waitForElement('[data-testid="chart"]');
  
  const chart = page.locator('[data-testid="chart"]');
  await expect(chart).toBeVisible();
});
```

**Test form submission:**
```typescript
test('submits form', async ({ page, panelNav }) => {
  await panelNav.goto('settings');
  
  const input = page.locator('input[name="name"]');
  await input.fill('New Name');
  
  const saveBtn = page.locator('button:has-text("Save")');
  await saveBtn.click();
  
  // Wait for success feedback
  await page.waitForTimeout(500);
});
```

**Test error handling:**
```typescript
test('handles errors gracefully', async ({ page, panelNav }) => {
  // Simulate API error
  await page.route('**/v1/console/api/**', (route) => {
    route.abort('failed');
  });

  await panelNav.goto('dashboard');
  
  // Panel should show error or recover
  const error = page.locator('[data-testid="error-message"]');
  const isVisible = await error.isVisible().catch(() => false);
  expect(isVisible || await panelNav.getMainContent().isVisible()).toBe(true);
});
```

---

## Debugging Failed Tests

### Step 1: Check Error Message

```bash
# Run test with verbose output
npm run test:e2e -- --grep "Dashboard" --reporter=verbose

# Show full trace on failure
npm run test:e2e -- --grep "Dashboard" --reporter=list
```

### Step 2: Enable Debugging

```bash
# Run in debug mode (interactive)
npm run test:e2e -- --debug

# Run with --trace=on (saves trace files)
npm run test:e2e -- --trace=on
```

### Step 3: Inspect Trace

```bash
# View recorded trace
npx playwright show-trace tests/e2e/trace.zip
```

### Step 4: Take Screenshots

Add to test:
```typescript
await page.screenshot({ path: `debug-${Date.now()}.png` });
```

### Common Issues & Solutions

| Issue | Cause | Solution |
|-------|-------|----------|
| "Timeout waiting for element" | Element doesn't exist or loads slowly | Increase timeout or check selector: `await panelNav.waitForElement(selector, 15000)` |
| "Navigation timeout" | Page load slow or infinite redirect | Check mock APIs, increase timeout to 20s |
| "Session expired" | Auth token expired | Delete `auth-state.json`, run again (will re-login) |
| "Element not visible" | CSS hidden or display:none | Use `.isVisible()` before `.click()` |
| "Flaky test" | Race condition or timing issue | Use explicit waits, avoid `waitForTimeout()` |

---

## CI/CD Integration

### GitHub Actions Workflow

Tests run automatically on:
- **Push to `main`** — full suite on all browsers
- **Pull requests** — lint + unit + E2E
- **Scheduled nightly** — 02:00 UTC daily

Workflow file: `.github/workflows/e2e-playwright.yml`

### Local CI Testing

```bash
# Simulate CI environment
CI=true npm run test:e2e

# This runs with:
# - 1 worker (sequential)
# - 2 retries on failure
# - No reuseExistingServer
```

### PR Checks

The workflow comments E2E results on PRs:

```
## E2E Test Results — chromium
✅ Passed: 234
❌ Failed: 0
⏭️ Skipped: 2
```

---

## Performance Baselines

### Measured Baselines (2026-09-19)

| Panel | Load Time | Target |
|-------|-----------|--------|
| dashboard | 2.3s | <5s ✅ |
| settings | 1.8s | <3s ✅ |
| skills | 2.1s | <4s ✅ |
| *average* | 2.1s | <4.5s ✅ |

### Assertion Example

```typescript
test('performance baseline', async ({ panelNav }) => {
  const startTime = Date.now();
  await panelNav.goto('dashboard');
  const loadTime = Date.now() - startTime;

  // Assert < 5 seconds
  expect(loadTime).toBeLessThan(5000);
});
```

### Recording Metrics

```typescript
// In test
const metrics = await panelNav.recordMetrics('dashboard');
console.log(`Load time: ${metrics.largestContentfulPaint}ms`);
```

---

## Troubleshooting

### "Playwright is not installed"

```bash
npx playwright install --with-deps
```

### "Cannot find module '@playwright/test'"

```bash
npm install --save-dev @playwright/test
```

### "Tests timeout on CI but pass locally"

Usually a slow backend or rate limiting:
```bash
# Increase timeout in playwright.config.ts
timeout: 30_000,  // was 15_000
```

### "Port 8765 already in use"

```bash
# Kill existing process
lsof -i :8765 | grep -v PID | awk '{print $2}' | xargs kill -9

# Or start console on different port
CONSOLE_BASE_URL=http://127.0.0.1:9999/console npm run test:e2e
```

### "Mock API not being used"

Check:
1. Route pattern matches (use `**/api/**` not `https://localhost:8765/api/**`)
2. Setup in `beforeEach()` not `describe()`
3. Test makes actual network call, not cached response

```typescript
// ❌ Wrong
test('my test', async ({ page }) => {
  await page.route('**/api/**', route => route.fulfill(...));
  // Test code
});

// ✅ Right
test.beforeEach(async ({ page }) => {
  await page.route('**/api/**', route => route.fulfill(...));
});
test('my test', async ({ page, panelNav }) => {
  // Test code — API is already mocked
});
```

---

## Reference

### Config Files

- **playwright.config.ts** — main Playwright config (browser projects, workers, timeout)
- **playwright.live.config.ts** — against live backend (no mocks)
- **playwright.adr0124.config.ts** — ADR-0124 auth flow tests
- **playwright.isolated.config.ts** — unit test variant (no login)

### Directories

- **tests/e2e/panel-*.spec.ts** — panel test suites (33 files)
- **tests/e2e/fixtures/** — shared utilities
- **tests/e2e/snapshots/** — screenshot baselines
- **playwright-report/** — generated HTML report

### npm Scripts

```bash
npm run test:e2e              # Run all E2E tests
npm run test:e2e:ui          # Interactive UI mode
npm run test:e2e:debug       # Debug mode
npm run test:e2e:headed      # Headed mode (visible browser)
```

---

## Contributing

When adding new panels:

1. **Generate test** — run `scripts/generate-panel-tests.py`
2. **Add custom tests** — if panel is complex, add specific assertions
3. **Mock APIs** — add `beforeEach()` with panel-specific mocks
4. **Test critical flows** — nav, form, data load, errors, edge cases
5. **Measure performance** — add baseline assertion

Example PR checklist:
- [ ] Panel test file created (`tests/e2e/panel-new.spec.ts`)
- [ ] At least 5 test cases written
- [ ] All tests pass locally (`npm run test:e2e`)
- [ ] Performance baseline measured
- [ ] API mocks are comprehensive

---

## References

- **Playwright Docs:** https://playwright.dev
- **Best Practices:** https://playwright.dev/docs/best-practices
- **API Reference:** https://playwright.dev/docs/api/class-test
- **Config Guide:** https://playwright.dev/docs/test-configuration

---

**Last Updated:** 2026-09-19  
**Maintainer:** CorvinOS Console Team  
**Phase:** 2 (E2E Test Infrastructure)
