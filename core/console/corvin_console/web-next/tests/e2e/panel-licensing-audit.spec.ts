/**
 * /app/licensing-audit — consolidated into Audit & Compliance (2026-09-20).
 *
 * The panel is gone; the route stays so existing bookmarks keep working. The
 * same /v1/console/v1/licensing/audit-events store used to back BOTH this panel
 * and the Learnings panel's "Audit Events" tab, so one set of records had two
 * homes and neither was the compliance page an auditor opens. It now lives once,
 * as the "Learning events" section of /app/compliance.
 *
 * What this asserts is the redirect and the destination — not the old panel.
 */

import { test, expect } from '@playwright/test';

test.describe('Licensing Audit (consolidated)', () => {
  test('redirects to Audit & Compliance', async ({ page }) => {
    await page.goto('/console/app/licensing-audit', { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(/\/app\/compliance/);
  });

  test('the compliance panel carries the learning events section', async ({ page }) => {
    await page.goto('/console/app/compliance', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('Learning events')).toBeVisible();
    // The records are content-free by construction — the card says so, and that
    // sentence is the compliance property, not decoration.
    await expect(page.getByText(/content-free by construction/i)).toBeVisible();
  });

  test('the old panel heading is gone, not merely relabelled', async ({ page }) => {
    await page.goto('/console/app/compliance', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'Licensing Audit' })).toHaveCount(0);
  });
});
