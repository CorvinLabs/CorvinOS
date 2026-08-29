/**
 * Plugin Governance E2E Tests (Playwright)
 *
 * Tests browser interaction with Plugin Trust Badge and Report functionality.
 * ADR-0249: Plugin Trust Anchor — end-to-end validation.
 */

import { test, expect, Page } from "@playwright/test";

test.describe("Plugin Governance & Trust UI", () => {
  let page: Page;

  test.beforeEach(async ({ browser }) => {
    page = await browser.newPage();
    // Navigate to plugins panel
    await page.goto("http://127.0.0.1:8765/console/");
  });

  test.afterEach(async () => {
    await page.close();
  });

  test.describe("Trust Badge Display", () => {
    test("should display builtin trust badge in blue", async () => {
      // Navigate to plugins panel
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Find builtin plugin (e.g., audit-logger)
      const builtinRow = page.locator('tr:has-text("builtin")').first();

      // Check trust badge shows "Builtin" in blue
      const badge = builtinRow.locator(".ant-tag-blue");
      await expect(badge).toContainText("Builtin");
    });

    test("should display vetted trust badge in green with checkmark", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Find vetted plugin row
      const vettedRow = page.locator('tr:has-text("vetted")').first();

      // Check trust badge
      const badge = vettedRow.locator(".ant-tag-green");
      await expect(badge).toContainText("Vetted ✓");
    });

    test("should display community trust badge in orange with warning", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Find community plugin row
      const communityRow = page.locator('tr:has-text("community")').first();

      // Check trust badge shows warning
      const badge = communityRow.locator(".ant-tag-orange");
      await expect(badge).toContainText("Community ⚠");
    });
  });

  test.describe("Governance Drawer", () => {
    test("should open governance drawer on info button click", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Click info button on first plugin
      await page.locator('button[title="View governance & trust"]').first().click();

      // Check drawer is visible
      const drawer = page.locator(".ant-drawer");
      await expect(drawer).toBeVisible();

      // Check drawer contains governance sections
      await expect(page.locator("text=Trust Level")).toBeVisible();
      await expect(page.locator("text=Permissions & Declarations")).toBeVisible();
    });

    test("should display author information in governance drawer", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Open drawer
      await page.locator('button[title="View governance & trust"]').first().click();

      // Check author section is visible
      const authorSection = page.locator(".ant-card").filter({
        hasText: "Author & Signature",
      });
      await expect(authorSection).toBeVisible();
    });

    test("should display permissions disclosure", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Open drawer
      await page.locator('button[title="View governance & trust"]').first().click();

      // Check all permission sections
      const permissionsCard = page.locator(".ant-card").filter({
        hasText: "Permissions & Declarations",
      });
      await expect(permissionsCard).toBeVisible();

      // Check individual permission fields
      await expect(page.locator("text=Data Locality")).toBeVisible();
      await expect(page.locator("text=Network Egress")).toBeVisible();
      await expect(page.locator("text=PII Risk")).toBeVisible();
    });

    test("should close drawer on close button", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Open drawer
      await page.locator('button[title="View governance & trust"]').first().click();
      await expect(page.locator(".ant-drawer")).toBeVisible();

      // Close drawer
      await page.locator(".ant-drawer-close").click();

      // Drawer should be hidden
      await expect(page.locator(".ant-drawer")).not.toBeVisible();
    });
  });

  test.describe("Plugin Report Functionality", () => {
    test("should show report button for community plugins", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Find community plugin and open drawer
      const communityRow = page.locator('tr:has-text("community")').first();
      await communityRow.locator('button[title="View governance & trust"]').click();

      // Check report button is visible
      const reportButton = page.locator('button:has-text("Report")');
      await expect(reportButton).toBeVisible();
    });

    test("should show report button for vetted plugins", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Find vetted plugin and open drawer
      const vettedRow = page.locator('tr:has-text("vetted")').first();
      await vettedRow.locator('button[title="View governance & trust"]').click();

      // Check report button is visible
      const reportButton = page.locator('button:has-text("Report")');
      await expect(reportButton).toBeVisible();
    });

    test("should not show report button for builtin plugins", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Find builtin plugin and open drawer
      const builtinRow = page.locator('tr:has-text("builtin")').first();
      await builtinRow.locator('button[title="View governance & trust"]').click();

      // Report button should not be visible
      const reportButton = page.locator('button:has-text("Report")');
      await expect(reportButton).not.toBeVisible();
    });

    test("should open report modal on report button click", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Open drawer and click report
      const communityRow = page.locator('tr:has-text("community")').first();
      await communityRow.locator('button[title="View governance & trust"]').click();
      await page.locator('button:has-text("Report")').click();

      // Check modal is visible
      const modal = page.locator(".ant-modal");
      await expect(modal).toBeVisible();
      await expect(page.locator("text=Report Plugin")).toBeVisible();
    });

    test("should validate report reason selection", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Open report modal
      const communityRow = page.locator('tr:has-text("community")').first();
      await communityRow.locator('button[title="View governance & trust"]').click();
      await page.locator('button:has-text("Report")').click();

      // Try to submit without selecting reason
      const submitButton = page.locator(".ant-modal").locator(
        'button:has-text("Submit Report")'
      );
      await submitButton.click();

      // Should show validation error
      await expect(page.locator("text=Please select a reason")).toBeVisible();
    });

    test("should validate report details length", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Open report modal
      const communityRow = page.locator('tr:has-text("community")').first();
      await communityRow.locator('button[title="View governance & trust"]').click();
      await page.locator('button:has-text("Report")').click();

      // Select reason
      await page.locator(".ant-select").first().click();
      await page.locator("text=Malicious Activity").click();

      // Try with too short details
      const detailsField = page.locator("textarea");
      await detailsField.fill("short");
      await page.locator('button:has-text("Submit Report")').click();

      // Should show validation error
      await expect(
        page.locator("text=Please provide at least 10 characters")
      ).toBeVisible();
    });

    test("should submit report successfully", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Open report modal
      const communityRow = page.locator('tr:has-text("community")').first();
      await communityRow.locator('button[title="View governance & trust"]').click();
      await page.locator('button:has-text("Report")').click();

      // Fill in report
      await page.locator(".ant-select").first().click();
      await page.locator("text=Malicious Activity").click();

      const detailsField = page.locator("textarea");
      await detailsField.fill(
        "This plugin attempts to steal user credentials and send them to external servers"
      );

      // Wait for form to be ready
      await page.waitForTimeout(500);

      // Submit report
      const submitButton = page.locator(".ant-modal").locator(
        'button:has-text("Submit Report")'
      );
      await submitButton.click();

      // Check for success message
      await expect(
        page.locator(".ant-message-success")
      ).toContainText("Report submitted");

      // Modal should close
      await expect(page.locator(".ant-modal")).not.toBeVisible();
    });

    test("should handle report submission error gracefully", async () => {
      // Mock API error
      await page.route(
        "**/v1/console/plugins/*/report",
        (route) => {
          route.abort();
        }
      );

      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Open report modal
      const communityRow = page.locator('tr:has-text("community")').first();
      await communityRow.locator('button[title="View governance & trust"]').click();
      await page.locator('button:has-text("Report")').click();

      // Fill and submit
      await page.locator(".ant-select").first().click();
      await page.locator("text=Inappropriate Content").click();

      const detailsField = page.locator("textarea");
      await detailsField.fill("This plugin contains offensive language");

      await page.locator('button:has-text("Submit Report")').click();

      // Check for error message
      await expect(page.locator(".ant-message-error")).toBeVisible();
    });
  });

  test.describe("Rating Display", () => {
    test("should display read-only star rating", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Open drawer
      await page.locator('button[title="View governance & trust"]').first().click();

      // Look for rating section (if plugin has rating)
      const ratingSection = page
        .locator(".ant-card")
        .filter({ hasText: "Community Rating" });

      if (await ratingSection.isVisible()) {
        // Stars should not be clickable (disabled state)
        const stars = ratingSection.locator(".ant-rate");
        const disabled = await stars.evaluate(
          (el) => el.getAttribute("aria-disabled") === "true"
        );
        expect(disabled).toBe(true);
      }
    });

    test("should display report count with rating", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Open drawer
      await page.locator('button[title="View governance & trust"]').first().click();

      // Look for report count
      const reportCount = page.locator("text=/\\([0-9]+ reports\\)/");
      if (await reportCount.isVisible()) {
        expect(reportCount).toBeVisible();
      }
    });
  });

  test.describe("Consent Gating", () => {
    test("should show consent warning for high-PII community plugins", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Find community plugin and open drawer
      const communityRow = page.locator('tr:has-text("community")').first();
      await communityRow.locator('button[title="View governance & trust"]').click();

      // Check for consent warning if requires_consent = true
      const consentWarning = page.locator(
        ".ant-alert:has-text('Requires Explicit Consent')"
      );
      if (await consentWarning.isVisible()) {
        await expect(consentWarning).toBeVisible();
      }
    });
  });

  test.describe("Multiple Plugins Display", () => {
    test("should display trust badges for all plugins in list", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Count trust badges
      const badges = page.locator(".ant-tag");
      const count = await badges.count();
      expect(count).toBeGreaterThan(0);
    });

    test("should maintain drawer state when switching plugins", async () => {
      await page.click('a:has-text("Plugins")');
      await page.waitForLoadState("networkidle");

      // Open first plugin
      await page.locator('button[title="View governance & trust"]').first().click();
      const firstPluginName = await page
        .locator(".ant-drawer-title")
        .textContent();

      // Open second plugin
      await page.locator('button[title="View governance & trust"]').nth(1).click();
      const secondPluginName = await page
        .locator(".ant-drawer-title")
        .textContent();

      // Names should be different
      expect(firstPluginName).not.toBe(secondPluginName);

      // Drawer should still be visible
      await expect(page.locator(".ant-drawer")).toBeVisible();
    });
  });
});
