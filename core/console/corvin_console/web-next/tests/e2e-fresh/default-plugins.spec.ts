/**
 * Standard plugins — what a FRESH install gets without anyone clicking install.
 *
 *   boot (empty CORVIN_HOME, no local marketplace) -> default_plugins.yaml provisioned in the
 *   background -> Video Producer is installed but NOT enabled (its manifest requires the
 *   operator's consent, which boot never grants) -> operator enables with consent -> panel in
 *   the sidebar. Registry/ledger are on disk, the source came from GitHub.
 *
 * Run (network + built SPA):  npx playwright test -c playwright.fresh-install.config.ts default-plugins
 *
 * Nothing in this file installs anything. If a test needs the plugin it waits for the
 * boot provisioning; a missing plugin is the bug under test.
 */
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const ROOT = process.env.CORVIN_FRESH_ROOT ?? "/tmp/corvin-fresh-install";
const HOME = path.join(ROOT, "home");
const TENANT_PLUGINS = path.join(HOME, "tenants", "_default", "plugins");
const API = "/v1/console";
const LIST = `${API}/api/v1/marketplace/default-plugins`;
const VP = "plugin:contributor-media-video_producer";

async function login(page: Page): Promise<void> {
  const r = await page.request.get(`${API}/auth/local-login`, { maxRedirects: 0 });
  expect([200, 302]).toContain(r.status());
  await expect
    .poll(async () => (await page.request.get(`${API}/auth/whoami`)).status(), { timeout: 20_000 })
    .toBe(200);
}

const vp = async (page: Page) =>
  ((await (await page.request.get(LIST)).json()).plugins as any[]).find((p) => p.index_id === VP);

test.describe.serial("standard plugins on a fresh install", () => {
  test("the list endpoint needs a session", async ({ request }) => {
    const r = await request.get(LIST);
    expect([401, 403]).toContain(r.status());
  });

  test("Video Producer is provisioned by boot alone (installed, consent pending)", async ({ page }) => {
    await login(page);
    // background thread + GitHub download: allow a generous window, but fail with the state.
    await expect
      .poll(async () => JSON.stringify(await vp(page)), { timeout: 100_000, intervals: [2_000] })
      .toContain('"installed":true');
    const e = await vp(page);
    expect(e, JSON.stringify(e)).toMatchObject({
      registry_id: "video_producer",
      offered: true,
      installed: true,
      min_version: "1.2.0",
    });
    expect(e.installed_version >= "1.2.0", e.installed_version).toBe(true);
    // boot never grants consent: the plugin waits, disabled, for the operator
    expect(e.enabled).toBe(false);
  });

  test("it is really on disk: registry record, ledger, marketplace source from GitHub", async () => {
    const reg = fs.readFileSync(path.join(TENANT_PLUGINS, "registry.yaml"), "utf8");
    expect(reg).toMatch(/^  video_producer:\s*$/m);
    const ledger = JSON.parse(fs.readFileSync(path.join(TENANT_PLUGINS, "default_plugins.json"), "utf8"));
    expect(ledger.offered[VP].registry_id).toBe("video_producer");
    expect(fs.statSync(path.join(TENANT_PLUGINS, "default_plugins.json")).mode & 0o777).toBe(0o600);
    // no sibling checkout in a fresh install -> the source can only be the GitHub sync
    expect(fs.existsSync(path.join(HOME, "marketplace-cache", "Corvin-Marketplace", "plugins"))).toBe(true);
  });

  test("the operator enables it with consent: sidebar entry + marketplace listing agree", async ({ page }) => {
    await login(page);
    const csrf = (await (await page.request.get(`${API}/auth/whoami`)).json()).csrf_token as string;
    const refused = await page.request.fetch(`${API}/api/v1/marketplace/plugins/${encodeURIComponent(VP)}/enable`, {
      method: "PATCH", headers: { "x-csrf-token": csrf }, data: {},
    });
    expect(refused.status(), "enable without consent must be refused").toBeGreaterThanOrEqual(400);
    expect((await vp(page)).enabled).toBe(false);
    const ok = await page.request.fetch(`${API}/api/v1/marketplace/plugins/${encodeURIComponent(VP)}/enable`, {
      method: "PATCH", headers: { "x-csrf-token": csrf }, data: { consent_granted: true },
    });
    expect(ok.status(), await ok.text()).toBe(200);
    const hit = ((await (await page.request.get(`${API}/api/v1/marketplace/plugins?q=video`)).json()).plugins as any[])
      .find((p) => p.id === VP);
    expect({ installed: hit.installed, enabled: hit.enabled }).toEqual({ installed: true, enabled: true });
    await page.goto("/console/app/", { waitUntil: "domcontentloaded" });
    await expect(page.locator('nav a[href*="video-producer"]:visible').first()).toBeVisible({ timeout: 45_000 });
  });

  test("an operator uninstall sticks: the list reports offered but not installed", async ({ page }) => {
    await login(page);
    const csrf = (await (await page.request.get(`${API}/auth/whoami`)).json()).csrf_token as string;
    const h = { "x-csrf-token": csrf };
    const mp = `${API}/api/v1/marketplace/plugins/${encodeURIComponent(VP)}`;
    await page.request.fetch(`${mp}/disable`, { method: "PATCH", headers: h });
    const u = await page.request.fetch(`${mp}/uninstall`, { method: "POST", headers: h, data: {} });
    expect(u.status(), await u.text()).toBe(200);
    const e = await vp(page);
    expect({ offered: e.offered, installed: e.installed }).toEqual({ offered: true, installed: false });
  });
});
