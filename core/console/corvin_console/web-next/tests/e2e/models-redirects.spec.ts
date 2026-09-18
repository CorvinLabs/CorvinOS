/**
 * ADR-0885 step 3 — the three old console paths redirect to the Models
 * console tabs, and the console is READ-ONLY under this spec: it never seeds
 * a record or changes a pin on the live host.
 *
 * Run: npx playwright test tests/e2e/models-redirects.spec.ts --project=chromium
 */
import { expect, test } from "@playwright/test";

// One live console, one session: run these in order rather than in parallel
// workers that race each other on the same host.
test.describe.configure({ mode: "serial" });
// Start every context WITHOUT the shared (possibly stale) auth-state.json and
// log in explicitly below: a stale cookie makes the SPA's first whoami 401,
// bounce through local-login to /console/ and come back to the LAST stored
// path — which drops the query string this spec is about (seen 2026-09-18).
test.use({ storageState: { cookies: [], origins: [] } });

const REDIRECTS: Array<[string, string]> = [
  ["/console/app/engine-config", "/console/app/models?tab=routing"],
  ["/console/app/model-cost-optimizer", "/console/app/models?tab=usage-cost"],
  ["/console/app/model-selection", "/console/app/models?tab=catalog"],
  ["/console/app/engines", "/console/app/models?tab=routing"],
  ["/console/app/engine-control", "/console/app/models?tab=routing"],
];

const MARKER_HEADER =
  "Which model serves each turn, what it costs, and what the selector has learned.";

// Establish the console session on the Models page FIRST. Without it, an old
// path with no live session bounces through /v1/console/auth/local-login and
// lands on /console/ — the app's login flow, not the router's redirect — and
// the assertion would measure the wrong thing (seen 2026-09-18).
test.beforeEach(async ({ page, baseURL }) => {
  // Every test gets a fresh browser context whose stored session may be stale.
  // Log in explicitly (loopback-only, credential-less GET) so the SPA's
  // whoami answers 200 on the very first navigation of each test.
  const origin = new URL(baseURL ?? "http://127.0.0.1:8765/console").origin;
  await page.goto(`${origin}/v1/console/auth/local-login`, { waitUntil: "domcontentloaded" });
  await page.goto("/console/app/models", { waitUntil: "domcontentloaded" });
  await expect(page.getByText(MARKER_HEADER)).toBeVisible({ timeout: 30_000 });
});

for (const [from, to] of REDIRECTS) {
  test(`${from} redirects to ${to}`, async ({ page }) => {
    await page.goto(from, { waitUntil: "domcontentloaded" });
    await expect(page.getByText(MARKER_HEADER)).toBeVisible({ timeout: 20_000 });
    await expect
      .poll(() => new URL(page.url()).pathname + new URL(page.url()).search, { timeout: 15_000 })
      .toBe(to);
  });
}

test("a bogus tab is rewritten to routing and the four tabs exist", async ({ page }) => {
  await page.goto("/console/app/models?tab=bogus", { waitUntil: "domcontentloaded" });
  await expect.poll(() => new URL(page.url()).search).toBe("?tab=routing");
  for (const name of ["Routing", "Usage & Cost", "Learning", "Catalog"]) {
    await expect(page.getByRole("tab", { name })).toBeVisible();
  }
});

test("the window caption is identical on Usage & Cost and Learning", async ({ page }) => {
  await page.goto("/console/app/models?tab=usage-cost", { waitUntil: "domcontentloaded" });
  const header = page.locator("text=/Counting (since|window)/").first();
  await expect(header).toBeVisible();
  const captionA = await header.textContent();
  await page.getByRole("tab", { name: "Learning" }).click();
  await expect.poll(() => new URL(page.url()).search).toBe("?tab=learning");
  const captionB = await page.locator("text=/Counting (since|window)/").first().textContent();
  expect(captionB).toBe(captionA);
});

test("the sidebar has one Models entry and no old entries", async ({ page }) => {
  await page.goto("/console/app/models", { waitUntil: "domcontentloaded" });
  await expect(page.getByText(MARKER_HEADER)).toBeVisible({ timeout: 20_000 });
  // The desktop sidebar is an <aside> (hidden below md; the chromium project's
  // viewport is desktop-sized). Exact names, page-wide: one "Models" link,
  // none of the two old entries.
  const sidebar = page.locator("aside").filter({ has: page.getByRole("link", { name: "Models", exact: true }) });
  await expect(sidebar.getByRole("link", { name: "Models", exact: true })).toHaveCount(1);
  await expect(page.getByRole("link", { name: "Engine Config", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Model Cost Optimizer", exact: true })).toHaveCount(0);
});
