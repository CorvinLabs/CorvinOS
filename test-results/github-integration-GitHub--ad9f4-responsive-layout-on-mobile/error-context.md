# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: github-integration.spec.ts >> GitHub Integration E2E >> should handle responsive layout on mobile
- Location: tests/e2e/github-integration.spec.ts:188:7

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: locator('h2')
Expected substring: "GitHub Integration"
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toContainText" with timeout 5000ms
  - waiting for locator('h2')

```

```yaml
- text: Loading session…
```

# Test source

```ts
  94  | 
  95  |   test('should navigate to webhook config', async ({ page }) => {
  96  |     await page.goto(`${CONSOLE_BASE}/app/settings/github/webhooks`)
  97  | 
  98  |     // Should show webhook panel
  99  |     await expect(page.locator('h2')).toContainText('GitHub Webhooks')
  100 |     await expect(page.locator('text=Event-driven synchronization from GitHub')).toBeVisible()
  101 |   })
  102 | 
  103 |   test('should have webhook registration form', async ({ page }) => {
  104 |     await page.goto(`${CONSOLE_BASE}/app/settings/github/webhooks`)
  105 | 
  106 |     // Should show token input
  107 |     const tokenInput = page.locator('input[placeholder*="ghp_"]')
  108 |     await expect(tokenInput).toBeVisible()
  109 | 
  110 |     // Should show register button
  111 |     const registerButton = page.locator('button:has-text("Register Webhook")')
  112 |     await expect(registerButton).toBeVisible()
  113 |   })
  114 | 
  115 |   test('should navigate to audit trail', async ({ page }) => {
  116 |     await page.goto(`${CONSOLE_BASE}/app/audit`)
  117 | 
  118 |     // Should show audit panel
  119 |     await expect(page.locator('h2')).toContainText('Sync Audit Trail')
  120 |     await expect(page.locator('text=GDPR Art. 30, 32')).toBeVisible()
  121 |   })
  122 | 
  123 |   test('should show audit statistics', async ({ page }) => {
  124 |     await page.goto(`${CONSOLE_BASE}/app/audit`)
  125 | 
  126 |     // Should display stats
  127 |     const stats = page.locator('text=Total Events')
  128 |     await expect(stats).toBeVisible()
  129 |   })
  130 | 
  131 |   test('should have chain verification', async ({ page }) => {
  132 |     await page.goto(`${CONSOLE_BASE}/app/audit`)
  133 | 
  134 |     // Should have verify button
  135 |     const verifyButton = page.locator('button:has-text("Verify Chain")')
  136 |     await expect(verifyButton).toBeVisible()
  137 |   })
  138 | 
  139 |   test('should navigate to releases', async ({ page }) => {
  140 |     await page.goto(`${CONSOLE_BASE}/app/releases`)
  141 | 
  142 |     // Should show release manager
  143 |     await expect(page.locator('h2')).toContainText('Release Manager')
  144 |   })
  145 | 
  146 |   test('should show version info', async ({ page }) => {
  147 |     await page.goto(`${CONSOLE_BASE}/app/releases`)
  148 | 
  149 |     // Should display version info
  150 |     const latestVersion = page.locator('text=Latest Version')
  151 |     await expect(latestVersion).toBeVisible()
  152 |   })
  153 | 
  154 |   test('should have create release button', async ({ page }) => {
  155 |     await page.goto(`${CONSOLE_BASE}/app/releases`)
  156 | 
  157 |     // Should have button
  158 |     const createButton = page.locator('button:has-text("New Release")')
  159 |     await expect(createButton).toBeVisible()
  160 |   })
  161 | 
  162 |   test('complete GitHub setup flow', async ({ page }) => {
  163 |     // 1. Go to GitHub settings
  164 |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
  165 |     await expect(page.locator('h1')).toContainText('GitHub Integration')
  166 | 
  167 |     // 2. Enter GitHub URL
  168 |     const urlInput = page.locator('input[placeholder*="https://github.com"]')
  169 |     await urlInput.fill('https://github.com/veegee82/tenant-shumway')
  170 | 
  171 |     // 3. Verify URL is accepted
  172 |     const connectButton = page.locator('button:has-text("Connect Repository")')
  173 |     await expect(connectButton).toBeEnabled()
  174 | 
  175 |     // 4. Check sync monitor is accessible
  176 |     await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)
  177 |     await expect(page.locator('h2')).toContainText('Sync Monitor')
  178 | 
  179 |     // 5. Check audit trail is accessible
  180 |     await page.goto(`${CONSOLE_BASE}/app/audit`)
  181 |     await expect(page.locator('h2')).toContainText('Audit')
  182 | 
  183 |     // 6. Check releases are accessible
  184 |     await page.goto(`${CONSOLE_BASE}/app/releases`)
  185 |     await expect(page.locator('h2')).toContainText('Release Manager')
  186 |   })
  187 | 
  188 |   test('should handle responsive layout on mobile', async ({ page }) => {
  189 |     await page.setViewportSize({ width: 375, height: 667 })
  190 | 
  191 |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
  192 | 
  193 |     // Page should still be visible and functional
> 194 |     await expect(page.locator('h2')).toContainText('GitHub Integration')
      |                                      ^ Error: expect(locator).toContainText(expected) failed
  195 |     await expect(page.locator('button:has-text("Connect Repository")')).toBeVisible()
  196 |   })
  197 | 
  198 |   test('should handle responsive layout on tablet', async ({ page }) => {
  199 |     await page.setViewportSize({ width: 768, height: 1024 })
  200 | 
  201 |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
  202 | 
  203 |     // Page should be readable
  204 |     await expect(page.locator('h2')).toContainText('GitHub Integration')
  205 |   })
  206 | 
  207 |   test('should preserve URL navigation', async ({ page }) => {
  208 |     // Test navigation between sections
  209 |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
  210 |     await expect(page.url()).toContain('/app/settings/github')
  211 | 
  212 |     await page.goto(`${CONSOLE_BASE}/app/audit`)
  213 |     await expect(page.url()).toContain('/app/audit')
  214 | 
  215 |     await page.goto(`${CONSOLE_BASE}/app/releases`)
  216 |     await expect(page.url()).toContain('/app/releases')
  217 |   })
  218 | })
  219 | 
```