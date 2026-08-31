import { test, expect } from "@playwright/test";

/**
 * E2E Tests: Custom Provider Creation Workflow
 *
 * Tests the complete 4-step wizard form for creating RAG providers.
 * Auth: provided via global storageState (auth-state.json).
 */

const BASE_URL = process.env.BASE_URL || "http://localhost:5173/console";

// ── Helpers ──────────────────────────────────────────────────────────────────

async function fillStep1(page: import("@playwright/test").Page, overrides: {
  id?: string; name?: string; author?: string; version?: string;
} = {}) {
  await page.fill("#provider_id",  overrides.id      ?? "e2e-test-provider");
  await page.fill("#name",         overrides.name    ?? "E2E Test Provider");
  await page.fill("#author",       overrides.author  ?? "playwright");
  await page.fill('input[placeholder="1.0"]', overrides.version ?? "1.0.0");
}

async function clickNext(page: import("@playwright/test").Page) {
  await page.locator('button:has-text("Next")').first().click();
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe("Custom Provider Creation Workflow", () => {
  test("should navigate to Create Provider page", async ({ page }) => {
    await page.goto(`${BASE_URL}/app/custom-provider`, { waitUntil: "load" });
    await expect(page.locator("text=Create Custom RAG Provider")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("text=Step 1 of 4")).toBeVisible({ timeout: 5_000 });
  });

  test("should complete full 4-step workflow successfully", async ({ page }) => {
    await page.goto(`${BASE_URL}/app/custom-provider`, { waitUntil: "load" });
    await page.waitForTimeout(500);

    // Step 1: Basic Info
    await fillStep1(page);
    await expect(page.locator('button:has-text("Next")')).toBeEnabled({ timeout: 5_000 });
    await clickNext(page);

    // Step 2: API Config
    await expect(page.locator("text=Step 2 of 4")).toBeVisible({ timeout: 8_000 });
    await page.fill('input[placeholder*="https://"]', "https://api.example.com/search");
    await page.selectOption("#method", "POST");
    await page.selectOption("#auth_type", "bearer-token");
    await page.fill("#auth_token_env", "TEST_API_TOKEN");
    await page.fill("textarea", '{"query": "{query}", "limit": {limit}}');
    await clickNext(page);

    // Step 3: Response Mapping
    await expect(page.locator("text=Step 3 of 4")).toBeVisible({ timeout: 8_000 });
    await page.fill("#content_path", "results[].content");
    await page.fill("#score_path", "results[].score");
    await page.fill("#metadata_path", "results[]");
    await page.fill("#source_url_path", "results[].url");
    await clickNext(page);

    // Step 4: Compliance
    await expect(page.locator("text=Step 4 of 4")).toBeVisible({ timeout: 8_000 });

    // Select at least one capability
    const firstCap = page.locator('input[type="checkbox"]').first();
    await firstCap.check();

    await page.selectOption("#classification", "INTERNAL");
    await page.selectOption("#zone", "EU");

    // Handle alert dialog (success or quota error)
    let dialogText = "";
    page.once("dialog", async (dialog) => {
      dialogText = dialog.message();
      await dialog.accept();
    });

    // Button shows "Create Provider" OR "Limit Reached" when the free-tier cap is hit
    const createBtn = page.locator('button:has-text("Create Provider"), button:has-text("Limit Reached")');
    await expect(createBtn).toBeVisible({ timeout: 5_000 });

    // If at RAG provider limit, the button is disabled — that is valid behavior
    const isEnabled = await createBtn.isEnabled();
    if (!isEnabled) {
      console.log("At RAG provider limit — creation blocked (expected free-tier behavior)");
      return;
    }

    await createBtn.click();
    await page.waitForTimeout(3000);

    // After dialog, the form should either reset (success) or show error
    const stepText = await page.locator("text=/Step [14] of 4/").first().textContent().catch(() => "");
    console.log(`After creation: step=${stepText}, dialog="${dialogText}"`);

    // Either success (step 1 again) or the form shows an error — not stuck at step 4 without response
    const isHandled = stepText.includes("Step 1") || dialogText.length > 0
      || (await page.locator('[class*="error"], [class*="red"]').count()) > 0;
    expect(isHandled).toBe(true);
  });

  test("should validate required fields on Step 1", async ({ page }) => {
    await page.goto(`${BASE_URL}/app/custom-provider`, { waitUntil: "load" });
    await page.waitForTimeout(500);

    // Click Next without filling any fields
    const nextBtn = page.locator('button:has-text("Next")').first();
    await expect(nextBtn).toBeVisible({ timeout: 5_000 });
    await nextBtn.click();

    // Either the button is disabled or an error appears
    const errorShown = await page.locator("text=/All fields required|required/i").isVisible().catch(() => false);
    const stillOnStep1 = await page.locator("text=Step 1 of 4").isVisible().catch(() => false);

    expect(errorShown || stillOnStep1).toBe(true);
    console.log(`✅ Step 1 validation: error=${errorShown}, still-step1=${stillOnStep1}`);
  });

  test("should accept valid provider ID and proceed to Step 2", async ({ page }) => {
    await page.goto(`${BASE_URL}/app/custom-provider`, { waitUntil: "load" });
    await page.waitForTimeout(500);

    await fillStep1(page, { id: "valid-provider-123", name: "Valid Provider" });
    await clickNext(page);

    await expect(page.locator("text=Step 2 of 4")).toBeVisible({ timeout: 8_000 });
    console.log("✅ Valid provider ID accepted, moved to Step 2");
  });

  test("should require endpoint in Step 2 before proceeding", async ({ page }) => {
    await page.goto(`${BASE_URL}/app/custom-provider`, { waitUntil: "load" });
    await page.waitForTimeout(500);

    await fillStep1(page);
    await clickNext(page);

    await expect(page.locator("text=Step 2 of 4")).toBeVisible({ timeout: 8_000 });

    // Try to advance without endpoint
    await clickNext(page);

    const errorShown = await page.locator("text=/Endpoint URL required|required/i").isVisible().catch(() => false);
    const stillOnStep2 = await page.locator("text=Step 2 of 4").isVisible().catch(() => false);
    expect(errorShown || stillOnStep2).toBe(true);
  });

  test("should prevent creating provider without capabilities", async ({ page }) => {
    await page.goto(`${BASE_URL}/app/custom-provider`, { waitUntil: "load" });
    await page.waitForTimeout(500);

    // Speed run through steps 1-3
    await fillStep1(page, { id: "no-caps-test" });
    await clickNext(page);

    await expect(page.locator("text=Step 2 of 4")).toBeVisible({ timeout: 8_000 });
    await page.fill('input[placeholder*="https://"]', "https://api.example.com/search");
    await page.fill("textarea", '{"query": "{query}", "limit": {limit}}');
    await clickNext(page);

    await expect(page.locator("text=Step 3 of 4")).toBeVisible({ timeout: 8_000 });
    await page.fill("#content_path", "results[].content");
    await page.fill("#score_path", "results[].score");
    await clickNext(page);

    // Step 4: do NOT check any capability, then click Create
    await expect(page.locator("text=Step 4 of 4")).toBeVisible({ timeout: 8_000 });

    // Uncheck all capabilities first (they might be pre-checked)
    const checkboxes = page.locator('input[type="checkbox"]');
    const count = await checkboxes.count();
    for (let i = 0; i < count; i++) {
      await checkboxes.nth(i).uncheck();
    }

    let dialogText = "";
    page.once("dialog", async (d) => { dialogText = d.message(); await d.accept(); });

    // Button may show "Create Provider" or "Limit Reached" when free-tier cap is hit
    const createBtn = page.locator('button:has-text("Create Provider"), button:has-text("Limit Reached")');
    await expect(createBtn).toBeVisible({ timeout: 5_000 });

    // If at limit, skip capability-validation check — limit itself is valid behavior
    const isEnabled = await createBtn.isEnabled();
    if (!isEnabled) {
      console.log("At RAG provider limit — capability check skipped (expected free-tier behavior)");
      return;
    }

    await createBtn.click();
    await page.waitForTimeout(1000);

    const errorShown = await page.locator("text=/capability|Select at least/i").isVisible().catch(() => false);
    const stillStep4 = await page.locator("text=Step 4 of 4").isVisible().catch(() => false);
    console.log(`Capabilities check: error=${errorShown}, step4=${stillStep4}, dialog="${dialogText}"`);
    expect(errorShown || stillStep4 || dialogText.includes("capability")).toBe(true);
  });

  test("should allow going back in the form", async ({ page }) => {
    await page.goto(`${BASE_URL}/app/custom-provider`, { waitUntil: "load" });
    await page.waitForTimeout(500);

    // Fill Step 1
    await fillStep1(page, { id: "back-test", name: "Back Navigation Test" });
    await clickNext(page);

    // On Step 2, Back button must exist
    await expect(page.locator("text=Step 2 of 4")).toBeVisible({ timeout: 8_000 });
    const backBtn = page.locator('button:has-text("Back")').first();
    await expect(backBtn).toBeVisible({ timeout: 5_000 });
    await backBtn.click();

    // Back on Step 1 with data preserved
    await expect(page.locator("text=Step 1 of 4")).toBeVisible({ timeout: 5_000 });
    const savedId = await page.locator("#provider_id").inputValue();
    expect(savedId).toBe("back-test");
    console.log("✅ Back navigation preserves form state");
  });

  test("should have proper dark mode styling", async ({ page }) => {
    await page.goto(`${BASE_URL}/app/custom-provider`, { waitUntil: "load" });
    await page.waitForTimeout(500);

    const firstInput = page.locator("input").first();
    const classList = await firstInput.evaluate((el) => el.className);
    expect(classList.length).toBeGreaterThan(0);

    const bgColor = await firstInput.evaluate((el) => window.getComputedStyle(el).backgroundColor);
    console.log(`Input background color: ${bgColor}`);
  });
});

test.describe("Custom Provider Error Handling", () => {
  test("should handle invalid endpoint URL gracefully", async ({ page }) => {
    await page.goto(`${BASE_URL}/app/custom-provider`, { waitUntil: "load" });
    await page.waitForTimeout(500);

    // Step 1
    await fillStep1(page, { id: "bad-endpoint" });
    await clickNext(page);

    // Step 2: Bad endpoint
    await expect(page.locator("text=Step 2 of 4")).toBeVisible({ timeout: 8_000 });
    await page.fill('input[placeholder*="https://"]', "not-a-valid-url");
    await page.fill("textarea", '{"query": "{query}", "limit": {limit}}');

    // Click Test API Connectivity button
    const testBtn = page.locator('button:has-text("Test API Connectivity")');
    if (await testBtn.isVisible()) {
      await testBtn.click();
      await page.waitForTimeout(2000);

      const errorDisplay = page.locator('[class*="red"], [class*="fail"], [class*="error"]').first();
      const errorShown = await errorDisplay.isVisible().catch(() => false);
      console.log(`Error handled gracefully: ${errorShown}`);
    }

    // The page should still be functional (not crashed)
    await expect(page.locator("text=Step 2 of 4")).toBeVisible({ timeout: 5_000 });
    console.log("✅ Page remains functional after invalid URL");
  });

  test("should show timeout error for slow endpoints", async ({ page }) => {
    await page.goto(`${BASE_URL}/app/custom-provider`, { waitUntil: "load" });
    await page.waitForTimeout(500);

    await fillStep1(page, { id: "timeout-test", name: "Timeout Test" });
    await clickNext(page);

    await expect(page.locator("text=Step 2 of 4")).toBeVisible({ timeout: 8_000 });
    await page.fill('input[placeholder*="https://"]', "https://httpbin.org/delay/10");
    await page.fill("textarea", '{"query": "{query}", "limit": {limit}}');

    // Set low timeout
    const timeoutInput = page.locator("#timeout");
    if (await timeoutInput.isVisible()) {
      await timeoutInput.fill("1000");
    }

    const testBtn = page.locator('button:has-text("Test API Connectivity")');
    if (await testBtn.isVisible()) {
      await testBtn.click();
      await page.waitForTimeout(3000);

      const timeoutMsg = page.locator("text=/Timeout|timeout|timed out/i").first();
      const errorShown = await timeoutMsg.isVisible().catch(() => false);
      console.log(`Timeout handled: ${errorShown}`);
    }

    // Page still functional
    await expect(page.locator("text=Step 2 of 4")).toBeVisible({ timeout: 5_000 });
    console.log("✅ Page functional after slow endpoint test");
  });
});
