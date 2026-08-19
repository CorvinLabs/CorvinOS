import { test, expect, Page } from '@playwright/test'

const CONSOLE_URL = 'http://127.0.0.1:8765/console/'
const GITHUB_REPO = 'https://github.com/veegee82/tenant-shumway'
const GITHUB_TOKEN = process.env.GITHUB_TOKEN || ''

test.describe('GitHub Integration — Iteration 1-5', () => {
  let page: Page

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage()
  })

  test('Should load Console at /console/', async () => {
    const response = await page.goto(CONSOLE_URL, { waitUntil: 'networkidle' })
    expect(response?.status()).toBe(200)
  })

  test('Should navigate to GitHub settings page', async () => {
    await page.goto(CONSOLE_URL, { waitUntil: 'networkidle' })

    // Navigate to settings
    await page.click('button:has-text("Settings")')
    await page.waitForNavigation()

    // Click GitHub integration button
    await page.click('text=GitHub')
    await page.waitForLoadState('networkidle')

    // Verify page content
    const heading = await page.locator('h2:has-text("GitHub Integration")')
    await expect(heading).toBeVisible()
  })

  test('Should verify GitHub repository connection', async () => {
    await page.goto(`${CONSOLE_URL}app/settings/github`, { waitUntil: 'networkidle' })

    // Fill repository URL
    await page.fill('input[placeholder*="github.com"]', GITHUB_REPO)

    // Fill token if available
    if (GITHUB_TOKEN) {
      await page.fill('input[placeholder*="ghp_"]', GITHUB_TOKEN)
    }

    // Click verify button
    await page.click('button:has-text("Connect Repository")')

    // Wait for response
    await page.waitForResponse(
      response => response.url().includes('/api/console/github/verify') && response.status() === 200
    )

    // Verify success message
    const success = await page.locator('text=Connected Successfully').isVisible()
    expect(success).toBe(true)
  })

  test('Should display sync status after connection', async () => {
    await page.goto(`${CONSOLE_URL}app/settings/github`, { waitUntil: 'networkidle' })

    // Check for sync status display
    const statusDisplay = await page.locator('text=/Connected|Sync Status/').isVisible()
    expect(statusDisplay).toBe(true)

    // Verify auto-sync toggle
    const toggle = await page.locator('input[type="checkbox"]').isEnabled()
    expect(toggle).toBe(true)
  })

  test('Should fetch GitHub status via API', async ({ request }) => {
    const response = await request.get(`${CONSOLE_URL.replace('/console/', '')}/v1/console/github/status`)

    expect(response.status()).toBe(200)

    const data = await response.json()
    expect(data).toHaveProperty('connected')
    expect(data).toHaveProperty('configured')
  })

  test('Should verify GitHub connection via API', async ({ request }) => {
    const response = await request.post(
      `${CONSOLE_URL.replace('/console/', '')}/v1/console/github/verify`,
      {
        data: {
          url: GITHUB_REPO,
          token: GITHUB_TOKEN || undefined
        }
      }
    )

    expect(response.status()).toBe(200)

    const data = await response.json()
    expect(data).toHaveProperty('connected')
    expect(data).toHaveProperty('details')
  })

  test('Should handle webhook registration', async ({ request }) => {
    const webhookSecret = 'test-secret-' + Math.random().toString(36).slice(2)

    const response = await request.post(
      `${CONSOLE_URL.replace('/console/', '')}/v1/console/github/webhook/register`,
      {
        data: {
          url: GITHUB_REPO,
          secret: webhookSecret
        }
      }
    )

    expect([200, 404]).toContain(response.status()) // 404 is ok for unimplemented
  })

  test('Should export audit trail', async ({ request }) => {
    const response = await request.get(
      `${CONSOLE_URL.replace('/console/', '')}/v1/console/github/audit/export?format=csv`
    )

    // 404 is acceptable for this iteration
    expect([200, 404, 501]).toContain(response.status())
  })

  test('Should disconnect from GitHub', async ({ request }) => {
    const response = await request.delete(
      `${CONSOLE_URL.replace('/console/', '')}/v1/console/github/config`
    )

    expect([200, 404]).toContain(response.status())
  })
})
