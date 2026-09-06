/**
 * PLAYWRIGHT E2E: Learning Dashboard (ADR-0613 loop closure) — REAL browser,
 * REAL console (the running corvin-webui.service on :8765), REAL backend.
 *
 * Run: BASE_URL=http://127.0.0.1:8765 npx playwright test -c playwright.e2e.config.ts tests/e2e/learning_dashboard.spec.ts
 *
 * What is proven (each is an assertion, not a console.log):
 *  1. the sidebar carries the Learning Dashboard entry (requiredFlag
 *     `learning_enabled` is registered + gated + resolves true — F9);
 *  2. the panel renders its three tabs from REAL data (empty is empty);
 *  3. submitting feedback performs the CSRF-protected POST and the backend
 *     answers 200 with the interpreted hypotheses (the former endpoint
 *     discarded the feedback and said "received" — F3);
 *  4. the Config History tab reflects the real version list;
 *  5. /learning/health reports the loop as operational (emitter booted).
 */
import { test, expect, Page } from '@playwright/test';

const DASHBOARD = '/console/app/learning-dashboard';

async function login(page: Page): Promise<void> {
  // localhost-only credential-less login: the TCP peer IS the authorisation
  await page.goto('/v1/console/auth/local-login');
  await page.goto(DASHBOARD);
  await expect(page.locator('text=🧠 Learning Dashboard')).toBeVisible({ timeout: 20_000 });
}

test.describe('Learning Dashboard E2E (real backend)', () => {
  test('sidebar shows the Learning Dashboard entry (learning_enabled resolves true)', async ({ page }) => {
    await login(page);
    const manifest = await page.request.get('/v1/console/capabilities/manifest');
    expect(manifest.ok()).toBeTruthy();
    const flags = (await manifest.json()).flags as Record<string, boolean>;
    expect(flags.learning_enabled).toBe(true);
    await expect(page.getByRole('link', { name: /Learning Dashboard/ }).first()).toBeVisible();
  });

  test('panel renders three tabs from real data', async ({ page }) => {
    await login(page);
    await expect(page.locator('button:has-text("📊 Patterns")')).toBeVisible();
    await expect(page.locator('button:has-text("⚙️ Config History")')).toBeVisible();
    await expect(page.locator('button:has-text("👤 Preferences")')).toBeVisible();
    // the counts come from the API, not from mock constants
    const versions = await page.request.get('/v1/console/learning/config-versions');
    expect(versions.ok()).toBeTruthy();
    const list = (await versions.json()) as unknown[];
    await expect(page.locator(`button:has-text("⚙️ Config History (${list.length})")`)).toBeVisible();
  });

  test('feedback submission is a real, CSRF-protected POST that the backend records', async ({ page }) => {
    await login(page);
    await page.locator('button:has-text("📊 Patterns")').click();
    await page.locator('text=💬 Rate Your Tasks').scrollIntoViewIfNeeded();
    const taskId = `pw-task-${Date.now()}`;
    await page.locator('input[placeholder*="task ID"]').fill(taskId);
    await page.locator('select').first().selectOption('excellent');

    const [response] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/v1/console/learning/feedback') && r.request().method() === 'POST'),
      page.locator('button:has-text("Submit")').click(),
    ]);
    expect(response.status(), await response.text()).toBe(200);
    const body = await response.json();
    expect(body.status).toBe('recorded');
    expect(body.task_id).toBe(taskId);
    expect(Array.isArray(body.hypotheses)).toBeTruthy();
    expect(body.hypotheses.some((h: { param: string }) => h.param === 'confidence_threshold')).toBeTruthy();
    // the form clears only on success
    await expect(page.locator('input[placeholder*="task ID"]')).toHaveValue('');
  });

  test('config history tab reflects the real version list', async ({ page }) => {
    await login(page);
    await page.locator('button:has-text("⚙️ Config History")').click();
    const versions = (await (await page.request.get('/v1/console/learning/config-versions')).json()) as Array<{ version_id: string }>;
    if (versions.length === 0) {
      await expect(page.locator('text=/No config changes yet/').first()).toBeVisible();
    } else {
      await expect(page.locator(`text=${versions[0].version_id}`).first()).toBeVisible();
    }
  });

  test('learning loop health is operational (emitter booted)', async ({ page }) => {
    await login(page);
    const health = await page.request.get('/v1/console/learning/health');
    expect(health.ok()).toBeTruthy();
    const body = await health.json();
    expect(body.status).toBe('operational');
    expect(body.emitter_booted).toBe(true);
    expect(body.tunable_skills).toContain('os.delegation_router');
  });
});
