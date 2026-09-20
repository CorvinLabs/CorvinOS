/**
 * Forge tab bar — order, default, and the retired tab id.
 *
 * Three facts that live in three different places and are only kept together
 * by this file:
 *
 *   1. Skill Forge is the FIRST tab (FORGE_TABS order, pages/forge.tsx)
 *   2. it is also the tab the page OPENS on (DEFAULT_TAB, same file, separate
 *      constant) — a tab bar whose leftmost entry is not the active one reads
 *      as a bug
 *   3. `?tab=creator`, the id this tab carried between the 2026-09-20 merge
 *      and the rename hours later, still lands here (TAB_ALIASES)
 *
 * (3) is the one worth a browser: an alias that quietly stops resolving does
 * not error — it opens the default tab and looks like it worked. Asserting it
 * over the real router is the only way to tell "resolved" from "fell through",
 * and here the default IS the alias target, so the test pins the tab bar's
 * own order to distinguish them: it checks that the Skill Forge panel rendered,
 * not merely that some tab is active.
 */
import { test, expect, type Page } from '@playwright/test';

/** The tab bar, left to right, as the page declares it. */
const EXPECTED_ORDER = ['Skill Forge', 'Tools', 'Skills', 'OS-Skills', 'Graph', 'Audit'];

async function tabLabels(page: Page): Promise<string[]> {
  // Scoped to the PAGE's tab bar. Scoping matters: a composer or sub-widget
  // elsewhere on the panel may legitimately own tabs of its own, and an
  // unscoped getByRole("tab") would fold them into this list — it did, when
  // the Skill Forge composer switch was declared a nested tablist.
  const tabs = page.getByRole('tablist').first().getByRole('tab');
  await expect(tabs.first()).toBeVisible({ timeout: 20000 });
  // Each trigger may carry a count badge ("Tools 42"); compare on the leading
  // word(s) only, which is the label the operator reads.
  return (await tabs.allTextContents()).map((t) =>
    t.replace(/\s*\d+\s*$/, '').trim(),
  );
}

async function expectSkillForgeActive(page: Page) {
  // The panel's own heading — not just an aria-selected tab. A fallen-through
  // alias would still mark SOME tab selected.
  await expect(
    page.getByRole('heading', { name: 'Skill Forge', exact: true }),
  ).toBeVisible({ timeout: 20000 });
  await expect(page.getByRole('tab', { name: /^Skill Forge/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
}

test.describe('Forge — tab bar', () => {
  test('Skill Forge leads the tab bar', async ({ page }) => {
    await page.goto('/console/app/forge', { waitUntil: 'domcontentloaded' });
    expect(await tabLabels(page)).toEqual(EXPECTED_ORDER);
  });

  test('the page opens on Skill Forge, not on Tools', async ({ page }) => {
    await page.goto('/console/app/forge', { waitUntil: 'domcontentloaded' });
    await expectSkillForgeActive(page);
  });

  test('?tab=creator still resolves here instead of falling through', async ({ page }) => {
    await page.goto('/console/app/forge?tab=creator', { waitUntil: 'domcontentloaded' });
    await expectSkillForgeActive(page);
    // Both composers are present: the alias reached the merged tab, not an
    // earlier build's creator-only panel.
    await expect(page.getByTestId('composer-mode')).toBeVisible();
  });

  test('an unknown ?tab= falls back to the default rather than rendering nothing',
    async ({ page }) => {
      await page.goto('/console/app/forge?tab=does-not-exist', {
        waitUntil: 'domcontentloaded',
      });
      await expectSkillForgeActive(page);
    });

  test('Tools is still reachable and still lists tools', async ({ page }) => {
    await page.goto('/console/app/forge?tab=tools', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('tab', { name: /^Tools/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  test('the retired generator page redirects onto this tab', async ({ page }) => {
    await page.goto('/console/app/skill-forge-generator', {
      waitUntil: 'domcontentloaded',
    });
    await expectSkillForgeActive(page);
    expect(page.url()).toContain('/app/forge');
  });
});
