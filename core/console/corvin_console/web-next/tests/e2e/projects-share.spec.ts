/**
 * Share on the served bundle (T-0402): the Export dialog (class gating, parts, history findings, a download, a large export as a job
 * with progress, done and failure), the Import dialog (upload, preview with the "Unverified origin" label, conflict rows each with its
 * fix, the picker, the Import button following the verdict, Cancel removing the staging, the result) and Duplicate, plus the list's
 * multi-select "Export selected (n)". The project endpoints are stubbed at the network boundary with the server's shapes and
 * sentences (routes/projects.py, projects_service.py); the real round trip is proven by tests/e2e/test_projects_share_e2e.py over
 * HTTP, and in the browser with nothing stubbed by its opt-in companion spec (listed in sandbox-origin.ts).
 *
 * Stub note: the import verdict answers `nested` for the place /home/u/code/notes, which the stubbed picker lets you choose - the
 * real picker would already refuse a nested place; here it proves that the dialog shows the verdict's conflict for a chosen place.
 *
 * Screenshots: SHOTS_DIR (default /tmp/phase34-shots), 1280 and 375 px in both themes.
 */
import fs from "node:fs";
import path from "node:path";
import { test, expect, type Page, type Route } from "@playwright/test";
import { HOME, MR, enterPath, stubPicker } from "./projects-picker-stub";

const OUT = process.env.SHOTS_DIR ?? "/tmp/phase34-shots";
const base = (id: string, title: string, classification: string) => ({
  id, path: `${MR}/${id}`, template: "research@1", state: "ok", title, docs_mode: "plain", kb: true, active: ["core", "kb"],
  features: { core: true, kb: true }, directory_peers: [], role: "research", classification, assistant_access: "read",
  added_at: "2026-11-08T10:00:00Z", storage: "managed", display_path: `Corvin workspace › ${id}`, location_warnings: [],
  catalogue: { available: true, features: [] },
});
const PROJECTS = {
  alpha: base("alpha", "Alpha research", "internal"),
  beta: base("beta", "Beta contracts", "confidential"),
  vault: base("vault", "Vault", "secret"),
  big: base("big", "Big media", "internal"),
  hist: base("hist", "History leak", "internal"),
  long: base("long", `Long ${"t".repeat(113)}`, "internal"),        // 118 characters: "<title> (copy)" would be 125
};
type Pid = keyof typeof PROJECTS;
const parts = (on: Record<string, boolean>) => ["files", "kb", "git_history", "videos_index", "generated"].map((name) => ({ name, included: !!(on[name] ?? name === "files") }));
const ID_TAKEN = 'A project "alpha" already exists in this list.';
const SAME = "This was exported from a project in this list. It can only be imported as a copy.";
const NESTED = "It lies inside another project (alpha).";

interface St {
  previews: string[];
  exports: Record<string, unknown>[];
  jobPolls: number;
  validated: number;
  verdicts: string[];
  applied: Record<string, unknown>[];
  cancelled: string[];
  duplicated: Record<string, unknown>[];
  list: Record<string, unknown>[];
}

async function stub(page: Page, opts: { gpg?: boolean; reject?: boolean; jobFails?: boolean } = {}): Promise<St> {
  const st: St = { previews: [], exports: [], jobPolls: 0, validated: 0, verdicts: [], applied: [], cancelled: [], duplicated: [],
    list: [PROJECTS.alpha, PROJECTS.beta, PROJECTS.vault] };
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: { available: true, templates: [], features: [] } }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: { projects_root: `${HOME}/projects`, projects_root_set: false,
    default_storage: "managed", managed_root: MR, workspace: "ok", projects: st.list } }));
  await page.route(/\/v1\/console\/chat\/sessions$/, (r) => r.fulfill({ json: { tenant_id: "_default", count: 0, sessions: [] } }));
  await stubPicker(page);
  for (const id of Object.keys(PROJECTS) as Pid[]) {
    await page.route(new RegExp(`/v1/console/projects/${id}$`), (r) => r.fulfill({ json: PROJECTS[id] }));
    await page.route(new RegExp(`/v1/console/projects/${id}/health$`), (r) => r.fulfill({ json: { project_id: id, ok: true, checks: [] } }));
    await page.route(new RegExp(`/v1/console/projects/${id}/duplicate$`), (r) => {
      const b = r.request().postDataJSON() as { project_id: string };
      st.duplicated.push({ from: id, ...b });
      const copy = { ...PROJECTS[id], id: b.project_id, title: PROJECTS[id].title, path: `${MR}/${b.project_id}`, display_path: `Corvin workspace › ${b.project_id}` };
      st.list = [...st.list, copy];
      return r.fulfill({ status: 201, json: { project: copy, warnings: [], notes: ["Links tracked in git are not on disk; git status shows them as deleted."], origin: "unverified" } });
    });
  }
  await page.route(/\/v1\/console\/projects\/-\/export\/preview\?/, (r) => {
    const u = new URL(r.request().url());
    const ids = (u.searchParams.get("ids") ?? "").split(",") as Pid[];
    const on = new Set((u.searchParams.get("parts") ?? "").split(",").filter(Boolean));
    st.previews.push([...on].sort().join(","));
    return r.fulfill({ json: { gpg: !!opts.gpg, notes: ["Archives inside the project are not checked for secrets."],
      warnings: [], projects: ids.map((id) => ({ id, title: PROJECTS[id].title, template: "research@1", role: "research",
        classification: PROJECTS[id].classification, docs_mode: "plain", features: PROJECTS[id].features,   // the live shape names the features
        parts: parts({ kb: on.has("kb"), git_history: on.has("git_history"), generated: on.has("generated") }),
        left_out: [{ path: ".env", reason: "secret_name" }, { path: "docs/latest", reason: "link" }, { path: ".mcp.json", reason: "machine_local" }],
        counts: { files: 212, bytes: 5_347_738 }, commits: on.has("git_history") ? 38 : null, kb_entries: 41,
        history_findings: id === "hist" && on.has("git_history") ? 1 : 0 })) } });
  });
  await page.route(/\/v1\/console\/projects\/-\/export$/, (r) => {
    const b = r.request().postDataJSON() as { project_ids: string[] };
    st.exports.push(b);
    if (b.project_ids.includes("big")) return r.fulfill({ status: 202, json: { job_id: "01JOBJOBJOBJOBJOBJOBJOBJOB", warnings: [] } });
    if (b.project_ids.includes("hist")) {
      return r.fulfill({ status: 409, json: { detail: { reason_class: "secret_in_history",
        message: "notes/key.txt in the git history looks like it contains a secret. Export without git history, or remove it from the history yourself." } } });
    }
    const name = b.project_ids.length === 1 ? `corvin-project-${b.project_ids[0]}-20261111.zip` : "corvin-projects-20261111.zip";
    return r.fulfill({ status: 200, body: Buffer.from("PK\x05\x06" + "\0".repeat(18)), headers: { "content-type": "application/zip",
      "content-disposition": `attachment; filename="${name}"` } });
  });
  await page.route(/\/v1\/console\/projects\/-\/exports\/01JOBJOBJOBJOBJOBJOBJOBJOB$/, (r) => {
    st.jobPolls += 1;
    if (st.jobPolls < 3) return r.fulfill({ json: { state: "running", progress: { files: 40 * st.jobPolls, bytes: 50_000_000 * st.jobPolls } } });
    if (opts.jobFails) return r.fulfill({ json: { state: "error", progress: { files: 80, bytes: 1 }, reason_class: "secret_shape",
      message: "media/notes.txt looks like it contains a secret. Exclude or clean it, then export again." } });
    return r.fulfill({ json: { state: "done", progress: { files: 900, bytes: 150_000_000 },
      download_url: "/v1/console/projects/-/exports/01JOBJOBJOBJOBJOBJOBJOBJOB/file", warnings: [] } });
  });
  const SID = "0123456789abcdef0123456789abcdef";
  await page.route(/\/v1\/console\/projects\/-\/import\/validate$/, (r) => {
    st.validated += 1;
    if (opts.reject) return r.fulfill({ status: 422, json: { detail: { reason_class: "integrity", message: "The bundle's contents do not match its manifest." } } });
    return r.fulfill({ json: { staging_id: SID, origin: "unverified", exporter: { app: "corvinos", version: "1.9.0" }, created_at: "2026-11-10T09:00:00Z",
      projects: [{ id: "alpha", title: "Alpha research", role: "research", classification: "public", docs_mode: "plain", template: "research@1",
        features: {}, counts: { files: 212, bytes: 5_347_738 }, parts: parts({ kb: true, git_history: true }), git: { head: "refs/heads/main" },
        left_out: [], warnings: [], source_project_uid: "a".repeat(32), default_location: { storage: "managed", path: `${MR}/alpha-2` },
        conflicts: [{ kind: "id_taken", message: ID_TAKEN, fix: { suggested_id: "alpha-2" } }, { kind: "same_source", message: SAME, fix: { suggested_id: "alpha-2" } }] }] } });
  });
  await page.route(new RegExp(`/v1/console/projects/-/import/${SID}/verdict\\?`), (r) => {
    const u = new URL(r.request().url());
    const pid = u.searchParams.get("project_id") ?? "alpha";
    const loc = u.searchParams.get("location");
    st.verdicts.push(`${pid}|${loc ?? ""}`);
    const conflicts = [];
    if (pid === "alpha") conflicts.push({ kind: "id_taken", message: ID_TAKEN, fix: { suggested_id: "alpha-2" } }, { kind: "same_source", message: SAME, fix: { suggested_id: "alpha-2" } });
    if (loc === `${HOME}/code/notes`) conflicts.push({ kind: "nested", message: NESTED, fix: { choose_location: true } });
    return r.fulfill({ json: { conflicts, location: loc ? { storage: "own", path: loc } : { storage: "managed", path: `${MR}/${pid}` } } });
  });
  await page.route(new RegExp(`/v1/console/projects/-/import/${SID}/apply$`), (r) => {
    const b = r.request().postDataJSON() as { project_id?: string; location?: string };
    st.applied.push(b);
    const id = b.project_id ?? "alpha";
    const p = { ...PROJECTS.alpha, id, path: b.location ?? `${MR}/${id}`, display_path: b.location ?? `Corvin workspace › ${id}` };
    st.list = [...st.list, p];
    return r.fulfill({ status: 201, json: { project: p, warnings: ["Git history could not be imported"],
      notes: ["A link tracked in git is not on disk, so git status shows it as deleted."], origin: "unverified" } });
  });
  await page.route(new RegExp(`/v1/console/projects/-/import/${SID}$`), (r: Route) => {
    if (r.request().method() === "DELETE") { st.cancelled.push(SID); return r.fulfill({ status: 204, body: "" }); }
    return r.fallback();
  });
  return st;
}

async function theme(page: Page, t: "light" | "dark") {
  await page.addInitScript((v) => { try { localStorage.setItem("corvin-theme", v); localStorage.setItem("corvin-dark-default-v2", "1"); } catch { /* ignore */ } }, t);
}

async function cleanText(page: Page, testid: string) {
  const text = await page.getByTestId(testid).innerText();
  expect(text).not.toMatch(/ADR-\d|CONCEPT-\d|REVIEW-\d|PLAN-\d|T-0\d{3}/);
  expect(text).not.toMatch(/\b(Projekt|Abbrechen|Datei|Ordner|wird|nicht)\b/);
}

const BUNDLE = { name: "corvin-project-alpha-20261110.zip", mimeType: "application/zip", buffer: Buffer.from("PK\x05\x06" + "\0".repeat(18)) };

async function openImport(page: Page) {
  await page.goto("/console/app/projects");
  await page.getByTestId("import-start").click();
  await page.getByTestId("import-file").setInputFiles(BUNDLE);
  await expect(page.getByTestId("import-preview")).toBeVisible();
}

test("export of an internal project: Included and Left out from the preview, the archive note, a download", async ({ page }) => {
  const st = await stub(page);
  await page.goto("/console/app/projects/alpha?tab=settings");
  await page.getByTestId("share-export").click();
  const dlg = page.getByTestId("export-dialog");
  await expect(dlg.getByTestId("export-included")).toContainText("212 files · 5.1 MB · knowledge base (41 entries) · git history (38 commits)");
  await expect(dlg.getByTestId("export-left-out-row")).toHaveText([".env (secret name)", "docs/latest (link)", ".mcp.json (machine-local)"]);
  await expect(dlg.getByTestId("export-note")).toHaveText("Archives inside the project are not checked for secrets.");
  await expect(dlg.getByTestId("export-class-line")).toHaveText("Data class: Internal — can be shared.");
  await expect(dlg.getByTestId("export-gpg-note")).toHaveText("A passphrase needs gpg, which is not installed on this machine.");
  await expect(dlg.getByTestId("export-passphrase")).toHaveCount(0);
  // a part toggle asks for a new preview
  await dlg.getByTestId("export-part-generated").check();
  await expect.poll(() => st.previews.at(-1)).toBe("generated,git_history,kb");
  await dlg.getByTestId("export-part-generated").uncheck();
  const dl = page.waitForEvent("download");
  await dlg.getByTestId("export-submit").click();
  expect((await dl).suggestedFilename()).toBe("corvin-project-alpha-20261111.zip");
  await expect(dlg.getByTestId("export-done")).toContainText("corvin-project-alpha-20261111.zip");
  expect(st.exports).toEqual([{ project_ids: ["alpha"], parts: { git_history: true, generated: false, kb: true }, confirm: false }]);
  await cleanText(page, "export-dialog");
});

test("a project without a knowledge base is not offered one in the export (REVIEW-0067 #17)", async ({ page }) => {
  await stub(page);
  await page.route(/\/v1\/console\/projects\/-\/export\/preview\?/, (r) => r.fulfill({ json: { gpg: false, notes: [], warnings: [], projects: [{
    id: "alpha", title: "Alpha research", template: "adopted@1", role: "", classification: "internal", docs_mode: "plain", features: {},
    parts: parts({ git_history: true }), left_out: [], counts: { files: 3, bytes: 900 }, commits: 2, kb_entries: 0, history_findings: 0 }] } }));
  await page.goto("/console/app/projects/alpha?tab=settings");
  await page.getByTestId("share-export").click();
  const dlg = page.getByTestId("export-dialog");
  await expect(dlg.getByTestId("export-included")).toContainText("3 files");
  await expect(dlg.getByTestId("export-part-history")).toBeVisible();
  await expect(dlg.getByTestId("export-part-kb")).toHaveCount(0);
});

test("a confidential project needs its typed id before Export ZIP turns on; gpg offers a passphrase", async ({ page }) => {
  const st = await stub(page, { gpg: true });
  await page.goto("/console/app/projects/beta?tab=settings");
  await page.getByTestId("share-export").click();
  const dlg = page.getByTestId("export-dialog");
  await expect(dlg.getByTestId("export-class-line")).toContainText("Type beta to confirm the export.");
  await expect(dlg.getByTestId("export-passphrase")).toBeVisible();
  await expect(dlg.getByTestId("export-submit")).toBeDisabled();
  await dlg.getByTestId("export-confirm").fill("bet");
  await expect(dlg.getByTestId("export-submit")).toBeDisabled();
  await dlg.getByTestId("export-confirm").fill("beta");
  await dlg.getByTestId("export-passphrase").fill("short");
  await expect(dlg.getByTestId("export-submit")).toBeDisabled();
  await dlg.getByTestId("export-passphrase").fill("long passphrase");
  const dl = page.waitForEvent("download");
  await dlg.getByTestId("export-submit").click();
  await dl;
  expect(st.exports[0]).toMatchObject({ project_ids: ["beta"], confirm: true, passphrase: "long passphrase" });
});

test("a secret project: Export ZIP is off with the reason, Duplicate is off with its reason", async ({ page }) => {
  const st = await stub(page);
  await page.goto("/console/app/projects/vault?tab=settings");
  await expect(page.getByTestId("share-duplicate")).toBeDisabled();
  await expect(page.getByTestId("share-duplicate-note")).toHaveText("Secret projects cannot be duplicated.");
  await page.getByTestId("share-export").click();
  const dlg = page.getByTestId("export-dialog");
  await expect(dlg.getByTestId("export-refused")).toHaveText("Secret projects cannot be exported.");
  await expect(dlg.getByTestId("export-submit")).toBeDisabled();
  expect(st.exports).toEqual([]);
});

test("history findings offer 'Export without git history'; the server's refusal is shown as its sentence", async ({ page }) => {
  const st = await stub(page);
  st.list = [...st.list, PROJECTS.hist];
  await page.goto("/console/app/projects/hist?tab=settings");
  await page.getByTestId("share-export").click();
  const dlg = page.getByTestId("export-dialog");
  await expect(dlg.getByTestId("export-history-findings")).toBeVisible();
  await dlg.getByTestId("export-submit").click();
  await expect(dlg.getByTestId("export-error")).toHaveText(/notes\/key\.txt in the git history looks like it contains a secret/);
  await dlg.getByTestId("export-without-history").click();
  await expect.poll(() => st.previews.at(-1)).toBe("kb");
  await expect(dlg.getByTestId("export-history-findings")).toHaveCount(0);
  await expect(dlg.getByTestId("export-part-history")).not.toBeChecked();
});

test("a large export runs as a job: progress, then a Download link to the job's file", async ({ page }) => {
  await stub(page);
  await page.goto("/console/app/projects/big?tab=settings");
  await page.getByTestId("share-export").click();
  const dlg = page.getByTestId("export-dialog");
  await dlg.getByTestId("export-submit").click();
  await expect(dlg.getByTestId("export-job-line")).toHaveText(/Building the ZIP: \d+ files, [\d.]+ MB so far…/);
  await expect(dlg.getByTestId("export-job")).toHaveAttribute("data-state", "done", { timeout: 10_000 });
  await expect(dlg.getByTestId("export-job-line")).toHaveText("The ZIP is ready.");
  await expect(dlg.getByTestId("export-download")).toHaveAttribute("href", "/v1/console/projects/-/exports/01JOBJOBJOBJOBJOBJOBJOBJOB/file");
});

test("a failed export job shows the server's sentence and no Download", async ({ page }) => {
  await stub(page, { jobFails: true });
  await page.goto("/console/app/projects/big?tab=settings");
  await page.getByTestId("share-export").click();
  const dlg = page.getByTestId("export-dialog");
  await dlg.getByTestId("export-submit").click();
  await expect(dlg.getByTestId("export-job")).toHaveAttribute("data-state", "error", { timeout: 10_000 });
  await expect(dlg.getByTestId("export-job-line")).toHaveText("media/notes.txt looks like it contains a secret. Exclude or clean it, then export again.");
  await expect(dlg.getByTestId("export-download")).toHaveCount(0);
});

test("import: the preview carries 'Unverified origin', the card, the default place; Import is off until a new id clears the conflicts", async ({ page }) => {
  const st = await stub(page);
  await openImport(page);
  const dlg = page.getByTestId("import-dialog");
  await expect(dlg.getByTestId("import-origin")).toHaveText("Unverified origin");
  await expect(dlg.getByTestId("import-from")).toContainText("From corvin-project-alpha-20261110.zip · exported 2026-11-10 by CorvinOS 1.9.0");
  await expect(dlg.getByTestId("import-will-create")).toContainText("212 files · 5.1 MB · knowledge base · git history");
  await expect(dlg.getByTestId("import-where-path")).toHaveText(`${MR}/alpha-2`);
  await expect(dlg.getByTestId("import-protection").getByTestId("class-badge")).toHaveAttribute("data-class", "internal");
  await expect(dlg.getByTestId("import-protection-text")).toHaveText("(the export says Public; imports start at Internal) · assistant: read only — change later in Settings");
  await expect(dlg.getByTestId("import-conflict")).toHaveCount(2);
  await expect(dlg.locator('[data-testid="import-conflict"][data-kind="id_taken"]')).toContainText(ID_TAKEN);
  await expect(dlg.locator('[data-testid="import-conflict"][data-kind="same_source"]')).toContainText(SAME);
  await expect(dlg.getByTestId("import-submit")).toBeDisabled();
  await dlg.getByTestId("import-fix-id").click();
  await expect(dlg.getByTestId("import-id")).toHaveValue("alpha-2");
  await expect(dlg.getByTestId("import-no-conflicts")).toBeVisible();
  await expect(dlg.getByTestId("import-submit")).toBeEnabled();
  expect(st.verdicts).toContain("alpha-2|");
  // typing an id again turns Import off until the verdict for that id is back
  await dlg.getByTestId("import-id").fill("alpha");
  await expect(dlg.getByTestId("import-submit")).toBeDisabled();
  await expect(dlg.getByTestId("import-conflict")).toHaveCount(2);
  await dlg.getByTestId("import-id").fill("alpha-two");
  await expect(dlg.getByTestId("import-submit")).toBeEnabled();
  await dlg.getByTestId("import-skip-history").check();
  await expect(dlg.getByTestId("import-will-create")).toHaveText(/212 files · 5\.1 MB · knowledge base$/);
  await dlg.getByTestId("import-submit").click();
  await expect(dlg.getByTestId("import-result")).toBeVisible();
  expect(st.applied).toEqual([{ project: "alpha", project_id: "alpha-two", parts: { git_history: false, kb: true } }]);
  await expect(dlg.getByTestId("import-result-warning")).toHaveText("Git history could not be imported");
  await expect(dlg.getByTestId("import-result-notes")).toContainText("git status shows it as deleted");
  await cleanText(page, "import-dialog");
  await dlg.getByTestId("import-close").click();
  await expect(page.locator('[data-testid="project-card"][data-project-id="alpha-two"]')).toBeVisible();
  expect(st.cancelled).toEqual([]);                                        // an applied staging is not "cancelled"
});

test("import: Change… opens the picker; a nested place shows the nested conflict and its fix opens the picker again", async ({ page }) => {
  const st = await stub(page);
  await openImport(page);
  const dlg = page.getByTestId("import-dialog");
  await dlg.getByTestId("import-fix-copy").click();
  await expect(dlg.getByTestId("import-no-conflicts")).toBeVisible();
  await dlg.getByTestId("import-change").click();
  await expect(page.getByTestId("folder-picker")).toBeVisible();
  await enterPath(page, "import-picker-input", `${HOME}/code/notes`);
  await page.getByTestId("picker-choose").click();
  await expect(dlg.getByTestId("import-where-path")).toHaveText(`${HOME}/code/notes`);
  await expect(dlg.locator('[data-testid="import-conflict"][data-kind="nested"]')).toContainText(NESTED);
  await expect(dlg.getByTestId("import-submit")).toBeDisabled();
  expect(st.verdicts).toContain(`alpha-2|${HOME}/code/notes`);
  await dlg.getByTestId("import-fix-place").click();
  await expect(page.getByTestId("folder-picker")).toBeVisible();
  await page.getByTestId("picker-cancel").click();
  await dlg.getByTestId("import-default-place").click();
  await expect(dlg.getByTestId("import-submit")).toBeEnabled();
});

test("import: Cancel removes the staged file and closes; a refused bundle shows the stage's sentence", async ({ page }) => {
  const st = await stub(page);
  await openImport(page);
  await page.getByTestId("import-dialog").getByTestId("import-cancel").click();
  await expect(page.getByTestId("import-dialog")).toHaveCount(0);
  await expect.poll(() => st.cancelled.length).toBe(1);
  expect(st.applied).toEqual([]);
});

test("import: a bundle the checks refuse says why and offers another file", async ({ page }) => {
  await stub(page, { reject: true });
  await page.goto("/console/app/projects");
  await page.getByTestId("import-start").click();
  await page.getByTestId("import-file").setInputFiles(BUNDLE);
  await expect(page.getByTestId("import-error")).toHaveText("The bundle's contents do not match its manifest.");
  await expect(page.getByTestId("import-choose")).toBeVisible();
});

test("duplicate: the next free id is suggested, the copy appears in the list", async ({ page }) => {
  const st = await stub(page);
  await page.goto("/console/app/projects/alpha?tab=settings");
  await page.getByTestId("share-duplicate").click();
  const dlg = page.getByTestId("duplicate-dialog");
  await expect(dlg.getByTestId("duplicate-id")).toHaveValue("alpha-2");
  // the copy is told apart by its title (integration test 2026-11-11 D3)
  await expect(dlg.getByTestId("duplicate-title")).toHaveValue("Alpha research (copy)");
  await expect(dlg.getByTestId("duplicate-where-path")).toHaveText("Corvin workspace › alpha-2");
  await dlg.getByTestId("duplicate-part-history").uncheck();
  // an empty title cannot be sent
  await dlg.getByTestId("duplicate-title").fill("");
  await expect(dlg.getByTestId("duplicate-submit")).toBeDisabled();
  await dlg.getByTestId("duplicate-title").fill("Alpha, second take");
  await dlg.getByTestId("duplicate-submit").click();
  await expect(dlg).toHaveAttribute("data-stage", "done");
  expect(st.duplicated).toEqual([{ from: "alpha", project_id: "alpha-2", title: "Alpha, second take", parts: { git_history: false, kb: true, generated: false } }]);
  await cleanText(page, "duplicate-dialog");
  await dlg.getByTestId("duplicate-close").click();
  await page.goto("/console/app/projects");
  await expect(page.locator('[data-testid="project-card"][data-project-id="alpha-2"]')).toBeVisible();
});

// REVIEW-0071 N5: a 118-character title used to pre-fill a 125-character default the server refused with 422
test("duplicate: a long title's default is clipped to the server's 120 characters and is sent as shown", async ({ page }) => {
  const st = await stub(page);
  st.list = [...st.list, PROJECTS.long];                                 // listed, so the next free id is "long-2"
  await page.goto("/console/app/projects/long?tab=settings");
  await page.getByTestId("share-duplicate").click();
  const dlg = page.getByTestId("duplicate-dialog");
  const expected = `Long ${"t".repeat(108)} (copy)`;
  expect([...expected].length).toBe(120);
  await expect(dlg.getByTestId("duplicate-title")).toHaveValue(expected);
  await expect(dlg.getByTestId("duplicate-id")).toHaveValue("long-2");
  await expect(dlg.getByTestId("duplicate-title-problem")).toHaveCount(0);
  await expect(dlg.getByTestId("duplicate-submit")).toBeEnabled();
  await dlg.getByTestId("duplicate-submit").click();
  await expect(dlg).toHaveAttribute("data-stage", "done");
  expect(st.duplicated).toHaveLength(1);
  expect(st.duplicated[0].title).toBe(expected);
});

test("multi-select: Export selected (2) exports one set; Corvin itself and broken projects have no checkbox", async ({ page }) => {
  const st = await stub(page);
  st.list = [{ id: "corvin", path: "/x", template: "builtin", state: "ok", builtin: true, title: "Corvin" }, ...st.list,
    { id: "gone", path: "/home/u/gone", template: "plain", state: "missing", reason: "the directory is missing" }];
  await page.goto("/console/app/projects");
  await expect(page.locator('[data-testid="project-card"][data-project-id="corvin"] [data-testid="project-select"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="project-card"][data-project-id="gone"] [data-testid="project-select"]')).toHaveCount(0);
  await page.locator('[data-testid="project-card"][data-project-id="alpha"] [data-testid="project-select"]').check();
  await page.locator('[data-testid="project-card"][data-project-id="beta"] [data-testid="project-select"]').check();
  await expect(page.getByTestId("export-selected")).toHaveText("Export selected (2)");
  await page.getByTestId("export-selected").click();
  const dlg = page.getByTestId("export-dialog");
  await expect(dlg.getByRole("heading")).toHaveText("Export 2 projects");
  await expect(dlg.getByTestId("export-project")).toHaveCount(2);
  await expect(dlg.getByTestId("export-submit")).toBeDisabled();             // beta is confidential
  await dlg.getByTestId("export-confirm").fill("beta");
  const dl = page.waitForEvent("download");
  await dlg.getByTestId("export-submit").click();
  expect((await dl).suggestedFilename()).toBe("corvin-projects-20261111.zip");
  expect(st.exports[0]).toMatchObject({ project_ids: ["alpha", "beta"], confirm: true });
  await dlg.getByTestId("export-cancel").click();
  await page.getByTestId("selection-clear").click();
  await expect(page.getByTestId("selection-bar")).toHaveCount(0);
});

for (const t of ["light", "dark"] as const) {
  for (const s of [{ name: "1280", width: 1280, height: 900 }, { name: "375", width: 375, height: 812 }]) {
    test(`share dialogs at ${s.name} px (${t})`, async ({ page }) => {
      await theme(page, t);
      await page.setViewportSize({ width: s.width, height: s.height });
      const st = await stub(page, { gpg: true });
      fs.mkdirSync(OUT, { recursive: true });
      const fits = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
      await page.goto("/console/app/projects/alpha?tab=settings");
      await expect(page.locator("html")).toHaveAttribute("data-theme", t);
      await page.getByTestId("share-section").scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(OUT, `share-section-${t}-${s.name}.png`), animations: "disabled" });
      await page.getByTestId("share-export").click();
      await expect(page.getByTestId("export-included")).toBeVisible();
      await fits();
      await page.screenshot({ path: path.join(OUT, `export-dialog-${t}-${s.name}.png`), animations: "disabled" });
      await page.keyboard.press("Escape");
      await page.goto("/console/app/projects/beta?tab=settings");
      await page.getByTestId("share-export").click();
      await expect(page.getByTestId("export-confirm")).toBeVisible();
      await page.screenshot({ path: path.join(OUT, `export-confidential-${t}-${s.name}.png`), animations: "disabled" });
      await page.keyboard.press("Escape");
      await page.getByTestId("share-duplicate").click();
      await expect(page.getByTestId("duplicate-id")).toHaveValue("beta-2");
      await page.screenshot({ path: path.join(OUT, `duplicate-dialog-${t}-${s.name}.png`), animations: "disabled" });
      await page.keyboard.press("Escape");
      // the import preview with two conflicts
      await openImport(page);
      await expect(page.getByTestId("import-conflict")).toHaveCount(2);
      await fits();
      await page.screenshot({ path: path.join(OUT, `import-conflicts-${t}-${s.name}.png`), animations: "disabled" });
      await page.getByTestId("import-fix-id").click();
      await expect(page.getByTestId("import-submit")).toBeEnabled();
      await page.getByTestId("import-submit").click();
      await expect(page.getByTestId("import-result")).toBeVisible();
      await page.screenshot({ path: path.join(OUT, `import-result-${t}-${s.name}.png`), animations: "disabled" });
      await page.getByTestId("import-close").click();
      await page.goto("/console/app/projects");
      await page.locator('[data-testid="project-card"][data-project-id="alpha"] [data-testid="project-select"]').check();
      await expect(page.getByTestId("selection-bar")).toBeVisible();
      await fits();
      await page.screenshot({ path: path.join(OUT, `list-selection-${t}-${s.name}.png`), animations: "disabled" });
      expect(st.applied.length).toBe(1);
    });
  }
}
