import { test, expect } from "@playwright/test";

const BASE = "http://127.0.0.1:8765";

test("Token Metrics panel is reachable from the Vibe Engineering nav and renders live data", async ({ page }) => {
  const apiCalls: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("token-metrics")) apiCalls.push(r.url());
  });

  // real login through the real transport
  await page.goto(`${BASE}/v1/console/auth/local-login`);
  await page.waitForURL(/\/console\//, { timeout: 20000 });

  // the nav entry must exist under Vibe Engineering — not just the route
  const navLink = page.locator('a[href$="/app/token-metrics"]:visible').first();
  await expect(navLink).toBeVisible();
  await expect(navLink).toContainText("Token Metrics");

  // click it like an operator would
  await navLink.click();
  await expect(page).toHaveURL(/\/app\/token-metrics/);

  // the page must actually render measured data, not the error/empty card
  await expect(page.getByRole("heading", { name: "Token Metrics Dashboard" })).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("Error Loading Metrics")).toHaveCount(0);
  await expect(page.getByText("No Metrics Available")).toHaveCount(0);
  await expect(page.getByText("Total Turns")).toBeVisible();

  // and it must have gone through the real API endpoint
  expect(apiCalls.some((u) => u.includes("/v1/console/vibe-engineering/token-metrics/"))).toBe(true);

  await page.screenshot({ path: "/tmp/claude-1000/-home-shumway-projects-CorvinOS/e2a86dc8-0443-4c33-b17d-2d962e1a6124/scratchpad/token-metrics.png", fullPage: true });
});
