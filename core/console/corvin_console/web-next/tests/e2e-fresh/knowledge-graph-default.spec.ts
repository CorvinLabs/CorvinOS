/**
 * Knowledge Graph — a first-party standard plugin: on a FRESH install it is installed AND enabled by
 * boot alone, with no operator step and no consent prompt, the tenant's own knowledge base is created
 * and bound to the task board, and the concept / ADR / implementation-plan gates work on that board
 * (ADR-2265).
 *
 *   boot (empty CORVIN_HOME, no local Corvin-Knowledge, no sibling marketplace)
 *     -> default_plugins.yaml installs + enables the plugin (buildin tier: vetted, no consent)
 *     -> the projector creates <tenant>/knowledge (git + kb engine) — nobody clicks anything
 *     -> the operator works in it with its own scripts/kb.py -> items on the board,
 *        gate findings shrink G2/G3 -> G4 only, the graph panel reads the same KB.
 *
 * Run (network + built SPA):  npx playwright test -c playwright.fresh-install.config.ts knowledge-graph
 * Nothing in this file installs or enables the plugin; a missing or disabled plugin is the bug under test.
 */
import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = process.env.CORVIN_FRESH_ROOT ?? "/tmp/corvin-fresh-install";
const HOME = path.join(ROOT, "home");
const TENANT = path.join(HOME, "tenants", "_default");
const KB = path.join(TENANT, "knowledge");
const API = "/v1/console";
const LIST = `${API}/api/v1/marketplace/default-plugins`;
const KG = "plugin:buildin-knowledge_management-corvin_knowledge";
const WALK = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "kb_gate_walkthrough.py");

async function login(page: Page): Promise<string> {
  const r = await page.request.get(`${API}/auth/local-login`, { maxRedirects: 0 });
  expect([200, 302]).toContain(r.status());
  await expect
    .poll(async () => (await page.request.get(`${API}/auth/whoami`)).status(), { timeout: 20_000 })
    .toBe(200);
  return (await (await page.request.get(`${API}/auth/whoami`)).json()).csrf_token as string;
}

const kg = async (page: Page) =>
  ((await (await page.request.get(LIST)).json()).plugins as any[]).find((p) => p.index_id === KG);
const kbStatus = async (page: Page) => (await page.request.get(`${API}/task-tracking/kb/status`)).json();
const walk = (step: string) => JSON.parse(execFileSync("python3", [WALK, KB, step], { encoding: "utf8" }));
const kbItems = async (page: Page) =>
  ((await (await page.request.get(`${API}/task-tracking/items`)).json()).items as any[])
    .filter((i) => String(i.external_ref ?? "").startsWith("kb:"));

test.describe.serial("Knowledge Graph on a fresh install", () => {
  test("boot alone installs AND enables it — no consent, no click", async ({ page }) => {
    await login(page);
    await expect
      .poll(async () => JSON.stringify(await kg(page)), { timeout: 100_000, intervals: [2_000] })
      .toContain('"installed":true');
    const e = await kg(page);
    expect(e, JSON.stringify(e)).toMatchObject({ registry_id: "corvin_knowledge", offered: true, installed: true });
    expect(e.installed_version >= "1.2.0", e.installed_version).toBe(true);
    await expect.poll(async () => (await kg(page)).enabled, { timeout: 30_000, intervals: [1_000] }).toBe(true);
    const registry = fs.readFileSync(path.join(TENANT, "plugins", "registry.yaml"), "utf8");
    expect(registry).toMatch(/^  corvin_knowledge:\s*$/m);
    // first-party: vetted origin, nothing asked of the operator
    const rec = ((await (await page.request.get(`${API}/plugins`)).json()).plugins as any[])
      .find((p) => p.plugin_id === "corvin_knowledge");
    expect(rec, "corvin_knowledge in the plugin list").toBeTruthy();
    expect(rec).toMatchObject({ origin: "vetted", requires_consent: false, enabled: true });
    // the enable is on the audit chain, attributed to the boot provisioner
    const audit = fs.readFileSync(path.join(TENANT, "global", "forge", "audit.jsonl"), "utf8");
    expect(audit).toContain("marketplace.enable");
  });

  test("the tenant knowledge base is created and bound with no restart and no operator step", async ({ page }) => {
    await login(page);
    await expect
      .poll(async () => { const s = await kbStatus(page); return `${s.enabled}/${s.state}`; },
        { timeout: 60_000, intervals: [1_000] })
      .toBe("true/ok");
    expect(fs.existsSync(path.join(KB, ".git"))).toBe(true);
    expect(fs.existsSync(path.join(KB, "scripts", "kb.py"))).toBe(true);
    const marker = JSON.parse(fs.readFileSync(path.join(KB, "kb", "_meta", "tenant_kb.json"), "utf8"));
    expect(marker.engine_commit).toMatch(/^[0-9a-f]{40}$/);
    const audit = fs.readFileSync(path.join(TENANT, "global", "forge", "audit.jsonl"), "utf8");
    expect(audit).toContain("kb.tenant_created");
    // the task board has no "enable the plugin" banner: nothing is missing
    await page.goto("/console/app/initiatives", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading").first()).toBeVisible({ timeout: 45_000 });
    await expect(page.getByTestId("kb-enable-plugin-banner")).toHaveCount(0);
  });

  test("work created in the knowledge base reaches the board", async ({ page }) => {
    await login(page);
    expect(walk("create").created).toEqual(["I-01", "CONCEPT-0001", "ADR-0001", "E-001", "T-0001"]);
    await expect
      .poll(async () => (await kbItems(page)).map((i) => `${i.kind}:${String(i.title).split(" · ")[0]}`).sort().join(","),
        { timeout: 30_000, intervals: [1_000] })
      .toBe("epic:E-001,initiative:I-01,task:T-0001");
    await page.goto("/console/app/initiatives", { waitUntil: "domcontentloaded" });
    await expect(page.getByText("E-001 · Build OIDC sign-in").first()).toBeVisible({ timeout: 45_000 });
  });

  test("concept, ADR and implementation-plan gates: findings close as the operator links the work", async ({ page }) => {
    await login(page);
    const before = walk("gates");
    // concept gate (G1) holds already: the decision names its concept; the epic implements the ADR
    expect(before.gates).not.toContain("G1_no_concept");
    expect(before.gates).toEqual(expect.arrayContaining(["G2_no_backedge", "G3_no_path_overlap"]));
    expect(before.ready).toBe(false);
    walk("close");
    const after = walk("gates");
    expect(after.blocking).toBe(0);
    // G1-G3 hold; G4 (a closed review) is the last one before "ready"
    expect(after.gates).toEqual(["G4_no_review"]);
    expect(after.ready).toBe(false);
    // the board followed the commit and still projects a consistent KB
    await expect.poll(async () => (await kbStatus(page)).state, { timeout: 30_000 }).toBe("ok");
    const epic = (await kbItems(page)).find((i) => String(i.title).startsWith("E-001"));
    expect(epic.labels).not.toContain("ready");
  });

  test("the Knowledge Graph panel reads the same knowledge base", async ({ page }) => {
    await login(page);
    const cfg = await (await page.request.get(`${API}/plugins/corvin-knowledge/config`)).json();
    expect(cfg.repo_path).toBe(KB);
    await expect
      .poll(async () => ((await (await page.request.get(`${API}/plugins/corvin-knowledge/graph`)).json()).entities as any[])
        .map((e) => e.label).sort().join(","), { timeout: 30_000, intervals: [1_000] })
      .toBe("ADR-0001,CONCEPT-0001,E-001,I-01,T-0001");
    await page.goto("/console/app/", { waitUntil: "domcontentloaded" });
    await expect(page.locator('nav a[href*="corvin-knowledge"]:visible').first()).toBeVisible({ timeout: 45_000 });
  });
});
