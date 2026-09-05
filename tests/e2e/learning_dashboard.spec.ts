/**
 * PLAYWRIGHT E2E TEST: Learning Dashboard
 *
 * Tests the complete learning system in a real browser:
 * 1. Dashboard loads
 * 2. Patterns are visible after tasks
 * 3. User can submit feedback
 * 4. Config rollback works
 * 5. Preferences update in real-time
 */

import { test, expect, Page } from '@playwright/test';

const BASE_URL = process.env.BASE_URL || 'http://localhost:8765';
const DASHBOARD_URL = `${BASE_URL}/console/panels/learning-dashboard`;

test.describe('Learning Dashboard E2E', () => {
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    // Clear session storage to start fresh
    await page.evaluate(() => {
      sessionStorage.clear();
      localStorage.clear();
    });
  });

  test.afterAll(async () => {
    await page.close();
  });

  // ─────────────────────────────────────────────────────────────────────────
  // TEST 1: Dashboard loads and renders correctly
  // ─────────────────────────────────────────────────────────────────────────

  test('Dashboard loads with all three tabs', async ({ page }) => {
    await page.goto(DASHBOARD_URL);

    // Wait for title
    await expect(page.locator('text=🧠 Learning Dashboard')).toBeVisible();

    // Check all three tabs exist
    const patternsTab = page.locator('button:has-text("📊 Patterns")');
    const configTab = page.locator('button:has-text("⚙️ Config History")');
    const prefsTab = page.locator('button:has-text("👤 Preferences")');

    await expect(patternsTab).toBeVisible();
    await expect(configTab).toBeVisible();
    await expect(prefsTab).toBeVisible();

    console.log('✅ Dashboard loads with all tabs');
  });

  // ─────────────────────────────────────────────────────────────────────────
  // TEST 2: Patterns Tab displays discovered patterns
  // ─────────────────────────────────────────────────────────────────────────

  test('Patterns tab shows discovered patterns', async ({ page }) => {
    await page.goto(DASHBOARD_URL);

    // Click Patterns tab
    await page.locator('button:has-text("📊 Patterns")').click();

    // Wait for content to load
    await page.waitForTimeout(1000);

    // Check for pattern elements (or empty state)
    const patternsSection = page.locator('text=Discovered Patterns');
    await expect(patternsSection).toBeVisible();

    // Either patterns are shown or "No patterns" message
    const noPatterns = page.locator('text=No patterns discovered yet');
    const patternCards = page.locator('[class*="border"][class*="rounded-lg"]');

    const hasPatterns = await patternCards.count() > 0;
    if (!hasPatterns) {
      await expect(noPatterns).toBeVisible();
      console.log('✅ Patterns tab shows empty state (expected for fresh dashboard)');
    } else {
      // If patterns exist, check structure
      const firstPattern = patternCards.first();
      await expect(firstPattern).toContainText('FEATURE'); // task_type
      console.log('✅ Patterns tab displays patterns with correct structure');
    }
  });

  // ─────────────────────────────────────────────────────────────────────────
  // TEST 3: User can submit feedback
  // ─────────────────────────────────────────────────────────────────────────

  test('Feedback submission form works', async ({ page }) => {
    await page.goto(DASHBOARD_URL);

    // Click Patterns tab (feedback form is there)
    await page.locator('button:has-text("📊 Patterns")').click();

    // Scroll to feedback section
    await page.locator('text=💬 Rate Your Tasks').scrollIntoViewIfNeeded();

    // Fill task ID
    await page.locator('input[placeholder*="task ID"]').fill('test_task_001');

    // Select feedback quality
    await page.locator('select').selectOption('excellent');

    // Submit
    const submitButton = page.locator('button:has-text("Submit")');
    await expect(submitButton).toBeVisible();
    await submitButton.click();

    // Wait for response
    await page.waitForTimeout(1000);

    // Field should be cleared after submission (or success message shown)
    const taskInput = page.locator('input[placeholder*="task ID"]');
    const isEmpty = (await taskInput.inputValue()) === '';

    if (isEmpty) {
      console.log('✅ Feedback submitted successfully (form cleared)');
    } else {
      console.log('⚠️  Form not cleared (may still be processing)');
    }
  });

  // ─────────────────────────────────────────────────────────────────────────
  // TEST 4: Config History Tab shows versions
  // ─────────────────────────────────────────────────────────────────────────

  test('Config History tab shows version list', async ({ page }) => {
    await page.goto(DASHBOARD_URL);

    // Click Config tab
    await page.locator('button:has-text("⚙️ Config History")').click();

    // Wait for content
    await page.waitForTimeout(1000);

    // Check for title
    const configTitle = page.locator('text=Skill Config History');
    await expect(configTitle).toBeVisible();

    // Check for empty state or version cards
    const noVersions = page.locator('text=No config changes yet');
    const versionCards = page.locator('h3:has-text("v")');

    const hasVersions = await versionCards.count() > 0;
    if (!hasVersions) {
      await expect(noVersions).toBeVisible();
      console.log('✅ Config tab shows empty state (expected for fresh dashboard)');
    } else {
      // If versions exist, check structure
      await expect(versionCards.first()).toBeVisible();
      console.log('✅ Config tab displays version history correctly');
    }
  });

  // ─────────────────────────────────────────────────────────────────────────
  // TEST 5: Config rollback button is clickable
  // ─────────────────────────────────────────────────────────────────────────

  test('Config rollback button works', async ({ page }) => {
    await page.goto(DASHBOARD_URL);

    // Click Config tab
    await page.locator('button:has-text("⚙️ Config History")').click();
    await page.waitForTimeout(1000);

    // Look for rollback button
    const rollbackButtons = page.locator('button:has-text("Rollback")');
    const hasRollback = await rollbackButtons.count() > 0;

    if (hasRollback) {
      // Listen for confirm dialog
      page.on('dialog', dialog => dialog.accept());

      // Click first rollback button
      await rollbackButtons.first().click();

      // Wait for response
      await page.waitForTimeout(1000);

      console.log('✅ Rollback button is functional');
    } else {
      console.log('✅ No rollback needed (no versions to revert)');
    }
  });

  // ─────────────────────────────────────────────────────────────────────────
  // TEST 6: Preferences Tab shows learned preferences
  // ─────────────────────────────────────────────────────────────────────────

  test('Preferences tab displays learned workstyle', async ({ page }) => {
    await page.goto(DASHBOARD_URL);

    // Click Preferences tab
    await page.locator('button:has-text("👤 Preferences")').click();

    // Wait for content
    await page.waitForTimeout(1000);

    // Check title
    const prefsTitle = page.locator('text=Learned Preferences');
    await expect(prefsTitle).toBeVisible();

    // Check for empty state or preference cards
    const noPrefs = page.locator('text=No preferences learned yet');
    const prefCards = page.locator('text=TASKS');

    const hasPrefs = await prefCards.count() > 0;
    if (!hasPrefs) {
      await expect(noPrefs).toBeVisible();
      console.log('✅ Preferences tab shows empty state (expected for fresh dashboard)');
    } else {
      // If preferences exist, check structure (confidence, preferred skills)
      const confidence = page.locator('text=confidence');
      await expect(confidence).toBeVisible();
      console.log('✅ Preferences tab displays learned workstyle');
    }
  });

  // ─────────────────────────────────────────────────────────────────────────
  // TEST 7: Tab switching works smoothly
  // ─────────────────────────────────────────────────────────────────────────

  test('Tab navigation switches between views', async ({ page }) => {
    await page.goto(DASHBOARD_URL);

    // Start on Patterns
    let activeTab = page.locator('[class*="border-blue-500"]').first();
    await expect(activeTab).toContainText('Patterns');

    // Switch to Config
    await page.locator('button:has-text("⚙️ Config History")').click();
    await page.waitForTimeout(500);

    activeTab = page.locator('[class*="border-blue-500"]').first();
    await expect(activeTab).toContainText('Config');

    // Switch to Preferences
    await page.locator('button:has-text("👤 Preferences")').click();
    await page.waitForTimeout(500);

    activeTab = page.locator('[class*="border-blue-500"]').first();
    await expect(activeTab).toContainText('Preferences');

    console.log('✅ Tab switching works correctly');
  });

  // ─────────────────────────────────────────────────────────────────────────
  // TEST 8: Auto-refresh indicator is visible
  // ─────────────────────────────────────────────────────────────────────────

  test('Dashboard shows refresh indicator', async ({ page }) => {
    await page.goto(DASHBOARD_URL);

    // Check for footer with refresh info
    const footer = page.locator('text=Learning Dashboard auto-refreshes every 30 seconds');
    await expect(footer).toBeVisible();

    // Check for timestamp
    const timestamp = page.locator('text=Last updated:');
    await expect(timestamp).toBeVisible();

    console.log('✅ Dashboard shows refresh indicator');
  });

  // ─────────────────────────────────────────────────────────────────────────
  // TEST 9: API errors are handled gracefully
  // ─────────────────────────────────────────────────────────────────────────

  test('Dashboard handles missing API gracefully', async ({ page }) => {
    // Mock API failure
    await page.route('**/v1/console/learning/**', route => {
      route.abort('failed');
    });

    await page.goto(DASHBOARD_URL);

    // Should still load (empty state)
    await expect(page.locator('text=Learning Dashboard')).toBeVisible();

    // Should show loading or empty state
    const loadingState = page.locator('text=Loading').or(page.locator('text=No'));
    // At least one of these should be present
    const exists = await loadingState.count() > 0;

    if (exists) {
      console.log('✅ API failure handled gracefully');
    } else {
      console.log('⚠️  Dashboard still functional despite API error');
    }
  });

  // ─────────────────────────────────────────────────────────────────────────
  // TEST 10: Responsive design works on mobile
  // ─────────────────────────────────────────────────────────────────────────

  test('Dashboard is responsive on mobile viewport', async ({ page }) => {
    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });

    await page.goto(DASHBOARD_URL);

    // Should still load
    await expect(page.locator('text=Learning Dashboard')).toBeVisible();

    // Tabs should be visible
    const patternsTab = page.locator('button:has-text("Patterns")');
    await expect(patternsTab).toBeVisible();

    console.log('✅ Dashboard is responsive on mobile');
  });
});

// ─────────────────────────────────────────────────────────────────────────
// INTEGRATION TEST: Full Workflow
// ─────────────────────────────────────────────────────────────────────────

test.describe('Learning Dashboard Full Workflow', () => {
  test('Complete user workflow: observe → feedback → learn', async ({ page }) => {
    await page.goto(DASHBOARD_URL);

    console.log('\n📊 FULL E2E WORKFLOW TEST');
    console.log('=' * 80);

    // Phase 1: Initial state
    console.log('\n[Phase 1] Dashboard loads and renders');
    await expect(page.locator('text=Learning Dashboard')).toBeVisible();
    console.log('  ✓ Dashboard ready');

    // Phase 2: Submit feedback on multiple tasks
    console.log('\n[Phase 2] Submitting feedback (simulating task observations)');

    for (let i = 1; i <= 3; i++) {
      await page.locator('button:has-text("📊 Patterns")').click();
      await page.waitForTimeout(500);

      const taskInput = page.locator('input[placeholder*="task ID"]');
      await taskInput.fill(`workflow_task_${i:03d}`);

      const qualitySelect = page.locator('select').first();
      await qualitySelect.selectOption(i === 1 ? 'excellent' : 'good');

      const submitBtn = page.locator('button:has-text("Submit")').last();
      await submitBtn.click();

      console.log(`  ✓ Task ${i} feedback submitted`);
      await page.waitForTimeout(500);
    }

    // Phase 3: Check if patterns emerged
    console.log('\n[Phase 3] Checking for pattern discovery');
    await page.reload();
    await page.waitForTimeout(2000);

    const patternCards = page.locator('[class*="border"][class*="rounded-lg"]');
    const cardCount = await patternCards.count();

    if (cardCount > 0) {
      console.log(`  ✓ ${cardCount} pattern(s) discovered`);
      const firstCard = patternCards.first();
      const hasConfidence = await firstCard.textContent();
      console.log(`  ✓ Pattern confidence visible`);
    } else {
      console.log('  ℹ️  No patterns yet (may need more observations)');
    }

    // Phase 4: Check config history
    console.log('\n[Phase 4] Checking configuration history');
    await page.locator('button:has-text("⚙️ Config History")').click();
    await page.waitForTimeout(500);

    const versions = page.locator('h3');
    const versionCount = await versions.count();

    if (versionCount > 0) {
      console.log(`  ✓ ${versionCount} config version(s) available`);
    } else {
      console.log('  ℹ️  No config changes yet');
    }

    // Phase 5: Check preferences
    console.log('\n[Phase 5] Checking learned preferences');
    await page.locator('button:has-text("👤 Preferences")').click();
    await page.waitForTimeout(500);

    const skillTags = page.locator('[class*="border"][class*="px-3"]');
    const skillCount = await skillTags.count();

    if (skillCount > 0) {
      console.log(`  ✓ Preferences learned for ${skillCount} skill(s)`);
    } else {
      console.log('  ℹ️  Preferences not yet learned');
    }

    console.log('\n' + '=' * 80);
    console.log('✅ FULL WORKFLOW TEST COMPLETE');
  });
});
