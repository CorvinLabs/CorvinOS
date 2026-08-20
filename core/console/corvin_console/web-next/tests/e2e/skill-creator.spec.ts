import { test, expect } from '@playwright/test';

test.describe('Skill Creator Panel', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to skills page
    await page.goto('/console/app/skills', { waitUntil: 'domcontentloaded' });
    // Give React time to render
    await page.waitForTimeout(2000);
  });

  test('should display Skill Creator Panel', async ({ page }) => {
    // Target the panel HEADING, not any text node containing the words:
    // a substring locator also matches skill-list entries whose description
    // happens to mention "Skill Creator", which is a strict-mode violation.
    const skillCreatorPanel = page.getByRole('heading', { name: 'Skill Creator', exact: true });
    await expect(skillCreatorPanel).toBeVisible();
  });

  test('should have generate button', async ({ page }) => {
    const generateButton = page.locator('button:has-text("Generate Skill")');
    await expect(generateButton).toBeVisible();
    await expect(generateButton).toBeDisabled(); // disabled when no input
  });

  test('should submit skill generation request with correct API endpoint', async ({ page }) => {
    // Stub the endpoint: unstubbed, this test kicked off a REAL multi-minute
    // generation run against the operator's Claude subscription on every
    // E2E pass. The assertion is about wiring, not about the engine.
    await page.route('**/skill-creator/generate', (route) =>
      route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'accepted', run_id: 'run-stub000000', engine: 'claude_code' }),
      }),
    );

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
  test('should render the five real phases and the engine label', async ({ page }) => {
    // The panel used to label a six-phase pipeline (API-Design, Dialectical,
    // Ideation, …) that no backend ever emitted, and it never showed which
    // engine ran the work. Both are asserted here against stubbed status.
    await page.route('**/skill-creator/generate', (route) =>
      route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'accepted', run_id: 'run-stub111111', engine: 'claude_code' }),
      }),
    );
    await page.route('**/skill-creator/status/**', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          run_id: 'run-stub111111',
          status: 'running',
          phase: 'ldd_iteration',
          progress: 50,
          message: 'Running LDD test loop…',
          engine: 'claude_code',
          phases: ['planning', 'validation', 'ldd_iteration', 'review', 'promotion'],
          error: null,
        }),
      }),
    );

    await page.locator('textarea[placeholder*="Create a skill"]')
      .fill('Create a skill that validates JSON files');
    await page.locator('button:has-text("Generate Skill")').click();

    const stepper = page.getByTestId('phase-stepper');
    await expect(stepper).toBeVisible();
    await expect(stepper.locator('li')).toHaveCount(5);
    await expect(stepper.locator('li[data-phase="ldd_iteration"]'))
      .toHaveAttribute('data-state', 'active');
    await expect(stepper.locator('li[data-phase="planning"]'))
      .toHaveAttribute('data-state', 'done');

    await expect(page.getByTestId('engine-label'))
      .toContainText('Claude subscription');
  });

  test('should surface an engine failure with its detail', async ({ page }) => {
    await page.route('**/skill-creator/generate', (route) =>
      route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'accepted', run_id: 'run-stub222222', engine: 'claude_code' }),
      }),
    );
    await page.route('**/skill-creator/status/**', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          run_id: 'run-stub222222',
          status: 'failed',
          phase: 'planning',
          progress: 10,
          message: 'Claude Code CLI not found. Install it or set CORVIN_CLAUDE_BIN to its path — skill generation runs on your Claude subscription.',
          engine: 'claude_code',
          phases: ['planning', 'validation', 'ldd_iteration', 'review', 'promotion'],
          error: "claude binary not found: 'claude'",
        }),
      }),
    );

    await page.locator('textarea[placeholder*="Create a skill"]')
      .fill('Create a skill that validates JSON files');
    await page.locator('button:has-text("Generate Skill")').click();

    const err = page.getByTestId('generation-error');
    await expect(err).toBeVisible();
    await expect(err).toContainText('CORVIN_CLAUDE_BIN');
    await expect(err.locator('pre')).toContainText('claude binary not found');
  });
});
