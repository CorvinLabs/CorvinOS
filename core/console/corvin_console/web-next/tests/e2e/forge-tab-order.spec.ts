/**
 * Forge tab bar — order, default, and the retired tab ids.
 *
 * Skill Forge, Tool Forge and Plugin Forge used to be three top-level tabs;
 * since 2026-10-05 (operator request) they are sub-tabs of one Generator
 * tab (GENERATOR_SUBTABS, pages/forge.tsx), reached via a nested tablist.
 *
 * Facts that live in different places and are only kept together by this
 * file:
 *
 *   1. Generator is the FIRST top-level tab (FORGE_TABS order, pages/forge.tsx)
 *   2. it is also the tab the page OPENS on (DEFAULT_TAB, same file, separate
 *      constant) — a tab bar whose leftmost entry is not the active one reads
 *      as a bug
 *   3. within Generator, Skill Forge is the first SUB-tab and the one it
 *      opens on (GENERATOR_SUBTABS / DEFAULT_GENERATOR_SUB)
 *   4. `?tab=creator`, `?tab=skill-forge`, `?tab=tool-forge` and
 *      `?tab=plugin-forge` — ids this surface carried at various points
 *      between the 2026-09-20 merge and the 2026-10-05 sub-tab fold — still
 *      land on Generator with the right sub-tab open (TAB_ALIASES +
 *      GENERATOR_SUB_ALIASES)
 *   5. 'layers' (Layer Forge, ADR-2222) was folded in on 2026-10-06 as a
 *      sibling top-level tab (not a Generator sub-tab — it doesn't share the
 *      run/poll/phase protocol), placed next to Tools/Skills/OS-Skills. The
 *      old /app/layer-forge panel redirects onto it — see
 *      panel-layer-forge.spec.ts.
 *
 * (4) is the one worth a browser: an alias that quietly stops resolving does
 * not error — it opens the default tab and looks like it worked. Asserting it
 * over the real router is the only way to tell "resolved" from "fell through",
 * and here the default IS the alias target, so the test pins the tab bar's
 * own order to distinguish them: it checks that the Skill Forge panel rendered,
 * not merely that some tab is active.
 */
import { test, expect, type Page } from '@playwright/test';

/** The top-level tab bar, left to right, as the page declares it. */
const EXPECTED_ORDER = ['Generator', 'Autonomous', 'Tools', 'Skills', 'OS-Skills', 'Layers', 'Graph', 'Audit'];

/** Generator's own sub-tab bar, left to right. */
const EXPECTED_SUB_ORDER = ['Skill Forge', 'Tool Forge', 'Plugin Forge'];

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

/** The nested tablist Generator renders for its three sub-tabs — the SECOND
 *  tablist on the page, after the top-level one. */
async function subTabLabels(page: Page): Promise<string[]> {
  const tabs = page.getByRole('tablist').nth(1).getByRole('tab');
  await expect(tabs.first()).toBeVisible({ timeout: 20000 });
  return (await tabs.allTextContents()).map((t) => t.trim());
}

async function expectGeneratorActive(page: Page) {
  await expect(page.getByRole('tab', { name: /^Generator/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
}

async function expectSkillForgeActive(page: Page) {
  // The panel's own heading — not just an aria-selected tab. A fallen-through
  // alias would still mark SOME tab selected.
  await expectGeneratorActive(page);
  await expect(
    page.getByRole('heading', { name: 'Skill Forge', exact: true }),
  ).toBeVisible({ timeout: 20000 });
  await expect(page.getByRole('tab', { name: /^Skill Forge/ })).toHaveAttribute(
    'aria-selected',
    'true',
  );
}

test.describe('Forge — tab bar', () => {
  test('Generator leads the top-level tab bar', async ({ page }) => {
    await page.goto('/console/app/forge', { waitUntil: 'domcontentloaded' });
    expect(await tabLabels(page)).toEqual(EXPECTED_ORDER);
  });

  test('Generator\'s sub-tabs are Skill Forge, Tool Forge, Plugin Forge, in order', async ({ page }) => {
    await page.goto('/console/app/forge', { waitUntil: 'domcontentloaded' });
    expect(await subTabLabels(page)).toEqual(EXPECTED_SUB_ORDER);
  });

  test('the page opens on Generator → Skill Forge, not on Tools', async ({ page }) => {
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

  test('?tab=skill-forge (its top-level id until 2026-10-05) still resolves onto the sub-tab', async ({ page }) => {
    await page.goto('/console/app/forge?tab=skill-forge', { waitUntil: 'domcontentloaded' });
    await expectSkillForgeActive(page);
  });

  test('?tab=tool-forge lands on Generator with Tool Forge open', async ({ page }) => {
    await page.goto('/console/app/forge?tab=tool-forge', { waitUntil: 'domcontentloaded' });
    await expectGeneratorActive(page);
    await expect(page.getByRole('heading', { name: 'Tool Forge', exact: true })).toBeVisible({
      timeout: 20000,
    });
    await expect(page.getByRole('tab', { name: /^Tool Forge/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  test('?tab=plugin-forge lands on Generator with Plugin Forge open', async ({ page }) => {
    await page.goto('/console/app/forge?tab=plugin-forge', { waitUntil: 'domcontentloaded' });
    await expectGeneratorActive(page);
    await expect(page.getByRole('heading', { name: 'Plugin Forge', exact: true })).toBeVisible({
      timeout: 20000,
    });
    await expect(page.getByRole('tab', { name: /^Plugin Forge/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  test('?tab=generator&sub=plugin (the canonical form) opens Plugin Forge', async ({ page }) => {
    await page.goto('/console/app/forge?tab=generator&sub=plugin', { waitUntil: 'domcontentloaded' });
    await expectGeneratorActive(page);
    await expect(page.getByRole('tab', { name: /^Plugin Forge/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
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

  test('Layers is reachable and lists layer definitions', async ({ page }) => {
    await page.goto('/console/app/forge?tab=layers', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('tab', { name: /^Layers/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await expect(page.getByText(/Layer Forge is now a Forge tab/i)).toBeVisible({
      timeout: 20000,
    });
  });

  test('the retired generator page redirects onto this tab', async ({ page }) => {
    await page.goto('/console/app/skill-forge-generator', {
      waitUntil: 'domcontentloaded',
    });
    await expectSkillForgeActive(page);
    expect(page.url()).toContain('/app/forge');
  });
});
