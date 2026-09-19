/**
 * ADR-0892 — ONE marketplace on the live host. READ-ONLY: this spec never
 * installs, enables or removes anything on the operator's tenant; the write
 * paths are proven by tests/unit/marketplace-page.test.tsx (MSW) and
 * tests/e2e/test_marketplace_console_e2e.py (HTTP, temp CORVIN_HOME).
 *
 *  - the six old paths redirect into the panel's tabs;
 *  - the sidebar has ONE "Marketplace" entry (the manifest's panel dedupes by
 *    path instead of adding a second entry that opened the 404 page);
 *  - Browse lists the real index with per-entry local state — a non-installable
 *    entry shows its blocker, never a button;
 *  - Installed lists the tenant registry; Packages and MCP tools answer.
 *
 * Run: npx playwright test tests/e2e/marketplace-console.spec.ts --project=chromium --workers=1
 */
import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });
test.use({ storageState: { cookies: [], origins: [] } });

const MARKER_HEADER =
  "Install, enable and remove plugins, skill packages and MCP tools — every action changes this install and is audited.";

const REDIRECTS: Array<[string, string]> = [
  ["/console/app/marketplace-hub", "/console/app/marketplace?tab=browse"],
  ["/console/app/plugin-center", "/console/app/marketplace?tab=installed"],
  ["/console/app/plugins", "/console/app/marketplace?tab=installed"],
  ["/console/app/extensions", "/console/app/marketplace?tab=installed"],
  ["/console/app/mcp-plugins", "/console/app/marketplace?tab=tools"],
  ["/console/app/packages", "/console/app/marketplace?tab=packages"],
];

test.beforeEach(async ({ page, baseURL }) => {
  const origin = new URL(baseURL ?? "http://127.0.0.1:8765/console").origin;
  const login = await page.request.get(`${origin}/v1/console/auth/local-login`, { maxRedirects: 0 });
  expect([302, 200]).toContain(login.status());
  await expect.poll(async () => (await page.request.get(`${origin}/v1/console/auth/whoami`)).status(),
                    { timeout: 20_000 }).toBe(200);
  await page.goto("/console/app/marketplace", { waitUntil: "domcontentloaded" });
  await expect(page.getByText(MARKER_HEADER)).toBeVisible({ timeout: 30_000 });
});

for (const [from, to] of REDIRECTS) {
  test(`${from} redirects to ${to}`, async ({ page }) => {
    await page.goto(from, { waitUntil: "domcontentloaded" });
    await expect(page).toHaveURL(new RegExp(to.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"));
    await expect(page.getByText(MARKER_HEADER)).toBeVisible();
  });
}

test("the sidebar has exactly one Marketplace entry and no Packages entry", async ({ page }) => {
  const nav = page.locator("nav");
  await expect(nav.getByRole("link", { name: "Marketplace", exact: true })).toHaveCount(1);
  await expect(nav.getByRole("link", { name: "Packages", exact: true })).toHaveCount(0);
  await expect(nav.locator('a[href*="plugin-center"]')).toHaveCount(0);
  await expect(nav.locator('a[href*="marketplace-hub"]')).toHaveCount(0);
});

test("Browse shows both tiers from the real index; a contributor entry is installable", async ({ page }) => {
  await page.goto("/console/app/marketplace?tab=browse", { waitUntil: "domcontentloaded" });
  const summary = page.getByTestId("browse-summary");
  await expect(summary).toBeVisible({ timeout: 30_000 });
  const text = (await summary.textContent()) ?? "";
  const m = text.match(/(\d+) of (\d+) entries shown · (\d+) installable/);
  expect(m, text).not.toBeNull();
  expect(Number(m![2])).toBeGreaterThan(0);
  // Two tiers, each with its own count line and explanation (ADR-0892 amendment).
  await expect(page.getByTestId("tier-buildin-summary")).toContainText(/\d+ of \d+ shown/);
  await expect(page.getByTestId("tier-contributor-summary")).toContainText(/\d+ of \d+ shown/);
  await expect(page.getByTestId("tier-contributor")).toContainText("enabling records your explicit consent");
  // The Video Producer is a contributor entry and installable from the checkout
  // (or already installed — either way it is a real button, never a blocker).
  const video = page.getByTestId("index-card-plugin:contributor-media-video_producer");
  await expect(video).toBeVisible();
  await expect(video.getByText(/Not installable on this build/)).toHaveCount(0);
  await expect(video.getByRole("button", { name: /^Install$|Manage on the Installed tab|Enable now/ })).toHaveCount(1);
  // The Knowledge Graph (contributor/knowledge_management) is listed and installable too.
  const knowledge = page.getByTestId("index-card-plugin:contributor-knowledge_management-corvin_knowledge");
  await expect(knowledge).toBeVisible();
  await expect(knowledge.getByText(/Not installable on this build/)).toHaveCount(0);
  // An installed entry hands off to the Installed tab rather than offering Install again.
  const installedCard = page.locator('[data-testid^="index-card-"]').filter({ hasText: "Manage on the Installed tab" }).first();
  if (await installedCard.count()) {
    await installedCard.getByRole("button", { name: "Manage on the Installed tab" }).click();
    await expect(page).toHaveURL(/tab=installed/);
  }
});

test("Installed, Packages and MCP tools tabs render their real state", async ({ page }) => {
  await page.goto("/console/app/marketplace?tab=installed", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("installed-summary")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("installed-summary")).toContainText(/\d+ plugins/);
  await page.getByRole("tab", { name: "Packages" }).click();
  await expect(page.getByTestId("packages-summary")).toContainText(/\d+ skill packages/);
  await page.getByRole("tab", { name: "MCP tools" }).click();
  // Either the real catalogue or the honest "not available on this build" — never a fake list.
  await expect(page.getByTestId("tools-summary").or(page.getByTestId("tools-unavailable"))).toBeVisible();
});
