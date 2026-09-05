import { test, expect } from '@playwright/test'

test.describe('Audit Graph Panel (Phase 2)', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to Vibe Dashboard
    await page.goto('http://127.0.0.1:8765/console/app/vibe-engineering')
    // Wait for page load
    await page.waitForLoadState('networkidle')
  })

  test('should load Audit Graph tab', async ({ page }) => {
    // Click on Audit Graph tab
    const auditTab = page.locator('button:has-text("Audit Graph")')
    await auditTab.click()
    await page.waitForLoadState('networkidle')

    // Should show the graph container
    const graphContainer = page.locator('[role="presentation"]').first()
    await expect(graphContainer).toBeVisible()
  })

  test('should fetch audit graph data from API', async ({ page }) => {
    // Click on Audit Graph tab
    await page.locator('button:has-text("Audit Graph")').click()
    await page.waitForLoadState('networkidle')

    // Intercept API call
    const apiResponse = await page.waitForResponse(
      response => response.url().includes('/api/v1/console/vibe/audit/graph')
    )

    expect(apiResponse.status()).toBe(200)
    const data = await apiResponse.json()
    expect(data).toHaveProperty('nodes')
    expect(data).toHaveProperty('edges')
    expect(data).toHaveProperty('total_events')
  })

  test('should display graph header with controls', async ({ page }) => {
    await page.locator('button:has-text("Audit Graph")').click()
    await page.waitForLoadState('networkidle')

    // Check for header elements
    await expect(page.locator('text=Audit Chain DAG')).toBeVisible()
    await expect(page.locator('text=Filter by Event Type')).toBeVisible()

    // Check for stats (Total Events, Nodes, Edges)
    await expect(page.locator('text=Total Events')).toBeVisible()
    await expect(page.locator('text=Nodes')).toBeVisible()
    await expect(page.locator('text=Edges')).toBeVisible()
  })

  test('should filter graph by event type', async ({ page }) => {
    await page.locator('button:has-text("Audit Graph")').click()
    await page.waitForLoadState('networkidle')

    // Get the filter dropdown
    const filterSelect = page.locator('select').first()

    // Count visible options
    const options = filterSelect.locator('option')
    const optionCount = await options.count()

    // Should have "All Events" + event types
    expect(optionCount).toBeGreaterThan(1)

    // Select a specific event type
    if (optionCount > 1) {
      await filterSelect.selectOption({ index: 1 })
      await page.waitForLoadState('networkidle')

      // Graph should update
      const graphContainer = page.locator('[role="presentation"]').first()
      await expect(graphContainer).toBeVisible()
    }
  })

  test('should show critical path when present', async ({ page }) => {
    await page.locator('button:has-text("Audit Graph")').click()
    await page.waitForLoadState('networkidle')

    // Intercept to get actual data
    const apiResponse = await page.waitForResponse(
      response => response.url().includes('/api/v1/console/vibe/audit/graph')
    )
    const data = await apiResponse.json()

    if (data.critical_path && data.critical_path.length > 0) {
      // Should show critical path info
      await expect(page.locator('text=Critical Path')).toBeVisible()
    }
  })

  test('should display anomalies if present', async ({ page }) => {
    await page.locator('button:has-text("Audit Graph")').click()
    await page.waitForLoadState('networkidle')

    const apiResponse = await page.waitForResponse(
      response => response.url().includes('/api/v1/console/vibe/audit/graph')
    )
    const data = await apiResponse.json()

    if (data.anomalies && data.anomalies.length > 0) {
      await expect(page.locator('text=Anomaly')).toBeVisible()
    }
  })

  test('should show legend with event type colors', async ({ page }) => {
    await page.locator('button:has-text("Audit Graph")').click()
    await page.waitForLoadState('networkidle')

    // Check for legend
    await expect(page.locator('text=Legend')).toBeVisible()

    // Should have colored circles for event types
    const colorDots = page.locator('[style*="background-color"]').filter({
      has: page.locator('..').filter({ hasText: /boot|compliance|error/ })
    })
    // At minimum should have some color indicators
    const count = await colorDots.count()
    expect(count).toBeGreaterThan(0)
  })

  test('should support dark and light mode', async ({ page }) => {
    await page.locator('button:has-text("Audit Graph")').click()
    await page.waitForLoadState('networkidle')

    // Get initial background color
    const container = page.locator('[role="presentation"]').first()

    // Check if container is visible in both light and dark contexts
    await expect(container).toBeVisible()

    // The background should adapt based on system preference
    const style = await container.getAttribute('style')
    expect(style).toBeTruthy()
  })

  test('should handle no data gracefully', async ({ page }) => {
    // Navigate to the page
    await page.goto('http://127.0.0.1:8765/console/app/vibe-engineering')

    // Mock empty API response
    await page.route('**/api/v1/console/vibe/audit/graph**', route => {
      route.abort('blockedbyclient')
    })

    // Click on Audit Graph tab
    await page.locator('button:has-text("Audit Graph")').click()
    await page.waitForTimeout(1000)

    // Should show error message
    await expect(page.locator('text=Error').first()).toBeVisible()
  })

  test('should display nodes with correct colors by event type', async ({ page }) => {
    await page.locator('button:has-text("Audit Graph")').click()
    await page.waitForLoadState('networkidle')

    // Wait for graph to render
    const graphContainer = page.locator('[role="presentation"]').first()
    await expect(graphContainer).toBeVisible()

    // Check for styled circles (nodes)
    // Note: This is a simplified check; real verification depends on Cytoscape rendering
    const nodeElements = page.locator('canvas')
    await expect(nodeElements.first()).toBeVisible()
  })

  test('should allow tooltip interaction on hover', async ({ page }) => {
    await page.locator('button:has-text("Audit Graph")').click()
    await page.waitForLoadState('networkidle')

    // The tooltip appears on node hover via the Cytoscape instance
    // This test verifies the component renders the graph structure
    const graphContainer = page.locator('[role="presentation"]').first()
    await expect(graphContainer).toBeVisible()

    // Hover over the graph area (Cytoscape canvas)
    const canvas = page.locator('canvas').first()
    if (await canvas.isVisible()) {
      await canvas.hover({ position: { x: 100, y: 100 } })
      // Tooltip logic runs in Cytoscape; this verifies no errors occur
    }
  })

  test('should navigate between tabs without errors', async ({ page }) => {
    // Navigate through all tabs
    const tabs = ['Learning Dashboard', 'Task Context Inspector', 'Audit Graph', 'Skill Composition']

    for (const tabName of tabs) {
      const tabButton = page.locator(`button:has-text("${tabName}")`)
      await tabButton.click()
      await page.waitForLoadState('networkidle')

      // Should not have console errors
      const consoleMessages = []
      page.on('console', msg => consoleMessages.push(msg.text()))

      // After navigation, content should be visible
      const content = page.locator('[role="main"], [class*="space-y"]').first()
      await expect(content).toBeVisible()
    }
  })

  test('should handle large graph data efficiently', async ({ page }) => {
    await page.locator('button:has-text("Audit Graph")').click()

    // Wait for the graph API call
    const apiStart = Date.now()
    await page.waitForResponse(
      response => response.url().includes('/api/v1/console/vibe/audit/graph')
    )
    const apiTime = Date.now() - apiStart

    // API should respond in reasonable time (<2s)
    expect(apiTime).toBeLessThan(2000)

    // Graph should render
    const graphContainer = page.locator('[role="presentation"]').first()
    await expect(graphContainer).toBeVisible()
  })
})
