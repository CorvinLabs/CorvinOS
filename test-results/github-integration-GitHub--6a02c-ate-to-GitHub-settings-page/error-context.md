# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: github-integration.spec.ts >> GitHub Integration E2E >> should navigate to GitHub settings page
- Location: tests/e2e/github-integration.spec.ts:18:7

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: locator('h2')
Expected substring: "GitHub Integration"
Received string:    "NordTech Sprint #23 — Priorisierter Backlog (85 SP Kapazität)"
Timeout: 5000ms

Call log:
  - Expect "toContainText" with timeout 5000ms
  - waiting for locator('h2')
    - waiting for navigation to finish...
    - navigated to "http://127.0.0.1:8765/console/"
    - locator resolved to <h2 class="font-serif text-2xl font-light tracking-tight">What's on your mind today?</h2>
    - unexpected value "What's on your mind today?"
    10 × locator resolved to <h2 class="mt-4 mb-2 font-serif text-xl font-light tracking-tight first:mt-0">NordTech Sprint #23 — Priorisierter Backlog (85 S…</h2>
       - unexpected value "NordTech Sprint #23 — Priorisierter Backlog (85 SP Kapazität)"

```

```yaml
- 'heading "NordTech Sprint #23 — Priorisierter Backlog (85 SP Kapazität)" [level=2]'
```

# Test source

```ts
  1   | /**
  2   |  * E2E Tests: Cross-Device-Learning GitHub Integration
  3   |  * Console: http://127.0.0.1:8765/console
  4   |  *
  5   |  * Tests complete flow:
  6   |  * 1. Navigate to GitHub settings
  7   |  * 2. Enter GitHub URL
  8   |  * 3. Verify connection
  9   |  * 4. Monitor live sync status
  10  |  * 5. View audit trail
  11  |  */
  12  | 
  13  | import { test, expect } from '@playwright/test'
  14  | 
  15  | const CONSOLE_BASE = 'http://127.0.0.1:8765/console'
  16  | 
  17  | test.describe('GitHub Integration E2E', () => {
  18  |   test('should navigate to GitHub settings page', async ({ page }) => {
  19  |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
> 20  |     await expect(page.locator('h2')).toContainText('GitHub Integration')
      |                                      ^ Error: expect(locator).toContainText(expected) failed
  21  |     await expect(page.locator('text=Connect your tenant to a GitHub repository')).toBeVisible()
  22  |   })
  23  | 
  24  |   test('should show disconnected state initially', async ({ page }) => {
  25  |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
  26  |     await expect(page.locator('text=Not connected')).toBeVisible()
  27  |   })
  28  | 
  29  |   test('should validate GitHub URL format', async ({ page }) => {
  30  |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
  31  | 
  32  |     const urlInput = page.locator('input[placeholder*="https://github.com"]')
  33  |     const connectButton = page.locator('button:has-text("Connect Repository")')
  34  | 
  35  |     // Invalid URL should disable button
  36  |     await urlInput.fill('https://gitlab.com/owner/repo')
  37  |     await expect(connectButton).toBeDisabled()
  38  | 
  39  |     // Valid URL should enable button
  40  |     await urlInput.fill('https://github.com/veegee82/tenant-shumway')
  41  |     await expect(connectButton).toBeEnabled()
  42  |   })
  43  | 
  44  |   test('should accept valid GitHub URLs', async ({ page }) => {
  45  |     await page.goto(`${CONSOLE_BASE}/app/settings/github`)
  46  | 
  47  |     const urlInput = page.locator('input[placeholder*="https://github.com"]')
  48  |     const connectButton = page.locator('button:has-text("Connect Repository")')
  49  | 
  50  |     // Test valid formats
  51  |     const validUrls = [
  52  |       'https://github.com/owner/repo',
  53  |       'https://github.com/my-org/my-repo',
  54  |       'https://github.com/tenant-shumway/skills-backup',
  55  |     ]
  56  | 
  57  |     for (const url of validUrls) {
  58  |       await urlInput.fill(url)
  59  |       await expect(connectButton).toBeEnabled()
  60  |     }
  61  |   })
  62  | 
  63  |   test('should navigate to sync monitor', async ({ page }) => {
  64  |     await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)
  65  | 
  66  |     // Should show monitor panel
  67  |     await expect(page.locator('h2')).toContainText('Sync Monitor')
  68  |     await expect(page.locator('text=Manage tenant-native skills')).toBeVisible()
  69  |   })
  70  | 
  71  |   test('should show worker status on monitor', async ({ page }) => {
  72  |     await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)
  73  | 
  74  |     // Should display worker status
  75  |     const statusCard = page.locator('text=Status')
  76  |     await expect(statusCard).toBeVisible()
  77  |   })
  78  | 
  79  |   test('should allow worker control (start/stop)', async ({ page }) => {
  80  |     await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)
  81  | 
  82  |     // Should have start/stop button
  83  |     const button = page.locator('button:has-text("Start Worker"), button:has-text("Stop Worker")')
  84  |     await expect(button).toBeVisible()
  85  |   })
  86  | 
  87  |   test('should display event log', async ({ page }) => {
  88  |     await page.goto(`${CONSOLE_BASE}/app/settings/github/sync-monitor`)
  89  | 
  90  |     // Should have event log section
  91  |     const eventLog = page.locator('text=Sync Events')
  92  |     await expect(eventLog).toBeVisible()
  93  |   })
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
```