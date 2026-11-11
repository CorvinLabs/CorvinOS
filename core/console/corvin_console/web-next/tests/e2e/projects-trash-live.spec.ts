/**
 * The Trash on the RUNNING console, nothing stubbed (T-0405, ADR-2321 Decision 12): a throwaway managed project is created through
 * the wizard, its files are deleted from the Danger zone with the typed id, the list shows "Trash (1)", Restore brings the card
 * back with the folder byte-identical, then it is deleted again and removed for good with "Delete permanently…".
 *
 * It touches exactly ONE project, `p5-trash-live`, which it creates in the tenant's managed workspace and which must not exist
 * before. Every other project and folder is left alone (checked: the other ids and the trash entries of other projects are the
 * same afterwards). On a failure the cleanup purges that project's trash entries, or removes it from the list and deletes exactly
 * `<managed_root>/p5-trash-live` when it still holds that project.yaml.
 *
 * Run: CORVIN_E2E_ALLOW_LIVE=1 CONSOLE_BASE_URL=http://127.0.0.1:8765/console \
 *      npx playwright test -c playwright.config.ts projects-trash-live --project=chromium
 */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

const ID = "p5-trash-live";
const TITLE = "P5 trash live";
const OUT = process.env.SHOTS_DIR ?? "/tmp/phase5-shots";

test.describe.configure({ mode: "serial" });

async function csrf(req: APIRequestContext): Promise<string> {
  const r = await req.get("/v1/console/auth/whoami");
  expect(r.ok()).toBeTruthy();
  return (await r.json()).csrf_token as string;
}

interface Listed { default_storage: string; managed_root: string; trash_count?: number; projects: { id: string; storage?: string }[] }
async function list(req: APIRequestContext): Promise<Listed> {
  const r = await req.get("/v1/console/projects");
  expect(r.ok()).toBeTruthy();
  return r.json() as Promise<Listed>;
}

async function trash(req: APIRequestContext): Promise<{ entry: string; id: string }[]> {
  const r = await req.get("/v1/console/projects/-/trash");
  expect(r.ok(), await r.text()).toBeTruthy();
  return (await r.json()).entries;
}

function tree(root: string): Record<string, string> {
  const out: Record<string, string> = {};
  const walk = (d: string) => {
    for (const n of fs.readdirSync(d).sort()) {
      const p = path.join(d, n);
      const rel = path.relative(root, p);
      if (rel.split(path.sep)[0] === ".git") continue;                 // git may pack objects in the background
      const st = fs.lstatSync(p);
      if (st.isSymbolicLink()) out[rel] = `link:${fs.readlinkSync(p)}`;
      else if (st.isDirectory()) { out[rel] = "dir"; walk(p); }
      else out[rel] = crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
    }
  };
  walk(root);
  return out;
}

async function cleanUp(req: APIRequestContext, managedRoot: string) {
  const h = { "X-CSRF-Token": await csrf(req) };
  for (const e of (await trash(req)).filter((x) => x.id === ID)) {
    await req.delete(`/v1/console/projects/-/trash/${encodeURIComponent(e.entry)}`, { headers: h, data: { confirm_id: ID } });
  }
  if ((await list(req)).projects.some((p) => p.id === ID)) await req.delete(`/v1/console/projects/${ID}`, { headers: h });
  const dir = path.join(managedRoot, ID);
  const yaml = path.join(dir, "project.yaml");
  if (path.dirname(dir) === managedRoot && fs.existsSync(yaml) && new RegExp(`\\bid:\\s*${ID}\\b`).test(fs.readFileSync(yaml, "utf8"))) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

async function shot(page: Page, name: string) {
  fs.mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
}

test("the Trash on the running console: delete to Trash, restore identical, delete permanently", async ({ page }) => {
  test.setTimeout(300_000);
  const req = page.request;
  const before = await list(req);
  expect(before.projects.some((p) => p.id === ID), `a project ${ID} already exists - remove it first`).toBe(false);
  const mr = before.managed_root;
  const dir = path.join(mr, ID);
  expect(fs.existsSync(dir), "the throwaway folder already exists").toBe(false);
  const others = before.projects.map((p) => p.id).sort();
  const otherTrash = (await trash(req)).map((e) => e.entry).sort();
  expect(otherTrash.some((e) => e.startsWith(`${ID}-`)), "a trash entry of the throwaway id already exists").toBe(false);
  try {
    // create the throwaway project in the Corvin workspace through the wizard
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/console/app/projects", { waitUntil: "domcontentloaded" });
    await page.getByTestId("new-project-button").click({ timeout: 45_000 });
    const wiz = page.getByTestId("new-project-wizard");
    await wiz.locator('[data-testid="template-option"][data-template="knowledge-only"]').click();
    await wiz.getByTestId("wizard-next").click();
    await wiz.getByTestId("wizard-title").fill(TITLE);
    await wiz.getByTestId("wizard-place-managed").check();
    await wiz.getByTestId("wizard-next").click();
    await wiz.getByTestId("wizard-next").click();
    await expect(wiz.getByTestId("review-location")).toHaveText(dir, { timeout: 30_000 });
    await wiz.getByTestId("wizard-create").click();
    await expect(page.getByTestId("new-project-wizard")).toHaveCount(0, { timeout: 90_000 });
    expect(fs.existsSync(path.join(dir, "project.yaml"))).toBe(true);
    fs.writeFileSync(path.join(dir, "notes-p5.md"), "# kept through the Trash\n");
    const hashed = tree(dir);

    // Danger zone: the button enables only on the exact id, the list then shows Trash (n)
    await page.goto(`/console/app/projects/${ID}?tab=settings`, { waitUntil: "domcontentloaded" });
    await page.getByTestId("delete-files").click({ timeout: 45_000 });
    const dlg = page.getByTestId("delete-dialog");
    await expect(dlg.getByTestId("delete-submit")).toBeDisabled();
    await dlg.getByTestId("delete-confirm").fill(ID.slice(0, -1));
    await expect(dlg.getByTestId("delete-submit")).toBeDisabled();
    await dlg.getByTestId("delete-confirm").fill(ID);
    await shot(page, "live-delete-dialog-1280");
    await dlg.getByTestId("delete-submit").click();
    await expect(page).toHaveURL(/\/console\/app\/projects$/, { timeout: 30_000 });
    await expect(page.getByTestId("trash-link")).toHaveText(`Trash (${otherTrash.length + 1})`, { timeout: 30_000 });
    expect(fs.existsSync(dir)).toBe(false);
    expect((await list(req)).projects.some((p) => p.id === ID)).toBe(false);
    await expect(page.locator(`[data-testid="project-card"][data-project-id="${ID}"]`)).toHaveCount(0);

    // the Trash section: Restore brings the card back, the folder byte-identical
    await page.getByTestId("trash-link").click();
    const row = page.locator(`[data-testid="trash-row"][data-project-id="${ID}"]`);
    await expect(row).toHaveCount(1);
    await shot(page, "live-trash-section-1280");
    await row.getByTestId("trash-restore").click();
    await expect(page.getByTestId("trash-note")).toHaveText(`${TITLE} was restored to the Corvin workspace.`, { timeout: 30_000 });
    expect(tree(dir)).toEqual(hashed);
    await page.getByTestId("trash-back").click();
    await expect(page.locator(`[data-testid="project-card"][data-project-id="${ID}"]`)).toBeVisible({ timeout: 30_000 });

    // delete again, then Delete permanently with the typed id
    const h = { "X-CSRF-Token": await csrf(req) };
    const del = await req.delete(`/v1/console/projects/${ID}/files`, { headers: h, data: { confirm_id: ID } });
    expect(del.status(), await del.text()).toBe(200);
    await page.goto("/console/app/projects?view=trash", { waitUntil: "domcontentloaded" });
    const row2 = page.locator(`[data-testid="trash-row"][data-project-id="${ID}"]`);
    await row2.getByTestId("trash-purge").click({ timeout: 30_000 });
    const pd = page.getByTestId("purge-dialog");
    await expect(pd.getByTestId("purge-submit")).toBeDisabled();
    await pd.getByTestId("purge-confirm").fill(ID);
    await pd.getByTestId("purge-submit").click();
    await expect(page.getByTestId("trash-note")).toHaveText(`${TITLE} was deleted permanently.`, { timeout: 30_000 });
    expect((await trash(req)).some((e) => e.id === ID)).toBe(false);
    expect(fs.existsSync(dir)).toBe(false);
    // nothing else changed
    expect((await list(req)).projects.map((p) => p.id).sort()).toEqual(others);
    expect((await trash(req)).map((e) => e.entry).sort()).toEqual(otherTrash);
  } finally {
    await cleanUp(req, mr);
  }
});
