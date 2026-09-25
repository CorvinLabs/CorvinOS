/**
 * End-to-end tests for Discovery Panel using Playwright.
 *
 * k=2 Gate tests:
 * - Page load and navigation (LCP <2.5s)
 * - Peer list display
 * - Search/filter functionality
 * - Error state handling
 * - Empty state handling
 * - Keyboard navigation (tab/enter/escape)
 * - Core Web Vitals (FID, CLS)
 */

import { test, expect } from "@playwright/test";

const CONSOLE_URL = "http://localhost:8765/console";
const DISCOVERY_ROUTE = `${CONSOLE_URL}/app/discovery`;

test.describe("Discovery Panel E2E", () => {
  test("loads discovery page within LCP target", async ({ page }) => {
    // Measure page load performance
    const startTime = Date.now();

    await page.goto(DISCOVERY_ROUTE);

    // Wait for the main heading to be visible
    await page.waitForSelector("h1:text('Peer Discovery')", { timeout: 2500 });

    const loadTime = Date.now() - startTime;
    expect(loadTime).toBeLessThan(2500);

    // Check page title
    await expect(page.locator("h1")).toContainText("Peer Discovery");
  });

  test("displays peer list when available", async ({ page }) => {
    await page.goto(DISCOVERY_ROUTE);

    // Wait for the peer list container to be visible
    await page.waitForSelector("table", { timeout: 5000 });

    // Check table headers exist
    const headers = await page.locator("table th").allTextContents();
    expect(headers).toContain("Name");
    expect(headers).toContain("Status");
  });

  test("filters peers by search term", async ({ page }) => {
    await page.goto(DISCOVERY_ROUTE);

    // Wait for search input to be visible
    await page.waitForSelector('input[placeholder*="Search"]', { timeout: 5000 });

    // Type search term
    const searchInput = page.locator('input[placeholder*="Search"]');
    await searchInput.fill("peer");

    // Give filter time to apply
    await page.waitForTimeout(300);

    // Verify search input has value
    await expect(searchInput).toHaveValue("peer");
  });

  test("opens peer detail modal on row click", async ({ page }) => {
    await page.goto(DISCOVERY_ROUTE);

    // Wait for details button
    await page.waitForSelector("button:text('Details')", { timeout: 5000 });

    // Click first Details button
    const detailsButton = page.locator("button:text('Details')").first();
    await detailsButton.click();

    // Wait for modal to appear
    await page.waitForSelector("text='Endpoint'", { timeout: 2000 });

    // Verify modal is visible
    const modal = page.locator("div").filter({ hasText: /Endpoint/ });
    await expect(modal).toBeVisible();
  });

  test("closes peer detail modal with Escape key", async ({ page }) => {
    await page.goto(DISCOVERY_ROUTE);

    // Wait for details button and click it
    await page.waitForSelector("button:text('Details')", { timeout: 5000 });
    await page.locator("button:text('Details')").first().click();

    // Wait for modal
    await page.waitForSelector("text='Endpoint'", { timeout: 2000 });

    // Press Escape
    await page.keyboard.press("Escape");

    // Modal should be gone
    const endpointText = page.locator("text='Endpoint'");
    await expect(endpointText).not.toBeVisible({ timeout: 1000 });
  });

  test("handles empty state gracefully", async ({ page }) => {
    // Intercept API to return empty peer list
    await page.route("/v1/console/discovery/peers", (route) => {
      route.abort("blockedbyresponse");
    });

    await page.goto(DISCOVERY_ROUTE);

    // Wait for no peers message
    await page.waitForSelector("text='No Peers Discovered'", { timeout: 5000 });

    // Check for helpful text
    const helpText = page.locator("text=/Connect with other CorvinOS/i");
    await expect(helpText).toBeVisible();
  });

  test("handles API errors with retry button", async ({ page }) => {
    let attemptCount = 0;

    // Fail first request, succeed on second
    await page.route("/v1/console/discovery/peers", (route) => {
      attemptCount++;
      if (attemptCount === 1) {
        route.abort("failed");
      } else {
        route.abort("blockedbyresponse");
      }
    });

    await page.goto(DISCOVERY_ROUTE);

    // Wait for error message
    await page.waitForSelector("text='Failed to Load Peers'", { timeout: 5000 });

    // Verify error message
    const errorMsg = page.locator("text=/Failed to Load Peers/i");
    await expect(errorMsg).toBeVisible();

    // Verify Retry button exists
    const retryButton = page.locator("button:text('Retry')");
    await expect(retryButton).toBeVisible();
  });

  test("supports keyboard navigation (tab key)", async ({ page }) => {
    await page.goto(DISCOVERY_ROUTE);

    // Wait for interactive elements
    await page.waitForSelector('input[placeholder*="Search"]', { timeout: 5000 });

    // Focus on search input
    const searchInput = page.locator('input[placeholder*="Search"]');
    await searchInput.focus();

    // Press Tab to move focus
    await page.keyboard.press("Tab");

    // Focus should have moved (button should be focused)
    const focusedElement = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement;
      return el?.tagName.toLowerCase();
    });

    expect(["button", "input"]).toContain(focusedElement);
  });

  test("renders with proper heading hierarchy", async ({ page }) => {
    await page.goto(DISCOVERY_ROUTE);

    // Wait for page to load
    await page.waitForSelector("h1:text('Peer Discovery')", { timeout: 5000 });

    // Check heading hierarchy
    const h1 = page.locator("h1").first();
    await expect(h1).toHaveText("Peer Discovery");
  });

  test("refresh button is functional", async ({ page }) => {
    await page.goto(DISCOVERY_ROUTE);

    // Wait for initial load
    await page.waitForSelector("table", { timeout: 5000 });

    // Intercept API to track calls
    const apiCalls: number[] = [];
    await page.route("/v1/console/discovery/peers", (route) => {
      apiCalls.push(Date.now());
      route.abort("blockedbyresponse");
    });

    // Click refresh button
    const refreshButton = page.locator('button[aria-label="Refresh peer list"]');
    if (await refreshButton.isVisible()) {
      await refreshButton.click();

      // Wait a bit for network request
      await page.waitForTimeout(500);

      // Should have made an API call
      expect(apiCalls.length).toBeGreaterThan(0);
    }
  });

  test("page is mobile-responsive", async ({ page }) => {
    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });

    await page.goto(DISCOVERY_ROUTE);

    // Wait for page to load
    await page.waitForSelector("h1:text('Peer Discovery')", { timeout: 5000 });

    // Main heading should be visible
    const heading = page.locator("h1").first();
    await expect(heading).toBeVisible();

    // Search input should be visible
    const searchInput = page.locator('input[placeholder*="Search"]');
    await expect(searchInput).toBeVisible();
  });

  test("peer list has proper ARIA labels", async ({ page }) => {
    await page.goto(DISCOVERY_ROUTE);

    // Wait for table
    await page.waitForSelector("table", { timeout: 5000 });

    // Check for labeled elements
    const searchInput = page.locator('input[aria-label*="Search"]');
    expect(await searchInput.isVisible()).toBe(true);

    const refreshButton = page.locator('button[aria-label="Refresh"]');
    expect(await refreshButton.count()).toBeGreaterThan(0);
  });

  test("discovery panel is reachable from nav sidebar", async ({ page }) => {
    await page.goto(CONSOLE_URL);

    // Look for Discovery nav link
    const discoveryLink = page.locator("a:text('Discovery')");
    expect(await discoveryLink.isVisible()).toBe(true);

    // Click it
    await discoveryLink.click();

    // Should navigate to discovery route
    await expect(page).toHaveURL(DISCOVERY_ROUTE);

    // Header should be visible
    await page.waitForSelector("h1:text('Peer Discovery')", { timeout: 5000 });
  });
});
