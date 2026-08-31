/**
 * E2E proof: RAG provider licence gate
 *
 * Free tier (no licence key): max 1 RAG provider.
 * Fixture state: one real provider already exists (spotify-charts-elastic.yaml)
 * — this means we start at 1/1 (limit reached).
 *
 * Tests:
 *  1. API confirms rag_providers_max = 1 (Free tier)
 *  2. RAG page — Providers tab shows "1/1" badge
 *  3. RAG page — upgrade banner visible, "Register New" button disabled
 *  4. Backend POST /custom-provider/create → 402 when at limit
 *  5. custom-provider.tsx shows banner when at limit
 *  6. custom-provider.tsx submit button blocked at compliance step
 *
 * Screenshots saved to ./outputs/ (auto-attached to Discord reply).
 */

import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import * as path from "path";
import * as os from "os";

const BASE_URL = "http://localhost:5173/console";
const API_BASE = "http://localhost:8765/v1/console";

const OUTDIR = path.join(
  os.homedir(),
  "projects/CorvinOS/.corvin/tenants/_default/sessions/voice/discord/1502103856740302964/outputs",
);

test.describe.configure({ mode: "serial" });

let ctx: BrowserContext;
let page: Page;
let csrf: string;

async function login(context: BrowserContext): Promise<void> {
  const whoami = await context.request.get(`${API_BASE}/auth/whoami`);
  expect(whoami.status()).toBe(200);
  csrf = (await whoami.json()).csrf_token as string;
}

test.describe("RAG providers — licence gate (Free tier, 1/1 filled)", () => {
  test.beforeAll(async ({ browser }) => {
    ctx = await browser.newContext({
      storageState: "./tests/e2e/auth-state.json",
    });
    await login(ctx);
    page = await ctx.newPage();
  });

  test.afterAll(async () => {
    await ctx?.close();
  });

  // ── 1. License API confirms rag_providers_max = 1 ────────────────────────

  test("1. API confirms Free tier — rag_providers_max = 1", async () => {
    const resp = await ctx.request.get(`${API_BASE}/license/info`);
    const info = await resp.json();

    expect(resp.status()).toBe(200);
    expect(info.tier).toBe("free");
    expect(info.limits?.rag_providers_max).toBe(1);

    await page.goto(`${BASE_URL}/app/rag`, { waitUntil: "load" });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${OUTDIR}/rag-lic-01-free-tier-confirmed.png` });
  });

  // ── 2. RAG page — badge shows 1/1 ────────────────────────────────────────

  test("2. RAG page — Providers tab shows 1/1 limit badge", async () => {
    await page.goto(`${BASE_URL}/app/rag`, { waitUntil: "load" });
    await page.waitForTimeout(1500);

    const badge = page.locator('[data-testid="rag-limit-badge"]');
    await expect(badge).toBeVisible({ timeout: 6000 });
    await expect(badge).toContainText("1/1");

    await page.screenshot({ path: `${OUTDIR}/rag-lic-02-badge-1-of-1.png` });
  });

  // ── 3. RAG page — banner + disabled button ────────────────────────────────

  test("3. RAG page — upgrade banner visible, Register button disabled", async () => {
    // Banner
    const banner = page.locator('[data-testid="rag-limit-banner"]');
    await expect(banner).toBeVisible({ timeout: 4000 });
    await expect(banner).toContainText("RAG provider limit reached");
    await expect(banner).toContainText("1/1");

    // Register New button must be disabled
    const registerBtn = page.locator('[data-testid="rag-register-btn"]');
    await expect(registerBtn).toBeDisabled();
    await expect(registerBtn).toContainText("Limit Reached");

    await page.screenshot({ path: `${OUTDIR}/rag-lic-03-banner-and-disabled-btn.png`, fullPage: true });
  });

  // ── 4. Backend POST → 402 ─────────────────────────────────────────────────

  test("4. Backend POST /custom-provider/create → 402 when at limit", async () => {
    const resp = await ctx.request.post(`${API_BASE}/custom-provider/create`, {
      headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
      data: {
        provider_id: "lic-gate-second-provider",
        name: "Licence Gate Second",
        description: "Should be blocked",
        author: "e2e-test",
        version: "1.0",
        endpoint: "https://example.com/search",
        method: "POST",
        timeout_ms: 5000,
        auth_type: "bearer-token",
        auth_token_env_var: "FAKE_TOKEN",
        query_format_sample: '{"query": "{query}", "limit": {limit}}',
        content_path: "$.results[*].text",
        score_path: "$.results[*].score",
        metadata_path: "",
        source_url_path: "",
        capabilities: ["keyword-search"],
        data_classification: "INTERNAL",
        compliance_zone: "EU",
      },
    });

    expect(resp.status()).toBe(402);
    const body = await resp.json();
    expect(body.detail?.error).toBe("license_limit");
    expect(body.detail?.feature).toBe("rag_providers_max");
    expect(body.detail?.upgrade_url).toContain("corvin-labs.com/pricing");

    await page.screenshot({ path: `${OUTDIR}/rag-lic-04-api-402.png` });
  });

  // ── 5. custom-provider.tsx — banner visible at limit ─────────────────────

  test("5. Custom-provider wizard — banner visible when at limit", async () => {
    await page.goto(`${BASE_URL}/app/custom-provider`, { waitUntil: "load" });
    await page.waitForTimeout(1500);

    const banner = page.locator('[data-testid="custom-provider-limit-banner"]');
    await expect(banner).toBeVisible({ timeout: 6000 });
    await expect(banner).toContainText("RAG provider limit reached");
    await expect(banner).toContainText("1/1");

    await page.screenshot({ path: `${OUTDIR}/rag-lic-05-wizard-banner.png`, fullPage: true });
  });

  // ── 6. custom-provider.tsx — submit button blocked at compliance step ─────

  test("6. Custom-provider wizard — submit button blocked at compliance step", async () => {
    // Navigate to step 4 (compliance) by filling required fields
    await page.fill('input[id="provider_id"]', "block-me-test");
    await page.fill('input[id="name"]', "Blocked Provider");
    await page.click('button:has-text("Next")');
    await page.waitForTimeout(400);

    await page.fill('input[id="endpoint"]', "https://example.com/search");
    await page.click('button:has-text("Next")');
    await page.waitForTimeout(400);

    await page.fill('input[id="content_path"]', "$.results[*].text");
    await page.fill('input[id="score_path"]', "$.results[*].score");
    await page.click('button:has-text("Next")');
    await page.waitForTimeout(400);

    // Step 4: compliance — submit button must be "Limit Reached" and disabled
    const submitBtn = page.locator('button:has-text("Limit Reached")');
    await expect(submitBtn).toBeVisible({ timeout: 4000 });
    await expect(submitBtn).toBeDisabled();

    await page.screenshot({ path: `${OUTDIR}/rag-lic-06-wizard-submit-blocked.png`, fullPage: true });
  });
});
