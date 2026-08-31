/**
 * E2E proof: Data Sources licence gate
 *
 * Free tier (no licence key):
 *   - Wizard shows PostgreSQL / MySQL adapter buttons LOCKED (LicenseGate overlay)
 *   - local_file button is UNLOCKED and navigates to the form
 *   - Backend POST for a non-local_file adapter → HTTP 402 Payment Required
 *   - Backend POST for local_file             → HTTP 201 Created
 *
 * Screenshots saved to ./outputs/ and automatically attached to the Discord reply.
 */

import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import path from "path";
import os from "os";

const BASE_URL = "http://localhost:5173/console";
const API_BASE  = "http://localhost:8765/v1/console";

const OUTDIR = path.join(
  os.homedir(),
  "projects/CorvinOS/.corvin/tenants/_default/sessions/voice/discord/1502103856740302964/outputs",
);

// ── Auth helpers ──────────────────────────────────────────────────────────────

test.describe.configure({ mode: "serial" });

let ctx: BrowserContext;
let page: Page;
let csrf: string;

async function login(context: BrowserContext): Promise<void> {
  const whoami = await context.request.get(`${API_BASE}/auth/whoami`);
  expect(whoami.status()).toBe(200);
  csrf = (await whoami.json()).csrf_token as string;
}

async function apiDelete(name: string): Promise<void> {
  await ctx.request.delete(`${API_BASE}/data-sources/${name}`, {
    headers: { "X-CSRF-Token": csrf },
  });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe("Data Sources — licence gate (Free tier)", () => {

  test.beforeAll(async ({ browser }) => {
    ctx = await browser.newContext({
      storageState: "./tests/e2e/auth-state.json",
    });
    await login(ctx);
    await apiDelete("lic-gate-test-localfile").catch(() => {});
    page = await ctx.newPage();
    await page.goto(`${BASE_URL}/app/data-sources`, { waitUntil: "load" });
    await page.waitForTimeout(1800);
  });

  test.afterAll(async () => {
    await apiDelete("lic-gate-test-localfile").catch(() => {});
    await ctx?.close();
  });

  // ── 1. Confirm current licence is Free (no key) ──────────────────────────

  test("1. API confirms Free tier — datasource_adapters_allowed = [local_file]", async () => {
    const resp = await ctx.request.get(`${API_BASE}/license/info`);
    const info = await resp.json();

    expect(resp.status()).toBe(200);
    expect(info.tier).toBe("free");

    // datasource_adapters_allowed must be the array ["local_file"]
    const allowed = info.limits?.datasource_adapters_allowed;
    expect(Array.isArray(allowed)).toBe(true);
    expect(allowed).toContain("local_file");
    expect(allowed).not.toContain("postgresql");
    expect(allowed).not.toContain("mysql");

    await page.screenshot({ path: `${OUTDIR}/lic-01-free-tier-confirmed.png` });
  });

  // ── 2. UI: wizard opens, non-local adapters are locked ───────────────────

  test("2. Connect wizard — PostgreSQL tile is locked with LicenseGate", async () => {
    await page.click('[data-testid="connect-database-btn"]');
    await page.waitForTimeout(800);

    // The LicenseGate wrapper for postgresql should be present
    const gate = page.locator('[data-testid="db-type-locked-postgresql"]');
    await expect(gate).toBeVisible({ timeout: 6000 });

    // The button inside is pointer-events-none (not directly clickable)
    const pgBtn = page.locator('[data-testid="db-type-postgresql"]');
    await expect(pgBtn).toBeVisible();

    // Pricing link is visible (not a centre overlay — just a small link below the tile name)
    await expect(gate.locator('text=Member plan required')).toBeVisible();

    await page.screenshot({ path: `${OUTDIR}/lic-02-wizard-pg-locked.png` });
  });

  test("3. Connect wizard — MySQL tile is also locked", async () => {
    const gate = page.locator('[data-testid="db-type-locked-mysql"]');
    await expect(gate).toBeVisible({ timeout: 4000 });
    await expect(gate.locator('text=Member plan required')).toBeVisible();

    await page.screenshot({ path: `${OUTDIR}/lic-03-wizard-mysql-locked.png` });
  });

  test("4. Connect wizard — local_file tile is NOT locked (freely clickable)", async () => {
    const gate = page.locator('[data-testid="db-type-locked-local_file"]');
    await expect(gate).not.toBeVisible();

    const tile = page.locator('[data-testid="db-type-local_file"]');
    await expect(tile).toBeVisible();

    // Click it — should open the form (no lock, no override needed)
    await tile.click();
    await page.waitForTimeout(500);

    // Form should appear (back button visible)
    await expect(page.locator('button:has-text("Cancel")')).toBeVisible({ timeout: 4000 });

    await page.screenshot({ path: `${OUTDIR}/lic-04-local-file-form-open.png` });

    // Close wizard
    await page.click('button:has-text("Cancel")');
    await page.waitForTimeout(400);
    // Close dialog
    const closeBtn = page.locator('[aria-label="Close"], button[data-state]').first();
    await closeBtn.click().catch(() => page.keyboard.press("Escape"));
    await page.waitForTimeout(400);
  });

  // ── 3. Backend gate: direct API calls ────────────────────────────────────

  test("5. Backend POST postgresql → 402 Payment Required", async () => {
    const resp = await ctx.request.post(`${API_BASE}/data-sources`, {
      headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
      data: {
        manifest: {
          dsi_version: "1",
          name: "lic-gate-test-pg",
          adapter: "postgresql",
          config: { host: "localhost", port: "5433", database: "spotify_dsi" },
          credentials: [],
          data_classification: "INTERNAL",
          data_residency: "any",
          tags: [],
          description: "Licence gate test",
        },
      },
    });

    expect(resp.status()).toBe(402);
    const body = await resp.json();
    expect(body.detail?.error).toBe("license_limit");
    expect(body.detail?.feature).toBe("datasource_adapters_allowed");
    expect(body.detail?.adapter).toBe("postgresql");

    await page.screenshot({ path: `${OUTDIR}/lic-05-api-pg-402.png` });
  });

  test("6. Backend POST mysql → 402 Payment Required", async () => {
    const resp = await ctx.request.post(`${API_BASE}/data-sources`, {
      headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
      data: {
        manifest: {
          dsi_version: "1",
          name: "lic-gate-test-mysql",
          adapter: "mysql",
          config: { host: "127.0.0.1", port: "3307", database: "spotify_dsi" },
          credentials: [],
          data_classification: "INTERNAL",
          data_residency: "any",
          tags: [],
          description: "Licence gate test",
        },
      },
    });

    expect(resp.status()).toBe(402);
    const body = await resp.json();
    expect(body.detail?.adapter).toBe("mysql");

    await page.screenshot({ path: `${OUTDIR}/lic-06-api-mysql-402.png` });
  });

  test("7. Backend POST local_file → 201 Created (always allowed on Free)", async () => {
    const resp = await ctx.request.post(`${API_BASE}/data-sources`, {
      headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
      data: {
        manifest: {
          dsi_version: "1",
          name: "lic-gate-test-localfile",
          adapter: "local_file",
          config: {
            path: `${os.homedir()}/projects/claude-playground/spotify-dsi-test/spotify_charts.db`,
            format: "auto",
          },
          credentials: [],
          data_classification: "INTERNAL",
          data_residency: "local",
          tags: [],
          description: "Licence gate test — local file always allowed",
        },
      },
    });

    expect(resp.status()).toBe(201);
    const body = await resp.json();
    expect(body.name).toBe("lic-gate-test-localfile");

    // Verify it appears in the list
    await page.reload({ waitUntil: "load" });
    await page.waitForTimeout(1200);
    await expect(page.locator('[data-testid="conn-row-lic-gate-test-localfile"]')).toBeVisible({ timeout: 6000 });

    await page.screenshot({ path: `${OUTDIR}/lic-07-localfile-201-in-list.png`, fullPage: true });
  });

  // ── 4. Final proof screenshot ─────────────────────────────────────────────

  test("8. Final proof — locked wizard + allowed local_file registered", async () => {
    // Re-open wizard to show the locked state one more time
    await page.click('[data-testid="connect-database-btn"]');
    await page.waitForTimeout(800);

    // Multiple locked tiles visible simultaneously
    await expect(page.locator('[data-testid="db-type-locked-postgresql"]')).toBeVisible();
    await expect(page.locator('[data-testid="db-type-locked-mysql"]')).toBeVisible();
    // local_file is NOT locked
    await expect(page.locator('[data-testid="db-type-locked-local_file"]')).not.toBeVisible();

    await page.screenshot({ path: `${OUTDIR}/lic-08-final-proof.png`, fullPage: true });

    // Close wizard
    await page.keyboard.press("Escape");
  });
});
