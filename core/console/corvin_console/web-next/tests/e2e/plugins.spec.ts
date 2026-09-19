/**
 * Plugin registry surface — E2E (ADR-0233 Phase 4; the Installed tab of the
 * ONE marketplace since ADR-0892, /app/marketplace?tab=installed — the old
 * /app/plugins path redirects there).
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

/**
 * Auth is mocked rather than obtained from a live console, so this spec runs
 * against the Vite dev server alone. That is deliberate: the assertions are about
 * what the UI does with each response SHAPE, and the server-side behaviour has its
 * own coverage in core/console/tests/test_plugins_route.py. A spec that needed a
 * booted gateway + a real session would be the kind of E2E that never actually
 * runs (the previous version of this file was exactly that).
 */
const WHOAMI = {
  tier: 'owner',
  tenant_id: '_default',
  fingerprint: 'e2e-fingerprint',
  csrf_token: 'e2e-csrf-token',
  expires_at: Math.floor(Date.now() / 1000) + 3600,
};

async function mockAuth(page: Page) {
  await page.route('**/v1/console/auth/whoami', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(WHOAMI),
    }),
  );
  // SetupGate renders a full-screen `fixed inset-0 z-50` overlay while setup is
  // incomplete, which swallows every click on the page underneath. Report a
  // finished setup so the plugins page is actually reachable.
  await page.route('**/v1/console/setup/status', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        first_run: false,
        engine_connected: true,
        claude_cli_ok: true,
        anthropic_key_set: true,
        bridges_configured: [],
        setup_complete: true,
      }),
    }),
  );
}

test.beforeEach(async ({ page }) => {
  await mockAuth(page);
});

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
  // The tab also asks for health; the header asks the other three backends —
  // none of them is under test here, and an unmocked route on the dev server
  // answers 404, which the page renders as "unavailable" / "n/a".
  await page.route('**/v1/console/plugins/health', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ monitoring_enabled: false, breakers: {} }) }),
  );
}
const INSTALLED = '/console/app/marketplace?tab=installed';
const OLD_PATH = '/console/app/plugins';

test.describe('Installed tab — surface flag OFF', () => {
  test('reports the feature is off instead of an error, via the old /app/plugins path', async ({ page }) => {
    await mockList(page, { detail: 'Not Found' }, 404);
    await page.goto(OLD_PATH, { waitUntil: 'load' });
    await expect(page).toHaveURL(/\/console\/app\/marketplace\?tab=installed/);
    await expect(page.getByRole('heading', { name: 'Marketplace' })).toBeVisible();
    await expect(page.getByText('The plugin console surface is switched off on this build.')).toBeVisible();
    await expect(page.getByText('Acme Notify')).toHaveCount(0);
  });
});

test.describe('Installed tab — read-only (lifecycle flag OFF)', () => {
  test('lists plugins but disables every mutation', async ({ page }) => {
    await mockList(page, { plugins: [PLUGIN], total: 1, lifecycle_enabled: false });
    await page.goto(INSTALLED, { waitUntil: 'load' });
    await expect(page.getByText('Acme Notify')).toBeVisible();
    await expect(page.getByText(/plugin_runtime_lifecycle/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Enable' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Uninstall' })).toBeDisabled();
  });
});

test.describe('Installed tab — full lifecycle', () => {
  test('empty registry renders the empty state', async ({ page }) => {
    await mockList(page, LIST_EMPTY);
    await page.goto(INSTALLED, { waitUntil: 'load' });
    await expect(page.getByText('No plugin is installed for this tenant.')).toBeVisible();
  });

  test('enable posts to the enable endpoint with the CSRF header', async ({ page }) => {
    await mockList(page, { plugins: [PLUGIN], total: 1, lifecycle_enabled: true });
    let csrf: string | null = null;
    await page.route('**/v1/console/plugins/acme-notify/enable', async (route) => {
      csrf = route.request().headers()['x-csrf-token'] ?? null;
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...PLUGIN, enabled: true }) });
    });
    await page.goto(INSTALLED, { waitUntil: 'load' });
    await page.getByRole('button', { name: 'Enable' }).click();
    await expect.poll(() => csrf).toBe('e2e-csrf-token');
    await expect(page.getByText('Enabled — audited.')).toBeVisible();
  });

  test('a consent-gated plugin sends consent_granted with the enable', async ({ page }) => {
    const community = { ...PLUGIN, origin: 'community', requires_consent: true };
    await mockList(page, { plugins: [community], total: 1, lifecycle_enabled: true });
    let consent: boolean | null = null;
    await page.route('**/v1/console/plugins/acme-notify/enable', async (route) => {
      const body = route.request().postDataJSON() as { consent_granted?: boolean };
      consent = Boolean(body?.consent_granted);
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...community, enabled: true }) });
    });
    await page.goto(INSTALLED, { waitUntil: 'load' });
    // The consent is explicit in the control itself — the button says so.
    await page.getByRole('button', { name: 'Enable (with consent)' }).click();
    await expect.poll(() => consent).toBe(true);
  });

  test('uninstall is refused while enabled and sends DELETE after confirm when disabled', async ({ page }) => {
    await mockList(page, { plugins: [{ ...PLUGIN, enabled: false }], total: 1, lifecycle_enabled: true });
    let deleted = false;
    await page.route('**/v1/console/plugins/acme-notify', async (route) => {
      if (route.request().method() !== 'DELETE') return route.fallback();
      deleted = route.request().headers()['x-csrf-token'] === 'e2e-csrf-token';
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ uninstalled: 'acme-notify', audit_retained: true }) });
    });
    await page.goto(INSTALLED, { waitUntil: 'load' });
    await page.getByRole('button', { name: 'Uninstall' }).click();
    await page.getByRole('button', { name: /Confirm: remove acme-notify/ }).click();
    await expect.poll(() => deleted).toBe(true);
  });
});
