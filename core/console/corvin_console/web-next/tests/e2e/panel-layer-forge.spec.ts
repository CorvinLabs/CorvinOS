/**
 * /app/layer-forge — consolidated into Forge as the "Layers" tab (2026-10-06).
 *
 * Layer Forge (ADR-2222) used to be its own top-level panel. It is now
 * components/forge/LayersTab.tsx, reached at /app/forge?tab=layers — the
 * same fold Skill/Tool/Plugin Forge went through on 2026-10-05, but as a
 * sibling top-level tab rather than a Generator sub-tab (it doesn't share
 * the run/poll/phase protocol those three do). The route stays mounted as a
 * redirect so existing bookmarks keep working; the backend API
 * (/v1/console/layer-forge/*) is unchanged by this move.
 *
 * What this asserts is the redirect and the destination — not the old panel.
 */

import { test, expect } from '@playwright/test';

test.describe('Layer Forge (consolidated into Forge)', () => {
  test('redirects to the Forge Layers tab', async ({ page }) => {
    await page.goto('/console/app/layer-forge', { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(/\/app\/forge\?tab=layers/);
  });

  test('the Layers tab renders the layer-definitions list, not a blank/old page', async ({ page }) => {
    await page.goto('/console/app/layer-forge', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('tab', { name: /^Layers/ })).toHaveAttribute(
      'aria-selected',
      'true',
      { timeout: 20000 },
    );
    await expect(page.getByRole('heading', { name: 'Definitions', exact: true })).toBeVisible({
      timeout: 20000,
    });
  });

  test('the old standalone page heading is gone, not merely relabelled', async ({ page }) => {
    await page.goto('/console/app/forge?tab=layers', { waitUntil: 'domcontentloaded' });
    // The retired page rendered an <h1>"Layer Forge" page title; the tab only
    // carries the marker caption + the "Definitions" card now.
    await expect(page.getByRole('heading', { name: 'Layer Forge', exact: true })).toHaveCount(0);
  });
});
