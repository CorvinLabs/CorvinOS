/**
 * Live-server E2E — drives the REAL console on :8765 (the production bundle the
 * corvin-console-watch service deployed), authenticating through the real
 * `/auth/local-login` redirect. Read-only: it navigates and reads, never mutates.
 *
 * Replaces the stale `token-metrics-panel.spec.ts`, which asserted a
 * `/app/token-metrics` sidebar entry that has never existed in NAV_GROUPS.
 *
 *   npx playwright test --config=playwright.live.config.ts
 */
import { test, expect } from "@playwright/test";

const BASE = process.env.CONSOLE_BASE_URL || "http://127.0.0.1:8765";

test("operator lands on the live console, the Dashboard nav entry works, and the SPA shell is served no-cache", async ({ page }) => {
  const shell = await page.request.get(`${BASE}/console/`);
  expect(shell.status()).toBe(200);
  // CLAUDE.md cache-header invariant: the SPA shell is never cached, hashed assets are immutable.
  expect(shell.headers()["cache-control"]).toContain("no-cache");
  const html = await shell.text();
  const asset = html.match(/assets\/(index-[^"']+\.js)/)?.[1];
  expect(asset, "entry bundle referenced by the shell").toBeTruthy();
  const bundle = await page.request.get(`${BASE}/console/assets/${asset}`);
  expect(bundle.status()).toBe(200);
  expect(bundle.headers()["cache-control"]).toContain("immutable");

  // real login through the real transport
  await page.goto(`${BASE}/v1/console/auth/local-login`);
  await page.waitForURL(/\/console\//, { timeout: 20000 });

  // the sidebar entry must exist (NAV_GROUPS) — not just the route (PANELS)
  const navLink = page.locator('a[href$="/app/dashboard"]:visible').first();
  await expect(navLink).toBeVisible();
  await expect(navLink).toContainText("Dashboard");
  await navLink.click();
  await expect(page).toHaveURL(/\/app\/dashboard/);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible({ timeout: 15000 });
});

test("session-gated vibe/audit routes answer the logged-in operator with tenant data (2026-09-07 hardening)", async ({ page }) => {
  // unauthenticated: 401, never data (F-C4 — these were open before)
  for (const path of ["/v1/console/vibe/health", "/v1/console/vibe/tasks/list", "/v1/console/audit/graph"]) {
    const anon = await page.request.get(`${BASE}${path}`, { headers: { cookie: "" } });
    expect(anon.status(), `${path} must be session-gated`).toBe(401);
  }

  await page.goto(`${BASE}/v1/console/auth/local-login`);
  await page.waitForURL(/\/console\//, { timeout: 20000 });

  const health = await page.request.get(`${BASE}/v1/console/vibe/health`);
  expect(health.status()).toBe(200);
  expect((await health.json()).module).toBe("vibe_context_inspector");

  const graph = await page.request.get(`${BASE}/v1/console/audit/graph?limit=50`);
  expect(graph.status()).toBe(200);
  const body = await graph.json();
  expect(Array.isArray(body.nodes)).toBe(true);
  expect(body.total_events).toBeGreaterThan(0); // the live chain is never empty

  const tasks = await page.request.get(`${BASE}/v1/console/vibe/tasks/list?limit=5`);
  expect(tasks.status()).toBe(200);
  expect(typeof (await tasks.json()).total).toBe("number");

  // the l5 router lives under the console prefix exactly once (F-C5)
  const gone = await page.request.get(`${BASE}/v1/console/v1/metrics/l5/status`);
  expect(gone.status()).toBe(404);
  const l5 = await page.request.get(`${BASE}/v1/console/metrics/l5/status`);
  expect(l5.status()).toBe(200);
});
