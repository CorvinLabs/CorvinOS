/**
 * The project Settings tab, section by section (T-0387, CONCEPT-0131 § C1/C2), on the served bundle. The project endpoints are
 * stubbed at the network boundary; the sections, their guards and the request bodies they send are the real ones. The server's
 * side (relocate, storage, remove keeps the folder) is proven over real HTTP by tests/e2e/test_projects_location_e2e.py.
 *
 * Screenshots: SHOTS_DIR (default /tmp/phase1-shots), 1280 and 375 px in both themes.
 */
import fs from "node:fs";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";
import { enterPath, stubPicker } from "./projects-picker-stub";

const OUT = process.env.SHOTS_DIR ?? "/tmp/phase2-shots";
const ID = "alpha";
const OK = {
  id: ID, path: "/home/u/.local/share/corvin/projects/w-0123456789/alpha", template: "research@1", state: "ok", title: "Alpha research",
  docs_mode: "plain", kb: false, active: ["core"], features: { core: true }, directory_peers: [], role: "research",
  classification: "internal", assistant_access: "read", added_at: "2026-11-08T10:00:00Z", storage: "managed",
  display_path: "Corvin workspace › alpha", location_warnings: [],
  catalogue: { available: true, features: [{ id: "core", title: "Project", description: "", active: true, requires: [], suggests: [], dependants: [], plugins: [] }] },
};
const HEALTH = { project_id: ID, ok: false, checks: [
  { id: "path_exists", ok: true, detail: "the project directory exists", at: "" },
  { id: "generated_files_in_sync", ok: false, detail: "out of sync (repair fixes): ['CLAUDE.md']", at: "" },
  { id: "manifest", ok: true, detail: "project.yaml is valid", at: "" }] };

interface State { detail: Record<string, unknown>; titles: string[]; relocated: string[]; removed: number; rekeyed: unknown[] }

async function stub(page: Page, detail: Record<string, unknown>, list: Record<string, unknown> = {}): Promise<State> {
  const st: State = { detail: { ...detail }, titles: [], relocated: [], removed: 0, rekeyed: [] };
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: { available: true, templates: [], features: [] } }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: { projects_root: "/home/u/projects", projects_root_set: false,
    default_storage: "managed", managed_root: "/home/u/.local/share/corvin/projects/w-0123456789", workspace: "ok", projects: [], ...list } }));
  await page.route(/\/v1\/console\/chat\/sessions$/, (r) => r.fulfill({ json: { tenant_id: "_default", count: 0, sessions: [] } }));
  await stubPicker(page);
  await page.route(new RegExp(`/v1/console/projects/${ID}$`), (r) => {
    if (r.request().method() === "DELETE") { st.removed += 1; return r.fulfill({ json: { removed: ID, files_deleted: false } }); }
    return r.fulfill({ json: st.detail });
  });
  await page.route(new RegExp(`/v1/console/projects/${ID}/health$`), (r) => r.fulfill({ json: HEALTH }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/title$`), (r) => {
    const t = (r.request().postDataJSON() as { title: string }).title;
    st.titles.push(t); st.detail = { ...st.detail, title: t };
    return r.fulfill({ json: st.detail });
  });
  await page.route(new RegExp(`/v1/console/projects/${ID}/relocate$`), (r) => {
    const loc = (r.request().postDataJSON() as { location: string }).location;
    st.relocated.push(loc);
    st.detail = { ...OK, path: loc, storage: "own", display_path: "~/moved/alpha" };
    return r.fulfill({ json: { ...st.detail, repaired_files: 1, warnings: [] } });
  });
  await page.route(/\/v1\/console\/projects\/settings\/storage$/, (r) => {
    st.rekeyed.push(r.request().postDataJSON());
    return r.fulfill({ json: { default_storage: "managed", projects_root: null, managed_root: "/home/u/.local/share/corvin/projects/w-fedcba9876", outside: [] } });
  });
  return st;
}

async function theme(page: Page, t: "light" | "dark") {
  await page.addInitScript((v) => { try { localStorage.setItem("corvin-theme", v); localStorage.setItem("corvin-dark-default-v2", "1"); } catch { /* ignore */ } }, t);
}

async function noIds(page: Page) {
  const text = await page.getByTestId("project-inspector").innerText();
  expect(text).not.toMatch(/ADR-\d|CONCEPT-\d|REVIEW-\d|T-0\d{3}/);
}

test("a managed project shows the seven sections in order; delete is a later update, Share offers Export and Duplicate", async ({ page }) => {
  await stub(page, OK);
  await page.goto(`/console/app/projects/${ID}?tab=settings`);
  const titles = page.getByTestId("settings-section").locator("h2");
  await expect(titles).toHaveText(["General", "Location", "Protection", "Use in a chat", "Maintenance", "Share", "Danger zone"]);
  await expect(page.getByTestId("storage-badge").first()).toHaveText("Corvin workspace");
  await expect(page.getByTestId("location-path")).toHaveText("Corvin workspace › alpha");
  await expect(page.getByTestId("location-path")).toHaveAttribute("title", OK.path);
  await expect(page.getByTestId("doctor-summary")).toHaveText("3 checks, 1 failed — out of sync (repair fixes): ['CLAUDE.md']");
  await expect(page.getByTestId("share-export")).toBeEnabled();
  await expect(page.getByTestId("share-duplicate")).toBeEnabled();
  await expect(page.getByTestId("delete-files")).toBeDisabled();
  await expect(page.getByTestId("delete-row")).toContainText("Available in a later update");
  await expect(page.getByTestId("locate-box")).toHaveCount(0);
  await noIds(page);
});

test("an own folder's delete row says it is not available, and a hint word in its path is warned about", async ({ page }) => {
  const warn = "The assistant may refuse some shell commands in a folder whose path contains 'forge'.";
  await stub(page, { ...OK, path: "/home/u/code/forge-lab", storage: "own", display_path: "~/code/forge-lab",
    location_warnings: [{ class: "hint_word", message: warn }] });
  await page.goto(`/console/app/projects/${ID}?tab=settings`);
  await expect(page.getByTestId("storage-badge").first()).toHaveText("Own folder");
  await expect(page.getByTestId("delete-row")).toContainText("(not available: own folder)");
  await expect(page.getByTestId("delete-files")).toHaveCount(0);
  await expect(page.getByTestId("location-warning")).toHaveText(warn);
});

test("rename offers Undo for 10 s, and Undo sends the old title back", async ({ page }) => {
  const st = await stub(page, OK);
  await page.clock.install();
  await page.goto(`/console/app/projects/${ID}?tab=settings`);
  await page.getByTestId("rename-input").fill("Alpha study");
  await page.getByTestId("rename-submit").click();
  await expect.poll(() => st.titles).toEqual(["Alpha study"]);
  await expect(page.getByTestId("rename-undo")).toBeVisible();
  await page.getByTestId("rename-undo").click();
  await expect.poll(() => st.titles).toEqual(["Alpha study", "Alpha research"]);
  await expect(page.getByTestId("rename-undo")).toHaveCount(0);
  await expect(page.getByTestId("rename-input")).toHaveValue("Alpha research");
  // a second rename: the Undo goes away by itself after 10 s
  await page.getByTestId("rename-input").fill("Alpha two");
  await page.getByTestId("rename-submit").click();
  await expect(page.getByTestId("rename-undo")).toBeVisible();
  await page.clock.runFor(10_500);
  await expect(page.getByTestId("rename-undo")).toHaveCount(0);
  expect(st.titles).toEqual(["Alpha study", "Alpha research", "Alpha two"]);
});

test("a missing folder shows the Location box; Locate folder relinks and the badge and path update without a reload", async ({ page }) => {
  const st = await stub(page, { id: ID, path: "/home/u/old/alpha", template: "research@1", state: "missing", storage: "own",
    display_path: "~/old/alpha", reason: "project.yaml is missing or unreadable" });
  await page.goto(`/console/app/projects/${ID}?tab=settings`);
  const box = page.getByTestId("locate-box");
  await expect(box).toContainText("Folder missing: project.yaml is missing or unreadable");
  await expect(page.getByTestId("settings-section").locator("h2")).toHaveText(["General", "Location", "Danger zone"]);
  await expect(page.getByTestId("locate-input")).toHaveCount(0);               // no bare path field: the picker chooses
  await page.getByTestId("locate-open").click();
  await expect(page.getByTestId("folder-picker")).toHaveAttribute("data-purpose", "relocate");
  await enterPath(page, "locate-input", "/home/u/code/scratch");
  await expect(page.getByTestId("picker-choose")).toBeEnabled();
  await page.getByTestId("picker-cancel").click();
  expect(st.relocated).toEqual([]);                                           // Cancel sends nothing
  await page.getByTestId("locate-open").click();
  await enterPath(page, "locate-input", "/home/u/moved/alpha");
  await expect(page.getByTestId("picker-verdict-text")).toHaveText("This folder does not exist.");
  await expect(page.getByTestId("picker-choose")).toBeDisabled();
  await enterPath(page, "locate-input", "/home/u/code/scratch");
  await page.getByTestId("picker-choose").click();
  await expect.poll(() => st.relocated).toEqual(["/home/u/code/scratch"]);
  await expect(page.getByTestId("inspector-state")).toHaveText("Ready");
  await expect(page.getByTestId("location-path")).toHaveText("~/moved/alpha");
  await expect(page.getByTestId("storage-badge").first()).toHaveText("Own folder");
  await expect(page.getByTestId("locate-box")).toHaveCount(0);
});

test("Remove from the list needs two clicks and says the files stay", async ({ page }) => {
  const st = await stub(page, OK);
  await page.goto(`/console/app/projects/${ID}?tab=settings`);
  await expect(page.getByTestId("danger-section")).toContainText("Remove from the list — the folder and every file stay.");
  await page.getByTestId("remove-start").click();
  expect(st.removed).toBe(0);
  await page.getByTestId("remove-confirm").click();
  await expect.poll(() => st.removed).toBe(1);
  await expect(page.getByTestId("remove-message")).toContainText("every file stay");
});

test("a workspace from another installation offers a new one, after the typed confirmation", async ({ page }) => {
  const st = await stub(page, OK, { workspace: "foreign" });
  await page.goto(`/console/app/projects/${ID}?tab=settings`);
  const box = page.getByTestId("rekey-box");
  await expect(box).toContainText("This workspace belongs to another Corvin installation.");
  await page.getByTestId("rekey-start").click();
  await expect(page.getByTestId("rekey-confirm")).toBeDisabled();
  await page.getByTestId("rekey-input").fill("new workspac");
  await expect(page.getByTestId("rekey-confirm")).toBeDisabled();
  await page.getByTestId("rekey-input").fill("new workspace");
  await page.getByTestId("rekey-confirm").click();
  await expect.poll(() => st.rekeyed).toEqual([{ rekey: true }]);
  await expect(page.getByTestId("rekey-note")).toContainText("new workspace");
});

for (const t of ["light", "dark"] as const) {
  for (const s of [{ name: "1280", width: 1280, height: 900 }, { name: "375", width: 375, height: 812 }]) {
    test(`the Settings tab fits ${s.name} px without sideways scrolling (${t})`, async ({ page }) => {
      await theme(page, t);
      await page.setViewportSize({ width: s.width, height: s.height });
      await stub(page, OK);
      await page.goto(`/console/app/projects/${ID}?tab=settings`);
      await expect(page.getByTestId("danger-section")).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("data-theme", t);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      fs.mkdirSync(OUT, { recursive: true });
      await page.screenshot({ path: path.join(OUT, `settings-managed-${t}-${s.name}.png`), fullPage: true });
      await page.getByTestId("move-open").click();
      await page.getByTestId("picker-place").filter({ hasText: "Home" }).click();
      await page.locator('[data-testid="picker-row"][data-name="code"]').click();
      await page.getByTestId("picker-new-name").fill("alpha-new");
      await expect(page.getByTestId("picker-choose")).toBeEnabled();
      await page.screenshot({ path: path.join(OUT, `settings-move-picker-${t}-${s.name}.png`) });
      await page.getByTestId("picker-choose").click();
      await page.getByTestId("move-start").click();
      await expect(page.getByTestId("move-confirm")).toBeVisible();
      await page.getByTestId("location-section").scrollIntoViewIfNeeded();
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      await page.screenshot({ path: path.join(OUT, `settings-move-confirm-${t}-${s.name}.png`) });
    });
    test(`the missing-folder Location box at ${s.name} px (${t})`, async ({ page }) => {
      await theme(page, t);
      await page.setViewportSize({ width: s.width, height: s.height });
      await stub(page, { id: ID, path: "/home/u/old/alpha", template: "research@1", state: "missing", storage: "own",
        display_path: "~/old/alpha", reason: "project.yaml is missing or unreadable" });
      await page.goto(`/console/app/projects/${ID}?tab=settings`);
      await expect(page.getByTestId("locate-box")).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      fs.mkdirSync(OUT, { recursive: true });
      await page.screenshot({ path: path.join(OUT, `settings-missing-${t}-${s.name}.png`), fullPage: true });
      await page.getByTestId("locate-open").click();
      await expect(page.getByTestId("folder-picker")).toBeVisible();
      await page.getByTestId("picker-place").filter({ hasText: "Home" }).click();
      await page.locator('[data-testid="picker-row"][data-name="code"]').click();
      await page.screenshot({ path: path.join(OUT, `settings-locate-picker-${t}-${s.name}.png`) });
    });
  }
}
