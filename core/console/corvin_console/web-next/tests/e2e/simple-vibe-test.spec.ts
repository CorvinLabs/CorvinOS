import { test, expect } from '@playwright/test';
import { setupMockApis } from '../fixtures/mock-api';

test.describe('VibeDashboard Simple Load Test', () => {
  test('page navigates to vibe-engineering without errors', async ({ page }) => {
    // Setup mocked APIs
    await setupMockApis(page);

    // Log page console errors
    page.on('console', msg => {
      console.log(`[Browser] ${msg.type()}: ${msg.text()}`);
    });

    page.on('pageerror', err => {
      console.log(`[Browser Error] ${err.message}`);
      console.log(err.stack);
    });

    // Navigate to vibe-engineering
    const response = await page.goto('/app/vibe-engineering', { waitUntil: 'domcontentloaded' });
    
    // Check that we got a response
    expect(response?.ok()).toBeTruthy();
    console.log(`Navigation complete. Status: ${response?.status()}`);

    // Wait a bit for React to render
    await page.waitForTimeout(2000);

    // Get page content
    const title = await page.title();
    console.log(`Page title: ${title}`);

    const bodyText = await page.locator('body').textContent();
    console.log(`Body text first 200 chars: ${bodyText?.substring(0, 200) || '(empty)'}`);

    // Check for any h1
    const h1Count = await page.locator('h1').count();
    console.log(`Number of h1s found: ${h1Count}`);

    if (h1Count > 0) {
      const h1Text = await page.locator('h1').first().textContent();
      console.log(`First h1 text: ${h1Text}`);
      expect(h1Text).toContain('Vibe Engineering');
    } else {
      // Debug: what elements are on the page?
      const divs = await page.locator('div').count();
      console.log(`Number of divs: ${divs}`);
      const buttons = await page.locator('button').count();
      console.log(`Number of buttons: ${buttons}`);
    }
  });
});
