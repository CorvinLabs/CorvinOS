/**
 * Plugin registry surface — E2E (ADR-0233 Phase 4).
 *
 * Both flag states are covered, because a flag tested in one state rots:
 *   • plugin_console_surface OFF → the page reports the feature is off (the REST
 *     route 404s), and no plugin list is rendered.
 *   • surface ON, plugin_runtime_lifecycle OFF → the list renders read-only and
 *     the toggles are disabled.
 *   • both ON → install → enable → change a setting → disable → uninstall.
 *
 * The REST layer is mocked at the route boundary so the spec exercises the UI
 * contract (what the page does with each response shape) without needing a live
 * registry; the server-side behaviour has its own tests in
 * core/console/tests/test_plugins_route.py.
 */
import { test, expect, type Page } from '@playwright/test';

const LIST_EMPTY = { plugins: [], total: 0, lifecycle_enabled: true };

const PLUGIN = {
  plugin_id: 'acme-notify',
  version: '1.0.0',
  display_name: 'Acme Notify',
  plugin_type: 'notification_backend',
  origin: 'vetted',
  pii_risk: 'low',
  enabled: false,
  requires_consent: false,
  settings: { channel: 'ops' },
  settings_schema: {
    type: 'object',
    properties: {
      channel: { type: 'string', title: 'Channel', default: 'ops' },
      depth: { type: 'integer', minimum: 1, maximum: 5, default: 3, title: 'Depth' },
      verbose: { type: 'boolean', default: false, title: 'Verbose' },
    },
    required: ['channel'],
  },
  dependencies: [],
  installed_at: '2026-07-26T10:00:00+00:00',
  last_error_type: null,
};

async function mockList(page: Page, body: unknown, status = 200) {
  await page.route('**/v1/console/plugins', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

test.describe('Plugins page — surface flag OFF', () => {
  test('reports the feature is off instead of an error', async ({ page }) => {
    await mockList(page, { detail: 'Not Found' }, 404);
    await page.goto('/app/plugins');

    await expect(page.getByRole('heading', { name: 'Plugins' })).toBeVisible();
    await expect(page.getByText('plugin_console_surface')).toBeVisible();
    await expect(page.getByText('Acme Notify')).toHaveCount(0);
  });
});

test.describe('Plugins page — read-only (lifecycle flag OFF)', () => {
  test('lists plugins but disables every mutation', async ({ page }) => {
    await mockList(page, {
      plugins: [PLUGIN],
      total: 1,
      lifecycle_enabled: false,
    });
    await page.goto('/app/plugins');

    await expect(page.getByText('Acme Notify')).toBeVisible();
    await expect(page.getByText('plugin_runtime_lifecycle')).toBeVisible();
    await expect(page.getByRole('button', { name: /Disabled/ })).toBeDisabled();
  });
});

test.describe('Plugins page — full lifecycle', () => {
  test('empty registry renders the empty state', async ({ page }) => {
    await mockList(page, LIST_EMPTY);
    await page.goto('/app/plugins');
    await expect(page.getByText('No plugins installed for this tenant.')).toBeVisible();
  });

  test('enable posts to the enable endpoint', async ({ page }) => {
    await mockList(page, { plugins: [PLUGIN], total: 1, lifecycle_enabled: true });
    let enableCalled = false;
    await page.route('**/v1/console/plugins/acme-notify/enable', async (route) => {
      enableCalled = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...PLUGIN, enabled: true }),
      });
    });

    await page.goto('/app/plugins');
    await page.getByRole('button', { name: /Disabled/ }).click();
    await expect.poll(() => enableCalled).toBe(true);
  });

  test('a consent-gated plugin asks before enabling', async ({ page }) => {
    const community = { ...PLUGIN, origin: 'community', requires_consent: true };
    await mockList(page, { plugins: [community], total: 1, lifecycle_enabled: true });

    const consentFlags: boolean[] = [];
    await page.route('**/v1/console/plugins/acme-notify/enable', async (route) => {
      const body = route.request().postDataJSON() as { consent_granted?: boolean };
      consentFlags.push(Boolean(body?.consent_granted));
      if (!body?.consent_granted) {
        return route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'acme-notify needs explicit consent' }),
        });
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...community, enabled: true }),
      });
    });

    await page.goto('/app/plugins');
    await page.getByRole('button', { name: /Disabled/ }).click();
    await expect(page.getByText(/needs explicit consent/)).toBeVisible();

    await page.getByRole('button', { name: 'I understand — enable anyway' }).click();
    await expect.poll(() => consentFlags).toEqual([false, true]);
  });

  test('settings form renders each schema type and saves the draft', async ({ page }) => {
    await mockList(page, { plugins: [PLUGIN], total: 1, lifecycle_enabled: true });
    let savedSettings: Record<string, unknown> | null = null;
    await page.route('**/v1/console/plugins/acme-notify/settings', async (route) => {
      const body = route.request().postDataJSON() as { settings: Record<string, unknown> };
      savedSettings = body.settings;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...PLUGIN, settings: body.settings }),
      });
    });

    await page.goto('/app/plugins');

    // string → text input, integer with both bounds → range, boolean → checkbox
    const channel = page.locator('#plugin-setting-channel');
    await expect(channel).toBeVisible();
    await expect(page.locator('#plugin-setting-depth')).toHaveAttribute('type', 'range');
    await expect(page.locator('#plugin-setting-verbose')).toHaveAttribute('type', 'checkbox');

    // Save is disabled until the draft actually differs.
    await expect(page.getByRole('button', { name: 'Save' })).toBeDisabled();
    await channel.fill('alerts');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect.poll(() => savedSettings).toEqual({ channel: 'alerts' });
  });

  test('a rejected save surfaces the reason and keeps the draft', async ({ page }) => {
    await mockList(page, { plugins: [PLUGIN], total: 1, lifecycle_enabled: true });
    await page.route('**/v1/console/plugins/acme-notify/settings', async (route) => {
      await route.fulfill({
        status: 422,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'settings rejected: 42 is not of type string' }),
      });
    });

    await page.goto('/app/plugins');
    await page.locator('#plugin-setting-channel').fill('nope');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByText(/Rejected/)).toBeVisible();
    await expect(page.locator('#plugin-setting-channel')).toHaveValue('nope');
  });

  test('uninstall is blocked while the plugin is enabled', async ({ page }) => {
    await mockList(page, {
      plugins: [{ ...PLUGIN, enabled: true }],
      total: 1,
      lifecycle_enabled: true,
    });
    await page.goto('/app/plugins');
    await expect(page.getByRole('button', { name: /Enabled/ })).toBeVisible();
    // The trash button is the only ghost icon button on the card.
    const trash = page.locator('button:has(svg.lucide-trash-2)');
    await expect(trash).toBeDisabled();
  });

  test('disable posts to the disable endpoint', async ({ page }) => {
    await mockList(page, {
      plugins: [{ ...PLUGIN, enabled: true }],
      total: 1,
      lifecycle_enabled: true,
    });
    let disableCalled = false;
    await page.route('**/v1/console/plugins/acme-notify/disable', async (route) => {
      disableCalled = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...PLUGIN, enabled: false }),
      });
    });

    await page.goto('/app/plugins');
    await page.getByRole('button', { name: /Enabled/ }).click();
    await expect.poll(() => disableCalled).toBe(true);
  });
});
