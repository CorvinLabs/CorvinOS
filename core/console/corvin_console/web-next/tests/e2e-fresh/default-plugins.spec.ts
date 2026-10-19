/**
 * Standard plugins — what a FRESH install gets without anyone clicking install.
 *
 *   boot (empty CORVIN_HOME, no local marketplace) -> default_plugins.yaml provisioned in the
 *   background -> CorvinTV (formerly Video Producer) is installed AND enabled — it is a first-party
 *   plugin of the buildin tier (origin vetted, requires_consent false), like the Knowledge Graph —
 *   -> panel in the sidebar, with no operator step and no consent prompt. Registry/ledger are on
 *   disk, the source came from GitHub (or, pre-push, from the local marketplace commit).
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
const VP = "plugin:buildin-media-corvintv";

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

  test("CorvinTV is provisioned by boot alone: installed AND enabled, no consent, no click", async ({ page }) => {
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
      min_version: "1.7.0",
    });
    expect(e.installed_version >= "1.7.0", e.installed_version).toBe(true);
    // first-party: boot enables it (nothing is asked of the operator)
    await expect.poll(async () => (await vp(page)).enabled, { timeout: 30_000, intervals: [1_000] }).toBe(true);
  });

  test("it is really on disk: registry record, ledger, marketplace source", async () => {
    const reg = fs.readFileSync(path.join(TENANT_PLUGINS, "registry.yaml"), "utf8");
    expect(reg).toMatch(/^  video_producer:\s*$/m);
    const ledger = JSON.parse(fs.readFileSync(path.join(TENANT_PLUGINS, "default_plugins.json"), "utf8"));
    expect(ledger.offered[VP].registry_id).toBe("video_producer");
    expect(fs.statSync(path.join(TENANT_PLUGINS, "default_plugins.json")).mode & 0o777).toBe(0o600);
    // no sibling checkout in a fresh install -> the source is the GitHub sync, or (pre-push mode) the
    // marketplace commit the harness exported
    const ghCache = fs.existsSync(path.join(HOME, "marketplace-cache", "Corvin-Marketplace", "plugins"));
    const prePush = fs.existsSync(path.join(ROOT, "marketplace", "plugins", "buildin", "media", "corvintv"));
    expect(ghCache || prePush, "plugin source present (GitHub cache or pre-push export)").toBe(true);
  });

  test("it is functional at once: the sidebar entry is CorvinTV and the panel API answers with the default chain", async ({ page }) => {
    await login(page);
    await page.goto("/console/app/", { waitUntil: "domcontentloaded" });
    const link = page.locator('nav a[href*="video-producer"]:visible').first();
    await expect(link).toBeVisible({ timeout: 45_000 });
    await expect(link).toContainText("CorvinTV");
    // the route loads the plugin source and answers: the default narration is the chain OpenAI -> edge-tts
    const r = await page.request.get(`${API}/video/settings`);
    expect(r.status(), await r.text()).toBe(200);
    const s = await r.json();
    expect(s.tts_engine).toBe("auto");
    expect(s.tts_engines).toContain("auto");
    expect((await page.request.get(`${API}/video/jobs`)).status()).toBe(200);
  });

  test("the Knowledge Graph is provisioned and enabled next to it: both standard plugins, every install", async ({ page }) => {
    await login(page);
    const KG = "plugin:buildin-knowledge_management-corvin_knowledge";
    const kg = async () =>
      ((await (await page.request.get(LIST)).json()).plugins as any[]).find((p) => p.index_id === KG);
    await expect.poll(async () => JSON.stringify(await kg()), { timeout: 100_000, intervals: [2_000] }).toContain('"installed":true');
    await expect.poll(async () => (await kg()).enabled, { timeout: 30_000, intervals: [1_000] }).toBe(true);
    expect((await vp(page)).enabled).toBe(true);
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
