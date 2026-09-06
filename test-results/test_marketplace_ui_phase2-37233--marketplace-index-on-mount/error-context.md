# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: test_marketplace_ui_phase2.spec.ts >> Marketplace UI Phase 2 >> should load marketplace index on mount
- Location: tests/e2e/test_marketplace_ui_phase2.spec.ts:25:7

# Error details

```
TimeoutError: page.waitForSelector: Timeout 5000ms exceeded.
Call log:
  - waiting for locator('[data-testid="plugin-center-tab-marketplace"]') to be visible
    - waiting for "http://127.0.0.1:8765/v1/console/auth/local-login" navigation to finish...

```

# Page snapshot

```yaml
- generic [active] [ref=f1e1]: Loading…
```

# Test source

```ts
  1   | /**
  2   |  * E2E Tests for Console Marketplace UI (Phase 2)
  3   |  *
  4   |  * Tests the complete marketplace workflow:
  5   |  * - Browsing marketplace extensions
  6   |  * - Searching and filtering by category
  7   |  * - Installing extensions
  8   |  * - Error handling
  9   |  * - Progress tracking
  10  |  * - State synchronization with plugins registry
  11  |  */
  12  | 
  13  | import { test, expect } from '@playwright/test'
  14  | 
  15  | const CONSOLE_URL = 'http://127.0.0.1:8765/console'
  16  | 
  17  | test.describe('Marketplace UI Phase 2', () => {
  18  |   test.beforeEach(async ({ page }) => {
  19  |     await page.goto(`${CONSOLE_URL}/#/app/plugin-center?tab=marketplace`)
> 20  |     await page.waitForSelector('[data-testid="plugin-center-tab-marketplace"]', {
      |                ^ TimeoutError: page.waitForSelector: Timeout 5000ms exceeded.
  21  |       timeout: 5000,
  22  |     })
  23  |   })
  24  | 
  25  |   test('should load marketplace index on mount', async ({ page }) => {
  26  |     // Check that the browse view is visible
  27  |     await expect(page.locator('input[placeholder*="Search"]')).toBeVisible()
  28  | 
  29  |     // Verify grid of extensions is rendered
  30  |     const extensions = page.locator('[class*="grid"] > [class*="rounded"]')
  31  |     const count = await extensions.count()
  32  |     expect(count).toBeGreaterThanOrEqual(0)
  33  |   })
  34  | 
  35  |   test('should display extension metadata correctly', async ({ page }) => {
  36  |     // Wait for extensions to load
  37  |     await page.waitForTimeout(500)
  38  | 
  39  |     // Get first extension card
  40  |     const firstCard = page.locator('[class*="grid"] > div').first()
  41  |     await expect(firstCard).toBeVisible()
  42  | 
  43  |     // Verify card contains expected fields
  44  |     const title = firstCard.locator('h3')
  45  |     const version = firstCard.locator('p:has-text("v")')
  46  |     const category = firstCard.locator('span[class*="px-2"]')
  47  | 
  48  |     await expect(title).toBeVisible()
  49  |     await expect(version).toBeVisible()
  50  |     await expect(category).toBeVisible()
  51  |   })
  52  | 
  53  |   test('should open extension detail modal on click', async ({ page }) => {
  54  |     // Wait for extensions
  55  |     await page.waitForTimeout(500)
  56  | 
  57  |     // Click first extension card
  58  |     const firstCard = page.locator('[class*="grid"] > div').first()
  59  |     await firstCard.click()
  60  | 
  61  |     // Verify modal appears with extension details
  62  |     const modal = page.locator('[class*="fixed"][class*="inset-0"]')
  63  |     await expect(modal).toBeVisible()
  64  | 
  65  |     // Verify modal content
  66  |     await expect(modal.locator('h2')).toBeVisible()
  67  |     await expect(modal.locator('text=Description')).toBeVisible()
  68  |   })
  69  | 
  70  |   test('should close modal on X button click', async ({ page }) => {
  71  |     // Open modal
  72  |     await page.waitForTimeout(500)
  73  |     const firstCard = page.locator('[class*="grid"] > div').first()
  74  |     await firstCard.click()
  75  | 
  76  |     // Wait for modal
  77  |     const closeButton = page.locator('button:has-text("✕")')
  78  |     await expect(closeButton).toBeVisible()
  79  | 
  80  |     // Click close button
  81  |     await closeButton.click()
  82  | 
  83  |     // Verify modal is hidden
  84  |     const modal = page.locator('[class*="fixed"][class*="inset-0"][class*="bg-black"]')
  85  |     await expect(modal).toBeHidden({ timeout: 1000 })
  86  |   })
  87  | 
  88  |   test('should search extensions by name', async ({ page }) => {
  89  |     const searchInput = page.locator('input[placeholder*="Search"]')
  90  |     await expect(searchInput).toBeVisible()
  91  | 
  92  |     // Type search term
  93  |     await searchInput.fill('auth')
  94  | 
  95  |     // Wait for filtering
  96  |     await page.waitForTimeout(300)
  97  | 
  98  |     // Verify results are filtered (should show fewer or zero results)
  99  |     const extensions = page.locator('[class*="grid"] > div')
  100 |     const count = await extensions.count()
  101 |     expect(count).toBeGreaterThanOrEqual(0)
  102 |   })
  103 | 
  104 |   test('should filter extensions by category', async ({ page }) => {
  105 |     // Wait for category buttons to be visible
  106 |     await page.waitForTimeout(500)
  107 | 
  108 |     // Get all category buttons (skip the "All" button at index 0)
  109 |     const categoryButtons = page.locator('button:has-text(/^[A-Z]/)').locator(
  110 |       'not(:text-is("All"))'
  111 |     )
  112 |     const categoryCount = await categoryButtons.count()
  113 | 
  114 |     if (categoryCount > 0) {
  115 |       // Click first category
  116 |       const firstCategory = categoryButtons.first()
  117 |       const categoryText = await firstCategory.textContent()
  118 | 
  119 |       await firstCategory.click()
  120 |       await page.waitForTimeout(300)
```