/**
 * The project menu and the change view in a chat's header (CONCEPT-0127 D5-D7), on the served bundle with a REAL chat session of the
 * console under test. The project endpoints are stubbed at the network boundary; the menu, the multiple choice, the panel, the diff and the
 * links are the real ones. Server guarantees: tests/e2e/test_projects_roles_e2e.py and test_projects_attach_e2e.py (real HTTP, real git).
 */
import { test, expect, type Page } from "@playwright/test";

const P = (id: string, role: string, cls: string, acc: string) => ({ id, path: `/home/u/projects/${id}`, template: "x@1", state: "ok", title: id.toUpperCase(),
  role, classification: cls, assistant_access: acc, active: ["core", "kb"], features: { core: true } });
const LIST = { projects_root: "/home/u/projects", projects: [P("alpha", "software", "internal", "write"), P("beta", "legal", "confidential", "read")] };
const F = (path: string, area: string, status: string, a: number | null, d: number | null, links: { id: string; kind: string }[] = []) => ({ path, area, status, additions: a, deletions: d, links });
const CHANGES = { project_id: "alpha", commit: null, has_repository: true, truncated: false, files: [
  F("docs/kb/tasks/T-0001-first.md", "knowledge", "untracked", 5, 0, [{ id: "T-0001", kind: "task" }]),
  F("src/app.py", "code", "modified", 3, 1), F("docs/notes/plan.md", "docs", "untracked", 2, 0), F("logo.png", "code", "modified", null, null)],
  commits: [{ sha: "a".repeat(40), short: "aaaaaaaaa", repo: "project", author: "kb", at: 1, subject: "close T-0002", links: [{ id: "T-0002", kind: "task" }] }] };

async function aSid(page: Page): Promise<string> {
  const r = await page.request.get("/v1/console/chat/sessions");
  expect(r.ok()).toBeTruthy();
  const sessions = (await r.json()).sessions as { sid: string }[];
  test.skip(sessions.length === 0, "needs at least one chat session on the console under test");
  return sessions[0].sid;
}

async function stub(page: Page, sid: string, opts: { attached?: string | null } = {}) {
  const state = { attached: opts.attached ?? null, bodies: [] as unknown[], commitAsked: [] as string[] };
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: LIST }));
  await page.route(/\/v1\/console\/projects\/chat-attachment/, (r) => r.fulfill({ json: { session_id: sid, project_id: state.attached, title: state.attached ? state.attached.toUpperCase() : null } }));
  await page.route(/\/v1\/console\/projects\/[a-z-]+\/attach$/, (r) => { const m = /projects\/([a-z-]+)\/attach/.exec(r.request().url()); state.bodies.push(r.request().postDataJSON()); state.attached = m![1]; return r.fulfill({ json: { attached: true, note: "ok" } }); });
  await page.route(/\/v1\/console\/projects\/detach$/, (r) => { state.attached = null; return r.fulfill({ json: { attached: false } }); });
  await page.route(/\/v1\/console\/projects\/alpha\/changes\/file/, (r) => {
    const u = new URL(r.request().url()); const path = u.searchParams.get("path");
    if (path === "docs/notes/plan.md") return r.fulfill({ json: { path, status: "untracked", area: "docs", binary: false, truncated: false, diff: "", text: "# Plan\nline two" } });
    if (path === "logo.png") return r.fulfill({ json: { path, status: "modified", area: "code", binary: true, truncated: false, diff: "", text: null } });
    return r.fulfill({ json: { path, status: "modified", area: "code", binary: false, truncated: false,
      diff: "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n@@ -1,2 +1,2 @@\n keep\n-old line\n+new line\n", text: null } });
  });
  await page.route(/\/v1\/console\/projects\/alpha\/changes(\?|$)/, (r) => {
    const u = new URL(r.request().url()); const c = u.searchParams.get("commit"); if (c) state.commitAsked.push(c);
    return r.fulfill({ json: c ? { ...CHANGES, commit: c, files: [F("src/old.py", "code", "committed", 9, 2)] } : CHANGES });
  });
  return state;
}

test("the project button lists the projects as multiple choice with class and access, and one click attaches", async ({ page }) => {
  const sid = await aSid(page);
  const st = await stub(page, sid);
  await page.goto(`/console/app/chat/${sid}`);
  const btn = page.getByTestId("project-menu-button");
  await expect(btn).toHaveAttribute("data-attached", "no");
  await expect(btn).toContainText("Project");
  await btn.click();
  const opts = page.getByTestId("project-option");
  await expect(opts).toHaveCount(3);                                                    // No project + two
  await expect(page.locator('[data-testid="project-option"][data-project=""]')).toBeChecked();
  await expect(page.getByTestId("project-menu-panel").getByTestId("class-badge").nth(1)).toHaveAttribute("data-class", "confidential");
  await page.locator('[data-testid="project-option"][data-project="alpha"]').click();
  await expect.poll(() => st.attached).toBe("alpha");
  expect(st.bodies[0]).toMatchObject({ session_id: sid });
  await expect(page.getByTestId("project-menu-panel")).toHaveCount(0);                  // one click, the menu closes
  await expect(btn).toHaveAttribute("data-attached", "yes");
  await expect(page.getByTestId("project-menu-label")).toHaveText("ALPHA");
  await expect(page.getByTestId("project-menu-count")).toHaveText("4");
});

test("a refusal by the data-flow gate is said in words and the choice does not change", async ({ page }) => {
  const sid = await aSid(page);
  await stub(page, sid);
  await page.route(/\/v1\/console\/projects\/beta\/attach$/, (r) => r.fulfill({ status: 409, json: { detail: { message: "[data-flow] Spawn rejected: classification CONFIDENTIAL is not allowed for engine claude_code", reason_class: "classification" } } }));
  await page.goto(`/console/app/chat/${sid}`);
  await page.getByTestId("project-menu-button").click();
  await page.locator('[data-testid="project-option"][data-project="beta"]').click();
  await expect(page.getByTestId("project-menu-error")).toContainText("not allowed for engine");
  await expect(page.getByTestId("project-menu-panel")).toBeVisible();                   // still open: the user sees why
  await expect(page.getByTestId("project-menu-button")).toHaveAttribute("data-attached", "no");
});

test("the change view groups files, opens a diff and a new file, links ids, switches to a commit and closes with Escape", async ({ page }) => {
  const sid = await aSid(page);
  const st = await stub(page, sid, { attached: "alpha" });
  await page.goto(`/console/app/chat/${sid}`);
  await page.getByTestId("project-menu-button").click();
  await expect(page.getByTestId("project-menu-settings")).toHaveAttribute("href", /\/app\/projects\/alpha\?tab=settings/);
  await expect(page.getByTestId("project-menu-storage")).toHaveAttribute("href", /\/app\/projects\/alpha\?tab=structure/);
  await expect(page.getByTestId("project-menu-storage")).toHaveText("Structure");   // one name for one place: the tab it opens (REVIEW-0067 #14)
  await page.getByTestId("project-menu-changes").click();
  const panel = page.getByTestId("changes-panel");
  await expect(panel).toBeVisible();
  await expect(page.getByTestId("changes-summary")).toHaveText("4 files, +10 -1");
  const areas = await page.getByTestId("changes-group").evaluateAll((els) => els.map((e) => (e as HTMLElement).dataset.area));
  expect(areas).toEqual(["code", "docs", "knowledge"]);
  await expect(panel.locator('[data-testid="change-link"][data-id="T-0001"]')).toHaveAttribute("href", /\/app\/initiatives\?scope=project:alpha/);
  // a modified file: the diff keeps +/- and marks lines by kind
  await panel.locator('[data-testid="change-row"][data-path="src/app.py"]').getByTestId("change-open").click();
  await expect(page.locator('[data-testid="change-diff"] [data-kind="add"]')).toHaveText("+new line");
  await expect(page.locator('[data-testid="change-diff"] [data-kind="del"]')).toHaveText("-old line");
  await page.getByTestId("changes-back").click();
  // a new file shows its text; a binary says so
  await panel.locator('[data-testid="change-row"][data-path="docs/notes/plan.md"]').getByTestId("change-open").click();
  await expect(page.getByTestId("change-diff")).toContainText("# Plan");
  await page.getByTestId("changes-back").click();
  await panel.locator('[data-testid="change-row"][data-path="logo.png"]').getByTestId("change-open").click();
  await expect(page.getByTestId("change-file-binary")).toBeVisible();
  await page.getByTestId("changes-back").click();
  // an earlier commit
  await page.getByTestId("changes-source").selectOption("a".repeat(40));
  await expect(page.getByTestId("changes-summary")).toContainText("Commit aaaaaaaaa");
  expect(st.commitAsked).toContain("a".repeat(40));
  await expect(page.locator('[data-testid="change-row"]')).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
});

test("a folder without git and a project with no changes each say why the list is empty", async ({ page }) => {
  const sid = await aSid(page);
  await stub(page, sid, { attached: "alpha" });
  await page.route(/\/v1\/console\/projects\/alpha\/changes(\?|$)/, (r) => r.fulfill({ json: { ...CHANGES, files: [], has_repository: false, commits: [] } }));
  await page.goto(`/console/app/chat/${sid}`);
  await page.getByTestId("project-menu-button").click();
  await page.getByTestId("project-menu-changes").click();
  await expect(page.getByTestId("changes-empty")).toContainText("not a git repository");
  await page.route(/\/v1\/console\/projects\/alpha\/changes(\?|$)/, (r) => r.fulfill({ json: { ...CHANGES, files: [], has_repository: true, commits: [] } }));
  await page.getByTestId("changes-close").click();
  await page.getByTestId("project-menu-button").click();
  await page.getByTestId("project-menu-changes").click();
  await expect(page.getByTestId("changes-empty")).toContainText("appear here");
});

test("an attachment the data-flow gate withholds is shown as withheld, in words and with its own icon", async ({ page }) => {
  const sid = await aSid(page);
  await stub(page, sid, { attached: "beta" });
  await page.route(/\/v1\/console\/projects\/chat-attachment/, (r) => r.fulfill({ json: { session_id: sid, project_id: "beta", title: "BETA",
    withheld: "[data-flow] classification SECRET is not allowed for engine claude_code" } }));
  await page.goto(`/console/app/chat/${sid}`);
  await expect(page.getByTestId("project-menu-withheld-icon")).toBeVisible();
  await page.getByTestId("project-menu-button").click();
  await expect(page.getByTestId("project-menu-withheld")).toContainText("not sent to the assistant right now");
  await expect(page.getByTestId("project-menu-withheld")).toContainText("SECRET");
});

test("each project in the menu carries its place badge next to class and access (T-0388)", async ({ page }) => {
  const sid = await aSid(page);
  await stub(page, sid);
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: { ...LIST, projects: [
    { ...P("alpha", "software", "internal", "write"), storage: "managed", display_path: "Corvin workspace › alpha" },
    { ...P("beta", "legal", "confidential", "read"), storage: "own", display_path: "~/code/beta" }] } }));
  await page.goto(`/console/app/chat/${sid}`);
  await page.getByTestId("project-menu-button").click();
  const panel = page.getByTestId("project-menu-panel");
  await expect(panel.getByTestId("storage-badge")).toHaveText(["Corvin workspace", "Own folder"]);
  await expect(panel.getByTestId("storage-badge").first()).toHaveAttribute("title", "Corvin workspace › alpha");
});
