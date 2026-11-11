/**
 * Projects panel, in the real browser on the bundle the console serves (T-0357). The two project
 * endpoints are stubbed at the network boundary (page.route); the page, bundle, registry, sidebar,
 * router and renderer are the real ones. Read-only: nothing here writes to the console.
 *
 * What must hold: the sidebar entry reaches the page (the PANELS + NAV_GROUPS pair), a list shows every
 * project with its state, a missing folder is shown as missing, the empty state names what is missing,
 * no ADR id or plugin index id is rendered, and the page fits a 375 px phone in both themes.
 */
import { enterPath, stubPicker } from "./projects-picker-stub";
import fs from "node:fs";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";

const TRASH_OUT = process.env.TRASH_SHOTS_DIR ?? "/tmp/phase5-shots";

const LIST = {
  projects_root: "/home/u/projects",
  projects: [
    { id: "plumbline", path: "/home/u/projects/plumbline", template: "video-engineering@1", state: "ok", title: "Plumbline explainer",
      docs_mode: "nested", active: ["core", "kb", "tasks", "graph", "gates", "claude", "videos", "video_producer"],
      features: { core: true, kb: true, tasks: true, graph: true, gates: true, claude: true, videos: true, video_producer: true, acs: false, experiments: false } },
    { id: "old-study", path: "/home/u/projects/old-study", template: "research-compute@1", state: "missing", reason: "project.yaml is missing or unreadable" },
  ],
};

async function stub(page: Page, opts: { projects: unknown; available?: boolean; reason?: string }) {
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({
    json: { available: opts.available ?? true, reason: opts.reason, templates: [], features: [] } }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: opts.projects }));
}

async function noIds(page: Page) {
  const text = await page.getByTestId("projects-page").innerText();
  expect(text).not.toMatch(/ADR-\d|CONCEPT-\d|plugin:buildin|plugin:contributor/);
}

test("the sidebar entry reaches the Projects page and the list shows every project with its state", async ({ page }) => {
  await stub(page, { projects: LIST });
  await page.goto("/console/app/dashboard");
  await page.getByRole("link", { name: "Projects", exact: true }).first().click();
  await expect(page).toHaveURL(/\/console\/app\/projects$/);
  const cards = page.getByTestId("project-card");
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0)).toHaveAttribute("data-project-id", "old-study");                 // needs attention: first
  await expect(cards.nth(0).getByTestId("project-state")).toHaveText("Directory missing");
  await expect(cards.nth(0).getByTestId("project-reason")).toContainText("project.yaml is missing");
  await expect(cards.nth(1).getByTestId("project-title")).toHaveText("Plumbline explainer");
  await expect(cards.nth(1).getByTestId("project-state")).toHaveText("Ready");
  const chips = cards.nth(1).getByTestId("project-features");
  for (const label of ["Knowledge base", "Tasks", "Knowledge graph", "Quality gates", "Claude Code rules", "Videos", "Video Producer"]) {
    await expect(chips).toContainText(label);
  }
  await expect(cards.nth(1).getByTestId("project-off")).toContainText("Off: Agentic compute, Experiments");
  await expect(cards.nth(1).getByTestId("project-path")).toHaveText("/home/u/projects/plumbline");
  await expect(page.getByTestId("default-place")).toContainText("New projects go to /home/u/projects.");
  await noIds(page);
});

test("an install without projects explains what a project is and offers no dead button", async ({ page }) => {
  await stub(page, { projects: { projects_root: "/home/u/projects", projects: [] } });
  await page.goto("/console/app/projects");
  await expect(page.getByTestId("projects-empty-headline")).toHaveText("No projects yet");
  await expect(page.getByTestId("projects-empty-body")).toContainText("never deletes a file");
  await expect(page.getByTestId("projects-empty").getByRole("button")).toHaveCount(0);
  await expect(page.getByTestId("projects-empty").getByRole("link")).toHaveCount(0);
  await noIds(page);
});

test("a disabled template plugin is named, with the way to enable it", async ({ page }) => {
  await stub(page, { projects: { projects_root: "/p", projects: [] }, available: false,
    reason: "the project templates plugin is absent for this tenant - enable it in the Marketplace" });
  await page.goto("/console/app/projects");
  await expect(page.getByTestId("projects-empty-headline")).toHaveText("Project templates are not enabled");
  await expect(page.getByTestId("projects-empty-body")).toContainText("enable it in the Marketplace");
  await page.getByRole("link", { name: /Open the Marketplace/ }).click();
  await expect(page).toHaveURL(/\/console\/app\/marketplace/);
});

test("a failed load is an error, not an empty list", async ({ page }) => {
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: { available: true, templates: [], features: [] } }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ status: 500, json: { detail: "boom" } }));
  await page.goto("/console/app/projects");
  await expect(page.getByTestId("projects-error")).toBeVisible();
  await expect(page.getByTestId("projects-empty")).toHaveCount(0);
});

for (const theme of ["light", "dark"] as const) {
  test(`the list fits a 375 px phone without sideways scrolling (${theme})`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    // the console is data-theme driven (NOT prefers-color-scheme): set the stored choice the page reads at boot
    await page.addInitScript((t) => { try { localStorage.setItem("corvin-theme", t); localStorage.setItem("corvin-dark-default-v2", "1"); } catch { /* ignore */ } }, theme);
    await stub(page, { projects: LIST });
    await page.goto("/console/app/projects");
    await expect(page.getByTestId("project-card")).toHaveCount(2);
    // positive control: the theme under test is the one actually rendered (a check that renders one theme twice proves nothing)
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `test-results/projects-${theme}-375.png`, fullPage: true });
  });
}

// ── where projects live (T-0388, CONCEPT-0131 § C3/C4) ──────────────────────────────────────────────

const MR = "/home/u/.local/share/corvin/projects/w-0123456789";
const W = (id: string, storage: string, p: string, display: string) => ({ id, path: p, template: "research@1", state: "ok", title: id.toUpperCase(),
  active: ["core"], features: { core: true }, storage, display_path: display, classification: "internal", assistant_access: "read" });
const CORVIN = { id: "corvin", path: "/home/u/.corvin/kb", template: "builtin@1", state: "ok", title: "Corvin", builtin: true, storage: "builtin",
  display_path: "/home/u/.corvin/kb", active: [], features: {} };
const WHERE_LIST = { projects_root: "/home/u/projects", projects_root_set: false, default_storage: "managed", managed_root: MR, workspace: "ok",
  projects: [CORVIN, W("alpha", "managed", `${MR}/alpha`, "Corvin workspace › alpha"), W("beta", "own", "/home/u/code/beta", "~/code/beta")] };

test("the header names the default place; the dialog saves a folder of my own through the storage route", async ({ page }) => {
  await stubPicker(page);
  let list: Record<string, unknown> = { ...WHERE_LIST };
  const puts: unknown[] = [];
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: { available: true, templates: [], features: [] } }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: list }));
  await page.route(/\/v1\/console\/projects\/settings\/storage$/, (r) => {
    const b = r.request().postDataJSON() as { default_storage: string; projects_root?: string };
    puts.push(b);
    list = { ...list, default_storage: b.default_storage, projects_root: b.projects_root ?? list.projects_root, projects_root_set: true };
    return r.fulfill({ json: { default_storage: b.default_storage, projects_root: b.projects_root ?? null, managed_root: MR, outside: ["alpha"] } });
  });
  await page.goto("/console/app/projects");
  await expect(page.getByTestId("default-place")).toContainText("New projects go to the Corvin workspace.");
  await page.getByTestId("default-place-change").click();
  const dlg = page.getByTestId("default-place-dialog");
  await expect(dlg.getByRole("heading")).toHaveText("Default place for new projects");
  await expect(dlg.getByTestId("place-managed")).toBeChecked();
  await expect(dlg).toContainText(MR);
  await expect(dlg).toContainText("Projects already somewhere else keep working; nothing is moved.");
  await dlg.getByTestId("place-root").check();
  await expect(dlg.getByTestId("place-save")).toBeDisabled();                      // a folder of my own needs a folder
  await expect(dlg.getByTestId("place-root-input")).toHaveCount(0);                // no bare path field: the picker chooses
  await dlg.getByTestId("place-root-folder-choose").click();
  await expect(page.getByTestId("folder-picker")).toHaveAttribute("data-purpose", "projects_root");
  await enterPath(page, "place-root-input", "/home/u/work");
  await expect(page.getByTestId("picker-choose")).toBeEnabled();
  expect(puts).toEqual([]);                                                        // Enter in the picker's field never submits the dialog behind it
  await expect(dlg).toBeVisible();
  await page.getByTestId("picker-choose").click();
  await expect(dlg.getByTestId("place-root-folder-value")).toHaveText("/home/u/work");
  await dlg.getByTestId("place-save").click();
  await expect.poll(() => puts).toEqual([{ default_storage: "root", projects_root: "/home/u/work" }]);
  await expect(page.getByTestId("default-place-dialog")).toHaveCount(0);
  await expect(page.getByTestId("default-place")).toContainText("New projects go to /home/u/work.");
});

test("a refused folder keeps the dialog open with the server's sentence", async ({ page }) => {
  await stubPicker(page);
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: { available: true, templates: [], features: [] } }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: WHERE_LIST }));
  await page.route(/\/v1\/console\/projects\/settings\/storage$/, (r) => r.fulfill({ status: 422,
    json: { detail: { message: "This is a system folder; projects cannot live here.", reason_class: "system_prefix" } } }));
  await page.goto("/console/app/projects");
  await page.getByTestId("default-place-change").click();
  const dlg = page.getByTestId("default-place-dialog");
  await dlg.getByTestId("place-root").check();
  await dlg.getByTestId("place-root-folder-choose").click();
  await enterPath(page, "place-root-input", "/home/u/code/notes");
  await page.getByTestId("picker-choose").click();
  await dlg.getByTestId("place-save").click();
  await expect(dlg.getByTestId("place-error")).toHaveText("This is a system folder; projects cannot live here.");
  await dlg.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByTestId("default-place-dialog")).toHaveCount(0);
});

test("every card has a Where row; the Where filter appears with two kinds and filters", async ({ page }) => {
  await stub(page, { projects: WHERE_LIST });
  await page.goto("/console/app/projects");
  const card = (id: string) => page.locator(`[data-testid="project-card"][data-project-id="${id}"]`);
  await expect(card("alpha").getByTestId("project-where")).toHaveText("Corvin workspace › alpha");
  await expect(card("alpha").getByTestId("project-where")).toHaveAttribute("data-storage", "managed");
  await expect(card("alpha").getByTestId("project-path")).toHaveAttribute("title", `${MR}/alpha`);
  await expect(card("beta").getByTestId("project-where")).toHaveText("Own folder ~/code/beta");
  await expect(card("corvin").getByTestId("project-where")).toHaveText("Corvin's knowledge base");
  const f = page.getByTestId("filter-where");
  await expect(f).toBeVisible();
  await f.selectOption("managed");
  await expect(page.getByTestId("project-card")).toHaveCount(1);
  await expect(page.getByTestId("project-card")).toHaveAttribute("data-project-id", "alpha");
  await f.selectOption("own");
  await expect(page.getByTestId("project-card")).toHaveAttribute("data-project-id", "beta");
  await f.selectOption("all");
  await expect(page.getByTestId("project-card")).toHaveCount(3);
  await noIds(page);
});

test("with one kind of place there is no Where filter", async ({ page }) => {
  await stub(page, { projects: { ...WHERE_LIST, projects: [CORVIN, W("alpha", "managed", `${MR}/alpha`, "Corvin workspace › alpha"),
    W("gamma", "managed", `${MR}/gamma`, "Corvin workspace › gamma")] } });
  await page.goto("/console/app/projects");
  await expect(page.getByTestId("project-card")).toHaveCount(3);
  await expect(page.getByTestId("filter-where")).toHaveCount(0);
});

for (const theme of ["light", "dark"] as const) {
  for (const s of [{ name: "1280", width: 1280, height: 900 }, { name: "375", width: 375, height: 812 }]) {
    test(`the list with Where rows and the default-place dialog at ${s.name} px (${theme})`, async ({ page }) => {
      const out = process.env.SHOTS_DIR ?? "/tmp/phase2-shots";
      await page.setViewportSize({ width: s.width, height: s.height });
      await page.addInitScript((t) => { try { localStorage.setItem("corvin-theme", t); localStorage.setItem("corvin-dark-default-v2", "1"); } catch { /* ignore */ } }, theme);
      await stubPicker(page);
      await stub(page, { projects: { ...WHERE_LIST, projects: [...WHERE_LIST.projects,
        { id: "old-study", path: "/home/u/old/old-study", template: "research@1", state: "missing", reason: "project.yaml is missing or unreadable",
          storage: "own", display_path: "~/old/old-study" }] } });
      await page.goto("/console/app/projects");
      await expect(page.getByTestId("project-card")).toHaveCount(4);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `${out}/list-${theme}-${s.name}.png`, fullPage: true });
      await page.getByTestId("default-place-change").click();
      await page.getByTestId("place-root").check();
      await expect(page.getByTestId("default-place-dialog")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `${out}/default-place-dialog-${theme}-${s.name}.png`, fullPage: false });
      await page.getByTestId("place-root-folder-choose").click();
      await page.getByTestId("picker-place").filter({ hasText: "Home" }).click();
      await page.locator('[data-testid="picker-row"][data-name="code"]').click();
      await page.getByTestId("picker-new-name").fill("work");
      await expect(page.getByTestId("picker-choose")).toBeEnabled();
      await page.screenshot({ path: `${out}/default-place-picker-${theme}-${s.name}.png`, fullPage: false });
      await page.getByTestId("picker-choose").click();
      await expect(page.getByTestId("place-root-folder-value")).toHaveText("/home/u/code/work");
      await page.screenshot({ path: `${out}/default-place-chosen-${theme}-${s.name}.png`, fullPage: false });
    });
  }
}

// ── the Trash (T-0405, ADR-2321 Decision 12) ─────────────────────────────────────────────────────────

const ENTRY = "old-notes-20261111T070000Z";
const TRASH_ROW = { entry: ENTRY, id: "old-notes", title: "Old notes", deleted_at: "2026-11-11T07:00:00Z", restorable_until: "2026-12-11T07:00:00Z", pending: false };
const RESTORED = { id: "old-notes", path: "/home/u/.local/share/corvin/projects/w-0123456789/old-notes", template: "research@1", state: "ok",
  title: "Old notes", storage: "managed", display_path: "Corvin workspace › old-notes", active: ["core"], features: { core: true } };

async function stubTrash(page: Page, opts: { entries?: typeof TRASH_ROW[]; restoreStatus?: number } = {}) {
  const st = { entries: [...(opts.entries ?? [TRASH_ROW])], restored: [] as string[], purged: [] as unknown[], offsets: [] as number[] };
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: { available: true, templates: [], features: [] } }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: { projects_root: "/home/u/projects", default_storage: "managed",
    managed_root: "/home/u/.local/share/corvin/projects/w-0123456789", workspace: "ok",
    projects: [LIST.projects[0], ...(st.restored.length ? [RESTORED] : [])], trashed: [], trash_count: st.entries.length } }));
  await page.route(/\/v1\/console\/projects\/-\/trash(\?[^/]*)?$/, (r) => {      // pages like the server (REVIEW-0063 m6)
    const offset = Number(new URL(r.request().url()).searchParams.get("offset") ?? "0");
    st.offsets.push(offset);
    const limit = 200;
    const page = st.entries.slice(offset, offset + limit);
    return r.fulfill({ json: { entries: page, ttl_days: 30, total: st.entries.length, offset, limit,
      next_offset: offset + limit < st.entries.length ? offset + limit : null } });
  });
  await page.route(/\/v1\/console\/projects\/-\/trash\/[^/]+\/restore$/, (r) => {
    if (opts.restoreStatus) return r.fulfill({ status: opts.restoreStatus, json: { detail: {
      message: "A folder named old-notes is already in the Corvin workspace; move it away, then restore.", reason_class: "folder_not_empty" } } });
    const entry = decodeURIComponent(new URL(r.request().url()).pathname.split("/").slice(-2)[0]);
    st.restored.push(entry);
    st.entries = st.entries.filter((e) => e.entry !== entry);
    return r.fulfill({ json: { ...RESTORED, warnings: [] } });
  });
  await page.route(/\/v1\/console\/projects\/-\/trash\/[^/]+$/, (r) => {
    if (r.request().method() !== "DELETE") return r.fallback();
    const entry = decodeURIComponent(new URL(r.request().url()).pathname.split("/").pop() ?? "");
    st.purged.push({ entry, body: r.request().postDataJSON() });
    st.entries = st.entries.filter((e) => e.entry !== entry);
    return r.fulfill({ json: { entry, purged: true } });
  });
  return st;
}

test("Trash (n) appears only when something is in it, and Restore brings the card back", async ({ page }) => {
  const st = await stubTrash(page);
  await page.goto("/console/app/projects");
  await expect(page.getByTestId("trash-link")).toHaveText("Trash (1)");
  await page.getByTestId("trash-link").click();
  await expect(page).toHaveURL(/view=trash/);
  const row = page.getByTestId("trash-row");
  await expect(row).toHaveCount(1);
  await expect(row.getByTestId("trash-title")).toHaveText("Old notes");
  await expect(row.getByTestId("trash-id")).toHaveText("old-notes");
  await expect(row.getByTestId("trash-deleted")).toHaveText("Nov 11, 2026");
  await expect(row.getByTestId("trash-until")).toHaveText("Dec 11, 2026");
  await expect(page.getByTestId("project-card")).toHaveCount(0);                // the Trash replaces the list
  await row.getByTestId("trash-restore").click();
  await expect(page.getByTestId("trash-note")).toHaveText("Old notes was restored to the Corvin workspace.");
  await expect(page.getByTestId("trash-empty")).toHaveText("Trash is empty.");
  expect(st.restored).toEqual([ENTRY]);
  await page.getByTestId("trash-back").click();
  await expect(page).not.toHaveURL(/view=trash/);
  await expect(page.locator('[data-testid="project-card"][data-project-id="old-notes"]')).toBeVisible();
  await expect(page.getByTestId("trash-link")).toHaveCount(0);
  await noIds(page);
});

test("a Trash larger than one page pages with the server: Older entries, Newer entries, and a range line", async ({ page }) => {
  // 205 entries, newest first by the time in the name; the server answers 200 per page (REVIEW-0063 m6)
  const many = Array.from({ length: 205 }, (_, i) => {
    const t = new Date(Date.UTC(2026, 10, 11, 7, 0, 0) - i * 60_000).toISOString().replace(/\.\d{3}Z$/, "Z");
    const id = `p${String(i).padStart(3, "0")}`;
    return { entry: `${id}-${t.replace(/[-:]/g, "")}`, id, title: `Project ${i}`, deleted_at: t, restorable_until: null, pending: false };
  });
  const st = await stubTrash(page, { entries: many });
  await page.goto("/console/app/projects?view=trash");
  await expect(page.getByTestId("trash-row")).toHaveCount(200);
  await expect(page.getByTestId("trash-range")).toHaveText("Showing 1–200 of 205");
  await expect(page.getByTestId("trash-newer")).toBeDisabled();
  await page.getByTestId("trash-older").click();
  await expect(page.getByTestId("trash-row")).toHaveCount(5);
  await expect(page.getByTestId("trash-range")).toHaveText("Showing 201–205 of 205");
  await expect(page.getByTestId("trash-row").first().getByTestId("trash-id")).toHaveText("p200");
  await expect(page.getByTestId("trash-older")).toBeDisabled();
  expect(st.offsets).toContain(200);
  fs.mkdirSync(TRASH_OUT, { recursive: true });
  await page.getByTestId("trash-pager").scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(TRASH_OUT, `trash-paging-${test.info().project.name}.png`), fullPage: true });
  // restoring every row of the last page steps back to the newer page instead of "Trash is empty"
  for (let i = 0; i < 5; i++) await page.getByTestId("trash-row").first().getByTestId("trash-restore").click();
  await expect(page.getByTestId("trash-range")).toHaveCount(0);
  await expect(page.getByTestId("trash-row")).toHaveCount(200);
  await expect(page.getByTestId("trash-empty")).toHaveCount(0);
});

test("with an empty Trash there is no Trash link; ?view=trash says Trash is empty", async ({ page }) => {
  await stubTrash(page, { entries: [] });
  await page.goto("/console/app/projects");
  await expect(page.getByTestId("project-card").first()).toBeVisible();
  await expect(page.getByTestId("trash-link")).toHaveCount(0);
  await page.goto("/console/app/projects?view=trash");
  await expect(page.getByTestId("trash-empty")).toHaveText("Trash is empty.");
});

test("Delete permanently needs the typed id and sends it; a refused restore shows the server's sentence on the row", async ({ page }) => {
  const st = await stubTrash(page, { restoreStatus: 409 });
  await page.goto("/console/app/projects?view=trash");
  await page.getByTestId("trash-restore").click();
  await expect(page.getByTestId("trash-row-error")).toContainText("A folder named old-notes is already in the Corvin workspace");
  await page.getByTestId("trash-purge").click();
  const dlg = page.getByTestId("purge-dialog");
  await expect(dlg.getByTestId("purge-sentence")).toHaveText("This removes the folder and every file in it for good. It cannot be undone.");
  await expect(dlg.getByTestId("purge-submit")).toBeDisabled();
  await dlg.getByTestId("purge-confirm").fill("old-note");
  await expect(dlg.getByTestId("purge-submit")).toBeDisabled();
  await dlg.getByTestId("purge-confirm").fill("old-notes");
  await expect(dlg.getByTestId("purge-submit")).toBeEnabled();
  await dlg.getByTestId("purge-submit").click();
  await expect(page.getByTestId("trash-note")).toHaveText("Old notes was deleted permanently.");
  await expect(page.getByTestId("trash-empty")).toBeVisible();
  expect(st.purged).toEqual([{ entry: ENTRY, body: { confirm_id: "old-notes" } }]);
});

for (const t of ["light", "dark"] as const) {
  for (const s of [{ name: "1280", width: 1280, height: 900 }, { name: "375", width: 375, height: 812 }]) {
    test(`the Trash section at ${s.name} px (${t})`, async ({ page }) => {
      await page.addInitScript((v) => { try { localStorage.setItem("corvin-theme", v); localStorage.setItem("corvin-dark-default-v2", "1"); } catch { /* ignore */ } }, t);
      await page.setViewportSize({ width: s.width, height: s.height });
      await stubTrash(page, { entries: [TRASH_ROW, { ...TRASH_ROW, entry: "billing-20261109T120000Z", id: "billing", title: "Billing rework",
        deleted_at: "2026-11-09T12:00:00Z", restorable_until: "2026-12-09T12:00:00Z" }] });
      await page.goto("/console/app/projects");
      await expect(page.getByTestId("trash-link")).toHaveText("Trash (2)");
      await expect(page.locator("html")).toHaveAttribute("data-theme", t);
      fs.mkdirSync(TRASH_OUT, { recursive: true });
      await page.screenshot({ path: path.join(TRASH_OUT, `list-trash-link-${t}-${s.name}.png`) });
      await page.getByTestId("trash-link").click();
      await expect(page.getByTestId("trash-row")).toHaveCount(2);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      await page.screenshot({ path: path.join(TRASH_OUT, `trash-section-${t}-${s.name}.png`), fullPage: true });
      await page.getByTestId("trash-purge").first().click();
      await page.getByTestId("purge-confirm").fill("old-notes");
      await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== "running"));   // the dialog's open animation
      await page.screenshot({ path: path.join(TRASH_OUT, `purge-dialog-${t}-${s.name}.png`) });
    });
  }
}
