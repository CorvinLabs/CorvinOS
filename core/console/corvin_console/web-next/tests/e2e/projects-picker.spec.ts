/**
 * The folder picker on the served bundle (T-0392, T-0393), opened from "Add existing folder" and from the wizard. The browse
 * routes are stubbed with a deterministic folder tree (projects-picker-stub.ts); the dialog, its keyboard model, its verdict
 * gating and the requests it sends are the real ones. The server's listing and rule are proven over HTTP by
 * tests/e2e/test_projects_browse_e2e.py; a separate keep-real spec drives the picker against the real routes.
 *
 * Screenshots: SHOTS_DIR (default /tmp/phase2-shots), 1280 and 375 px in both themes.
 */
import fs from "node:fs";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";
import { HINT, HOME, MR, SENT, enterPath, stubPicker } from "./projects-picker-stub";

const OUT = process.env.SHOTS_DIR ?? "/tmp/phase2-shots";

async function base(page: Page) {
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: { available: true, templates: [], features: [] } }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: { projects_root: `${HOME}/projects`, projects_root_set: false,
    default_storage: "managed", managed_root: MR, workspace: "ok", projects: [] } }));
}

async function openAdoptPicker(page: Page) {
  await page.goto("/console/app/projects");
  await page.getByTestId("adopt-start").click();
  await page.getByTestId("adopt-folder-choose").click();
  const picker = page.getByTestId("folder-picker");
  await expect(picker).toBeVisible();
  return picker;
}

const current = (picker: ReturnType<Page["getByTestId"]>) => picker.getByTestId("picker-crumb-current");
const rowOf = (picker: ReturnType<Page["getByTestId"]>, name: string) => picker.locator(`[data-testid="picker-row"][data-name="${name}"]`);

async function theme(page: Page, t: "light" | "dark") {
  await page.addInitScript((v) => { try { localStorage.setItem("corvin-theme", v); localStorage.setItem("corvin-dark-default-v2", "1"); } catch { /* ignore */ } }, t);
}

test("Choose stays disabled until the server's verdict is ok; choosing fills the field and Add folder sends it", async ({ page }) => {
  await base(page);
  const st = await stubPicker(page);
  let body: { location?: string } | null = null;
  await page.route(/\/v1\/console\/projects\/adopt$/, (r) => { body = r.request().postDataJSON(); return r.fulfill({ status: 201, json: { id: "notes" } }); });
  const picker = await openAdoptPicker(page);
  await expect(picker.getByTestId("picker-title")).toHaveText("Choose a folder");
  // opens at the Corvin workspace; the workspace itself is not a choice
  await expect(current(picker)).toHaveText("w-0123456789");
  await expect(picker.getByTestId("picker-verdict-text")).toHaveText(SENT.workspace_root);
  await expect(picker.getByTestId("picker-choose")).toBeDisabled();
  await picker.locator('[data-testid="picker-place"][data-kind="home"]').click();
  await expect(picker.getByTestId("picker-verdict-text")).toHaveText(SENT.top_level);
  await expect(picker.getByTestId("picker-choose")).toBeDisabled();
  await expect(rowOf(picker, ".corvin")).toHaveCount(0);                      // hidden until "Show hidden"
  await rowOf(picker, "code").click();
  await expect(picker.getByTestId("picker-verdict-text")).toHaveText(SENT.nested);
  await expect(picker.getByTestId("picker-choose")).toBeDisabled();
  await expect(rowOf(picker, "alpha").getByTestId("picker-row-note")).toHaveText("Already a project (Alpha research)");
  await rowOf(picker, "notes").click();
  await expect(picker.getByTestId("picker-verdict-text")).toHaveText("Can be used");
  await expect(picker.getByTestId("picker-target")).toHaveText(`${HOME}/code/notes`);
  await expect(picker.getByTestId("picker-choose")).toBeEnabled();
  await expect(picker.getByTestId("picker-new-name")).toHaveCount(0);           // adopting needs an existing folder
  await picker.getByTestId("picker-choose").click();
  await expect(page.getByTestId("folder-picker")).toHaveCount(0);
  await expect(page.getByTestId("adopt-folder-value")).toHaveText(`${HOME}/code/notes`);
  await page.getByTestId("adopt-submit").click();
  await expect.poll(() => body?.location).toBe(`${HOME}/code/notes`);
  expect(st.calls.every((c) => c.endsWith("|adopt|"))).toBe(true);
});

test("a foreign row, a link, Corvin's data and a system folder cannot be entered and say why", async ({ page }) => {
  await base(page);
  await stubPicker(page);
  const picker = await openAdoptPicker(page);
  await picker.locator('[data-testid="picker-place"][data-kind="home"]').click();
  await rowOf(picker, "code").click();
  await expect(current(picker)).toHaveText("code");
  for (const [name, note] of [["beta-tools", "belongs to another workspace"], ["corvin-link", "link — cannot be chosen"], ["private", "no permission"]]) {
    const r = rowOf(picker, name);
    await expect(r).toHaveAttribute("data-enterable", "false");
    await expect(r.getByTestId("picker-row-note")).toHaveText(note);
    await r.click({ force: true });                                             // a user can click it; nothing opens
    await expect(current(picker)).toHaveText("code");
  }
  // Corvin's own data shows with Show hidden, and cannot be entered
  await picker.getByTestId("picker-crumb").filter({ hasText: "u" }).click();
  await picker.getByTestId("picker-hidden").check();
  await expect(rowOf(picker, ".corvin").getByTestId("picker-row-note")).toHaveText("Corvin's own data");
  await expect(rowOf(picker, ".corvin")).toHaveAttribute("data-enterable", "false");
  // a system folder named by path: no listing, the rule's sentence, Choose off
  await enterPath(page, "adopt-location", "/etc");
  await expect(picker.getByTestId("picker-blocked")).toHaveText("This folder cannot be opened here.");
  await expect(picker.getByTestId("picker-verdict-text")).toHaveText(SENT.system_prefix);
  await expect(picker.getByTestId("picker-choose")).toBeDisabled();
  // a relative path is refused by the input rule before anything is listed
  await picker.getByTestId("adopt-location").fill("code/x");
  await picker.getByTestId("adopt-location").press("Enter");
  await expect(picker.getByTestId("picker-error")).toContainText(SENT.path_rule);
  await expect(picker.getByTestId("picker-choose")).toBeDisabled();
  await expect(page.getByTestId("folder-picker")).toBeVisible();               // the dialog never closes on an error
});

test("a folder that does not answer in time keeps the dialog open; Retry asks again", async ({ page }) => {
  await base(page);
  const st = await stubPicker(page);
  const picker = await openAdoptPicker(page);
  await picker.locator('[data-testid="picker-place"][data-kind="home"]').click();
  await rowOf(picker, "code").click();
  await rowOf(picker, "nas-mount").click();
  await expect(picker.getByTestId("picker-slow")).toContainText("did not answer in time");
  await expect(picker.getByTestId("picker-verdict-text")).toHaveText(SENT.slow);
  await expect(picker.getByTestId("picker-choose")).toBeDisabled();
  await expect(page.getByTestId("folder-picker")).toBeVisible();
  await picker.getByTestId("picker-retry").click();
  await expect(picker.getByTestId("picker-verdict-text")).toHaveText("Can be used");
  expect(st.slowHits[`${HOME}/code/nas-mount`]).toBe(2);
});

test("keyboard only: open, type to jump, Enter in, Backspace up, Space marks, Enter chooses; Esc cancels and keeps the value", async ({ page }) => {
  await base(page);
  await stubPicker(page);
  await page.goto("/console/app/projects");
  await page.getByTestId("adopt-start").focus();
  await page.keyboard.press("Enter");
  await page.getByTestId("adopt-folder-choose").focus();
  await page.keyboard.press("Enter");
  const picker = page.getByTestId("folder-picker");
  await expect(picker.getByTestId("picker-list")).toBeFocused();
  await expect(current(picker)).toHaveText("w-0123456789");
  await page.keyboard.press("Backspace");
  await expect(current(picker)).toHaveText("projects");
  // Ctrl+L: the path field, from the keyboard
  await page.keyboard.press("Control+l");
  await expect(picker.getByTestId("adopt-location")).toBeFocused();
  await page.keyboard.type(HOME);
  await page.keyboard.press("Enter");
  await expect(current(picker)).toHaveText("u");
  await picker.getByTestId("picker-list").focus();
  await page.keyboard.type("c");
  await expect(picker.locator('[data-testid="picker-row"][aria-selected="true"]')).toHaveAttribute("data-name", "code");
  await page.keyboard.press("Enter");
  await expect(current(picker)).toHaveText("code");
  await page.keyboard.type("no");
  await expect(picker.locator('[data-testid="picker-row"][aria-selected="true"]')).toHaveAttribute("data-name", "notes");
  await page.keyboard.press("ArrowDown");
  await expect(picker.locator('[data-testid="picker-row"][aria-selected="true"]')).toHaveAttribute("data-name", "private");
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("Enter");
  await expect(current(picker)).toHaveText("notes");
  await page.keyboard.press("Space");
  await expect(picker.getByTestId("picker-choose")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("adopt-folder-value")).toHaveText(`${HOME}/code/notes`);
  // open again, wander off, Esc: the value stays
  await page.getByTestId("adopt-folder-choose").focus();
  await page.keyboard.press("Enter");
  await expect(picker.getByTestId("picker-list")).toBeFocused();
  await expect(current(picker)).toHaveText("notes");                           // reopens at the chosen folder
  await page.keyboard.press("Backspace");
  await expect(current(picker)).toHaveText("code");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("folder-picker")).toHaveCount(0);
  await expect(page.getByTestId("adopt-form")).toBeVisible();                   // Esc closed only the picker
  await expect(page.getByTestId("adopt-folder-value")).toHaveText(`${HOME}/code/notes`);
});

test("New folder here: the name rule is shown inline; a valid name gets its own verdict before Choose", async ({ page }) => {
  await base(page);
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: { available: true, templates: [
    { id: "knowledge-only", version: 1, title: "Knowledge only", description: "", features: ["core"], offered: ["core"], docs_mode: "plain" }], features: [] } }));
  const st = await stubPicker(page);
  await page.goto("/console/app/projects");
  await page.getByTestId("new-project-button").click();
  const wiz = page.getByTestId("new-project-wizard");
  await wiz.getByTestId("template-option").first().click();
  await wiz.getByTestId("wizard-next").click();
  await wiz.getByTestId("wizard-title").fill("Alpha two");
  await wiz.getByTestId("wizard-place-custom").check();
  await expect(wiz.getByTestId("wizard-location")).toHaveCount(0);              // no bare path field before the picker opens
  await wiz.getByTestId("wizard-folder-choose").click();
  const picker = page.getByTestId("folder-picker");
  await expect(picker.getByTestId("picker-title")).toHaveText('Choose a folder for "Alpha two"');
  await picker.locator('[data-testid="picker-place"][data-kind="home"]').click();
  await rowOf(picker, "code").click();
  await picker.getByTestId("picker-new-name").fill(".alpha");
  await expect(picker.getByTestId("picker-name-rule")).toHaveText("Use letters, digits, '-', '_' or '.', no leading dot, at most 80 characters.");
  await expect(picker.getByTestId("picker-choose")).toBeDisabled();
  await picker.getByTestId("picker-new-name").fill("alpha-2");
  await expect(picker.getByTestId("picker-target")).toHaveText(`${HOME}/code/alpha-2`);
  await expect(picker.getByTestId("picker-verdict-text")).toHaveText("Can be used — a new folder will be created when you continue.");
  await expect(picker.getByTestId("picker-choose")).toBeEnabled();
  expect(st.calls).toContain(`${HOME}/code/alpha-2|create|`);
  await picker.getByTestId("picker-choose").click();
  await expect(wiz.getByTestId("wizard-folder-value")).toHaveText(`${HOME}/code/alpha-2`);
  await expect(wiz.getByTestId("wizard-folder-badge")).toHaveText("Own folder");
  await expect(wiz.getByTestId("wizard-title")).toHaveValue("Alpha two");      // the wizard kept its state
});

test("a folder whose path contains a hint word is warned about and Choose stays enabled", async ({ page }) => {
  await base(page);
  await stubPicker(page);
  const picker = await openAdoptPicker(page);
  await enterPath(page, "adopt-location", `${HOME}/code/forge-lab`);
  await expect(picker.getByTestId("picker-verdict-text")).toHaveText("Can be used");
  await expect(picker.getByTestId("picker-warning")).toHaveText(HINT);
  await expect(picker.getByTestId("picker-choose")).toBeEnabled();
});

test("a star adds the current folder to Favourites; Clear recent places empties Recent", async ({ page }) => {
  await base(page);
  const st = await stubPicker(page);
  const picker = await openAdoptPicker(page);
  await expect(picker.locator('[data-testid="picker-place-group"][data-group="recent"]')).toContainText("notes");
  await expect(picker.locator('[data-testid="picker-place"][data-kind="documents"]')).toBeDisabled();
  await expect(picker.locator('[data-testid="picker-place"][data-kind="drive"]')).toContainText("(removable)");
  await picker.locator('[data-testid="picker-place"][data-kind="home"]').click();
  await rowOf(picker, "code").click();
  await picker.getByTestId("picker-favourite").click();
  await expect.poll(() => st.favourites).toEqual([[`${HOME}/code`]]);
  await expect(picker.locator('[data-testid="picker-place"][data-kind="favourite"]')).toHaveAttribute("data-path", `${HOME}/code`);
  await expect(picker.getByTestId("picker-favourite")).toHaveAttribute("aria-pressed", "true");
  await picker.getByTestId("picker-clear-recent").click();
  await expect.poll(() => st.clearedRecent).toBe(1);
  await expect(picker.locator('[data-testid="picker-place-group"][data-group="recent"]')).toHaveCount(0);
});

test("the picker carries no ADR id and no German word", async ({ page }) => {
  await base(page);
  await stubPicker(page);
  const picker = await openAdoptPicker(page);
  await picker.locator('[data-testid="picker-place"][data-kind="home"]').click();
  await rowOf(picker, "code").click();
  const text = await picker.innerText();
  expect(text).not.toMatch(/ADR-\d|CONCEPT-\d|REVIEW-\d|T-0\d{3}/);
  expect(text).not.toMatch(/\b(Ordner|wählen|Abbrechen|Pfad)\b/);
});

test("at 375 px the picker is a full-screen sheet: chips, collapsed breadcrumbs, 44 px rows, a sticky footer", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await base(page);
  await stubPicker(page);
  const picker = await openAdoptPicker(page);
  await picker.locator('[data-testid="picker-place"][data-kind="home"]').click();
  await rowOf(picker, "code").click();
  const box = await picker.boundingBox();
  expect(box && Math.round(box.width)).toBe(375);
  expect(box && Math.round(box.height)).toBeGreaterThanOrEqual(800);
  const chips = picker.getByTestId("picker-place");
  const a = await chips.nth(0).boundingBox();
  const b = await chips.nth(1).boundingBox();
  expect(a && b && Math.abs(a.y - b.y)).toBeLessThan(2);                         // one row of chips
  await expect(picker.getByTestId("picker-crumb")).toHaveText(["‹ u"]);
  await expect(current(picker)).toHaveText("code");
  const r = await rowOf(picker, "notes").boundingBox();
  expect(r && r.height).toBeGreaterThanOrEqual(44);
  const f = await picker.getByTestId("picker-footer").boundingBox();
  expect(f && f.y + f.height).toBeLessThanOrEqual(812.5);
  await expect(picker.getByTestId("picker-choose")).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
});

for (const t of ["light", "dark"] as const) {
  for (const s of [{ name: "1280", width: 1280, height: 900 }, { name: "375", width: 375, height: 812 }]) {
    test(`picker screenshot ${t} ${s.name}`, async ({ page }) => {
      await theme(page, t);
      await page.setViewportSize({ width: s.width, height: s.height });
      await base(page);
      await stubPicker(page);
      const picker = await openAdoptPicker(page);
      await expect(page.locator("html")).toHaveAttribute("data-theme", t);
      await picker.locator('[data-testid="picker-place"][data-kind="home"]').click();
      await rowOf(picker, "code").click();
      await expect(rowOf(picker, "notes")).toBeVisible();
      await page.keyboard.press("ArrowDown");
      fs.mkdirSync(OUT, { recursive: true });
      await page.screenshot({ path: path.join(OUT, `picker-${t}-${s.name}.png`) });
      await enterPath(page, "adopt-location", `${HOME}/code/forge-lab`);
      await expect(picker.getByTestId("picker-warning")).toBeVisible();
      await page.screenshot({ path: path.join(OUT, `picker-warning-${t}-${s.name}.png`) });
    });
  }
}
