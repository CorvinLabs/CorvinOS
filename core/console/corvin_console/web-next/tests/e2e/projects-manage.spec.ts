/**
 * Adopt, rename and move on the served bundle (T-0362). The project endpoints are stubbed at the
 * network boundary; the forms, their guards and the request bodies they send are the real ones.
 * The server-side guarantees (one file written, only the title changes, nothing overwritten) are
 * proven by tests/e2e/test_projects_ops_e2e.py over real HTTP.
 */
import { test, expect, type Page } from "@playwright/test";
import { enterPath, stubPicker } from "./projects-picker-stub";

const ID = "plumbline";
const DETAIL = {
  id: ID, path: "/home/u/projects/plumbline", template: "video-engineering@1", state: "ok", title: "Plumbline explainer",
  docs_mode: "nested", kb: true, active: ["core", "kb"], features: {}, directory_peers: [],
  catalogue: { available: true, features: [{ id: "core", title: "Project", description: "", active: true, requires: [], suggests: [], dependants: [], plugins: [] }] },
};

async function base(page: Page) {
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: { available: true, templates: [], features: [] } }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: { projects_root: "/home/u/projects", projects: [] } }));
  await page.route(new RegExp(`/v1/console/projects/${ID}$`), (r) => r.fulfill({ json: DETAIL }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/health$`), (r) => r.fulfill({ json: { project_id: ID, ok: true, checks: [] } }));
  await stubPicker(page);
}

test("Add existing folder sends the folder chosen in the picker and the optional title, and is disabled while none is chosen", async ({ page }) => {
  await base(page);
  let body: { location?: string; title?: string } | null = null;
  await page.route(/\/v1\/console\/projects\/adopt$/, (r) => { body = r.request().postDataJSON(); return r.fulfill({ status: 201, json: { ...DETAIL, id: "lie-copy" } }); });
  await page.goto("/console/app/projects");
  await page.getByTestId("adopt-start").click();
  await expect(page.getByTestId("adopt-submit")).toBeDisabled();
  await expect(page.getByTestId("adopt-location")).toHaveCount(0);              // no bare path field: the picker chooses
  await page.getByTestId("adopt-folder-choose").click();
  await enterPath(page, "adopt-location", "/home/u/projects/lie-copy");
  await page.getByTestId("picker-choose").click();
  await page.getByTestId("adopt-title").fill("Lie detector");
  await page.getByTestId("adopt-submit").click();
  await expect.poll(() => body?.location).toBe("/home/u/projects/lie-copy");
  expect(body?.title).toBe("Lie detector");
  await expect(page.getByTestId("adopt-note")).toContainText("lie-copy");
});

test("an adopt the server refuses on confirm (the folder changed after the verdict) shows the server's reason", async ({ page }) => {
  await base(page);
  await page.route(/\/v1\/console\/projects\/adopt$/, (r) => r.fulfill({ status: 409, json: { detail: { message: "This folder is inside another project, or contains one.", reason_class: "nested" } } }));
  await page.goto("/console/app/projects");
  await page.getByTestId("adopt-start").click();
  await page.getByTestId("adopt-folder-choose").click();
  await enterPath(page, "adopt-location", "/home/u/code/notes");
  await page.getByTestId("picker-choose").click();
  await page.getByTestId("adopt-submit").click();
  await expect(page.getByTestId("adopt-note")).toContainText("inside another project");
});

test("Rename sends only the title; Move needs a second confirming click and then sends the location", async ({ page }) => {
  await base(page);
  let renamed: { title?: string } | null = null;
  let moved: { location?: string } | null = null;
  await page.route(new RegExp(`/v1/console/projects/${ID}/title$`), (r) => { renamed = r.request().postDataJSON(); return r.fulfill({ json: { ...DETAIL, title: "A better name" } }); });
  await page.route(new RegExp(`/v1/console/projects/${ID}/move$`), (r) => { moved = r.request().postDataJSON(); return r.fulfill({ json: { ...DETAIL, path: "/home/u/projects/new", repaired_files: 2 } }); });
  await page.goto(`/console/app/projects/${ID}?tab=settings`);
  await expect(page.getByTestId("rename-submit")).toBeDisabled();             // unchanged title
  await page.getByTestId("rename-input").fill("A better name");
  await page.getByTestId("rename-submit").click();
  await expect.poll(() => renamed?.title).toBe("A better name");
  await expect(page.getByTestId("manage-note")).toContainText("Only project.yaml changed");

  await expect(page.getByTestId("move-input")).toHaveCount(0);                 // no bare path field: "Move…" opens the picker
  await page.getByTestId("move-open").click();
  await expect(page.getByTestId("picker-title")).toHaveText('Choose a folder for "Plumbline explainer"');
  await enterPath(page, "move-input", "/home/u/projects/new");
  await expect(page.getByTestId("picker-verdict-text")).toHaveText("Can be used — a new folder will be created when you continue.");
  await page.getByTestId("picker-choose").click();
  await expect(page.getByTestId("move-dest")).toHaveText("/home/u/projects/new");
  await page.getByTestId("move-start").click();
  expect(moved).toBeNull();                                                    // not sent before the confirmation
  await page.getByTestId("move-confirm").click();
  await expect.poll(() => moved?.location).toBe("/home/u/projects/new");
  await expect(page.getByTestId("move-note")).toContainText("2 machine-local file(s) regenerated");
});

test("Use in a chat: attach sends the chosen chat, detach appears once the chat is attached to this project", async ({ page }) => {
  await base(page);
  await page.route(/\/v1\/console\/chat\/sessions$/, (r) => r.fulfill({ json: { tenant_id: "_default", count: 1, sessions: [
    { sid: "chat1", chat_key: "k", title: "Video script chat", created_at: 1, last_active_at: 2, turn_count: 3, workdir: "/w" }] } }));
  let attachedTo: string | null = null;
  let body: { session_id?: string } | null = null;
  await page.route(/\/v1\/console\/projects\/chat-attachment/, (r) => r.fulfill({ json: { session_id: "chat1", project_id: attachedTo, title: attachedTo } }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/attach$`), (r) => { body = r.request().postDataJSON(); attachedTo = ID; return r.fulfill({ json: { attached: true, note: "Applies from the chat's next message." } }); });
  await page.route(/\/v1\/console\/projects\/detach$/, (r) => { attachedTo = null; return r.fulfill({ json: { attached: false } }); });
  await page.goto(`/console/app/projects/${ID}?tab=settings`);
  await expect(page.getByTestId("attach-submit")).toBeDisabled();             // no chat chosen
  await page.getByTestId("attach-chat").selectOption("chat1");
  await page.getByTestId("attach-submit").click();
  await expect.poll(() => body?.session_id).toBe("chat1");
  await expect(page.getByTestId("attach-note-chat")).toContainText("next message");
  await expect(page.getByTestId("detach-submit")).toBeVisible();
  await page.getByTestId("detach-submit").click();
  await expect(page.getByTestId("attach-submit")).toBeVisible();
});

test("Use in a chat with no chats says how to get one", async ({ page }) => {
  await base(page);
  await page.route(/\/v1\/console\/chat\/sessions$/, (r) => r.fulfill({ json: { tenant_id: "_default", count: 0, sessions: [] } }));
  await page.goto(`/console/app/projects/${ID}?tab=settings`);
  await expect(page.getByTestId("attach-no-chats")).toContainText("Start one in the console");
});
