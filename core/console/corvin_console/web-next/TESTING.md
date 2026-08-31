# CorvinOS Web Console - E2E Testing Guide

## Overview

This document describes the comprehensive E2E test suite for the CorvinOS web console. The test suite consists of 250+ tests across 4 specialized suites, ensuring robust coverage of functionality, security, and edge cases.

## Test Suites

### 1. Comprehensive UI Integration Tests (64 tests)
**File:** `tests/e2e/comprehensive-ui-integration.spec.ts`

Tests core UI/UX functionality:
- Navigation & layout responsiveness
- Chat functionality and message handling
- Task management and interactions
- Settings form handling and persistence
- Compliance and audit features
- Error recovery and invalid routes
- Keyboard navigation and accessibility
- Page load performance (< 30s)
- State preservation across reloads

**Run:** `npm run test:e2e -- tests/e2e/comprehensive-ui-integration.spec.ts`

### 2. Complete Feature Coverage Tests (28 tests)
**File:** `tests/e2e/complete-feature-coverage.spec.ts`

Tests all website features with multi-step dependencies:
- Chat → Tasks → Settings → Compliance workflow
- API Keys → Bridges → Workflows integration
- Engines → Compute Jobs → Monitoring pipeline
- Forge → Skills → LDD configuration
- Organizations → People → Roles management
- Voice → Agent Hub → Cowork collaboration
- Personas → Connectors → Files processing
- Cross-feature integration workflows
- State persistence across page reloads
- Error handling and recovery

**Run:** `npm run test:e2e -- tests/e2e/complete-feature-coverage.spec.ts`

### 3. Edge Cases & Failure Scenarios (70 tests)
**File:** `tests/e2e/edge-cases-and-failures.spec.ts`

Tests critical edge cases and failure handling:
- API error responses (400, 401, 403, 500, 503, timeouts)
- Data validation edge cases (long input, special characters, null values)
- Session & authentication failures
- Performance stress tests (rapid requests, large datasets)
- Offline & reconnection handling
- Data consistency under concurrent access
- File operation edge cases
- Navigation edge cases (back/forward, deep linking)

**Run:** `npm run test:e2e -- tests/e2e/edge-cases-and-failures.spec.ts`

### 4. Security, Session & Authentication Tests (88 tests)
**File:** `tests/e2e/security-session-auth.spec.ts`

Tests critical security and session management:
- XSS prevention (input sanitization, script injection)
- CSRF protection (token validation, state-changing requests)
- Input sanitization (SQL injection, path traversal, null bytes)
- Data protection (console/storage, password fields)
- Session timeout and idle detection
- Token management (storage, refresh, expiry)
- Multi-device session handling
- Cookie & storage security
- Rate limiting & brute force protection
- Permission & authorization enforcement

**Run:** `npm run test:e2e -- tests/e2e/security-session-auth.spec.ts`

## Running Tests Locally

### All Tests
```bash
npm run test:e2e
```

### Specific Suite
```bash
npm run test:e2e -- tests/e2e/comprehensive-ui-integration.spec.ts
npm run test:e2e -- tests/e2e/security-session-auth.spec.ts
```

### Single Test
```bash
npm run test:e2e -- -g "should send message"
```

### With UI Mode (Interactive)
```bash
npx playwright test --ui
```

### Debug Mode (Step-through)
```bash
npx playwright test --debug
```

### Headed Mode (Watch execution)
```bash
npx playwright test --headed
```

## Viewing Reports

```bash
npx playwright show-report
```

Opens HTML report showing:
- Test results with status
- Screenshots on failure
- Network traces and timings
- Video recordings (if enabled)
- Full execution traces

## CI/CD Integration

### On Push to Main
- Runs all 4 test suites in parallel across Chromium + Firefox
- Publishes results to GitHub Actions
- Uploads HTML reports as artifacts
- Comments on PRs with test summary
- Fails build if tests fail

**Workflow:** `.github/workflows/test.yml`

### Nightly Runs
- Runs all tests with 2x retries for deeper regression detection
- Executes at 2 AM UTC daily
- Generates performance reports
- Retention: 60 days

**Workflow:** `.github/workflows/e2e-nightly.yml`

### Manual Trigger
Tests can be manually triggered via GitHub Actions UI without code changes.

## Test Configuration

**Playwright Config:** `playwright.config.ts`

Key settings:
- Base URL: `http://localhost:5173`
- Auto-starts dev server via `npm run dev`
- Projects: Chromium, Firefox, Mobile Chrome
- Retries: 0 locally, 2 on CI
- Screenshots: On failure
- Traces: On first retry
- Timeout: 30 seconds per test

### Browsers
- ✅ Chromium (Desktop + Mobile)
- ✅ Firefox (Desktop)
- ⚠️ WebKit (Disabled - requires system deps)
- ⚠️ Mobile Safari (Disabled - requires system deps)

## Test Patterns & Best Practices

### Common Selectors
```typescript
// Text-based (accessible)
page.locator('button:has-text("Send")')
page.locator('text=/send|submit/i')

// Role-based (semantic)
page.locator('[role="button"]')
page.locator('[role="list"]')

// Flexible for optional elements
const isVisible = await element.isVisible().catch(() => false);
if (isVisible) { /* handle */ }
```

### Waiting & Timeout
```typescript
// Wait for navigation
await page.waitForLoadState('networkidle');

// Wait for specific element
await expect(element).toBeVisible({ timeout: 5000 });

// Wait for condition
await page.waitForFunction(() => document.readyState === 'complete');
```

### Data Validation
```typescript
// Check for required attributes
const value = await input.inputValue();
expect(value).toBeTruthy();

// Verify content exists
const content = await page.content();
expect(content.length).toBeGreaterThan(100);
```

## Test Coverage Goals

**Current Status:** 250+ tests, 99.6% passing

**Coverage Areas:**
- ✅ Core UI/UX (64 tests)
- ✅ Feature workflows with dependencies (28 tests)
- ✅ Edge cases & error handling (70 tests)
- ✅ Security & session management (88 tests)
- 🔄 Accessibility (pending phase 2)
- 🔄 Mobile-specific (pending phase 2)
- 🔄 Pagination & search (pending phase 2)
- 🔄 Forms & data validation (pending phase 2)
- 🔄 Offline functionality (pending phase 2)

## Debugging Tips

### View Logs
```bash
# In debug mode, Playwright Inspector shows:
- Browser console logs
- Network requests
- DOM state at each step
- Selector highlighting
```

### Trace Files
```bash
# Enable traces
npx playwright test --trace on

# View trace (opens automatically in report)
npx playwright show-report
```

### Screenshots on Failure
Automatically captured for all failures. View in HTML report under each test.

### Common Issues

**"Element not found"**
- Check if UI text changed
- Verify element is visible before interaction
- Use `--debug` mode to inspect DOM

**Flaky tests**
- Increase timeout: `{ timeout: 10000 }`
- Add explicit waits: `await page.waitForLoadState('networkidle')`
- Use stricter selectors

**Tests pass locally, fail on CI**
- Check environment differences
- Verify deps are installed: `npm ci` not `npm install`
- Run full suite, not just subset

## Adding New Tests

1. **Create test file** in `tests/e2e/` matching pattern
2. **Use existing patterns** for consistency
3. **Handle optional elements** (not all UIs appear in every scenario)
4. **Add descriptive names** and messages
5. **Group related tests** with `test.describe()`
6. **Update this README** if adding new suite

### Template
```typescript
test.describe('Feature Name', () => {
  test('should do something', async ({ page }) => {
    await page.goto('/app/section');
    await page.waitForLoadState('networkidle');

    const element = page.locator('[selector]');
    await expect(element).toBeVisible();

    // Interact
    await element.click();

    // Verify
    expect(await page.content()).toContain('expected');
  });
});
```

## CI/CD Pipeline

### .github/workflows/test.yml
- Runs on: Push to main, Pull requests
- Matrix: 4 test suites × 2 browsers = 8 parallel jobs
- Artifacts: HTML reports, JSON results
- PR Comments: Test summary & stats

### .github/workflows/e2e-nightly.yml
- Runs: Daily at 2 AM UTC
- Scope: All suites with increased retries
- Reports: Performance summary
- Retention: 60 days

## Performance Benchmarks

Target page load times:
- Chat page: < 5 seconds
- Tasks page: < 5 seconds
- Settings page: < 5 seconds
- Full suite runtime: < 20 minutes (parallel)

## Troubleshooting CI Failures

**Check:**
1. Test report HTML for exact failure
2. Screenshots on failure (captured automatically)
3. CI env vs local env differences
4. Browser version compatibility

**Common CI issues:**
- Network timeout: increase timeout, or check server
- Missing env vars: check GitHub Actions secrets
- Port conflicts: CI is parallel, port might be in use

## References

- [Playwright Docs](https://playwright.dev/)
- [Best Practices](https://playwright.dev/docs/best-practices)
- [Debugging Guide](https://playwright.dev/docs/debug)
- [GitHub Actions Integration](https://playwright.dev/docs/ci)
