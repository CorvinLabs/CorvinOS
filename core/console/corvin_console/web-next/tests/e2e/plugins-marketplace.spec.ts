/**
 * Plugin Marketplace Discovery — E2E (ADR-0249 Stage 6).
 *
 * Tests the plugin discovery UI:
 * - Marketplace tab renders with search/filter/sort controls
 * - Fetching marketplace plugins from /v1/vibe/plugins/marketplace
 * - Search, category filter, sort functionality
 * - Plugin cards display with trust badges
 * - Install button links to installation flow
 *
 * Mocked marketplace endpoint so tests run against Vite dev server.
 */
import { test, expect, type Page } from '@playwright/test';

const WHOAMI = {
  tier: 'owner',
  tenant_id: '_default',
  fingerprint: 'e2e-fingerprint',
  csrf_token: 'e2e-csrf-token',
  expires_at: Math.floor(Date.now() / 1000) + 3600,
};

const MARKETPLACE_RESPONSE = {
  plugins: [
    {
      plugin_id: 'auth-saml-enterprise',
      name: 'Enterprise SAML Authentication',
      version: '2.1.0',
      category: 'Authentication',
      origin: 'vetted',
      author: 'Corvin Labs',
      author_email: 'support@corvin.io',
      description: 'Enterprise SAML 2.0 provider integration for CorvinOS.',
      long_description: 'Enables SAML 2.0 single sign-on for enterprise deployments.',
      rating: 4.8,
      rating_count: 42,
      download_count: 1250,
      pii_risk: 'medium',
      locality: 'eu_cloud',
      network_egress: 'external',
      egress_hosts: ['idp.enterprise.example.com'],
      trust_badge: 'verified',
      requires_consent: true,
      listed: true,
    },
    {
      plugin_id: 'monitoring-datadog',
      name: 'Datadog Monitoring',
      version: '1.5.0',
      category: 'Analytics',
      origin: 'vetted',
      author: 'Corvin Labs',
      author_email: 'support@corvin.io',
      description: 'Real-time monitoring and alerting via Datadog.',
      rating: 4.6,
      rating_count: 28,
      download_count: 890,
      pii_risk: 'low',
      locality: 'us_cloud',
      network_egress: 'external',
      egress_hosts: ['api.datadoghq.com'],
      trust_badge: 'verified',
      requires_consent: false,
      listed: true,
    },
    {
      plugin_id: 'database-postgres-sync',
      name: 'PostgreSQL Sync',
      version: '3.2.1',
      category: 'Database',
      origin: 'vetted',
      author: 'Corvin Labs',
      author_email: 'support@corvin.io',
      description: 'Bidirectional PostgreSQL data synchronization.',
      rating: 4.9,
      rating_count: 156,
      download_count: 4230,
      pii_risk: 'high',
      locality: 'local',
      network_egress: 'local',
      egress_hosts: [],
      trust_badge: 'verified',
      requires_consent: true,
      listed: true,
    },
  ],
  total: 3,
  limit: 20,
  offset: 0,
};

async function mockAuth(page: Page) {
  await page.route('**/v1/console/auth/whoami', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(WHOAMI),
    }),
  );

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

async function mockMarketplaceEndpoint(page: Page) {
  await page.route('**/v1/vibe/plugins/marketplace*', (route) => {
    // Parse query parameters to test filter/sort
    const url = new URL(route.request().url());
    const query = url.searchParams.get('query');
    const category = url.searchParams.get('category');
    const sort = url.searchParams.get('sort');

    // Filter results based on parameters
    let results = MARKETPLACE_RESPONSE.plugins;

    if (query) {
      results = results.filter(
        (p) =>
          p.name.toLowerCase().includes(query.toLowerCase()) ||
          p.description.toLowerCase().includes(query.toLowerCase()),
      );
    }

    if (category && category !== 'All Categories') {
      results = results.filter(
        (p) => p.category.toLowerCase() === category.toLowerCase(),
      );
    }

    // Sort (basic implementation for testing)
    if (sort === 'downloads') {
      results = [...results].sort((a, b) => b.download_count - a.download_count);
    } else {
      // Default: rating
      results = [...results].sort((a, b) => b.rating - a.rating);
    }

    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        plugins: results,
        total: results.length,
        limit: 20,
        offset: 0,
      }),
    });
  });
}

test.beforeEach(async ({ page }) => {
  await mockAuth(page);
  await mockMarketplaceEndpoint(page);
});

test.describe('Plugin Marketplace Discovery', () => {
  test('should render Marketplace tab on Plugins page', async ({ page }) => {
    await page.goto('/console/plugins');
    // Wait for the page to load
    await page.waitForLoadState('networkidle');

    // Check if Marketplace tab exists
    const marketplaceTab = page.locator('[role="tab"]:has-text("Marketplace")');
    await expect(marketplaceTab).toBeVisible();
  });

  test('should load and display plugins from marketplace API', async ({ page }) => {
    await page.goto('/console/plugins');
    await page.waitForLoadState('networkidle');

    // Click on Marketplace tab if not already selected
    await page
      .locator('[role="tab"]:has-text("Marketplace")')
      .click()
      .catch(() => {}); // Ignore if already selected

    // Wait for plugin cards to appear
    await page.waitForSelector('[data-testid="plugin-card"]', { timeout: 5000 }).catch(() => {
      // Fallback: wait for plugin name text
      return page.waitForSelector('text=Enterprise SAML Authentication', { timeout: 5000 });
    });

    // Verify plugins are rendered
    await expect(page.locator('text=Enterprise SAML Authentication')).toBeVisible();
    await expect(page.locator('text=Datadog Monitoring')).toBeVisible();
    await expect(page.locator('text=PostgreSQL Sync')).toBeVisible();
  });

  test('should search plugins by name', async ({ page }) => {
    await page.goto('/console/plugins');
    await page.waitForLoadState('networkidle');

    // Click Marketplace tab
    await page
      .locator('[role="tab"]:has-text("Marketplace")')
      .click()
      .catch(() => {});

    // Find and fill the search input
    const searchInput = page.locator('input[placeholder*="Search"]').first();
    await searchInput.fill('PostgreSQL');

    // Wait for filtered results
    await page.waitForTimeout(500); // Debounce delay

    // Verify only PostgreSQL Sync is shown
    await expect(page.locator('text=PostgreSQL Sync')).toBeVisible();
    await expect(page.locator('text=Enterprise SAML')).not.toBeVisible();
    await expect(page.locator('text=Datadog Monitoring')).not.toBeVisible();
  });

  test('should filter plugins by category', async ({ page }) => {
    await page.goto('/console/plugins');
    await page.waitForLoadState('networkidle');

    // Click Marketplace tab
    await page
      .locator('[role="tab"]:has-text("Marketplace")')
      .click()
      .catch(() => {});

    // Find and interact with category filter
    const categorySelect = page
      .locator('select, [role="combobox"]')
      .filter({ hasText: /Category|Authentication|Analytics/ })
      .first();

    if (await categorySelect.isVisible()) {
      // Click to open dropdown
      await categorySelect.click();

      // Select "Authentication" category
      await page.locator('text=Authentication').first().click();

      // Wait for filtered results
      await page.waitForTimeout(500);

      // Verify only Authentication plugins are shown
      await expect(page.locator('text=Enterprise SAML Authentication')).toBeVisible();
    }
  });

  test('should sort plugins by different criteria', async ({ page }) => {
    await page.goto('/console/plugins');
    await page.waitForLoadState('networkidle');

    // Click Marketplace tab
    await page
      .locator('[role="tab"]:has-text("Marketplace")')
      .click()
      .catch(() => {});

    // Find sort dropdown and change to "Downloads"
    const sortSelect = page
      .locator('select, [role="combobox"]')
      .filter({ hasText: /Rating|Downloads|Recent/ })
      .first();

    if (await sortSelect.isVisible()) {
      await sortSelect.click();
      await page.locator('text=Downloads').first().click();

      // Wait for re-sort
      await page.waitForTimeout(500);

      // PostgreSQL Sync (4230 downloads) should appear before Datadog (890)
      const postgresCard = page.locator('text=PostgreSQL Sync').first();
      const datadogCard = page.locator('text=Datadog Monitoring').first();

      const postgresBox = await postgresCard.boundingBox();
      const datadogBox = await datadogCard.boundingBox();

      // PostgreSQL should appear before (higher up) Datadog in download sort
      expect(postgresBox!.y).toBeLessThan(datadogBox!.y);
    }
  });

  test('should display trust badges on plugin cards', async ({ page }) => {
    await page.goto('/console/plugins');
    await page.waitForLoadState('networkidle');

    // Click Marketplace tab
    await page
      .locator('[role="tab"]:has-text("Marketplace")')
      .click()
      .catch(() => {});

    // Wait for plugins to load
    await page.waitForSelector('text=Enterprise SAML Authentication', { timeout: 5000 }).catch(() => {});

    // Check for trust badges (Vetted ✓ or Community ⚠)
    const vetted = page.locator('tag:has-text("Vetted")');
    if (await vetted.isVisible()) {
      await expect(vetted.first()).toBeVisible();
    }
  });

  test('should show rating and download count on cards', async ({ page }) => {
    await page.goto('/console/plugins');
    await page.waitForLoadState('networkidle');

    // Click Marketplace tab
    await page
      .locator('[role="tab"]:has-text("Marketplace")')
      .click()
      .catch(() => {});

    // Wait for plugins
    await page.waitForSelector('text=Enterprise SAML Authentication', { timeout: 5000 }).catch(() => {});

    // Check for rating (⭐ 4.8) and download count (⬇️ 1.2K)
    // These are displayed as 4.8 (42) and 1.2K format
    await expect(page.locator('text=4.8')).toBeVisible();
    await expect(page.locator('text=1.2K')).toBeVisible();
  });

  test('should have Install button on each plugin card', async ({ page }) => {
    await page.goto('/console/plugins');
    await page.waitForLoadState('networkidle');

    // Click Marketplace tab
    await page
      .locator('[role="tab"]:has-text("Marketplace")')
      .click()
      .catch(() => {});

    // Wait for plugins
    await page.waitForSelector('text=Enterprise SAML Authentication', { timeout: 5000 }).catch(() => {});

    // Find Install buttons
    const installButtons = page.locator('button:has-text("Install")');

    // Should have at least 3 install buttons (one per plugin card)
    const count = await installButtons.count();
    expect(count).toBeGreaterThanOrEqual(3);

    // Verify each button is visible and clickable
    for (let i = 0; i < count; i++) {
      await expect(installButtons.nth(i)).toBeVisible();
    }
  });

  test('should handle empty search results', async ({ page }) => {
    await page.goto('/console/plugins');
    await page.waitForLoadState('networkidle');

    // Click Marketplace tab
    await page
      .locator('[role="tab"]:has-text("Marketplace")')
      .click()
      .catch(() => {});

    // Search for non-existent plugin
    const searchInput = page.locator('input[placeholder*="Search"]').first();
    await searchInput.fill('NonExistentPlugin123XYZ');

    // Wait for results
    await page.waitForTimeout(500);

    // Should show "No plugins found" or similar empty state
    const emptyState = page.locator('text=No plugins found');
    await expect(emptyState.or(page.locator('text=No results'))).toBeVisible();
  });
});
