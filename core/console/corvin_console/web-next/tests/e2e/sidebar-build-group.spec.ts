/**
 * The Build group's sidebar entries actually go somewhere.
 *
 * Written when /app/workflows turned out to have no sidebar entry at all:
 * d6c3f3c3 (2026-09-19, "remove dead-code pages that have zero production
 * callers") dropped the line as collateral — its own message listed ten other
 * pages, workflows was not among them, and the `Workflow, // workflows` entry
 * it left behind in ICON_MAP is what gave it away. The route, the page and the
 * backend all kept working; the page was simply unreachable by navigation for
 * a day.
 *
 * tests/unit/panel-nav-wiring.test.ts now fails when a page mounted in App.tsx
 * has no NAV_GROUPS link. That is the cheap check. This one is the expensive
 * half it cannot do: that clicking the entry in a real browser lands on a page
 * that RENDERS — a link to a panel that only shows its error state is not a
 * restored capability.
 */
import { test, expect } from '@playwright/test';

test.describe('Sidebar — Build group', () => {
  test('Workflows is listed and opens the workflows page', async ({ page }) => {
    await page.goto('/console/app/dashboard', { waitUntil: 'domcontentloaded' });

    const entry = page.getByRole('link', { name: 'Workflows', exact: true });
    await expect(entry).toBeVisible({ timeout: 20000 });

    await entry.click();
    await expect(page).toHaveURL(/\/app\/workflows$/, { timeout: 20000 });

    // Rendered, not just routed: the list page's own heading, and no crash
    // boundary in its place.
    await expect(
      page.getByRole('heading', { name: /workflow/i }).first(),
    ).toBeVisible({ timeout: 20000 });
    await expect(page.getByText(/something went wrong/i)).toHaveCount(0);
  });

  test('Forge sits beside it and still opens on Skill Forge', async ({ page }) => {
    await page.goto('/console/app/dashboard', { waitUntil: 'domcontentloaded' });

    await page.getByRole('link', { name: 'Forge', exact: true }).click();
    await expect(page).toHaveURL(/\/app\/forge$/, { timeout: 20000 });
    await expect(
      page.getByRole('heading', { name: 'Skill Forge', exact: true }),
    ).toBeVisible({ timeout: 20000 });
  });
});
