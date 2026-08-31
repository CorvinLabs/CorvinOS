# Testing Guide — CorvinOS Console (web-next)

**Last Updated:** 2026-06-02  
**Test Suite:** Vitest (unit/integration) + Playwright (E2E) + GitHub Actions (CI/CD)

---

## Quick Start

### Run All Tests Locally

```bash
cd core/console/corvin_console/web-next

# Unit & Integration Tests
npm run test

# E2E Tests (Playwright)
npm run test:e2e

# Type Check + Lint
npm run type-check && npm run lint

# Build
npm run build
```

### Run Tests in CI

```bash
git push origin <branch>
# GitHub Actions automatically triggers:
# 1. test job (unit/integration, Node 18.x + 20.x)
# 2. e2e job (Playwright on Ubuntu)
# 3. coverage-check job (codecov)
# 4. build job (production build)
```

---

## Test Architecture

### 1. Unit & Integration (Vitest)

**Location:** `tests/unit/` and `tests/integration/`

**Command:** `npm run test`

**Coverage:** Component logic, hooks, utilities

**Reports:**
- Console output + exit code
- Coverage summary written to `coverage-report.txt`
- Full coverage in `coverage/`

### 2. E2E (Playwright)

**Location:** `tests/e2e/`

**Command:** `npm run test:e2e`

**Coverage:** Full user flows, navigation, state persistence, compliance

**Reports:**
- Playwright HTML report in `playwright-report/`
- Screenshots + trace files for failed tests
- `test-results/` directory with detailed error context

### 3. CI/CD Pipeline (GitHub Actions)

**Workflow:** `.github/workflows/test.yml`

**Jobs:**
1. **test** — Unit/integration, Node 18.x + 20.x
2. **e2e** — Playwright on Ubuntu (depends on test job)
3. **coverage-check** — Codecov upload, PR comment
4. **build** — Production build (depends on test + e2e)

**Triggers:**
- Push to `main` or `develop`
- Pull requests targeting `main` or `develop`

---

## Running Tests Locally

### Unit & Integration Tests

```bash
# Run all tests
npm run test

# Run specific test file
npm run test -- src/components/chat.test.tsx

# Watch mode (re-run on file change)
npm run test -- --watch

# Coverage report
npm run test -- --coverage
```

### E2E Tests

```bash
# Run all E2E tests
npm run test:e2e

# Run specific spec file
npm run test:e2e -- tests/e2e/critical-flows/chat.spec.ts

# Run specific test (filter by name)
npm run test:e2e -- --grep "Chat Flow"

# Run in headed mode (see browser)
npm run test:e2e -- --headed

# Run single browser (chromium, firefox, webkit)
npm run test:e2e -- --project chromium

# Debug mode (opens Inspector)
npm run test:e2e -- --debug
```

### View Playwright Reports

```bash
# After running E2E tests:
npm run test:e2e

# Open HTML report in browser
npx playwright show-report
```

---

## Test File Structure

### Unit Test Example

```typescript
// src/lib/task-db.test.ts
import { describe, it, expect } from 'vitest';
import { saveTask, getTask } from './task-db';

describe('TaskDB', () => {
  it('should save and retrieve a task', async () => {
    const task = { task_id: 'task-1', status: 'pending' };
    await saveTask(task);
    const retrieved = await getTask('task-1');
    expect(retrieved).toEqual(task);
  });
});
```

### E2E Test Example

```typescript
// tests/e2e/chat-flow.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Chat Flow', () => {
  test('should send message and display response', async ({ page }) => {
    await page.goto('/app/chat');
    const textarea = page.locator('textarea').first();
    await textarea.fill('Hello');
    await page.locator('button:has-text("Send")').click();
    await expect(page.locator('text=Hello')).toBeVisible();
  });
});
```

---

## CI/CD Workflow Details

### Trigger: `git push origin <branch>`

1. **Checkout** — Get latest code
2. **Setup Node** — Install Node.js (18.x + 20.x for unit/int, 18.x for E2E)
3. **npm ci** — Install dependencies (clean install)
4. **Type Check** — `npm run type-check`
5. **Lint** — `npm run lint` (non-blocking)
6. **Unit Tests** — `npm run test -- --coverage`
7. **E2E Tests** — (depends on test passing)
   - Install Playwright browsers
   - Start dev server (`npm run dev &`)
   - Wait for localhost:5173
   - Run Playwright (`npm run test:e2e`)
8. **Coverage Check** — Upload to codecov, post PR comment
9. **Build** — `npm run build` (depends on test + e2e passing)

### Artifacts

- `test-results-<node-version>/` — Unit/int test output
- `playwright-report/` — Playwright HTML report
- `build-output/dist/` — Production build

---

## Handling Test Failures

### Flaky E2E Tests

**Common Issues:**
1. **Element not found** — Page not fully loaded
2. **Timeout** — Network slow or dev server not ready
3. **Assertion failed** — Feature not yet implemented

**Solutions:**
- Increase timeout: `{ timeout: 10000 }`
- Use `page.waitForLoadState('networkidle')`
- Check screenshot in `test-results/` for UI state

### Local vs CI Mismatch

If a test passes locally but fails in CI:
1. Check `playwright-report/` artifact in GitHub Actions
2. Compare dev server behavior
3. Run E2E tests locally with `npm run test:e2e`
4. If still passing, might be timing issue — add waits

### Debugging Failed E2E Tests

```bash
# Run with browser visible
npm run test:e2e -- --headed

# Run with inspector
npm run test:e2e -- --debug

# Re-run failed tests only
npm run test:e2e -- --last-failed
```

---

## Best Practices

### Writing Tests

1. **Use data-testid** for element targeting (avoid brittle selectors)
   ```typescript
   // Good
   page.locator('[data-testid="chat-input"]')
   
   // Avoid
   page.locator('div > div > textarea')
   ```

2. **Wait for readiness, not time**
   ```typescript
   // Good
   await page.waitForLoadState('networkidle');
   
   // Avoid
   await page.waitForTimeout(5000);
   ```

3. **Test behavior, not implementation**
   ```typescript
   // Good
   await expect(page.locator('text=Task saved')).toBeVisible();
   
   // Avoid
   expect(component.state.saved).toBe(true);
   ```

### Maintenance

1. **Keep specs focused** — One flow per test file
2. **Reuse fixtures** — Use `test.beforeEach()` for common setup
3. **Update snapshots carefully** — Review all changes before committing
4. **Remove xfail/skip** — Document why if kept long-term

---

## Coverage Goals

| Metric | Target | Current |
|--------|--------|---------|
| Statements | 80% | TBD |
| Branches | 75% | TBD |
| Functions | 80% | TBD |
| Lines | 80% | TBD |

View coverage report:
```bash
npm run test -- --coverage
open coverage/index.html
```

---

## Troubleshooting

### "Playwright browsers not installed"
```bash
npx playwright install --with-deps
```

### "Port 5173 already in use"
```bash
# Kill process using port
lsof -i :5173 | grep -v PID | awk '{print $2}' | xargs kill -9
```

### "Test timeout in CI but passes locally"
- CI uses `workers: 1` (sequential), local uses multiple workers (parallel)
- Increase timeout for CI-sensitive tests
- Check dev server startup time in GitHub Actions log

### "Coverage report not uploaded"
- Ensure `CODECOV_TOKEN` is set in GitHub repo settings
- Check codecov.yml configuration
- Verify coverage files exist before upload step

---

## CI/CD Status Badge

Add to README.md:
```markdown
![Tests](https://github.com/<org>/<repo>/actions/workflows/test.yml/badge.svg)
```

---

## Related Documentation

- **Playwright Docs:** https://playwright.dev/docs/intro
- **Vitest Docs:** https://vitest.dev/
- **GitHub Actions:** https://docs.github.com/en/actions
- **ADR-0082:** Frontend Persistence Layer (Task persistence testing)

---

## Questions?

Refer to the team's wiki or reach out to @claude-code.
