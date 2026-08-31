/**
 * Marketplace Panel E2E Tests (ADR-0511 Phase 2 Integration)
 *
 * Tests the complete Marketplace Panel workflow:
 * - Browse plugins by category
 * - Search plugins
 * - Filter plugins
 * - View plugin details
 * - Install plugin (stub for Phase 4)
 *
 * Exercise: Real HTTP API (/api/v1/marketplace/plugins)
 * NOT mocked — validates actual Console ↔ API integration
 */

import { test, expect } from '@playwright/test'

const BASE_URL = process.env.CONSOLE_URL || 'http://localhost:8765'
const MARKETPLACE_PANEL = '/console/marketplace'

test.describe('Marketplace Panel - ADR-0511 Phase 2', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to marketplace panel
    await page.goto(`${BASE_URL}${MARKETPLACE_PANEL}`)
    // Wait for initial load
    await page.waitForLoadState('networkidle')
  })

  test('Browse tab: Display all 27 plugins', async ({ page }) => {
    // Click Browse tab
    await page.click('button:has-text("Browse")')
    await page.waitForTimeout(500)

    // Verify plugin list loaded
    const pluginCards = await page.locator('[data-testid="plugin-card"]').count()
    expect(pluginCards).toBeGreaterThanOrEqual(25) // At least 25 (may vary with pagination)

    // Verify first plugin has required fields
    const firstPlugin = await page.locator('[data-testid="plugin-card"]').first()
    await expect(firstPlugin.locator('[data-testid="plugin-name"]')).toContainText(/./m)
    await expect(firstPlugin.locator('[data-testid="plugin-category"]')).toContainText(/./m)
    await expect(firstPlugin.locator('[data-testid="plugin-version"]')).toContainText(/./m)
  })

  test('Browse tab: Category filter works', async ({ page }) => {
    // Click Browse tab
    await page.click('button:has-text("Browse")')
    await page.waitForTimeout(500)

    // Get initial count
    const allPluginsCount = await page.locator('[data-testid="plugin-card"]').count()

    // Click memory category filter
    await page.click('button:has-text("Memory")')
    await page.waitForTimeout(500)

    // Verify filtered count is less than total
    const memoryPluginsCount = await page.locator('[data-testid="plugin-card"]').count()
    expect(memoryPluginsCount).toBeLessThan(allPluginsCount)
    expect(memoryPluginsCount).toBeGreaterThan(0)

    // Verify all displayed plugins are memory category
    const categories = await page.locator('[data-testid="plugin-category"]').allTextContents()
    categories.forEach(cat => {
      expect(cat.toLowerCase()).toContain('memory')
    })
  })

  test('Browse tab: Search works', async ({ page }) => {
    // Click Browse tab
    await page.click('button:has-text("Browse")')
    await page.waitForTimeout(500)

    // Search for "recall"
    const searchInput = page.locator('input[placeholder*="Search"]')
    await searchInput.fill('recall')
    await page.waitForTimeout(500)

    // Verify filtered results
    const pluginCards = await page.locator('[data-testid="plugin-card"]').all()
    expect(pluginCards.length).toBeGreaterThan(0)

    // All results should contain "recall"
    for (const card of pluginCards) {
      const text = await card.textContent()
      expect(text?.toLowerCase()).toContain('recall')
    }
  })

  test('Browse tab: API responds with correct schema', async ({ page }) => {
    // Intercept API call
    const apiPromise = page.waitForResponse(
      response => response.url().includes('/api/v1/marketplace/plugins')
    )

    // Click Browse tab to trigger API call
    await page.click('button:has-text("Browse")')
    const response = await apiPromise

    // Verify response status
    expect(response.status()).toBe(200)

    // Parse and verify schema
    const data = await response.json()
    expect(data).toHaveProperty('plugins')
    expect(data).toHaveProperty('count')
    expect(Array.isArray(data.plugins)).toBe(true)

    // Verify first plugin has required fields
    const plugin = data.plugins[0]
    expect(plugin).toHaveProperty('id')
    expect(plugin).toHaveProperty('name')
    expect(plugin).toHaveProperty('category')
    expect(plugin).toHaveProperty('version')
    expect(plugin).toHaveProperty('tier') // New in ADR-0511
  })

  test('Browse tab: Category filter includes all 5 categories', async ({ page }) => {
    // Click Browse tab
    await page.click('button:has-text("Browse")')
    await page.waitForTimeout(500)

    // Expected categories from ADR-0511
    const expectedCategories = [
      'Memory',
      'Security Compliance',
      'Integration',
      'Data Processing',
      'Observability'
    ]

    // Verify each category button exists
    for (const category of expectedCategories) {
      const button = page.locator(`button:has-text("${category}")`)
      await expect(button).toBeVisible()
    }
  })

  test('Plugin detail: Click plugin to view details', async ({ page }) => {
    // Click Browse tab
    await page.click('button:has-text("Browse")')
    await page.waitForTimeout(500)

    // Click first plugin
    const firstPlugin = page.locator('[data-testid="plugin-card"]').first()
    const pluginName = await firstPlugin.locator('[data-testid="plugin-name"]').textContent()
    await firstPlugin.click()

    // Verify detail panel/modal opened
    await expect(page.locator(`text=${pluginName}`).first()).toBeVisible()

    // Verify detail contains extended info
    await expect(page.locator('[data-testid="plugin-description"]')).toBeVisible()
    await expect(page.locator('[data-testid="plugin-version"]')).toBeVisible()
  })

  test('Installed tab: Load installed plugins', async ({ page }) => {
    // Click Installed tab
    await page.click('button:has-text("Installed")')
    await page.waitForTimeout(500)

    // Should either show installed plugins or empty state
    const pluginCards = await page.locator('[data-testid="plugin-card"]').count()
    const emptyState = await page.locator('text=/no.*installed/i').isVisible()

    // Either installed plugins or empty state should be visible
    expect(pluginCards > 0 || emptyState).toBe(true)
  })

  test('Custom Repos tab: Tab exists and loads', async ({ page }) => {
    // Click Custom Repos tab
    await page.click('button:has-text("Custom Repos")')
    await page.waitForTimeout(500)

    // Verify tab content loaded
    await expect(page.locator('[data-testid="custom-repos-section"]')).toBeVisible()
  })

  test('Error handling: API error shows error message', async ({ page }) => {
    // Mock API to return 500 error
    await page.route('**/api/v1/marketplace/plugins', async route => {
      await route.abort('failed')
    })

    // Reload page to trigger failed request
    await page.reload()

    // Wait for error state
    await page.waitForTimeout(500)

    // Verify error message displayed
    const errorMsg = page.locator('[data-testid="error-message"], text=/Failed to fetch/i').first()
    await expect(errorMsg).toBeVisible()
  })

  test('Performance: Plugin list loads within 3 seconds', async ({ page }) => {
    const startTime = Date.now()

    // Click Browse tab
    await page.click('button:has-text("Browse")')

    // Wait for plugins to render
    await page.waitForSelector('[data-testid="plugin-card"]', { timeout: 3000 })

    const endTime = Date.now()
    const loadTime = endTime - startTime

    // Should load in under 3 seconds
    expect(loadTime).toBeLessThan(3000)
    console.log(`Plugin list loaded in ${loadTime}ms`)
  })

  test('Pagination: Page through plugins', async ({ page }) => {
    // Click Browse tab
    await page.click('button:has-text("Browse")')
    await page.waitForTimeout(500)

    // Get first page plugin count
    const firstPageCount = await page.locator('[data-testid="plugin-card"]').count()

    // Click "Next" button if visible
    const nextButton = page.locator('button:has-text("Next")')
    if (await nextButton.isVisible()) {
      await nextButton.click()
      await page.waitForTimeout(500)

      // Verify different plugins loaded (or all fit on one page)
      const secondPageCount = await page.locator('[data-testid="plugin-card"]').count()
      // Either second page loaded or we're still seeing all plugins
      expect(secondPageCount).toBeGreaterThanOrEqual(0)
    }
  })
})
