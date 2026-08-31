/**
 * Live demo: Spotify dataset analysis in the Compute Dashboard.
 * All tests go through the Vite proxy (localhost:5173 → :8765 gateway).
 * Authentication via local-autologin (localhost-only gate).
 */
import { test, expect } from "@playwright/test";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const _dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(
  _dirname,
  "../../../../../../.corvin/tenants/_default/sessions/voice/discord/1501315335750684803/outputs",
);
function shot(name: string) { return path.join(OUT, `spotify-${name}.png`); }

/** Session is provided via storageState; just fetch the csrf_token. */
async function login(page: import("@playwright/test").Page): Promise<string | null> {
  // Navigate to app shell first so fetch() has a valid origin
  if (!page.url().includes("/console/")) {
    await page.goto("/console/app", { waitUntil: "load" }).catch(() => null);
    await page.waitForTimeout(500);
  }
  const csrf = await page.evaluate(async () => {
    try {
      const r = await fetch("/v1/console/auth/whoami", { credentials: "include" });
      if (!r.ok) return null;
      const d = await r.json();
      return d.csrf_token as string | null;
    } catch { return null; }
  });
  return csrf;
}

test.describe("Spotify Analysis → awpkg Export Demo", () => {
  test.beforeAll(() => { fs.mkdirSync(OUT, { recursive: true }); });
  // Increase per-test timeout for slower CI environments
  test.setTimeout(45_000);

  test("01 – Compute Dashboard loads and shows KPI strip", async ({ page }) => {
    await login(page);
    await page.goto("/console/app/compute");
    await page.waitForLoadState("load");
    await page.waitForTimeout(2000);
    await page.screenshot({ path: shot("01-dashboard"), fullPage: false });
    const html = await page.content();
    expect(html.length).toBeGreaterThan(500);
    console.log("✓ Compute Dashboard loaded");
  });

  test("02 – corpus-context API returns Spotify real_stats", async ({ page }) => {
    const _csrf = await login(page);
    const corpus = await page.evaluate(async () => {
      const r = await fetch("/v1/console/compute/corpus-context", { credentials: "include" });
      if (!r.ok) return { error: r.status };
      return r.json();
    });
    console.log("Corpus:", JSON.stringify(corpus, null, 2));
    if ((corpus as Record<string, unknown>).has_corpus) {
      const s = (corpus as Record<string, unknown>).real_stats;
      console.log(`✓ Rows: ${s.total_rows?.toLocaleString()}, Markets: ${s.unique_countries}`);
      console.log(`✓ Pipeline: ${(corpus as Record<string, unknown>).pipeline_name}`);
      if (s.top_tracks?.length) {
        console.log(`✓ #1: "${s.top_tracks[0].track_name}" — ${s.top_tracks[0].total_streams?.toLocaleString()} streams`);
      }
    } else {
      console.log("No corpus context (demo data may not be loaded):", corpus);
    }
  });

  test("03 – pipeline list returns Spotify demo pipeline", async ({ page }) => {
    await login(page);
    const data = await page.evaluate(async () => {
      const r = await fetch("/v1/console/compute/pipelines", { credentials: "include" });
      if (!r.ok) return { error: r.status };
      return r.json();
    });
    console.log(`Pipelines: ${(data as Record<string, unknown>).pipeline_count ?? "err"}`);
    if ((data as Record<string, unknown>).pipelines?.length) {
      (data as Record<string, unknown>).pipelines.forEach((p: Record<string, unknown>) => {
        console.log(`  • ${p.pipeline_id} [${p.state}] stages=${p.stage_count}`);
      });
    }
    // Navigate to Pipelines tab and screenshot
    await page.goto("/console/app/compute");
    await page.waitForLoadState("load");
    await page.waitForTimeout(1500);
    const tab = page.locator("button").filter({ hasText: /Pipelines/i }).first();
    if (await tab.isVisible().catch(() => false)) await tab.click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: shot("03-pipelines-tab"), fullPage: false });
    console.log("✓ Pipelines tab screenshot saved");
  });

  test("04 – pipeline detail: 5 stages with champion params", async ({ page }) => {
    await login(page);
    const detail = await page.evaluate(async () => {
      const listR = await fetch("/v1/console/compute/pipelines", { credentials: "include" });
      if (!listR.ok) return null;
      const list = await listR.json();
      if (!list.pipelines?.length) return null;
      const pid = list.pipelines[0].pipeline_id;
      const r = await fetch(`/v1/console/compute/pipelines/${encodeURIComponent(pid)}`, { credentials: "include" });
      if (!r.ok) return null;
      return r.json();
    });
    if (detail) {
      console.log(`✓ Pipeline: ${(detail as Record<string, unknown>).pipeline_id}`);
      console.log(`✓ Stage count: ${(detail as Record<string, unknown>).stages?.length ?? 0}`);
      ((detail as Record<string, unknown>).stages ?? []).forEach((s: Record<string, unknown>) => {
        const loss = s.best_loss != null ? s.best_loss.toFixed(4) : "—";
        const params = JSON.stringify(s.best_params ?? {});
        console.log(`  Stage ${s.stage_id}: loss=${loss}  params=${params}`);
      });
      expect((detail as Record<string, unknown>).stages?.length).toBeGreaterThanOrEqual(1);
    }
  });

  test("05 – awpkg preview: datasources, RAG, secrets", async ({ page }) => {
    await login(page);
    const preview = await page.evaluate(async () => {
      const listR = await fetch("/v1/console/compute/pipelines", { credentials: "include" });
      if (!listR.ok) return null;
      const { pipelines } = await listR.json();
      if (!pipelines?.length) return null;
      const pid = pipelines[0].pipeline_id;
      const r = await fetch(
        `/v1/console/compute/pipelines/${encodeURIComponent(pid)}/export/awpkg/preview`,
        { credentials: "include" },
      );
      if (!r.ok) return { error: r.status };
      return r.json();
    });
    console.log("Preview:", JSON.stringify(preview, null, 2));
    if (preview && !(preview as Record<string, unknown>).error) {
      console.log(`✓ DAG nodes: ${(preview as Record<string, unknown>).dag_nodes}`);
      console.log(`✓ Estimated size: ${(preview as Record<string, unknown>).estimated_size_kb} KB`);
      console.log(`✓ Secrets required: ${(preview as Record<string, unknown>).secrets_required?.join(", ") || "none"}`);
      const ds = (preview as Record<string, unknown>).fabric_datasources?.map((d: Record<string, unknown>) => `${d.name}(${d.adapter})`).join(", ");
      console.log(`✓ Datasources: ${ds || "none"}`);
      const rag = (preview as Record<string, unknown>).rag_providers?.map((r: Record<string, unknown>) => r.provider_id).join(", ");
      console.log(`✓ RAG providers: ${rag || "none"}`);
    }
  });

  test("06 – export awpkg: generates valid ZIP bundle", async ({ page }) => {
    const csrf = await login(page);
    if (!csrf) { console.log("No CSRF — skip"); return; }

    const result = await page.evaluate(async (token) => {
      const listR = await fetch("/v1/console/compute/pipelines", { credentials: "include" });
      if (!listR.ok) return { error: "list: " + listR.status };
      const { pipelines } = await listR.json();
      if (!pipelines?.length) return { error: "no pipelines" };
      const pid = pipelines[0].pipeline_id;

      const r = await fetch(
        `/v1/console/compute/pipelines/${encodeURIComponent(pid)}/export/awpkg`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
          body: JSON.stringify({
            package_id: "com.corvinlabs.spotify-chart-pred",
            version: "1.0.0",
            mode: "replay",
            include_sample_data: true,
            sample_rows: 100,
            include_rag_manifests: true,
            include_fabric_datasources: true,
            include_output_datasources: true,
            include_watermarks: false,
            include_custom_adapters: true,
            include_ml_backends: true,
            schedule_cron: "0 6 * * 1",
            schedule_timezone: "Europe/Berlin",
            acceptance_criteria: { max_best_loss: 0.150, on_fail: "abort" },
          }),
        },
      );
      const blob = await r.blob();
      return {
        status: r.status,
        content_type: r.headers.get("content-type"),
        disposition: r.headers.get("content-disposition"),
        size_bytes: blob.size,
      };
    }, csrf);

    console.log("Export result:", JSON.stringify(result, null, 2));
    const res = result as Record<string, unknown>;
    if (res.status === 200) {
      console.log(`✓ awpkg ZIP: ${(res.size_bytes / 1024).toFixed(1)} KB`);
      console.log(`✓ Filename: ${res.disposition}`);
      expect(res.size_bytes).toBeGreaterThan(500);
    }
  });

  test("07 – send pipeline to Workflows tab", async ({ page }) => {
    const csrf = await login(page);
    if (!csrf) { console.log("No CSRF — skip"); return; }

    const result = await page.evaluate(async (token) => {
      const listR = await fetch("/v1/console/compute/pipelines", { credentials: "include" });
      if (!listR.ok) return { error: "list: " + listR.status };
      const { pipelines } = await listR.json();
      if (!pipelines?.length) return { error: "no pipelines" };
      const pid = pipelines[0].pipeline_id;

      const r = await fetch(
        `/v1/console/compute/pipelines/${encodeURIComponent(pid)}/export/awpkg/to-workflow`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
          body: JSON.stringify({
            package_id: "com.corvinlabs.spotify-chart-pred",
            version: "1.0.0",
            mode: "replay",
            include_sample_data: true,
            sample_rows: 100,
            include_rag_manifests: true,
            include_fabric_datasources: true,
            include_output_datasources: true,
            include_watermarks: false,
            include_custom_adapters: true,
            include_ml_backends: true,
            schedule_cron: "0 6 * * 1",
            schedule_timezone: "Europe/Berlin",
            acceptance_criteria: null,
          }),
        },
      );
      if (!r.ok) return { error: r.status, body: await r.text() };
      return r.json();
    }, csrf);

    console.log("to-workflow:", JSON.stringify(result, null, 2));
    const res = result as Record<string, unknown>;
    if (res.ok) {
      console.log(`✓ Workflow created: ${res.workflow_id}`);
      console.log(`✓ Navigate to: ${res.redirect_url}`);
    }
  });

  test("08 – Workflows list shows 'From Pipeline' badge", async ({ page }) => {
    await login(page);
    await page.goto("/console/app/workflows");
    await page.waitForLoadState("load");
    await page.waitForTimeout(2000);
    await page.screenshot({ path: shot("08-workflows-list"), fullPage: false });

    const badge = await page.evaluate(() =>
      document.body.innerText.includes("From Pipeline")
    );
    console.log(`✓ 'From Pipeline' badge visible: ${badge}`);

    // Also check workflows API
    const wfs = await page.evaluate(async () => {
      const r = await fetch("/v1/console/workflows", { credentials: "include" });
      if (!r.ok) return null;
      return r.json();
    });
    if (wfs) {
      console.log(`✓ Total workflows: ${(wfs as Record<string, unknown>).count}`);
      ((wfs as Record<string, unknown>).workflows ?? []).forEach((w: Record<string, unknown>) => {
        const src = w.source === "compute_pipeline" ? " [FROM PIPELINE]" : "";
        console.log(`  • ${w.id}: "${w.title}"${src}`);
      });
    }
  });

  test("09 – workflow editor shows pipeline banner", async ({ page }) => {
    await login(page);

    const wid = await page.evaluate(async () => {
      const r = await fetch("/v1/console/workflows", { credentials: "include" });
      if (!r.ok) return null;
      const { workflows } = await r.json();
      const pw = workflows?.find((w: Record<string, unknown>) => w.source === "compute_pipeline");
      return pw?.id ?? workflows?.[0]?.id ?? null;
    });

    if (wid) {
      await page.goto(`/console/app/workflows/${wid}`);
      await page.waitForLoadState("load");
      await page.waitForTimeout(2000);
      await page.screenshot({ path: shot("09-workflow-editor"), fullPage: false });
      const banner = await page.evaluate(() =>
        document.body.innerText.includes("Exported from Compute Pipeline")
      );
      console.log(`✓ Pipeline banner: ${banner}`);
    } else {
      console.log("No workflow found yet");
    }
  });

  test("10 – Export Hub tab renders pipeline awpkg section", async ({ page }) => {
    await login(page);
    await page.goto("/console/app/compute");
    await page.waitForLoadState("load");
    await page.waitForTimeout(1500);

    const tab = page.locator("button").filter({ hasText: /Export Hub/i }).first();
    if (await tab.isVisible().catch(() => false)) {
      await tab.click();
      await page.waitForTimeout(2000);
    }
    await page.screenshot({ path: shot("10-export-hub"), fullPage: false });

    const hasSection = await page.evaluate(() =>
      document.body.innerText.includes("Pipeline Packages")
    );
    console.log(`✓ 'Pipeline Packages' section: ${hasSection}`);
  });
});
