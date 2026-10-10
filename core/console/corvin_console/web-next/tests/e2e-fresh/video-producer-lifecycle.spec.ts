/**
 * Video Producer — full plugin lifecycle on a FRESH install, source of truth = GitHub.
 *
 *   fresh install -> install from the marketplace -> enabled -> sidebar panel ->
 *   produce a video -> uninstall -> panel gone
 *
 * plus adversarial cases (double install, uninstall racing an install, uninstall
 * while enabled, repeated/blind uninstall, bad ids, missing CSRF/session, reinstall).
 *
 * Run (needs network + a built SPA):
 *   npx playwright test -c playwright.fresh-install.config.ts
 *
 * What makes it "fresh" is scripts/start-fresh-install-backend.sh: file export of
 * HEAD, empty CORVIN_HOME, no sibling Corvin-Marketplace checkout, no
 * CORVIN_MARKETPLACE_* override. The plugin index and SOURCE can therefore only have come
 * from github.com/CorvinLabs/Corvin-Marketplace — test 1 proves that byte-for-byte.
 *
 * Tests are independent: each one drives the backend into the state it needs
 * (`ensure`) instead of relying on the previous test, so one product bug does not
 * mask every test after it.
 *
 * Nothing is flipped by hand before the first install: a fresh install must work as
 * shipped (plugin_runtime_lifecycle / plugin_console_surface default ON, the wizard's
 * "Install and enable" leaves the plugin on).
 */
import { expect, test, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const INDEX_ID = "plugin:contributor-media-video_producer";
const REGISTRY_ID = "video_producer";
const GITHUB_RAW = "https://raw.githubusercontent.com/CorvinLabs/Corvin-Marketplace/main";
const GITHUB_REPO = "https://github.com/CorvinLabs/Corvin-Marketplace.git";
const ROOT = process.env.CORVIN_FRESH_ROOT ?? "/tmp/corvin-fresh-install";
const HOME = path.join(ROOT, "home");
const TENANT_PLUGINS = path.join(HOME, "tenants", "_default", "plugins");
const API = "/v1/console";
const MP = `${API}/api/v1/marketplace`;
const enc = encodeURIComponent;

// ── helpers ──────────────────────────────────────────────────────────────────

async function login(page: Page): Promise<void> {
  const r = await page.request.get(`${API}/auth/local-login`, { maxRedirects: 0 });
  expect([200, 302]).toContain(r.status());
  await expect
    .poll(async () => (await page.request.get(`${API}/auth/whoami`)).status(), { timeout: 20_000 })
    .toBe(200);
}

async function csrf(page: Page): Promise<string> {
  const r = await page.request.get(`${API}/auth/whoami`);
  return (await r.json()).csrf_token as string;
}

type Res = { status: number; body: any; text: string };

async function call(page: Page, method: string, url: string, body?: unknown, opts: { csrf?: boolean } = {}): Promise<Res> {
  const headers: Record<string, string> = {};
  if (opts.csrf !== false) headers["x-csrf-token"] = await csrf(page);
  const r = await page.request.fetch(url, {
    method,
    headers,
    ...(body === undefined ? {} : { data: body }),
  });
  const text = await r.text();
  let parsed: any = null;
  try { parsed = JSON.parse(text); } catch { /* not JSON */ }
  return { status: r.status(), body: parsed, text };
}

async function entry(page: Page): Promise<any> {
  const r = await page.request.get(`${MP}/plugins?q=video`);
  expect(r.status()).toBe(200);
  const hit = ((await r.json()).plugins as any[]).find((p) => p.id === INDEX_ID);
  expect(hit, "Video Producer must be in the marketplace index").toBeTruthy();
  return hit;
}

/** Drive the backend into `want` using the same API the UI calls. */
async function ensure(page: Page, want: "uninstalled" | "installed" | "enabled"): Promise<void> {
  let e = await entry(page);
  if (want === "uninstalled") {
    if (e.enabled) await call(page, "PATCH", `${MP}/plugins/${enc(INDEX_ID)}/disable`);
    if (e.installed) await call(page, "POST", `${MP}/plugins/${enc(INDEX_ID)}/uninstall`, {});
  } else {
    if (!e.installed) {
      const r = await call(page, "POST", `${MP}/plugins/${enc(INDEX_ID)}/install`, { wait: true });
      expect(r.body?.status, r.text).toBe("completed");
    }
    e = await entry(page);
    if (want === "enabled" && !e.enabled) {
      const r = await call(page, "PATCH", `${MP}/plugins/${enc(INDEX_ID)}/enable`, { consent_granted: true });
      expect(r.status, r.text).toBe(200);
    }
    if (want === "installed" && e.enabled) await call(page, "PATCH", `${MP}/plugins/${enc(INDEX_ID)}/disable`);
  }
  e = await entry(page);
  expect({ installed: e.installed, enabled: e.enabled }).toEqual({
    installed: want !== "uninstalled",
    enabled: want === "enabled",
  });
}

/** What is on disk for the plugin — the three places an install writes to. */
function onDisk(): { registry: boolean; instance: boolean; panel: boolean } {
  const reg = path.join(TENANT_PLUGINS, "registry.yaml");
  const panels = path.join(TENANT_PLUGINS, "panel_registry.json");
  return {
    registry: fs.existsSync(reg) && new RegExp(`^  ${REGISTRY_ID}:\\s*$`, "m").test(fs.readFileSync(reg, "utf8")),
    instance: fs.existsSync(path.join(TENANT_PLUGINS, "instances", REGISTRY_ID)),
    panel: fs.existsSync(panels) && fs.readFileSync(panels, "utf8").includes(`"${REGISTRY_ID}"`),
  };
}

function registryRecordCount(): number {
  const reg = path.join(TENANT_PLUGINS, "registry.yaml");
  if (!fs.existsSync(reg)) return 0;
  return (fs.readFileSync(reg, "utf8").match(new RegExp(`^  ${REGISTRY_ID}:\\s*$`, "gm")) ?? []).length;
}

// :visible — the page also carries a (hidden) mobile nav with the same entries.
const sidebarLink = (page: Page) => page.locator('nav a[href*="video-producer"]:visible');

async function raw(pathInRepo: string): Promise<Buffer> {
  const r = await fetch(`${GITHUB_RAW}/${pathInRepo}`, { signal: AbortSignal.timeout(30_000) });
  expect(r.status, `GitHub raw ${pathInRepo}`).toBe(200);
  return Buffer.from(await r.arrayBuffer());
}
const sha = (b: Buffer | string) => createHash("sha256").update(b).digest("hex");

async function openMarketplaceBrowse(page: Page): Promise<void> {
  await page.goto("/console/app/marketplace?tab=browse", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("browse-summary")).toBeVisible({ timeout: 45_000 });
}

/** Walk the install wizard to its end; returns once the confirm step is shown. */
async function runInstallWizard(page: Page): Promise<void> {
  const card = page.getByTestId(`index-card-${INDEX_ID}`);
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: /^Install$/ }).click();
  const dlg = page.getByRole("dialog");
  await expect(dlg.getByText(`Install Video Producer`)).toBeVisible();
  // The wizard auto-advances from "dependencies" after a short delay, so click
  // whichever forward button is currently there until "Install and enable" is pressed.
  for (let i = 0; i < 6; i++) {
    const next = dlg.getByRole("button", { name: /Next: Choose version|Next: Review|Install and enable/ }).first();
    await expect(next).toBeEnabled({ timeout: 20_000 });
    const label = (await next.textContent()) ?? "";
    await next.click();
    if (/Install and enable/.test(label)) break;
  }
  await expect(dlg.getByText("Installation completed")).toBeVisible({ timeout: 60_000 });
}

// ── tests ────────────────────────────────────────────────────────────────────

test.describe("Video Producer plugin lifecycle — fresh install, GitHub marketplace", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("0. the backend really is a fresh install with no local marketplace checkout", async ({ page }) => {
    expect(fs.existsSync(path.join(ROOT, "Corvin-Marketplace")), "no sibling checkout next to the exported CorvinOS").toBe(false);
    expect(fs.existsSync(path.join(ROOT, "CorvinOS", ".git")), "git-archive export, not a clone").toBe(false);
    expect(registryRecordCount()).toBe(0);
    expect(onDisk()).toEqual({ registry: false, instance: false, panel: false });
    const e = await entry(page);
    expect({ installed: e.installed, enabled: e.enabled }).toEqual({ installed: false, enabled: false });
    // nothing in the sidebar yet
    await page.goto("/console/app/marketplace", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("marketplace-console")).toBeVisible({ timeout: 45_000 });
    await expect(sidebarLink(page)).toHaveCount(0);
  });

  test("1. the index the console serves is GitHub's (Corvin-Marketplace main), not a local copy", async ({ page }) => {
    const served = await entry(page);
    const gh = JSON.parse((await raw("index/plugins.json")).toString("utf8"));
    const ghEntry = (gh.plugins as any[]).find((p) => p.id === INDEX_ID);
    expect(ghEntry, "entry exists on GitHub main").toBeTruthy();
    expect(served.version).toBe(ghEntry.version);
    expect(served.distribution?.source_url).toBe(ghEntry.distribution?.source_url);
    expect(served.distribution?.source_url).toContain("github.com/CorvinLabs/Corvin-Marketplace");
    // sanity: the repo head the test compares against is reachable and is a real commit
    const head = execFileSync("git", ["ls-remote", GITHUB_REPO, "HEAD"], { timeout: 30_000 }).toString().split(/\s+/)[0];
    expect(head).toMatch(/^[0-9a-f]{40}$/);
    test.info().annotations.push({ type: "github-head", description: head });
  });

  test("2. a fresh install can install a plugin as shipped — no hidden feature flag", async ({ page }) => {
    const flags = await call(page, "GET", `${API}/settings/features`);
    const byId = Object.fromEntries((flags.body.features as any[]).map((f) => [f.id, f]));
    for (const id of ["plugin_runtime_lifecycle", "plugin_console_surface"]) {
      expect(byId[id], id).toMatchObject({ enabled: true, source: "default" });
    }
    const r = await call(page, "POST", `${MP}/plugins/${enc(INDEX_ID)}/install`, { wait: true });
    expect(r.body?.status, r.text).toBe("completed");
    // the Installed tab's backing route answers on a fresh install
    expect((await page.request.get(`${API}/plugins`)).status()).toBe(200);
    await ensure(page, "uninstalled");
  });

  test("3. the wizard's \"Install and enable\" pulls the source from GitHub, byte-matches it and leaves the plugin ON", async ({ page }) => {
    await ensure(page, "uninstalled");
    await openMarketplaceBrowse(page);
    await runInstallWizard(page);

    // default ON: no second click was needed
    const e = await entry(page);
    expect({ installed: e.installed, enabled: e.enabled }).toEqual({ installed: true, enabled: true });
    await expect(page.getByRole("dialog").getByText(/Enabled — its panel/)).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).first().click();
    const card = page.getByTestId(`index-card-${INDEX_ID}`);
    await expect(card.getByText("enabled", { exact: true }), "card carries the enabled badge").toBeVisible({ timeout: 15_000 });
    await expect(card.getByRole("button", { name: /Manage on the Installed tab/ })).toBeVisible();
    // ...and the panel is in the sidebar right away, without a reload
    await expect(sidebarLink(page)).toHaveCount(1, { timeout: 15_000 });

    // The plugin SOURCE on this install is GitHub main, byte for byte.
    const cache = path.join(HOME, "marketplace-cache", "Corvin-Marketplace", "plugins", "contributor", "media", "video_producer");
    expect(fs.existsSync(cache), "source was synced from GitHub into the install's own cache").toBe(true);
    for (const f of ["plugin.yaml", "plugin.json", "provider.py"]) {
      const remote = await raw(`plugins/contributor/media/video_producer/${f}`);
      expect(sha(fs.readFileSync(path.join(cache, f))), `${f} == GitHub main`).toBe(sha(remote));
    }
    expect(onDisk().registry).toBe(true);
  });

  test("4. the consent gate holds: a bare API install stays disabled, 'enable after install' needs the consent flag", async ({ page }) => {
    await ensure(page, "uninstalled");
    const bare = await call(page, "POST", `${MP}/plugins/${enc(INDEX_ID)}/install`, { wait: true });
    expect(bare.body?.status, bare.text).toBe("completed");
    expect((await entry(page)).enabled).toBe(false);
    await ensure(page, "uninstalled");

    const noConsent = await call(page, "POST", `${MP}/plugins/${enc(INDEX_ID)}/install`, { wait: true, enable_after_install: true });
    expect(noConsent.body).toMatchObject({ status: "completed", enabled: false });
    expect(String(noConsent.body.enable_error)).toMatch(/consent/i);
    expect((await entry(page)).enabled).toBe(false);
    await ensure(page, "uninstalled");

    const consent = await call(page, "POST", `${MP}/plugins/${enc(INDEX_ID)}/install`, { wait: true, enable_after_install: true, consent_granted: true });
    expect(consent.body).toMatchObject({ status: "completed", enabled: true });
    expect((await entry(page)).enabled).toBe(true);
  });

  test("5. enable (consent) -> the Video Producer panel appears in the sidebar and opens", async ({ page }) => {
    await ensure(page, "installed");
    await page.goto("/console/app/marketplace?tab=installed", { waitUntil: "domcontentloaded" });
    const row = page.getByTestId(`installed-row-${REGISTRY_ID}`);
    await expect(row).toBeVisible({ timeout: 45_000 });
    await expect(sidebarLink(page), "no panel while the plugin is only installed").toHaveCount(0);
    await row.getByRole("button", { name: /^Enable/ }).click();
    await expect(page.getByTestId(`installed-msg-${REGISTRY_ID}`)).toContainText(/Enabled/i, { timeout: 30_000 });

    expect((await entry(page)).enabled).toBe(true);
    // live refresh without a reload is a UX nicety — soft; the requirement is the reload case
    await expect.soft(sidebarLink(page), "panel shows up without a manual reload").toHaveCount(1, { timeout: 10_000 });
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(sidebarLink(page)).toHaveCount(1, { timeout: 30_000 });
    await sidebarLink(page).click();
    await expect(page).toHaveURL(/\/app\/video-producer/);
    await expect(page.getByTestId("video-producer")).toBeVisible({ timeout: 30_000 });
  });

  test("6. a video is produced through the panel and the MP4 is real", async ({ page }) => {
    test.setTimeout(600_000);
    await ensure(page, "enabled");
    await page.goto("/console/app/video-producer", { waitUntil: "domcontentloaded" });
    const unavailable = page.getByText(/plugin is not available on this build/i);
    await expect(page.getByTestId("video-producer").or(unavailable).first()).toBeVisible({ timeout: 45_000 });
    expect(
      await unavailable.count(),
      "panel is enabled and in the sidebar, but the plugin's backend was never loaded (video API answers 503)",
    ).toBe(0);

    await page.getByTestId("task-input").fill("Explain in two very short scenes why the sky is blue.");
    const created = page.waitForResponse((r) => r.url().includes("/video/jobs") && r.request().method() === "POST");
    await page.getByTestId("start-production").click();
    const res = await created;
    expect(res.status(), `POST /video/jobs: ${await res.text()}`).toBe(200);
    const jobId = (await res.json()).job_id as string;

    await expect
      .poll(async () => (await (await page.request.get(`${API}/video/jobs/${jobId}`)).json()).status, {
        timeout: 540_000, intervals: [3_000],
        message: "job reaches a terminal state",
      })
      .toMatch(/^(complete|completed|error|failed)$/);
    const job = await (await page.request.get(`${API}/video/jobs/${jobId}`)).json();
    expect(job.status, `job error: ${job.error_message}`).toMatch(/^complete/);

    // the panel layout: the produced video fills the stage, the composer sits directly under it, the library follows
    const stageVideo = page.getByTestId("stage-video");
    await expect(stageVideo, "the new video is selected and plays on the stage").toBeVisible({ timeout: 60_000 });
    await expect(stageVideo).toHaveAttribute("src", new RegExp(`/video/videos/${jobId}/download$`));
    const box = async (id: string) => (await page.getByTestId(id).boundingBox())!;
    const [stage, composer, library, vp] = [await box("studio"), await box("composer"), await box("library"), page.viewportSize()!];
    expect(stage.width, "the stage spans the panel").toBeGreaterThan(vp.width * 0.6);
    expect(composer.y, "the composer is directly under the stage").toBeGreaterThanOrEqual(stage.y + stage.height - 1);
    expect(composer.y - (stage.y + stage.height), "no gap between stage and composer").toBeLessThan(8);
    expect(library.y, "the library follows the composer").toBeGreaterThan(composer.y + composer.height - 1);
    await page.getByTestId("studio").hover();
    await page.getByTestId("fullscreen").click();
    await expect.poll(() => page.evaluate(() => document.fullscreenElement?.getAttribute("data-testid") ?? null), { message: "fullscreen shows the stage" }).toBe("studio");
    await page.evaluate(() => document.exitFullscreen());
    await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(false);
    await expect(page.getByTestId(`revise-${jobId}`), "a produced video can be revised").toBeVisible();

    const dl = await page.request.get(`${API}/video/videos/${jobId}/download`);
    expect(dl.status()).toBe(200);
    expect(dl.headers()["content-type"]).toContain("video");
    const bytes = await dl.body();
    expect(bytes.length).toBeGreaterThan(10_000);
    expect(bytes.subarray(4, 8).toString("latin1"), "MP4 'ftyp' box").toBe("ftyp");
    // decode it for real when ffprobe exists on the test host
    try {
      const f = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "vp-")), "out.mp4");
      fs.writeFileSync(f, bytes);
      const dur = parseFloat(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).toString());
      expect(dur).toBeGreaterThan(1);
    } catch (e: any) {
      if (e?.code !== "ENOENT") throw e;
      test.info().annotations.push({ type: "skipped-check", description: "ffprobe not installed on the test host" });
    }

    // ── verify the artefact itself, not just that a file came back ────────────────────────────
    const qm = await (await page.request.get(`${API}/video/jobs/${jobId}/quality-metrics`)).json();
    // Every technical check must pass. "timing" and "runtime" compare the model's duration GUESS with the
    // real narration length — the runtime follows the voice, not the plan — so they are reported, not gated.
    const PLAN_DRIFT = new Set(["timing", "runtime"]);
    const failed = qm.checks.filter((c: any) => c.status === "fail" && !PLAN_DRIFT.has(c.id));
    expect(failed, `quality checks failed: ${JSON.stringify(failed)}`).toEqual([]);
    for (const c of qm.checks.filter((c: any) => PLAN_DRIFT.has(c.id) && c.status !== "pass"))
      test.info().annotations.push({ type: "plan-drift", description: `${c.label}: ${c.detail}` });
    expect(qm.video).toMatchObject({ codec: "h264", width: 1920, height: 1080 });
    expect(qm.audio, "narration track present").toBeTruthy();
    expect(qm.subtitles, "no subtitles: no stream, no caption file, nothing burned in").toMatchObject({ streams: 0, files: [] });
    const md = qm.source.metadata;
    expect(md.renderers.length, "one renderer entry per scene").toBeGreaterThanOrEqual(2);
    expect(md.renderers, "every scene is an animated web slide, none fell back to the plain placeholder").toEqual(
      md.renderers.map(() => "web"),
    );
    expect(Array.isArray(md.layout_collisions), "the overlap check shipped in the installed plugin ran").toBe(true);
    // a collision the plugin resolved (without chips, compact variant, bullets, quote) leaves a clean slide;
    // only "kept" means a colliding slide went into the video
    const kept = md.layout_collisions.filter((c: any) => c.action === "kept");
    expect(kept, `colliding slides in the video: ${JSON.stringify(kept)}`).toEqual([]);
    // ADR-2245: every web scene ran on a cue timeline built from its own narration audio
    expect(Array.isArray(md.cues), "the cue timeline shipped in the installed plugin ran").toBe(true);
    expect(md.cues.length, "one cue record per web scene").toBe(md.renderers.length);
    for (const c of md.cues) {
      expect(["llm", "fallback"], `scene ${c.scene}: beats source`).toContain(c.beats_source);
      expect(c.items.every((it: any) => typeof it.at === "number" && it.at >= 0),
        `scene ${c.scene}: every item has a reveal time`).toBe(true);
    }

    // the poster is the first slide: it carries the real mark (chevron, bar and the gold dot #C9A227)
    const poster = await page.request.get(`${API}/video/videos/${jobId}/poster`);
    expect(poster.status()).toBe(200);
    try {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vp-poster-"));
      const png = path.join(dir, "poster.png");
      fs.writeFileSync(png, await poster.body());
      const raw = (vf: string, file = png) =>
        execFileSync("ffmpeg", ["-v", "error", "-i", file, "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], { maxBuffer: 1 << 26 });
      const corner = raw("scale=1920:1080,crop=140:90:160:950");
      let gold = 0;
      for (let i = 0; i < corner.length; i += 3) {
        if (Math.abs(corner[i] - 0xc9) < 28 && Math.abs(corner[i + 1] - 0xa2) < 28 && Math.abs(corner[i + 2] - 0x27) < 40) gold++;
      }
      expect(gold, "the gold dot of the CorvinOS mark is drawn in the footer").toBeGreaterThan(40);

      // the video moves and is not blank: an early and a late frame differ, and neither is one flat colour
      const mp4 = path.join(dir, "out.mp4");
      fs.writeFileSync(mp4, bytes);
      const frameAt = (t: number) => execFileSync(
        "ffmpeg", ["-v", "error", "-ss", String(t), "-i", mp4, "-frames:v", "1", "-vf", "scale=96:54,format=gray", "-f", "rawvideo", "-"],
        { maxBuffer: 1 << 24 });
      // a scene opens empty and fills as the narration names its items (ADR-2245), so sample across the video
      const dur = qm.summary.rendered_s;
      const frames = [0.15, 0.3, 0.45, 0.6, 0.75, 0.9].map((f) => frameAt(Math.max(0.2, dur * f)));
      const spread = (b: Buffer) => Math.max(...b) - Math.min(...b);
      expect(Math.max(...frames.map(spread)), "frames are not blank").toBeGreaterThan(20);
      let diff = 0;
      for (const f of frames.slice(1)) {
        let d = 0;
        for (let i = 0; i < Math.min(f.length, frames[0].length); i++) d += Math.abs(f[i] - frames[0][i]);
        diff = Math.max(diff, d);
      }
      expect(diff, "the video changes over time (animation, scene change)").toBeGreaterThan(500);
    } catch (e: any) {
      if (e?.code !== "ENOENT") throw e;
      test.info().annotations.push({ type: "skipped-check", description: "ffmpeg not installed on the test host" });
    }
  });

  test("6b. a PowerPoint deck is imported as a style, previewed, saved, and the produced video carries ITS accent, not the Corvin mark", async ({ page }) => {
    test.setTimeout(600_000);
    await ensure(page, "enabled");
    // Synthetic deck from the plugin's own fixtures (generated, no real data). Needs python3 + Pillow on the test host.
    const fixtures = process.env.CORVIN_STYLE_FIXTURES_DIR
      ?? "/home/shumway/projects/Corvin-Marketplace/plugins/contributor/media/video_producer/tests";
    const deckPath = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "vp-deck-")), "brand.pptx");
    try {
      execFileSync("python3", ["-I", "-c",
        "import sys; sys.path.insert(0, sys.argv[1]); import style_fixtures as f; open(sys.argv[2], 'wb').write(f.make_deck())",
        fixtures, deckPath]);
    } catch (e: any) {
      test.skip(true, `synthetic deck could not be generated (${e?.code ?? "python error"}); set CORVIN_STYLE_FIXTURES_DIR`);
    }
    const ACCENT = [0xc2, 0x18, 0x5b]; // DISTINCT_SCHEME accent1 in style_fixtures.py

    await page.goto("/console/app/video-producer", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("video-producer")).toBeVisible({ timeout: 45_000 });
    const name = `E2E brand ${Date.now()}`;

    await page.getByTestId("deck-input").setInputFiles(deckPath);
    const nameInput = page.getByTestId("style-name");
    await expect(nameInput, "the deck is read and the draft is shown").toBeVisible({ timeout: 60_000 });
    // previews need the web renderer; when present they are three real frames, otherwise the honest empty state
    await expect(page.getByTestId("preview-frames").or(page.getByTestId("previews-empty")).first()).toBeVisible({ timeout: 60_000 });
    if (await page.getByTestId("preview-frames").count()) {
      await expect(page.getByTestId("preview-frames").locator("img")).toHaveCount(3);
    } else {
      test.info().annotations.push({ type: "skipped-check", description: "no preview renderer on the test host" });
    }
    await nameInput.fill(name);
    const saved = page.waitForResponse((r) => r.url().endsWith("/video/styles") && r.request().method() === "POST");
    await page.getByTestId("style-save").click();
    const sres = await saved;
    expect(sres.status(), `POST /video/styles: ${await sres.text()}`).toBe(201);
    const styleId = (await sres.json()).style.id as string;
    await expect(page.getByTestId("style-dialog")).toHaveCount(0);
    await expect(page.getByTestId("style-chip"), "the new style is chosen for the next video").toContainText(name);

    await page.getByTestId("task-input").fill("Explain in two very short scenes why the sky is blue.");
    const created = page.waitForResponse((r) => r.url().includes("/video/jobs") && r.request().method() === "POST");
    await page.getByTestId("start-production").click();
    const res = await created;
    expect(res.request().postDataJSON().style_id, "the chosen style travels with the job").toBe(styleId);
    expect(res.status(), `POST /video/jobs: ${await res.text()}`).toBe(200);
    const jobId = (await res.json()).job_id as string;
    await expect
      .poll(async () => (await (await page.request.get(`${API}/video/jobs/${jobId}`)).json()).status, { timeout: 540_000, intervals: [3_000] })
      .toMatch(/^(complete|completed|error|failed)$/);
    const job = await (await page.request.get(`${API}/video/jobs/${jobId}`)).json();
    expect(job.status, `job error: ${job.error_message}`).toMatch(/^complete/);

    try {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vp-style-"));
      const png = path.join(dir, "poster.png");
      fs.writeFileSync(png, await (await page.request.get(`${API}/video/videos/${jobId}/poster`)).body());
      const rgb = (vf: string) => execFileSync("ffmpeg", ["-v", "error", "-i", png, "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], { maxBuffer: 1 << 26 });
      const near = (b: Buffer, c: number[], tol: number) => {
        let n = 0;
        for (let i = 0; i < b.length; i += 3) if (Math.abs(b[i] - c[0]) < tol && Math.abs(b[i + 1] - c[1]) < tol && Math.abs(b[i + 2] - c[2]) < tol) n++;
        return n;
      };
      expect(near(rgb("scale=480:270"), ACCENT, 30), "the poster carries the imported accent colour").toBeGreaterThan(20);
      // the footer corner where the CorvinOS mark's gold dot (#C9A227) sits in a built-in video
      expect(near(rgb("scale=1920:1080,crop=140:90:160:950"), [0xc9, 0xa2, 0x27], 28), "the CorvinOS mark is not drawn").toBeLessThan(10);
    } catch (e: any) {
      if (e?.code !== "ENOENT") throw e;
      test.info().annotations.push({ type: "skipped-check", description: "ffmpeg not installed on the test host" });
    }

    // clean up through the UI so later tests start from a style-less tenant
    await page.getByTestId("styles-toggle").click();
    await page.getByTestId(`style-delete-${styleId}`).click();
    await page.getByTestId(`style-delete-confirm-${styleId}`).click();
    await expect(page.getByTestId(`style-row-${styleId}`)).toHaveCount(0);
  });

  test("7. disable removes the panel; uninstall via the UI removes everything, panel gone", async ({ page }) => {
    await ensure(page, "enabled");
    await page.goto("/console/app/marketplace?tab=installed", { waitUntil: "domcontentloaded" });
    const row = page.getByTestId(`installed-row-${REGISTRY_ID}`);
    await expect(row).toBeVisible({ timeout: 45_000 });
    await expect(sidebarLink(page)).toHaveCount(1, { timeout: 30_000 });

    // uninstalling an ENABLED plugin is refused (the UI says why; the record stays)
    await row.getByRole("button", { name: /^Uninstall$/ }).click();
    await expect(page.getByTestId(`installed-msg-${REGISTRY_ID}`)).toContainText(/disable the plugin first/i);
    expect((await entry(page)).installed).toBe(true);

    await row.getByRole("button", { name: /^Disable$/ }).click();
    await expect(page.getByTestId(`installed-msg-${REGISTRY_ID}`)).toContainText(/Disabled/i, { timeout: 30_000 });
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByTestId(`installed-row-${REGISTRY_ID}`)).toBeVisible({ timeout: 45_000 });
    await expect(sidebarLink(page), "disabled -> panel is gone").toHaveCount(0, { timeout: 30_000 });

    await page.getByTestId(`installed-row-${REGISTRY_ID}`).getByRole("button", { name: /^Uninstall$/ }).click();
    await page.getByRole("button", { name: new RegExp(`Confirm: remove ${REGISTRY_ID}`) }).click();
    await expect(page.getByTestId(`installed-msg-${REGISTRY_ID}`)).toContainText(/Uninstalled/i, { timeout: 30_000 });

    expect(await entry(page)).toMatchObject({ installed: false, enabled: false });
    expect(onDisk(), "registry record, instance dir and panel entry are all gone").toEqual({ registry: false, instance: false, panel: false });
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(sidebarLink(page)).toHaveCount(0);
    // the deep link no longer renders the plugin's page
    await page.goto("/console/app/video-producer", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("video-producer")).toHaveCount(0, { timeout: 15_000 });
  });

  test("8. the audit trail records install, enable and uninstall and outlives the plugin", async ({ page }) => {
    await ensure(page, "uninstalled");
    await ensure(page, "enabled");
    await ensure(page, "uninstalled");
    const chain = path.join(HOME, "tenants", "_default", "global", "forge", "audit.jsonl");
    const events = fs.readFileSync(chain, "utf8").trim().split("\n").map((l) => JSON.parse(l));
    const actions = events
      .filter((e) => e.event_type === "console.action_performed" && String(e.details?.target_id) === INDEX_ID)
      .map((e) => e.details.action);
    expect(actions).toEqual(expect.arrayContaining(["marketplace.install", "marketplace.enable", "marketplace.uninstall"]));
    // hash chain is linked end to end (each record points at its predecessor)
    for (let i = 1; i < events.length; i++) expect(events[i].prev_hash, `record ${i}`).toBe(events[i - 1].hash);
  });

  // ── adversarial ────────────────────────────────────────────────────────────

  test.describe("adversarial", () => {
    test("A1. installing twice is idempotent: one record, no error", async ({ page }) => {
      await ensure(page, "uninstalled");
      const a = await call(page, "POST", `${MP}/plugins/${enc(INDEX_ID)}/install`, { wait: true });
      const b = await call(page, "POST", `${MP}/plugins/${enc(INDEX_ID)}/install`, { wait: true });
      expect(a.body?.status, a.text).toBe("completed");
      expect(b.body?.status, b.text).toBe("completed");
      expect(b.body?.already_installed).toBe(true);
      expect(registryRecordCount()).toBe(1);
    });

    test("A2. N concurrent installs leave exactly one intact record and no 5xx", async ({ page }) => {
      await ensure(page, "uninstalled");
      const results = await Promise.all(
        Array.from({ length: 5 }, () => call(page, "POST", `${MP}/plugins/${enc(INDEX_ID)}/install`, { wait: true })),
      );
      for (const r of results) expect(r.status, r.text).toBeLessThan(500);
      expect(results.filter((r) => r.body?.status === "completed").length).toBeGreaterThanOrEqual(1);
      expect(registryRecordCount()).toBe(1);
      expect(await entry(page)).toMatchObject({ installed: true, enabled: false });
      // and the registry file is still parseable by the console (listing works)
      expect((await page.request.get(`${API}/plugins`)).status()).toBe(200);
    });

    test("A3. uninstall racing an in-flight install ends consistent (all-or-nothing), never half-installed", async ({ page }) => {
      for (let round = 0; round < 3; round++) {
        await ensure(page, "uninstalled");
        const started = await call(page, "POST", `${MP}/plugins/${enc(INDEX_ID)}/install`, { wait: false });
        expect(started.status, started.text).toBeLessThan(500);
        const jobId = started.body?.job_id as string;
        const un = await call(page, "POST", `${MP}/plugins/${enc(INDEX_ID)}/uninstall`, {});
        expect(un.status, `uninstall during install (round ${round}): ${un.text}`).toBeLessThan(500);
        await expect
          .poll(async () => (await (await page.request.get(`${MP}/install/${jobId}/progress`)).json()).status, { timeout: 60_000 })
          .toMatch(/^(completed|failed)$/);
        const d = onDisk();
        const e = await entry(page);
        // whichever side won, the three on-disk traces and the API agree with each other
        expect(d.registry, `round ${round}: ${JSON.stringify(d)}`).toBe(e.installed);
        expect(d.instance, `round ${round}: ${JSON.stringify(d)}`).toBe(e.installed);
        expect(registryRecordCount()).toBeLessThanOrEqual(1);
      }
      // the install is still recoverable afterwards
      await ensure(page, "uninstalled");
      await ensure(page, "installed");
    });

    test("A4. uninstalling an enabled plugin is refused (409) and changes nothing", async ({ page }) => {
      await ensure(page, "enabled");
      const r = await call(page, "POST", `${MP}/plugins/${enc(INDEX_ID)}/uninstall`, {});
      expect(r.status, r.text).toBe(409);
      expect(await entry(page)).toMatchObject({ installed: true, enabled: true });
      expect(onDisk().registry).toBe(true);
    });

    test("A5. enabling without consent is refused (409) — the consent gate is not bypassable", async ({ page }) => {
      await ensure(page, "installed");
      const r = await call(page, "PATCH", `${MP}/plugins/${enc(INDEX_ID)}/enable`, {});
      expect(r.status, r.text).toBe(409);
      expect((await entry(page)).enabled).toBe(false);
      await expect(async () => {
        const caps = await (await page.request.get(`${API}/capabilities`)).json();
        expect(caps.plugin_panels).toEqual([]);
      }).toPass();
    });

    test("A6. uninstalling something that is not installed answers 404, not 500", async ({ page }) => {
      await ensure(page, "uninstalled");
      const r = await call(page, "POST", `${MP}/plugins/${enc(INDEX_ID)}/uninstall`, {});
      expect(r.status, r.text).toBe(404);
    });

    test("A7. enable / disable of a plugin that is not installed fail cleanly (4xx, not 500)", async ({ page }) => {
      await ensure(page, "uninstalled");
      for (const verb of ["enable", "disable"]) {
        const r = await call(page, "PATCH", `${MP}/plugins/${enc(INDEX_ID)}/${verb}`, { consent_granted: true });
        expect(r.status, `${verb}: ${r.text}`).toBeLessThan(500);
        expect(r.status).toBeGreaterThanOrEqual(400);
      }
      expect(onDisk()).toEqual({ registry: false, instance: false, panel: false });
    });

    test("A8. unknown / malformed plugin ids never install, never touch the filesystem outside the registry", async ({ page }) => {
      await ensure(page, "uninstalled");
      const ids = [
        "plugin:contributor-media-does_not_exist",
        "plugin:contributor-media-..%2F..%2Fetc",
        "plugin:buildin-memory-",
        "../../etc/passwd",
        "plugin:contributor-media-video_producer%00",
        "x".repeat(300),
        "plugin:evil-media-video_producer",
      ];
      for (const id of ids) {
        const r = await call(page, "POST", `${MP}/plugins/${id.includes("%") ? id : enc(id)}/install`, { wait: true });
        expect(r.status, `${id}: ${r.text}`).toBeLessThan(500);
        if (r.status === 200) expect(r.body?.status, `${id}: ${r.text}`).toBe("failed");
      }
      expect(onDisk()).toEqual({ registry: false, instance: false, panel: false });
      expect((await page.request.get(`${API}/plugins`)).status()).toBe(200);
    });

    test("A9. dependency plan: unknown ids are refused; the Video Producer has none missing", async ({ page }) => {
      const ok = await page.request.get(`${MP}/plugins/${enc(INDEX_ID)}/dependencies`);
      expect(ok.status()).toBe(200);
      expect((await ok.json()).dependency_tree.missing).toBe(false);
      const bad = await page.request.get(`${MP}/plugins/${enc("plugin:contributor-media-nope")}/dependencies`);
      expect(bad.status()).toBeGreaterThanOrEqual(400);
      expect(bad.status()).toBeLessThan(500);
    });

    test("A10. mutations need a session and a CSRF token", async ({ page, playwright, baseURL }) => {
      await ensure(page, "uninstalled");
      const noCsrf = await call(page, "POST", `${MP}/plugins/${enc(INDEX_ID)}/install`, { wait: true }, { csrf: false });
      expect(noCsrf.status).toBe(403);
      const anon = await playwright.request.newContext({ baseURL: baseURL! });
      const r = await anon.post(`${MP}/plugins/${enc(INDEX_ID)}/install`, { data: { wait: true } });
      expect(r.status()).toBe(401);
      await anon.dispose();
      expect(onDisk().registry).toBe(false);
    });

    test("A11. the video API refuses work while the plugin is not installed+enabled", async ({ page }) => {
      for (const state of ["uninstalled", "installed"] as const) {
        await ensure(page, state);
        const r = await call(page, "POST", `${API}/video/jobs`, { task: "should be refused" });
        expect([403, 404, 409]).toContain(r.status); // refused because of the plugin state...
        expect(r.text).not.toMatch(/not available/i); // ...not because the module happens to be unimportable
      }
    });

    test("A12. full cycle twice: reinstall after uninstall works and leaves no residue", async ({ page }) => {
      for (let i = 0; i < 2; i++) {
        await ensure(page, "enabled");
        const panels = (await (await page.request.get(`${API}/capabilities`)).json()).plugin_panels as any[];
        expect(panels.map((p) => p.route), `cycle ${i}`).toContain("video-producer");
        await ensure(page, "uninstalled");
        const after = (await (await page.request.get(`${API}/capabilities`)).json()).plugin_panels as any[];
        expect(after.map((p) => p.route), `cycle ${i}`).not.toContain("video-producer");
        expect(onDisk(), `cycle ${i}`).toEqual({ registry: false, instance: false, panel: false });
      }
    });

    test("A13. host prerequisites the plugin shells out to are present (ffmpeg)", async () => {
      let found = true;
      try { execFileSync("ffmpeg", ["-version"], { stdio: "ignore" }); } catch { found = false; }
      test.skip(!found, "ffmpeg missing on the test host — test 6 cannot produce a video here");
    });
  });
});
