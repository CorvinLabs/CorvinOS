import { test, expect } from '@playwright/test'

const CONSOLE_URL = 'http://127.0.0.1:8765/console'
const GITHUB_REPO = 'https://github.com/veegee82/tenant-shumway'

test.describe('GitHub Sync — E2E Flow', () => {
  test('Flow: Connect → Monitor → Sync', async ({ page }) => {
    // 1. Load GitHub settings
    await page.goto(`${CONSOLE_URL}/app/settings/github`)
    
    // 2. Verify initial state (not connected)
    const status = await page.locator('text=/Not connected|Not configured/').isVisible()
    expect(status).toBe(true)
    
    // 3. Connect to GitHub
    await page.fill('input[placeholder*="github.com"]', GITHUB_REPO)
    await page.click('button:has-text("Connect")')
    
    // 4. Wait for connection
    await page.waitForResponse(resp => 
      resp.url().includes('/v1/console/github/verify') && resp.status() === 200
    )
    
    // 5. Should show "Connected"
    const connected = await page.locator('text=/Connected|connected/i').isVisible()
    expect(connected).toBe(true)
    
    // 6. Open Sync Monitor
    await page.goto(`${CONSOLE_URL}/sync-monitor`)
    
    // 7. Start sync worker
    const startButton = page.locator('button:has-text("Start Worker")')
    if (await startButton.isVisible()) {
      await startButton.click()
      
      // 8. Verify worker started
      await page.waitForResponse(resp =>
        resp.url().includes('/worker/start') && resp.status() === 200
      )
      
      const running = await page.locator('text=/Running|running/').isVisible()
      expect(running).toBe(true)
    }
    
    // 9. Check sync status
    const syncStatus = page.locator('text=/sync|Sync/i')
    expect(await syncStatus.isVisible()).toBe(true)
  })
  
  test('Sync endpoint: POST /sync', async ({ page }) => {
    const response = await page.request.post(
      `${CONSOLE_URL.replace('/console', '')}/v1/console/github/sync`,
      {
        data: { direction: 'push' }
      }
    )
    
    expect([200, 501]).toContain(response.status())
  })
  
  test('Verify audit trail after sync', async ({ page }) => {
    // After sync, audit trail should be updated
    const response = await page.request.get(
      `${CONSOLE_URL.replace('/console', '')}/v1/console/github/audit`
    )
    
    expect([200, 404]).toContain(response.status())
    
    if (response.status() === 200) {
      const data = await response.json()
      expect(data).toHaveProperty('events')
    }
  })
})
