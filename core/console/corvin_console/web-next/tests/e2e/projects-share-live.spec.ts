/**
 * Share on the LIVE console, nothing stubbed (T-0402): a throwaway project is exported through the Export dialog (a planted symlink
 * shows under Left out, the ZIP downloads), the downloaded file is imported through the Import dialog as a copy (the preview names
 * the real conflicts, Import turns on only after the new id), and the project is duplicated through the Duplicate dialog.
 *
 * It creates exactly three projects in the tenant's managed workspace - `p34-share-src`, `p34-share-src-2` (the import),
 * `p34-share-src-3` (the duplicate) - and leaves nothing behind: each entry is removed from the list and exactly its folder
 * (`<managed_root>/<id>`, holding that id's project.yaml) is deleted. No other project or folder is touched.
 *
 * Run: CORVIN_E2E_ALLOW_LIVE=1 CONSOLE_BASE_URL=http://127.0.0.1:8765/console \
 *      npx playwright test -c playwright.config.ts projects-share-live --project=chromium
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test, expect, type APIRequestContext } from "@playwright/test";

const SRC = "p34-share-src";
const IMPORTED = `${SRC}-2`;
const COPY = `${SRC}-3`;
const MINE = [SRC, IMPORTED, COPY];
const OUT = process.env.SHOTS_DIR ?? "/tmp/phase34-shots";

test.describe.configure({ mode: "serial" });

async function csrf(req: APIRequestContext): Promise<string> {
  const r = await req.get("/v1/console/auth/whoami");
  expect(r.ok()).toBeTruthy();
  return (await r.json()).csrf_token as string;
}

async function list(req: APIRequestContext) {
  const r = await req.get("/v1/console/projects");
  expect(r.ok()).toBeTruthy();
  return r.json() as Promise<{ managed_root: string; projects: { id: string; path: string; storage?: string }[] }>;
}

/** Removes the throwaway entries and deletes exactly the folders this spec created. */
async function cleanUp(req: APIRequestContext, managedRoot: string) {
  const h = { "X-CSRF-Token": await csrf(req) };
  const l = await list(req);
  for (const id of MINE) {
    if (l.projects.some((p) => p.id === id)) await req.delete(`/v1/console/projects/${id}`, { headers: h });
    const dir = path.join(managedRoot, id);
    const yaml = path.join(dir, "project.yaml");
    if (path.dirname(dir) === managedRoot && fs.existsSync(yaml) && new RegExp(`\\bid:\\s*${id}\\s*$`, "m").test(fs.readFileSync(yaml, "utf8"))) {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  }
}

test("share on the running console: export a real project, import it as a copy, duplicate it", async ({ page }) => {
  test.setTimeout(300_000);
  const req = page.request;
  const before = await list(req);
  const mr = before.managed_root;
  for (const id of MINE) {
    expect(before.projects.some((p) => p.id === id), `a project ${id} already exists - remove it first`).toBe(false);
    expect(fs.existsSync(path.join(mr, id)), `the folder ${id} already exists`).toBe(false);
  }
  const others = before.projects.map((p) => p.id).sort();
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "p34-share-"));
  try {
    // the source project, through the real plan -> create
    const h = { "X-CSRF-Token": await csrf(req) };
    const plan = await req.post("/v1/console/projects/plan", { headers: h, data: { template: "knowledge-only", title: "P34 share check", project_id: SRC } });
    expect(plan.status(), await plan.text()).toBe(200);
    const created = await req.post("/v1/console/projects", { headers: h, data: { plan_id: (await plan.json()).plan_id } });
    expect(created.status(), await created.text()).toBe(201);
    const srcDir = path.join(mr, SRC);
    fs.writeFileSync(path.join(srcDir, "notes.md"), "# Share check\n");
    fs.symlinkSync("notes.md", path.join(srcDir, "latest-link"));

    // Export: the real preview, the planted link under Left out, a download
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(`/console/app/projects/${SRC}?tab=settings`, { waitUntil: "domcontentloaded" });
    await page.getByTestId("share-export").click({ timeout: 45_000 });
    const ex = page.getByTestId("export-dialog");
    await expect(ex.getByTestId("export-included")).toContainText(/\d+ files? · /, { timeout: 60_000 });
    await expect(ex.getByTestId("export-left-out-row").filter({ hasText: "latest-link (link)" })).toHaveCount(1);
    await expect(ex.getByTestId("export-note")).toHaveText("Archives inside the project are not checked for secrets.");
    await expect(ex.getByTestId("export-class-line")).toHaveText("Data class: Internal — can be shared.");
    await page.screenshot({ path: path.join(OUT, "live-export-dialog-1280.png"), animations: "disabled" });
    const dlEvent = page.waitForEvent("download", { timeout: 120_000 });
    await ex.getByTestId("export-submit").click();
    const dl = await dlEvent;
    expect(dl.suggestedFilename()).toMatch(new RegExp(`^corvin-project-${SRC}-\\d{8}\\.zip$`));
    const zip = path.join(work, dl.suggestedFilename());
    await dl.saveAs(zip);
    expect(fs.readFileSync(zip).subarray(0, 2).toString()).toBe("PK");
    await ex.getByTestId("export-cancel").click();

    // Import: the real validate report, its conflicts, Import only after the new id
    await page.goto("/console/app/projects", { waitUntil: "domcontentloaded" });
    await page.getByTestId("import-start").click({ timeout: 45_000 });
    await page.getByTestId("import-file").setInputFiles(zip);
    const im = page.getByTestId("import-dialog");
    await expect(im.getByTestId("import-preview")).toBeVisible({ timeout: 120_000 });
    await expect(im.getByTestId("import-origin")).toHaveText("Unverified origin");
    await expect(im.locator('[data-testid="import-conflict"][data-kind="id_taken"]')).toContainText(`A project "${SRC}" already exists in this list.`);
    await expect(im.locator('[data-testid="import-conflict"][data-kind="same_source"]')).toBeVisible();
    await expect(im.getByTestId("import-where-path")).toHaveText(path.join(mr, IMPORTED));
    await expect(im.getByTestId("import-submit")).toBeDisabled();
    await page.screenshot({ path: path.join(OUT, "live-import-conflicts-1280.png"), animations: "disabled" });
    await im.getByTestId("import-fix-copy").click();
    await expect(im.getByTestId("import-id")).toHaveValue(IMPORTED);
    await expect(im.getByTestId("import-no-conflicts")).toBeVisible({ timeout: 30_000 });
    await expect(im.getByTestId("import-submit")).toBeEnabled();
    await im.getByTestId("import-submit").click();
    await expect(im.getByTestId("import-result")).toBeVisible({ timeout: 180_000 });
    await page.screenshot({ path: path.join(OUT, "live-import-result-1280.png"), animations: "disabled" });
    await im.getByTestId("import-close").click();
    const imported = page.locator(`[data-testid="project-card"][data-project-id="${IMPORTED}"]`);
    await expect(imported).toBeVisible({ timeout: 30_000 });
    expect(fs.readFileSync(path.join(mr, IMPORTED, "notes.md"), "utf8")).toBe("# Share check\n");
    expect(fs.existsSync(path.join(mr, IMPORTED, "latest-link"))).toBe(false);          // links never travel
    const after = (await list(req)).projects.find((p) => p.id === IMPORTED);
    expect(after?.storage).toBe("managed");

    // Duplicate: the next free id, the copy in the list
    await page.goto(`/console/app/projects/${SRC}?tab=settings`, { waitUntil: "domcontentloaded" });
    await page.getByTestId("share-duplicate").click({ timeout: 45_000 });
    const du = page.getByTestId("duplicate-dialog");
    await expect(du.getByTestId("duplicate-id")).toHaveValue(COPY, { timeout: 30_000 });
    await du.getByTestId("duplicate-submit").click();
    await expect(du).toHaveAttribute("data-stage", "done", { timeout: 180_000 });
    await du.getByTestId("duplicate-close").click();
    await page.goto("/console/app/projects", { waitUntil: "domcontentloaded" });
    await expect(page.locator(`[data-testid="project-card"][data-project-id="${COPY}"]`)).toBeVisible({ timeout: 45_000 });
    expect(fs.readFileSync(path.join(mr, COPY, "notes.md"), "utf8")).toBe("# Share check\n");
  } finally {
    await cleanUp(req, mr);
    fs.rmSync(work, { recursive: true, force: true });
  }
  const end = await list(req);
  expect(end.projects.map((p) => p.id).sort()).toEqual(others);
  for (const id of MINE) expect(fs.existsSync(path.join(mr, id))).toBe(false);
});
