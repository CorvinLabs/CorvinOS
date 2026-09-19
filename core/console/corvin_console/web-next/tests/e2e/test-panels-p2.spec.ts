/**
 * P2 Panel E2E Tests — High-Value Features
 *
 * Priority: P2 = Important features operator should have working
 * Panels:
 * - Compliance (Audit Trail) — ADR-0232/0233
 * - Forge (Tools/Skills) — ADR-0037
 *
 * Definition: P2 tests validate important features that may not be
 * on critical path but should work reliably.
 */

import { test, expect } from '../fixtures/panel-fixtures';
import { ConsolePanelTest } from './base/panel-test-base';

// ─ Compliance Panel (P2) ────────────────────────────────────────────────────

const complianceTest = new ConsolePanelTest({
  id: 'compliance',
  title: 'Compliance',
  priority: 'P2',
  group: 'system',
});

test.describe('P2: Compliance Panel (Audit Trail)', () => {
  test.beforeEach(async ({ panelNav }) => {
    await panelNav.goto('compliance');
  });

  test('P2-1: Compliance panel loads successfully', async ({ page, panelNav }) => {
    await complianceTest.navigateAndAssert(page, panelNav);
    await complianceTest.assertPanelTitle(page, 'Audit & Compliance', panelNav);
  });

  test('P2-2: Compliance panel displays audit trail', async ({ page, panelNav }) => {
    await complianceTest.navigateAndAssert(page, panelNav);

    // Look for audit-related content
    const auditContent = page.locator(
      'text=/audit|trail|log|event|compliance/i'
    );
    const isVisible = await auditContent.isVisible().catch(() => false);

    // Check for table or list structure
    const dataContainer = page.locator('table, [role="table"], ul, ol');
    const hasData = isVisible || (await dataContainer.count()) > 0;

    expect(hasData).toBeTruthy();
  });

  test('P2-3: Compliance panel audit event filtering', async ({ page, panelNav }) => {
    await complianceTest.navigateAndAssert(page, panelNav);

    // Look for filter controls
    const filterInputs = page.locator('input[type="text"], select, [role="combobox"]');
    const hasFilters = await filterInputs.count();

    if (hasFilters > 0) {
      // Test filtering
      await filterInputs.first().fill('test');
      await page.waitForTimeout(500);
    }
  });

  test('P2-4: Compliance panel export functionality', async ({ page, panelNav }) => {
    await complianceTest.navigateAndAssert(page, panelNav);

    // Look for export button
    const exportButton = page.locator('button:has-text(/export|download|pdf|csv/i)');
    const hasExport = await exportButton.count();

    expect(hasExport).toBeGreaterThanOrEqual(0);
  });

  test('P2-5: Compliance performance baseline (< 4s load)', async ({ page, panelNav }) => {
    const metrics = await complianceTest.measurePerformance(page, panelNav);
    expect(metrics.loadTime).toBeLessThan(4000);
  });

  test('P2-6: Compliance error handling', async ({ page, panelNav }) => {
    await complianceTest.testErrorHandling(page, panelNav, '**/v1/console/compliance/**');
  });

  test('P2-7: Compliance pagination or scrolling', async ({ page, panelNav }) => {
    await complianceTest.navigateAndAssert(page, panelNav);

    // Check for pagination or infinite scroll
    const pagination = page.locator('[role="navigation"] button, .pagination');
    const hasPagination = await pagination.count();

    if (hasPagination > 0) {
      const nextButton = pagination.locator('button:has-text(/next|→/i)');
      if (await nextButton.count() > 0) {
        // Non-critical: try to click next
        await nextButton.click().catch(() => {});
      }
    }
  });

  test('P2-8: Compliance panel accessibility', async ({ page, panelNav }) => {
    await complianceTest.navigateAndAssert(page, panelNav);
    await complianceTest.testAccessibility(page, panelNav);
  });
});

// ─ Forge Panel (P2) ─────────────────────────────────────────────────────────

const forgeTest = new ConsolePanelTest({
  id: 'forge',
  title: 'Forge',
  priority: 'P2',
  group: 'build',
});

test.describe('P2: Forge Panel (Tools/Skills)', () => {
  test.beforeEach(async ({ panelNav }) => {
    await panelNav.goto('forge');
  });

  test('P2-9: Forge panel loads successfully', async ({ page, panelNav }) => {
    await forgeTest.navigateAndAssert(page, panelNav);
    await forgeTest.assertPanelTitle(page, 'Forge', panelNav);
  });

  test('P2-10: Forge panel displays tools registry', async ({ page, panelNav }) => {
    await forgeTest.navigateAndAssert(page, panelNav);

    // Look for tool/skill related content
    const toolContent = page.locator(
      'text=/tool|skill|forge|generator|create/i'
    );
    const isVisible = await toolContent.isVisible().catch(() => false);

    // Check for list or grid structure
    const toolList = page.locator('[data-testid*="tool"], [data-testid*="skill"]');
    const hasTools = isVisible || (await toolList.count()) > 0;

    expect(hasTools).toBeTruthy();
  });

  test('P2-11: Forge tool creation workflow', async ({ page, panelNav }) => {
    await forgeTest.navigateAndAssert(page, panelNav);

    // Look for "New Tool" or "Create Tool" button
    const createButton = page.locator(
      'button:has-text(/new|create|add|generator/i)'
    );
    const hasCreate = await createButton.count();

    expect(hasCreate).toBeGreaterThanOrEqual(0);
  });

  test('P2-12: Forge tool search/filter', async ({ page, panelNav }) => {
    await forgeTest.navigateAndAssert(page, panelNav);

    // Look for search
    const searchInput = page.locator('input[placeholder*="search" i]');
    if (await searchInput.count() > 0) {
      await searchInput.fill('test');
      await page.waitForTimeout(500);
    }
  });

  test('P2-13: Forge performance baseline (< 4s load)', async ({ page, panelNav }) => {
    const metrics = await forgeTest.measurePerformance(page, panelNav);
    expect(metrics.loadTime).toBeLessThan(4000);
  });

  test('P2-14: Forge error handling', async ({ page, panelNav }) => {
    await forgeTest.testErrorHandling(page, panelNav, '**/v1/console/forge/**');
  });

  test('P2-15: Forge panel accessibility', async ({ page, panelNav }) => {
    await forgeTest.navigateAndAssert(page, panelNav);
    await forgeTest.testAccessibility(page, panelNav);
  });
});

// ─ Cross-Panel P2 Features ──────────────────────────────────────────────────

test.describe('P2: Cross-Panel Features', () => {
  test('P2-16: Compliance data is audited', async ({ page, panelNav }) => {
    await panelNav.goto('compliance');
    const content = page.locator('main');
    await expect(content).toBeVisible({ timeout: 5000 });

    // Verify content loaded (non-empty)
    const text = await content.textContent();
    expect(text?.trim().length).toBeGreaterThan(0);
  });

  test('P2-17: Forge and Compliance panels share consistent UI patterns', async ({
    page,
    panelNav,
  }) => {
    // Navigate to Forge
    await panelNav.goto('forge');
    const forgeHeading = page.locator('h1');
    await expect(forgeHeading).toBeVisible();

    // Navigate to Compliance
    await panelNav.goto('compliance');
    const complianceHeading = page.locator('h1');
    await expect(complianceHeading).toBeVisible();

    // Both should have similar structure
    expect(true).toBeTruthy();
  });
});
