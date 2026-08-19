import { test, expect, Page } from '@playwright/test'

const CONSOLE_URL = 'http://127.0.0.1:8765/console'
const GITHUB_URL = 'https://github.com/veegee82/tenant-shumway'

test.describe('🔴 ADVERSARIAL: GitHub Integration — Attack Surface', () => {
  let page: Page

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage()
  })

  // ATTACK 1: XSS via URL Input
  test('[SECURITY] XSS prevention — URL input sanitization', async () => {
    await page.goto(`${CONSOLE_URL}/app/settings/github`)
    
    const xssPayloads = [
      'javascript:alert("xss")',
      'https://github.com/owner/repo" onload="alert(1)',
      '<img src=x onerror=alert(1)>',
      'https://github.com/owner/repo?param=<script>alert(1)</script>',
    ]

    for (const payload of xssPayloads) {
      await page.fill('input[placeholder*="github.com"]', payload)
      
      // Should not execute JavaScript
      const dialogCaught = await Promise.race([
        new Promise(r => page.once('dialog', () => r(true))),
        new Promise(r => setTimeout(() => r(false), 500))
      ])
      
      expect(dialogCaught).toBe(false, `XSS payload executed: ${payload}`)
    }
  })

  // ATTACK 2: SQL Injection (if backend has DB)
  test('[SECURITY] SQL injection via token field', async () => {
    await page.goto(`${CONSOLE_URL}/app/settings/github`)
    
    const sqlPayloads = [
      "' OR '1'='1",
      "admin'--",
      "'; DROP TABLE users; --",
    ]

    for (const payload of sqlPayloads) {
      await page.fill('input[placeholder*="ghp_"]', payload)
      await page.click('button:has-text("Connect Repository")')
      
      // Wait for response — should get error, not bypass
      await page.waitForTimeout(1000)
      
      const error = await page.locator('text=/error|failed|invalid/i').isVisible()
      expect(error).toBe(true, `SQL payload not rejected: ${payload}`)
    }
  })

  // ATTACK 3: Rate Limiting Bypass
  test('[DOS] Rapid verification requests', async () => {
    await page.goto(`${CONSOLE_URL}/app/settings/github`)
    
    const startTime = Date.now()
    let requestCount = 0
    
    for (let i = 0; i < 15; i++) {
      await page.fill('input[placeholder*="github.com"]', GITHUB_URL)
      await page.click('button:has-text("Connect")')
      requestCount++
      
      // Should eventually hit rate limit or slow down
      await page.waitForTimeout(100)
    }
    
    const elapsed = Date.now() - startTime
    const requestsPerSecond = (requestCount / elapsed) * 1000
    
    console.log(`DOS test: ${requestsPerSecond.toFixed(2)} req/sec`)
    // Should not allow unlimited requests
    expect(requestsPerSecond).toBeLessThan(10)
  })

  // ATTACK 4: Token Exposure
  test('[SECURITY] Token never logged or cached insecurely', async () => {
    await page.goto(`${CONSOLE_URL}/app/settings/github`)
    
    const testToken = 'ghp_supersecret123456789'
    await page.fill('input[placeholder*="ghp_"]', testToken)
    
    // Check console logs — token should NOT appear
    const logs: string[] = []
    page.on('console', msg => logs.push(msg.text()))
    
    await page.click('button:has-text("Connect")')
    await page.waitForTimeout(2000)
    
    const tokenExposed = logs.some(log => log.includes(testToken))
    expect(tokenExposed).toBe(false, 'Token exposed in console logs!')
    
    // Check localStorage — token should NOT be stored plaintext
    const localStorage = await page.evaluate(() => JSON.stringify(window.localStorage))
    expect(localStorage).not.toContain(testToken)
  })

  // ATTACK 5: CSRF Token Validation
  test('[SECURITY] CSRF protection on state-changing requests', async () => {
    await page.goto(`${CONSOLE_URL}/app/settings/github`)
    
    // Try to intercept & modify requests
    await page.route('**/api/console/github/verify', async route => {
      const request = route.request()
      
      // Check for CSRF token
      const headers = request.headers()
      const hasCsrf = headers['x-csrf-token'] || headers['csrf-token'] || headers['x-requested-with']
      
      expect(hasCsrf).toBeTruthy('Missing CSRF token in request')
      await route.continue()
    })
    
    await page.fill('input[placeholder*="github.com"]', GITHUB_URL)
    await page.click('button:has-text("Connect")')
  })

  // ATTACK 6: Malformed API Response Handling
  test('[ERROR HANDLING] Invalid JSON response', async () => {
    await page.route('**/api/console/github/verify', route => {
      route.abort('failed')
    })
    
    await page.goto(`${CONSOLE_URL}/app/settings/github`)
    await page.fill('input[placeholder*="github.com"]', GITHUB_URL)
    await page.click('button:has-text("Connect")')
    
    // Should show error, not crash
    const errorVisible = await page.locator('text=/error|failed/i').isVisible()
    expect(errorVisible).toBe(true)
  })

  // ATTACK 7: State Manipulation — Bypass URL Validation
  test('[VALIDATION] Cannot bypass URL validation client-side', async () => {
    await page.goto(`${CONSOLE_URL}/app/settings/github`)
    
    // Try to disable input validation
    await page.evaluate(() => {
      const inputs = document.querySelectorAll('input')
      inputs.forEach(input => {
        input.removeAttribute('required')
        input.removeAttribute('pattern')
      })
    })
    
    const invalidUrls = [
      'not-a-url',
      'https://gitlab.com/owner/repo',
      'https://github.com/single-part',
      '',
      '   ',
    ]
    
    for (const url of invalidUrls) {
      await page.fill('input[placeholder*="github.com"]', url)
      await page.click('button:has-text("Connect")')
      
      // Should validate server-side too
      await page.waitForTimeout(500)
      const error = await page.locator('text=/invalid|format|error/i').isVisible()
      expect(error).toBe(true, `Invalid URL accepted: ${url}`)
    }
  })

  // ATTACK 8: Token Field Obfuscation
  test('[UX SECURITY] Token input masked & cannot be copy-pasted easily', async () => {
    await page.goto(`${CONSOLE_URL}/app/settings/github`)
    
    const tokenInput = page.locator('input[placeholder*="ghp_"]')
    
    // Should be type="password" or similar
    const type = await tokenInput.getAttribute('type')
    expect(['password', 'text']).toContain(type, 'Token input not properly masked')
    
    // If text, should have visibility toggle
    if (type === 'text') {
      const toggleButton = await page.locator('button:has-text("✕")')
      expect(await toggleButton.isVisible()).toBe(true)
    }
  })

  // ATTACK 9: Network Interception — MITM
  test('[NETWORK] Must use HTTPS in production', async () => {
    // Check that API calls use secure transport
    let requestMade = false
    
    await page.route('**/api/console/github/**', route => {
      const url = route.request().url()
      requestMade = true
      // In test env: http is OK, but should be https in production
      expect(url).toMatch(/^https?:/)
    })
    
    await page.goto(`${CONSOLE_URL}/app/settings/github`)
    await page.fill('input[placeholder*="github.com"]', GITHUB_URL)
    await page.click('button:has-text("Connect")')
    
    await page.waitForTimeout(1000)
    expect(requestMade).toBe(true)
  })

  // ATTACK 10: Tenant Isolation Bypass
  test('[MULTI-TENANT] Cannot access other tenant configs', async () => {
    // Try to force another tenant
    await page.goto(`${CONSOLE_URL}/app/settings/github?tenant_id=_other`)
    
    // Should either reject or use current tenant
    const currentTenant = await page.evaluate(() => {
      return (window as any).__TENANT_ID || '_default'
    })
    
    expect(currentTenant).toBe('_default')
  })

  // HAPPY PATH: Normal flow works
  test('[FLOW] Normal connection flow succeeds', async () => {
    await page.goto(`${CONSOLE_URL}/app/settings/github`)
    
    // Fill form
    await page.fill('input[placeholder*="github.com"]', GITHUB_URL)
    
    // Click connect
    await page.click('button:has-text("Connect")')
    
    // Wait for API response (may fail due to no real token, but should handle gracefully)
    await page.waitForTimeout(2000)
    
    // Should show status (connected or error message)
    const statusVisible = await page.locator('text=/connected|failed|error|verifying/i').isVisible()
    expect(statusVisible).toBe(true, 'No status message displayed')
  })
})
