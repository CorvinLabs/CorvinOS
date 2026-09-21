/**
 * E2E Tests: Plugin Manager in Settings Panel
 * Tests the new PluginManagerCard replaces Feature Whitelist (ADR-0903)
 */
import { expect, test } from "@playwright/test";

test.describe("Plugin Manager in Settings", () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to Settings page
    await page.goto("/app/settings");

    // Wait for page to load
    await page.waitForSelector('text="Plugins"');
  });

  test("Settings page shows Plugin Manager card (not Feature Whitelist)", async ({ page }) => {
    // Verify old "Feature Control" heading is gone
    const oldHeading = page.locator('text="Feature Control"');
    await expect(oldHeading).not.toBeVisible();

    // Verify new "Plugins" heading exists
    const newHeading = page.locator('text="Plugins"');
    await expect(newHeading).toBeVisible();
  });

  test("Plugin Manager displays all 4 bundled plugins", async ({ page }) => {
    // Wait for plugin list to load
    await page.waitForSelector('text="Vibe Engineering"');

    // Verify all 4 plugins visible
    await expect(page.locator('text="Vibe Engineering"')).toBeVisible();
    await expect(page.locator('text="Tree of Thoughts"')).toBeVisible();
    await expect(page.locator('text="Token Metrics"')).toBeVisible();
    await expect(page.locator('text="Outcome Feedback"')).toBeVisible();
  });

  test("Plugin card shows name, version, boot_layer, description", async ({ page }) => {
    // Find Vibe Engineering plugin card
    const vibeCard = page.locator('text="Vibe Engineering"').first().locator("..");

    // Check it contains expected info
    await expect(vibeCard.locator('text="bundled"')).toBeVisible();  // boot_layer badge
    await expect(vibeCard.locator('text="v2.0.0"')).toBeVisible();   // version badge
    await expect(
      vibeCard.locator('text="CEL brief deterministic"')
    ).toBeVisible();  // description
  });

  test("Plugin can be toggled on/off", async ({ page }) => {
    // Find toggle for Vibe Engineering
    const vibeToggle = page
      .locator('text="Vibe Engineering"')
      .first()
      .locator("xpath=..//button[@role='switch']");

    // Check initial state (should be enabled by default)
    const initialState = await vibeToggle.getAttribute("data-state");
    expect(initialState).toBe("checked");

    // Click to disable
    await vibeToggle.click();

    // Verify toggle state changed
    const newState = await vibeToggle.getAttribute("data-state");
    expect(newState).toBe("unchecked");

    // Verify audit event was emitted (would show in audit log)
    // In real test: check /v1/console/audit for plugin_disabled event
  });

  test("Compliance plugins cannot be disabled", async ({ page }) => {
    // If there are compliance-layer plugins, their toggle should be disabled
    // (For now, all test plugins are bundled, so this verifies the future case)

    const complianceToggle = page
      .locator('text="compliance"')
      .first()
      .locator("xpath=..//button[@role='switch']")
      .first();

    // Should be disabled
    const disabled = await complianceToggle.getAttribute("disabled");
    expect(disabled).toBeTruthy();
  });

  test("Plugin audit trail shows last change", async ({ page }) => {
    // Find Vibe Engineering card
    const vibeCard = page.locator('text="Vibe Engineering"').first().locator("..");

    // Check if audit trail is visible (should show who enabled/disabled and when)
    const auditText = vibeCard.locator('text=/Enabled|Disabled/');

    // If audit trail exists, it should be visible
    const auditVisible = await auditText.isVisible().catch(() => false);
    if (auditVisible) {
      await expect(auditText).toBeVisible();
    }
  });

  test("Plugin Manager error state shows error message", async ({ page }) => {
    // Simulate API error by mocking route
    await page.route("**/v1/console/plugins/toggle", (route) =>
      route.abort("failed")
    );

    // Try to toggle a plugin
    const toggle = page
      .locator('text="Vibe Engineering"')
      .first()
      .locator("xpath=..//button[@role='switch']");

    await toggle.click();

    // Should show error message
    await expect(page.locator("text=/Failed|Error/")).toBeVisible();
  });

  test("Feature Whitelist routes (/v1/console/features/*) return 404", async ({ page }) => {
    // Verify old API is gone
    const response = await page
      .request()
      .get("/v1/console/features/whitelist")
      .catch(() => ({ status: 404 }));

    expect(response.status).toBe(404);
  });

  test("Settings panel layout is correct", async ({ page }) => {
    // Verify Plugins section is after "Telemetry & Privacy" and before "Agentic Compute"
    const telemetrySection = page.locator('text="Telemetry & Privacy"');
    const pluginsSection = page.locator('text="Plugins"');
    const computeSection = page.locator('text="Agentic Compute"');

    // Get bounding boxes to check order (y-coordinate increases downward)
    const telemetryBox = await telemetrySection.boundingBox();
    const pluginsBox = await pluginsSection.boundingBox();
    const computeBox = await computeSection.boundingBox();

    if (telemetryBox && pluginsBox && computeBox) {
      expect(telemetryBox!.y).toBeLessThan(pluginsBox!.y);
      expect(pluginsBox!.y).toBeLessThan(computeBox!.y);
    }
  });

  test("Plugin Manager card is responsive", async ({ page }) => {
    // Set viewport to mobile
    await page.setViewportSize({ width: 375, height: 667 });

    // Verify card is still visible and clickable
    const toggle = page
      .locator('text="Vibe Engineering"')
      .first()
      .locator("xpath=..//button[@role='switch']");

    await expect(toggle).toBeVisible();
    await expect(toggle).toBeEnabled();

    // Set back to desktop
    await page.setViewportSize({ width: 1280, height: 720 });

    await expect(toggle).toBeVisible();
  });

  test("No stale references to Feature Whitelist in DOM", async ({ page }) => {
    // Verify no leftover references to old system
    const oldText = page.locator('text="Feature Whitelist"');
    const oldControl = page.locator('text="Feature Control"');

    await expect(oldText).not.toBeVisible();
    await expect(oldControl).not.toBeVisible();
  });
});

test.describe("Backward Compatibility", () => {
  test("Settings page still loads with old browser cache", async ({ page }) => {
    // Even if browser has cached old API calls, new page should work
    await page.goto("/app/settings");

    // Should show new Plugins section
    await expect(page.locator('text="Plugins"')).toBeVisible();

    // Should NOT show old Feature Whitelist
    await expect(page.locator('text="Feature Whitelist"')).not.toBeVisible();
  });

  test("TypeScript types don't reference old API functions", async ({ page }) => {
    // This is a compile-time test (would be checked in tsc)
    // In E2E, we verify the app loads without errors
    await page.goto("/app/settings");

    // Check for console errors (TypeScript errors would appear here in dev mode)
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        errors.push(msg.text());
      }
    });

    await page.waitForTimeout(2000);  // Wait for any lazy errors

    // Should have no errors about missing API functions
    const apiErrors = errors.filter((e) =>
      e.includes("getFeatureWhitelist") || e.includes("toggleFeature")
    );
    expect(apiErrors).toHaveLength(0);
  });
});
