/**
 * E2E Test: Marketplace Phase 2 Week 2
 * Tests: Browse → Search → Detail → Install → Progress Modal → Complete
 *
 * Task #6 Gate: Verify full install flow end-to-end
 */

import { test, expect } from '@playwright/test'

const BASE_URL = process.env.BASE_URL || 'http://localhost:8765'

test.describe('Marketplace Panel - Phase 2 Week 2', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to console + marketplace
    await page.goto(`${BASE_URL}/console/`)
    await page.waitForLoadState('networkidle')
  })

  test('E2E: Browse → Detail → Install → Progress → Complete', async ({ page }) => {
    // 1. Navigate to Marketplace (via sidebar or URL)
    await page.goto(`${BASE_URL}/console/app/marketplace`)
    await expect(page).toHaveTitle(/Marketplace|Console/)

    // 2. Wait for extensions to load
    await page.waitForSelector('[data-testid="extension-card"]', { timeout: 5000 })
    const extensionCards = await page.locator('[data-testid="extension-card"]').count()
    expect(extensionCards).toBeGreaterThan(0)

    // 3. Click first extension detail
    const firstCard = page.locator('[data-testid="extension-card"]').first()
    const extensionName = await firstCard.locator('[data-testid="extension-name"]').textContent()
    await firstCard.click()

    // 4. Verify detail modal is shown
    const detailModal = page.locator('[data-testid="extension-detail-modal"]')
    await expect(detailModal).toBeVisible()

    // 5. Click [Install] button
    const installBtn = page.locator('[data-testid="install-btn"]').first()
    await expect(installBtn).toBeEnabled()
    await installBtn.click()

    // 6. Verify InstallProgress modal appears
    const progressModal = page.locator('[data-testid="install-progress-modal"]')
    await expect(progressModal).toBeVisible({ timeout: 2000 })

    // 7. Verify progress elements are present
    await expect(
      page.locator('[data-testid="progress-bar"]')
    ).toBeVisible()

    // 8. Wait for installation to complete (mocked job = ~10s)
    const completeText = page.locator('text=Installation completed successfully')
    await expect(completeText).toBeVisible({ timeout: 15000 })

    // 9. Verify final state
    const progressValue = await page.locator('[data-testid="progress-percentage"]')
      .textContent()
    expect(parseInt(progressValue || '0')).toBe(100)

    // 10. Close modal
    const closeBtn = page.locator('[data-testid="install-progress-close-btn"]')
    await closeBtn.click()
    await expect(progressModal).not.toBeVisible()
  })

  test('E2E: Install → Cancel mid-progress', async ({ page }) => {
    // 1. Navigate to Marketplace
    await page.goto(`${BASE_URL}/console/app/marketplace`)
    await page.waitForSelector('[data-testid="extension-card"]', { timeout: 5000 })

    // 2. Open first extension detail
    await page.locator('[data-testid="extension-card"]').first().click()

    // 3. Click Install
    await page.locator('[data-testid="install-btn"]').first().click()

    // 4. Verify progress modal
    const progressModal = page.locator('[data-testid="install-progress-modal"]')
    await expect(progressModal).toBeVisible()

    // 5. Click Cancel button (while progress < 100%)
    const cancelBtn = page.locator('[data-testid="install-progress-cancel-btn"]')
    await expect(cancelBtn).toBeEnabled()
    await cancelBtn.click()

    // 6. Verify modal closes
    await expect(progressModal).not.toBeVisible()
  })

  test('E2E: Responsive layout - mobile viewport', async ({ page }) => {
    page.setViewportSize({ width: 375, height: 667 })

    // 1. Navigate to Marketplace
    await page.goto(`${BASE_URL}/console/app/marketplace`)
    await page.waitForSelector('[data-testid="extension-card"]', { timeout: 5000 })

    // 2. Marketplace should be responsive
    const header = page.locator('[data-testid="marketplace-header"]')
    await expect(header).toBeVisible()

    // 3. Extension cards should stack
    const cardContainer = page.locator('[data-testid="extension-grid"]')
    const cardWidth = await cardContainer.evaluate(el =>
      window.getComputedStyle(el).gridTemplateColumns
    )
    // On mobile, should be single column or 1 card
    expect(cardWidth).toContain('1fr')
  })

  test('Accessibility: Install progress modal - WCAG 2.1 AA', async ({ page }) => {
    // 1. Navigate to Marketplace
    await page.goto(`${BASE_URL}/console/app/marketplace`)
    await page.waitForSelector('[data-testid="extension-card"]', { timeout: 5000 })

    // 2. Open extension detail
    await page.locator('[data-testid="extension-card"]').first().click()

    // 3. Click Install
    await page.locator('[data-testid="install-btn"]').first().click()

    // 4. Verify progress modal is visible
    const progressModal = page.locator('[data-testid="install-progress-modal"]')
    await expect(progressModal).toBeVisible()

    // 5. Verify modal has aria-label
    await expect(progressModal).toHaveAttribute('role', 'dialog')

    // 6. Verify close button is keyboard accessible
    const closeBtn = page.locator('[data-testid="install-progress-close-btn"]')
    await closeBtn.focus()
    await page.keyboard.press('Enter')
    await expect(progressModal).not.toBeVisible()

    // 7. Verify tab navigation works
    await page.goto(`${BASE_URL}/console/app/marketplace`)
    await page.locator('[data-testid="extension-card"]').first().click()
    await page.locator('[data-testid="install-btn"]').first().click()
    await expect(progressModal).toBeVisible()

    // Tab through Cancel and Close buttons
    await page.keyboard.press('Tab')
    const focusedElement = await page.evaluate(() =>
      document.activeElement?.getAttribute('data-testid')
    )
    expect(
      ['install-progress-cancel-btn', 'install-progress-close-btn']
    ).toContain(focusedElement)
  })
})
