/**
 * Marketplace Panel — render regression (2026-09-01)
 *
 * The panel shipped twice with a crash that no existing marketplace spec caught:
 *
 *   1. `selectedExtension is not defined` — a half-finished rename left the JSX
 *      referencing identifiers that no longer existed. The RouteErrorBoundary ate
 *      it, so the panel showed nothing.
 *   2. `new URL(`${BASE}/...`)` — BASE is root-relative, so URL() threw inside the
 *      fetch's try/catch and the grid stayed empty with "No extensions found".
 *
 * Both are invisible to a spec that only asserts on selectors, which is why this
 * one fails the test on ANY uncaught page error. It drives the real route
 * (`/console/app/marketplace`, not `/console/marketplace`) over real HTTP.
 */

import { test, expect } from '@playwright/test'

// Absolute path: playwright resolves a leading-slash path against the ORIGIN, so
// '/app/marketplace' would drop the '/console' segment of baseURL.
const PANEL = '/console/app/marketplace'

test.describe('Marketplace panel renders', () => {
  test('browse grid loads plugins with no uncaught page error', async ({ page }) => {
    const pageErrors: string[] = []
    page.on('pageerror', e => pageErrors.push(e.message))

    await page.goto(PANEL, { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: 'Marketplace', level: 1 })).toBeVisible()

    // The index resolves to a real plugins.json (ADR-0512 amendment); an empty
    // grid means the index was not resolved, not that there are no plugins.
    const cards = page.locator('h3')
    await expect.poll(() => cards.count(), { timeout: 15_000 }).toBeGreaterThan(0)

    expect(pageErrors, 'uncaught page errors').toEqual([])
  })

  test('detail modal opens for a plugin', async ({ page }) => {
    const pageErrors: string[] = []
    page.on('pageerror', e => pageErrors.push(e.message))

    await page.goto(PANEL, { waitUntil: 'domcontentloaded' })
    const firstCard = page.locator('h3').first()
    await expect(firstCard).toBeVisible({ timeout: 15_000 })
    const name = await firstCard.innerText()

    await firstCard.click()
    await expect(page.getByRole('heading', { level: 2, name })).toBeVisible()
    await expect(page.getByText('Author', { exact: true })).toBeVisible()

    expect(pageErrors, 'uncaught page errors').toEqual([])
  })

  test('installed and custom-repo tabs render', async ({ page }) => {
    const pageErrors: string[] = []
    page.on('pageerror', e => pageErrors.push(e.message))

    await page.goto(PANEL, { waitUntil: 'domcontentloaded' })

    // /api/v1/plugins 404s while `plugin_console_surface` is off — the panel must
    // show that as a notice, not crash and not report it as a fetch failure.
    await page.getByRole('button', { name: 'Installed' }).click()
    await expect(page.getByText(/No installed extensions yet|Plugin console surface is disabled/))
      .toBeVisible({ timeout: 10_000 })

    await page.getByRole('button', { name: 'Custom Repos' }).click()
    await page.waitForTimeout(1000)

    expect(pageErrors, 'uncaught page errors').toEqual([])
  })
})
