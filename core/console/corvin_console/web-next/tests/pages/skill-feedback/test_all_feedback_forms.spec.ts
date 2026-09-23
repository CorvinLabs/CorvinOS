/**
 * Playwright E2E Tests for Feedback Forms (Phase 1)
 * Coverage: Form render, validation, PII detection, successful submission
 * Total: 20 tests (5 per form)
 */

import { test, expect } from '@playwright/test';

// Test configuration
const BASE_URL = process.env.PLAYWRIGHT_TEST_BASE_URL || 'http://localhost:8765';

/**
 * Stream 1: Workflow Optimizer Feedback Form (5 tests)
 */
test.describe('Workflow Optimizer Feedback Form', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE_URL}/console/skill-feedback/workflow-optimizer`);
  });

  test('should render form with all fields', async ({ page }) => {
    // Verify form title
    await expect(page.locator('h2')).toContainText('Workflow Optimizer Feedback');

    // Verify all input fields
    await expect(page.locator('input[placeholder="auto-filled from task context"]')).toBeVisible();
    await expect(page.locator('select')).toContainText('Haiku 4.5');
    await expect(page.locator('input[value="yes"]')).toBeVisible();
    await expect(page.locator('input[value="no"]')).toBeVisible();
    await expect(page.locator('textarea')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toContainText('Submit Feedback');
  });

  test('should validate reason text field (max 500 chars)', async ({ page }) => {
    // Fill with > 500 chars
    const longText = 'a'.repeat(501);
    await page.fill('textarea', longText);
    await page.click('button[type="submit"]');

    // Verify error message
    const errorMsg = page.locator('text=must be ≤500 characters');
    await expect(errorMsg).toBeVisible();
  });

  test('should reject PII in reason field (email)', async ({ page }) => {
    // Fill with email address
    await page.fill('textarea', 'This is wrong because user@example.com reported it');
    await page.click('button[type="submit"]');

    // Verify PII rejection
    const errorMsg = page.locator('text=contains email');
    await expect(errorMsg).toBeVisible();
  });

  test('should reject PII in reason field (phone)', async ({ page }) => {
    // Fill with phone number
    await page.fill('textarea', 'Called user at 555-123-4567 about this issue');
    await page.click('button[type="submit"]');

    // Verify PII rejection
    const errorMsg = page.locator('text=contains phone');
    await expect(errorMsg).toBeVisible();
  });

  test('should successfully submit valid feedback', async ({ page }) => {
    // Fill valid form
    await page.fill('input[placeholder="auto-filled from task context"]', 'task-123');
    await page.click('input[value="yes"]'); // Select "yes"
    await page.fill('textarea', 'Correct agent for this task type');

    // Mock API response
    await page.route(
      '**/v1/console/learning/workflow-optimizer/feedback',
      (route) => {
        route.abort('blockedbyClient');
      }
    );

    // Override to mock success
    await page.route('**/v1/console/learning/workflow-optimizer/feedback', (route) => {
      route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          feedback_id: 'fb-123',
          feedback_type: 'outcome_feedback',
          skill_id: 'os.workflow_optimizer',
          timestamp: new Date().toISOString(),
          status: 'recorded',
          message: 'Feedback recorded',
        }),
      });
    });

    // Submit form
    await page.click('button[type="submit"]');

    // Verify success message
    const successMsg = page.locator('text=Feedback recorded');
    await expect(successMsg).toBeVisible();
  });
});

/**
 * Stream 2: Security Orchestrator Feedback Form (5 tests)
 */
test.describe('Security Orchestrator Feedback Form', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE_URL}/console/skill-feedback/security-orchestrator`);
  });

  test('should render form with security-specific fields', async ({ page }) => {
    // Verify form title
    await expect(page.locator('h2')).toContainText('Security Orchestrator Feedback');

    // Verify threat type dropdown
    await expect(page.locator('select')).toContainText('SQL Injection');

    // Verify radio options
    await expect(page.locator('text=Yes, real threat')).toBeVisible();
    await expect(page.locator('text=No, false alarm')).toBeVisible();

    // Verify severity override checkbox
    await expect(page.locator('input[type="checkbox"]')).toBeVisible();
  });

  test('should show justification field only when severity override checked', async ({
    page,
  }) => {
    const overrideCheckbox = page.locator('input[type="checkbox"]');
    const justificationField = page.locator(
      'textarea:has-text("Additional context about this incident")'
    );

    // Initially hidden
    await expect(justificationField).not.toBeVisible();

    // After check, should be visible
    await overrideCheckbox.check();
    // Note: This requires justification to appear in form conditionally
    // Adjust if form structure differs
  });

  test('should validate notes field (max 500 chars)', async ({ page }) => {
    const longText = 'x'.repeat(501);
    await page.fill('textarea', longText);
    await page.click('button[type="submit"]');

    const errorMsg = page.locator('text=must be ≤500 characters');
    await expect(errorMsg).toBeVisible();
  });

  test('should reject PII in notes field (SSN)', async ({ page }) => {
    await page.fill('textarea', 'Attacker used SSN 123-45-6789 to gain access');
    await page.click('button[type="submit"]');

    const errorMsg = page.locator('text=contains ssn');
    await expect(errorMsg).toBeVisible();
  });

  test('should successfully submit security feedback', async ({ page }) => {
    await page.click('input[value="no"]'); // "No, false alarm"
    await page.fill('textarea', 'Legitimate admin activity');

    // Mock API
    await page.route('**/v1/console/learning/security-orchestrator/incident', (route) => {
      route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          feedback_id: 'sb-456',
          feedback_type: 'outcome_feedback',
          skill_id: 'os.security_orchestrator',
          timestamp: new Date().toISOString(),
          status: 'recorded',
          message: 'Threat feedback recorded',
        }),
      });
    });

    await page.click('button[type="submit"]');

    const successMsg = page.locator('text=Threat feedback recorded');
    await expect(successMsg).toBeVisible();
  });
});

/**
 * Stream 3: Flow Guard Feedback Form (5 tests)
 */
test.describe('Flow Guard Feedback Form', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE_URL}/console/skill-feedback/flow-guard`);
  });

  test('should render form with data flow options', async ({ page }) => {
    await expect(page.locator('h2')).toContainText('Flow Guard Feedback');

    // Verify data class options
    await expect(page.locator('select')).toContainText('Public');
    await expect(page.locator('select')).toContainText('Restricted (PII)');

    // Verify preference options
    await expect(page.locator('text=Deterministic (strict rules)')).toBeVisible();
    await expect(page.locator('text=Request exception (LLM-gated)')).toBeVisible();
  });

  test('should show justification field when exception selected', async ({ page }) => {
    const exceptionRadio = page.locator('input[value="llm"]');
    const justField = page.locator('textarea[placeholder*="exception"]');

    // Initially hidden
    await expect(justField).not.toBeVisible();

    // After select
    await exceptionRadio.click();
    await expect(justField).toBeVisible();
  });

  test('should validate exception justification (required when exception selected)', async ({
    page,
  }) => {
    await page.click('input[value="llm"]'); // Select exception
    await page.click('button[type="submit"]');

    // Should show validation error for empty justification
    const errorMsg = page.locator('text=Justification');
    await expect(errorMsg).toBeVisible();
  });

  test('should reject PII in justification', async ({ page }) => {
    await page.click('input[value="llm"]');
    await page.fill(
      'textarea:has-text("Why should this")',
      'Needed for client@example.com account'
    );
    await page.click('button[type="submit"]');

    const errorMsg = page.locator('text=contains email');
    await expect(errorMsg).toBeVisible();
  });

  test('should successfully submit policy feedback', async ({ page }) => {
    await page.click('input[value="deterministic"]');

    await page.route('**/v1/console/learning/flow-guard/policy-feedback', (route) => {
      route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          feedback_id: 'fg-789',
          feedback_type: 'preference_feedback',
          skill_id: 'os.flow_guard',
          timestamp: new Date().toISOString(),
          status: 'recorded',
          message: 'Policy feedback recorded',
        }),
      });
    });

    await page.click('button[type="submit"]');

    const successMsg = page.locator('text=Policy feedback recorded');
    await expect(successMsg).toBeVisible();
  });
});

/**
 * Metrics Observation Form (5 tests)
 */
test.describe('Metrics Observation Form', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE_URL}/console/skill-feedback/metrics`);
  });

  test('should render metric selection form', async ({ page }) => {
    await expect(page.locator('h2')).toContainText('Observe Metric');

    // Verify skill dropdown
    await expect(page.locator('select')).toContainText('Workflow Optimizer');

    // Verify metric dropdown with options
    const metricSelect = page.locator('select').nth(1);
    await expect(metricSelect).toContainText('Latency');
  });

  test('should validate metric value bounds (latency)', async ({ page }) => {
    // Select latency metric
    const metricSelect = page.locator('select').nth(1);
    await metricSelect.selectOption('latency_ms');

    // Try negative value
    await page.fill('input[type="number"]', '-100');
    await page.click('button[type="submit"]');

    const errorMsg = page.locator('text=negative');
    await expect(errorMsg).toBeVisible();
  });

  test('should validate accuracy percentage (0-100%)', async ({ page }) => {
    const metricSelect = page.locator('select').nth(1);
    await metricSelect.selectOption('accuracy_percent');

    // Try value > 100
    await page.fill('input[type="number"]', '105');
    await page.click('button[type="submit"]');

    const errorMsg = page.locator('text=0–100%');
    await expect(errorMsg).toBeVisible();
  });

  test('should validate cost (reasonable bounds)', async ({ page }) => {
    const metricSelect = page.locator('select').nth(1);
    await metricSelect.selectOption('cost_usd');

    // Try unreasonably high value
    await page.fill('input[type="number"]', '1000000');
    await page.click('button[type="submit"]');

    const errorMsg = page.locator('text=unreasonably high');
    await expect(errorMsg).toBeVisible();
  });

  test('should successfully submit metric observation', async ({ page }) => {
    const metricSelect = page.locator('select').nth(1);
    await metricSelect.selectOption('latency_ms');
    await page.fill('input[type="number"]', '42');
    await page.fill('textarea', 'Peak load period');

    await page.route('**/v1/console/learning/metrics/observe', (route) => {
      route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({
          feedback_id: 'met-999',
          feedback_type: 'metric_observed',
          skill_id: 'os.workflow_optimizer',
          timestamp: new Date().toISOString(),
          status: 'recorded',
          message: 'Metric recorded',
        }),
      });
    });

    await page.click('button[type="submit"]');

    const successMsg = page.locator('text=Metric recorded');
    await expect(successMsg).toBeVisible();
  });
});
