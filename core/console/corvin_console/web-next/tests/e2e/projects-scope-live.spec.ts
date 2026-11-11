/**
 * The knowledge-base switcher on the RUNNING console, nothing stubbed: the Tasks board and the Knowledge Graph for All,
 * Corvin and a project with a knowledge base, a project without one (listed, disabled, with its reason), an unknown
 * ?scope= id and a reload - in both themes at 1280 and 375 px, saved as screenshots for a human to look at.
 *
 * Needs a project WITH a knowledge base (SCOPE_PROJECT, default "t") and one WITHOUT (SCOPE_NO_KB, default
 * "explorer-demo") registered on the console. Output: SHOTS_DIR (default /tmp/scope-switch).
 * Run: CORVIN_E2E_ALLOW_LIVE=1 CONSOLE_BASE_URL=http://127.0.0.1:8765/console \
 *      npx playwright test -c playwright.config.ts projects-scope-live --project=chromium
 */
import fs from "node:fs";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";

const PID = process.env.SCOPE_PROJECT ?? "t";
const NO_KB = process.env.SCOPE_NO_KB ?? "explorer-demo";
const OUT = process.env.SHOTS_DIR ?? "/tmp/scope-switch";
const SIZES = [{ name: "1280", width: 1280, height: 800 }, { name: "375", width: 375, height: 812 }];

async function theme(page: Page, t: "light" | "dark") {
  await page.addInitScript((v) => { try { localStorage.setItem("corvin-theme", v); localStorage.setItem("corvin-dark-default-v2", "1"); } catch { /* ignore */ } }, t);
}

async function shot(page: Page, name: string, t: string, size: string) {
  fs.mkdirSync(OUT, { recursive: true });
  expect(await page.locator("html").getAttribute("data-theme")).toBe(t);
  await page.screenshot({ path: path.join(OUT, `${name}-${t}-${size}.png`), fullPage: false });
}

/** "<n> of <m> nodes shown" -> m */
async function graphTotal(page: Page): Promise<number> {
  const txt = (await page.getByTestId("knowledge-summary").textContent()) ?? "";
  return Number(/of (\d+) nodes/.exec(txt)?.[1] ?? NaN);
}

for (const t of ["light", "dark"] as const) {
  for (const s of SIZES) {
    test(`knowledge-base switcher, ${t}, ${s.name}`, async ({ page }) => {
      test.setTimeout(180_000);
      await theme(page, t);
      await page.setViewportSize({ width: s.width, height: s.height });

      // --- Tasks: All (default) ---
      await page.goto("/console/app/initiatives", { waitUntil: "domcontentloaded" });
      const counts = page.getByTestId("tasks-scope-counts");
      await expect(counts).toContainText("All knowledge bases:", { timeout: 60_000 });
      await expect(page.getByTestId("scope-select")).toHaveValue("all");
      const noKb = page.getByTestId("scope-select").locator(`option[value="project:${NO_KB}"]`);
      await expect(noKb).toHaveAttribute("disabled", "");
      await expect(noKb).toContainText("no knowledge base - switch it on in Features");
      await expect(page.getByTestId("scope-unavailable").locator(`a[href$="/app/projects/${NO_KB}?tab=features"]`)).toHaveCount(1);
      await shot(page, "tasks-all", t, s.name);

      // --- Tasks: the project; the counters are its own and match the list ---
      await page.getByTestId("scope-select").selectOption(`project:${PID}`);
      await expect(page).toHaveURL(new RegExp(`scope=project(%3A|:)${PID}`));
      await expect(counts).toContainText(`: `, { timeout: 30_000 });
      // the project's answer can take a while on a busy host (the projector may be rebuilding its graph): wait for it, not 5 s
      await expect(counts).not.toContainText("All knowledge bases", { timeout: 30_000 });
      await expect(page.getByTestId("kpi-scope-note")).not.toContainText("All knowledge bases", { timeout: 30_000 });
      const itemsText = (await counts.textContent()) ?? "";
      const nItems = Number(/: (\d+) items/.exec(itemsText)?.[1] ?? NaN);
      expect(nItems).toBeGreaterThan(0);
      expect(nItems).toBeLessThan(100);   // the project's own few items, not the whole store
      await shot(page, "tasks-project", t, s.name);
      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(page.getByTestId("scope-select")).toHaveValue(`project:${PID}`, { timeout: 60_000 });
      await expect(counts).toContainText(`: ${nItems} items`, { timeout: 60_000 });

      // --- Tasks: Corvin ---
      await page.getByTestId("scope-select").selectOption("bound");
      await expect(counts).toContainText("Corvin:", { timeout: 30_000 });

      // --- Tasks: an unknown and an unavailable id fall back to All, saying why ---
      await page.goto("/console/app/initiatives?scope=project:no-such-project", { waitUntil: "domcontentloaded" });
      await expect(page.getByTestId("scope-note")).toHaveText('There is no project "no-such-project". Showing All knowledge bases instead.', { timeout: 60_000 });
      await expect(page.getByTestId("scope-select")).toHaveValue("all");
      await expect(counts).toContainText("All knowledge bases:", { timeout: 60_000 });
      await shot(page, "tasks-unknown", t, s.name);
      await page.goto(`/console/app/initiatives?scope=project:${NO_KB}`, { waitUntil: "domcontentloaded" });
      await expect(page.getByTestId("scope-note")).toContainText(`cannot be shown: no knowledge base`, { timeout: 60_000 });

      // --- Graph: the default scope. It is "Corvin" when the graph reads Corvin's own knowledge base; when its Repository
      // setting names another one, the default is named after that folder and Corvin is listed on its own (REVIEW-0067 M4).
      // what is true on this install, read from the API: the graph setting's folder vs the built-in project's folder
      const cfg = await (await page.request.get("/v1/console/plugins/corvin-knowledge/config")).json() as { repo_path: string };
      const list = await (await page.request.get("/v1/console/projects")).json() as { projects: { builtin?: boolean; path: string }[] };
      const builtinPath = list.projects.find((p) => p.builtin)?.path ?? "";
      const separate = !!builtinPath && cfg.repo_path.replace(/\/+$/, "") !== builtinPath.replace(/\/+$/, "");
      await page.goto("/console/app/corvin-knowledge", { waitUntil: "domcontentloaded" });
      const repo = page.getByTestId("knowledge-repo");
      await expect(page.getByTestId("scope-select").locator('option[value="project:corvin"]')).toHaveCount(separate ? 1 : 0, { timeout: 60_000 });
      const boundName = ((await page.getByTestId("scope-select").locator('option[value="bound"]').textContent()) ?? "").trim();
      expect(separate ? boundName !== "Corvin" && cfg.repo_path.endsWith(boundName) : boundName === "Corvin").toBe(true);
      await expect(repo).toContainText(`${boundName} · Repository:`, { timeout: 60_000 });
      await expect(page.getByTestId("scope-select")).toHaveValue("bound");
      await expect.poll(() => graphTotal(page), { timeout: 60_000 }).toBeGreaterThan(0);
      const boundTotal = await graphTotal(page);
      await expect(repo).not.toContainText(`/projects/${PID}/docs`);
      await shot(page, "graph-corvin", t, s.name);

      // --- Graph: the project - its own repository and graph (built on demand when missing) ---
      await page.getByTestId("scope-select").selectOption(`project:${PID}`);
      await expect(repo).toContainText(`/projects/${PID}/docs`, { timeout: 90_000 });
      await expect.poll(() => graphTotal(page), { timeout: 90_000 }).toBeGreaterThan(0);
      const projectTotal = await graphTotal(page);
      expect(projectTotal).not.toBe(boundTotal);
      await shot(page, "graph-project", t, s.name);
      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(repo).toContainText(`/projects/${PID}/docs`, { timeout: 90_000 });

      // --- Graph: Corvin itself, when it is a scope of its own: the board's knowledge base, not the graph setting's ---
      let corvinTotal = 0;
      if (separate) {
        await page.getByTestId("scope-select").selectOption("project:corvin");
        await expect(repo).toContainText("Corvin · Repository:", { timeout: 90_000 });
        await expect.poll(() => graphTotal(page), { timeout: 90_000 }).toBeGreaterThan(0);
        corvinTotal = await graphTotal(page);
        await shot(page, "graph-corvin-own", t, s.name);
      }

      // --- Graph: All knowledge bases = the sum of every knowledge base it joins ---
      // The expected total is computed from the per-scope counts read on this install (the default scope and every selectable
      // project, from the same graph endpoint the page calls), never from a fixed assumption about which projects exist: an
      // install with more knowledge-base projects - a duplicate or an imported copy among them, sharing its original's uids -
      // must still add up, nothing dropped (REVIEW-0071 N6).
      const scopes = await page.getByTestId("scope-select").locator("option").evaluateAll((os) =>
        os.filter((o) => !(o as HTMLOptionElement).disabled).map((o) => (o as HTMLOptionElement).value)
          .filter((v) => v === "bound" || v.startsWith("project:")));
      expect(scopes).toContain("bound");
      expect(scopes).toContain(`project:${PID}`);
      const countOf = async (v: string): Promise<number> => {
        const q = v === "bound" ? "" : `?project=${encodeURIComponent(v.slice("project:".length))}`;
        const r = await page.request.get(`/v1/console/plugins/corvin-knowledge/graph${q}`, { timeout: 90_000 });
        expect(r.ok(), `graph ${v}`).toBe(true);
        return ((await r.json()) as { entities: unknown[] }).entities.length;
      };
      const perScope = Object.fromEntries(await Promise.all(scopes.map(async (v) => [v, await countOf(v)] as const)));
      expect(perScope[`project:${PID}`]).toBe(projectTotal);              // the API counts are the page's counts
      if (separate) expect(perScope["project:corvin"]).toBe(corvinTotal);
      const expectedAll = Object.values(perScope).reduce((a, b) => a + b, 0);
      await page.getByTestId("scope-select").selectOption("all");
      await expect(repo).toContainText(`${scopes.length} knowledge bases`, { timeout: 90_000 });
      await expect.poll(() => graphTotal(page), { timeout: 90_000 }).toBe(expectedAll);
      expect(expectedAll).toBeGreaterThanOrEqual(boundTotal + projectTotal + corvinTotal);
      await shot(page, "graph-all", t, s.name);

      // --- Graph: an unavailable project falls back to Corvin, saying why ---
      await page.goto(`/console/app/corvin-knowledge?scope=project:${NO_KB}`, { waitUntil: "domcontentloaded" });
      await expect(page.getByTestId("scope-note")).toContainText("cannot be shown: no knowledge base", { timeout: 60_000 });
      await expect(page.getByTestId("scope-select")).toHaveValue("bound");
      await expect(repo).toContainText(`${boundName} · Repository:`, { timeout: 60_000 });
      await shot(page, "graph-unavailable", t, s.name);
    });
  }
}
