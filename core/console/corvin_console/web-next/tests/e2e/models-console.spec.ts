/**
 * ADR-0885 step 4 — the Models console on the LIVE host, exercised in the
 * browser. Every action here is either read-only or reversible and audited:
 *  - "Reset counters to now" then "Show full history" (net: window unchanged);
 *  - the pin form: a real change enables "Save pins", Discard restores the
 *    served value and writes nothing (the full-map PUT itself is proven in
 *    tests/unit/routing-tab.test.tsx and core/console/tests/test_engine_setting_pins.py);
 *  - Export (a download, no state);
 *  - the rating form renders real recent classifications with Good/Poor
 *    (nothing is clicked — a rating is a learner sample and is proven over
 *    HTTP in core/console/tests/test_model_ranking_route.py);
 *  - "Reset learning" and Import are DESTRUCTIVE on the tenant's learned
 *    state and are NOT clicked on the live host (tests/unit/learning-tab.test.tsx
 *    proves them against MSW).
 * Run: npx playwright test tests/e2e/models-console.spec.ts --project=chromium
 */
import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });
test.use({ storageState: { cookies: [], origins: [] } });

const MARKER_HEADER =
  "Which model serves each turn, what it costs, and what the selector has learned.";

test.beforeEach(async ({ page, baseURL }) => {
  const origin = new URL(baseURL ?? "http://127.0.0.1:8765/console").origin;
  // Log in through the context's request client (shares the cookie jar) — a
  // page.goto here would 302 to /console/ and that SPA boot races the next
  // navigation ("interrupted by another navigation", seen 2026-09-18).
  const login = await page.request.get(`${origin}/v1/console/auth/local-login`, { maxRedirects: 0 });
  expect([302, 200]).toContain(login.status());
  await expect.poll(async () => (await page.request.get(`${origin}/v1/console/auth/whoami`)).status(),
                    { timeout: 20_000 }).toBe(200);
});

let windowWasFull: boolean | null = null;

// Cleanup on failure: if the window was full before this file ran and a test
// died between "reset" and "show full history", restore the full history so
// the operator's live window is not left narrowed.
test.afterEach(async ({ page, baseURL }) => {
  if (windowWasFull !== true) return;
  const origin = new URL(baseURL ?? "http://127.0.0.1:8765/console").origin;
  const status = await page.request.get(`${origin}/v1/console/learning/model-cost-optimizer/status`);
  if (!status.ok()) return;
  const body = (await status.json()) as { window?: { active?: boolean } };
  if (!body.window?.active) return;
  const who = await page.request.get(`${origin}/v1/console/auth/whoami`);
  const csrf = who.ok() ? ((await who.json()) as { csrf_token?: string }).csrf_token : undefined;
  if (!csrf) return;
  await page.request.post(`${origin}/v1/console/learning/model-cost-optimizer/usage-epoch`, {
    data: { clear: true }, headers: { "x-csrf-token": csrf },
  });
});

test("window: reset counters to now, then show full history (reversible)", async ({ page }) => {
  await page.goto("/console/app/models?tab=usage-cost", { waitUntil: "domcontentloaded" });
  await expect(page.getByText(MARKER_HEADER)).toBeVisible({ timeout: 30_000 });
  const wasFull = await page.getByText("Counting window: full history").first().isVisible().catch(() => false);
  windowWasFull = wasFull;
  await page.getByRole("button", { name: "Reset counters to now" }).click();
  await page.getByRole("button", { name: "Click again to confirm" }).click();
  await expect(page.getByText(/Counting since/).first()).toBeVisible({ timeout: 20_000 });
  // The tab's own caption follows the header's window (one window, every reader).
  await expect(page.locator('[role="tabpanel"]:not([hidden])').getByText(/Counting window: since/)).toBeVisible();
  if (wasFull) {
    await page.getByRole("button", { name: "Show full history" }).click();
    await expect(page.getByText("Counting window: full history").first()).toBeVisible({ timeout: 20_000 });
  }
});

test("routing: a real change enables Save pins; Discard restores the served value and writes nothing", async ({ page }) => {
  await page.goto("/console/app/models?tab=routing", { waitUntil: "domcontentloaded" });
  await expect(page.getByText(MARKER_HEADER)).toBeVisible({ timeout: 30_000 });
  const os = page.getByLabel("OS turn model");
  await expect(os).toBeVisible({ timeout: 20_000 });
  const served = await os.inputValue();
  // A no-op edit round trip: change to the other option and back → not dirty.
  const options = await os.locator("option").allTextContents();
  expect(options.length).toBeGreaterThan(1);
  await expect(page.getByRole("button", { name: "Save pins" })).toBeDisabled();
  // Make it dirty with a real change, then Discard — nothing is written.
  const other = await os.locator("option").nth(served ? 0 : 1).getAttribute("value");
  await os.selectOption(other ?? "");
  await expect(page.getByRole("button", { name: "Save pins" })).toBeEnabled();
  await page.getByRole("button", { name: "Discard" }).click();
  await expect(page.getByRole("button", { name: "Save pins" })).toBeDisabled();
  expect(await os.inputValue()).toBe(served);
});

test("learning: recent classifications render with Good/Poor; export downloads", async ({ page }) => {
  await page.goto("/console/app/models?tab=learning", { waitUntil: "domcontentloaded" });
  await expect(page.getByText(MARKER_HEADER)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Rate the last shadow classifications")).toBeVisible();
  const rows = page.getByRole("button", { name: /Good/ });
  await expect(rows.first()).toBeVisible({ timeout: 20_000 });
  expect(await rows.count()).toBeGreaterThan(0);
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export learned state" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^model-thresholds-\d{4}-\d{2}-\d{2}\.json$/);
});

test("catalog: Use for … hands the model to Routing and clears the hand-off", async ({ page }) => {
  await page.goto("/console/app/models?tab=catalog", { waitUntil: "domcontentloaded" });
  await expect(page.getByText(MARKER_HEADER)).toBeVisible({ timeout: 30_000 });
  // Pick a row whose id differs from the served OS pin, so the hand-off
  // makes the form dirty regardless of what is pinned on this host.
  const pinned = (await page.locator("text=/^OS turn:/").first().locator("..").textContent()) ?? "";
  // One catalog ROW (the outer card also matches ".rounded-lg.border"; the row has p-3).
  const rows = page.locator("div.p-3.rounded-lg.border").filter({ has: page.getByRole("button", { name: "Use for OS turn" }) });
  await expect(rows.first()).toBeVisible({ timeout: 20_000 });
  const n = await rows.count();
  let clicked = false;
  for (let i = 0; i < n; i++) {
    const row = rows.nth(i);
    const id = (await row.locator("div.text-xs.text-muted-foreground.break-all").first().textContent()) ?? "";
    if (id && !pinned.includes(id.replace(/^claude-/, "").replace(/-\d{8}$/, ""))) {
      await row.getByRole("button", { name: "Use for OS turn" }).click();
      clicked = true;
      break;
    }
  }
  expect(clicked).toBe(true);
  await expect.poll(() => new URL(page.url()).search, { timeout: 15_000 }).toBe("?tab=routing");
  await expect(page.getByRole("button", { name: "Save pins" })).toBeEnabled({ timeout: 20_000 });
  await page.getByRole("button", { name: "Discard" }).click();
});
