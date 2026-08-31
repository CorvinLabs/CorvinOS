/**
 * E2E Tests: Cross-Device-Learning GitHub Integration
 * Console: http://127.0.0.1:8765/console
 *
 * Tests complete flow:
 * 1. Navigate to GitHub settings
 * 2. Enter GitHub URL
 * 3. Verify connection
 * 4. Monitor live sync status
 * 5. View audit trail
 */

import { test, expect } from '@playwright/test'

const CONSOLE_BASE = 'http://127.0.0.1:8765/console'

test.describe('GitHub Integration E2E', () => {
  test('should navigate to GitHub settings page', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/settings/github`)
    await expect(page.locator('h2')).toContainText('GitHub Integration')
    await expect(page.locator('text=Connect your tenant to a GitHub repository')).toBeVisible()
  })

  test('should show disconnected state initially', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/settings/github`)
    await expect(page.locator('text=Not connected')).toBeVisible()
  })

  test('should validate GitHub URL format', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/settings/github`)

    const urlInput = page.locator('input[placeholder*="https://github.com"]')
    const connectButton = page.locator('button:has-text("Connect Repository")')

    // Invalid URL should disable button
    await urlInput.fill('https://gitlab.com/owner/repo')
    await expect(connectButton).toBeDisabled()

    // Valid URL should enable button
    await urlInput.fill('https://github.com/veegee82/tenant-shumway')
    await expect(connectButton).toBeEnabled()
  })

  test('should accept valid GitHub URLs', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/settings/github`)

    const urlInput = page.locator('input[placeholder*="https://github.com"]')
    const connectButton = page.locator('button:has-text("Connect Repository")')

    // Test valid formats
    const validUrls = [
      'https://github.com/owner/repo',
      'https://github.com/my-org/my-repo',
      'https://github.com/tenant-shumway/skills-backup',
    ]

    for (const url of validUrls) {
      await urlInput.fill(url)
      await expect(connectButton).toBeEnabled()
    }
  })

  test('should navigate to sync monitor', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)

    // Should show monitor panel
    await expect(page.locator('h2')).toContainText('Sync Monitor')
    await expect(page.locator('text=Manage tenant-native skills')).toBeVisible()
  })

  test('should show worker status on monitor', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)

    // Should display worker status
    const statusCard = page.locator('text=Status')
    await expect(statusCard).toBeVisible()
  })

  test('should allow worker control (start/stop)', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)

    // Should have start/stop button
    const button = page.locator('button:has-text("Start Worker"), button:has-text("Stop Worker")')
    await expect(button).toBeVisible()
  })

  test('should display event log', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)

    // Should have event log section
    const eventLog = page.locator('text=Sync Events')
    await expect(eventLog).toBeVisible()
  })

  test('should navigate to webhook config', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/settings/github/webhooks`)

    // Should show webhook panel
    await expect(page.locator('h2')).toContainText('GitHub Webhooks')
    await expect(page.locator('text=Event-driven synchronization from GitHub')).toBeVisible()
  })

  test('should have webhook registration form', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/settings/github/webhooks`)

    // Should show token input
    const tokenInput = page.locator('input[placeholder*="ghp_"]')
    await expect(tokenInput).toBeVisible()

    // Should show register button
    const registerButton = page.locator('button:has-text("Register Webhook")')
    await expect(registerButton).toBeVisible()
  })

  test('should navigate to audit trail', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/audit`)

    // Should show audit panel
    await expect(page.locator('h2')).toContainText('Sync Audit Trail')
    await expect(page.locator('text=GDPR Art. 30, 32')).toBeVisible()
  })

  test('should show audit statistics', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/audit`)

    // Should display stats
    const stats = page.locator('text=Total Events')
    await expect(stats).toBeVisible()
  })

  test('should have chain verification', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/audit`)

    // Should have verify button
    const verifyButton = page.locator('button:has-text("Verify Chain")')
    await expect(verifyButton).toBeVisible()
  })

  test('should navigate to releases', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/releases`)

    // Should show release manager
    await expect(page.locator('h2')).toContainText('Release Manager')
  })

  test('should show version info', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/releases`)

    // Should display version info
    const latestVersion = page.locator('text=Latest Version')
    await expect(latestVersion).toBeVisible()
  })

  test('should have create release button', async ({ page }) => {
    await page.goto(`${CONSOLE_BASE}/app/releases`)

    // Should have button
    const createButton = page.locator('button:has-text("New Release")')
    await expect(createButton).toBeVisible()
  })

  test('complete GitHub setup flow', async ({ page }) => {
    // 1. Go to GitHub settings
    await page.goto(`${CONSOLE_BASE}/app/settings/github`)
    await expect(page.locator('h1')).toContainText('GitHub Integration')

    // 2. Enter GitHub URL
    const urlInput = page.locator('input[placeholder*="https://github.com"]')
    await urlInput.fill('https://github.com/veegee82/tenant-shumway')

    // 3. Verify URL is accepted
    const connectButton = page.locator('button:has-text("Connect Repository")')
    await expect(connectButton).toBeEnabled()

    // 4. Check sync monitor is accessible
    await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)
    await expect(page.locator('h2')).toContainText('Sync Monitor')

    // 5. Check audit trail is accessible
    await page.goto(`${CONSOLE_BASE}/app/audit`)
    await expect(page.locator('h2')).toContainText('Audit')

    // 6. Check releases are accessible
    await page.goto(`${CONSOLE_BASE}/app/releases`)
    await expect(page.locator('h2')).toContainText('Release Manager')
  })

  test('should handle responsive layout on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 })

    await page.goto(`${CONSOLE_BASE}/app/settings/github`)

    // Page should still be visible and functional
    await expect(page.locator('h2')).toContainText('GitHub Integration')
    await expect(page.locator('button:has-text("Connect Repository")')).toBeVisible()
  })

  test('should handle responsive layout on tablet', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 })

    await page.goto(`${CONSOLE_BASE}/app/settings/github`)

    // Page should be readable
    await expect(page.locator('h2')).toContainText('GitHub Integration')
  })

  test('should preserve URL navigation', async ({ page }) => {
    // Test navigation between sections
    await page.goto(`${CONSOLE_BASE}/app/settings/github`)
    await expect(page.url()).toContain('/app/settings/github')

    await page.goto(`${CONSOLE_BASE}/app/audit`)
    await expect(page.url()).toContain('/app/audit')

    await page.goto(`${CONSOLE_BASE}/app/releases`)
    await expect(page.url()).toContain('/app/releases')
  })
})
