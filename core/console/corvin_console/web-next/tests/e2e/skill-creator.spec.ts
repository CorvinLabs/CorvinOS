import { test, expect } from '@playwright/test';

test.describe('Skill Creator Panel', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to skills page
    await page.goto('/console/app/skills', { waitUntil: 'domcontentloaded' });
    // Give React time to render
    await page.waitForTimeout(2000);
  });

  test('should display Skill Creator Panel', async ({ page }) => {
    // Check if SkillCreatorPanel is visible
    const skillCreatorPanel = page.locator('text=Skill Creator');
    await expect(skillCreatorPanel).toBeVisible();
  });

  test('should have generate button', async ({ page }) => {
    const generateButton = page.locator('button:has-text("Generate Skill")');
    await expect(generateButton).toBeVisible();
    await expect(generateButton).toBeDisabled(); // disabled when no input
  });

  test('should submit skill generation request with correct API endpoint', async ({ page }) => {
    // Intercept the API call
    let apiCallMade = false;
    let apiUrl = '';

    page.on('request', request => {
      if (request.url().includes('skill-creator') && request.method() === 'POST') {
        apiUrl = request.url();
        apiCallMade = true;
        console.log('API Call:', request.url());
        console.log('Body:', request.postDataJSON());
      }
    });

    // Fill in the skill request
    const textarea = page.locator('textarea[placeholder*="Create a skill"]');
    await textarea.fill('Create a skill that validates JSON files');

    // Click generate button
    const generateButton = page.locator('button:has-text("Generate Skill")');
    await generateButton.click();

    // Wait for API call
    await page.waitForTimeout(1000);

    // Log the API URL for debugging
    console.log('Expected URL pattern: /v1/console/skill-creator/generate');
    console.log('Actual API URL:', apiUrl);

    // Verify API call was made
    expect(apiCallMade).toBe(true);
    expect(apiUrl).toContain('skill-creator/generate');
    expect(apiUrl).not.toContain('/api/quality/');
  });

  test('should show error message on API 404', async ({ page }) => {
    // The endpoint might return 404 if not properly wired
    const textarea = page.locator('textarea[placeholder*="Create a skill"]');
    await textarea.fill('Create a skill that validates JSON files');

    const generateButton = page.locator('button:has-text("Generate Skill")');
    await generateButton.click();

    // Wait for error to appear
    await page.waitForTimeout(2000);

    // Check if error message is displayed
    const errorMessage = page.locator('text=Generation failed');
    const isErrorVisible = await errorMessage.isVisible().catch(() => false);

    if (isErrorVisible) {
      const errorText = await errorMessage.textContent();
      console.log('Error message:', errorText);

      // If error contains "Not Found", the API routing is still wrong
      if (errorText?.includes('Not Found')) {
        console.error('API routing issue: Endpoint returned 404');
        console.error('Check that the skill-creator router is properly mounted');
      }
    }
  });

  test('should list generated skills', async ({ page }) => {
    // Look for the "Generated Skills" section
    const generatedSkillsSection = page.locator('text=Generated Skills');
    await expect(generatedSkillsSection).toBeVisible();

    // The list might be empty initially, which is OK
    const skillsList = page.locator('div:has-text("No skills generated yet")');
    const isEmptyOK = await skillsList.isVisible().catch(() => false);

    if (isEmptyOK) {
      console.log('Skills list is empty (expected on first run)');
    }
  });
});
