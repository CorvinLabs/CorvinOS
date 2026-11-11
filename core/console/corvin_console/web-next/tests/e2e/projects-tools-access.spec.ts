/**
 * REVIEW-0069 N1 on the served bundle: a feature that brings tools (Agentic compute) gives them to an attached chat only when the
 * assistant may edit files. The Features row, "Use in a chat" and the chat's project menu say so, with a link to the setting. The
 * project endpoints are stubbed at the network boundary; the server's half (feature `tools`, attachment `tools_withheld`, no MCP
 * config for a read-only project) is proven over real HTTP by tests/e2e/test_projects_review_0066_0068_attach_e2e.py.
 *
 * Screenshots: FIXD_SHOTS (default /tmp/fixd-shots), 1280 and 375 px in both themes.
 */
import fs from "node:fs";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";

const OUT = process.env.FIXD_SHOTS ?? "/tmp/fixd-shots";
const ID = "alpha";
const ROW = (id: string, title: string, tools: boolean) => ({ id, title, description: "", active: true, requires: [], suggests: [], dependants: [], plugins: [], tools });
const DETAIL = (access: "read" | "write") => ({
  id: ID, path: "/home/u/projects/alpha", template: "research@1", state: "ok", title: "Alpha research", docs_mode: "plain", kb: false,
  active: ["core", "acs"], features: { core: true, acs: true }, directory_peers: [], role: "research", classification: "internal",
  assistant_access: access, storage: "own", display_path: "~/projects/alpha", location_warnings: [],
  catalogue: { available: true, features: [ROW("core", "Project", false), ROW("acs", "Agentic compute", true)] },
});
const HEALTH = { project_id: ID, ok: true, checks: [{ id: "path_exists", ok: true, detail: "the project directory exists", at: "" }] };

async function stub(page: Page, access: "read" | "write") {
  await page.route(/\/v1\/console\/projects\/templates/, (r) => r.fulfill({ json: { available: true, templates: [], features: [] } }));
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: { projects_root: "/home/u/projects", projects: [{ ...DETAIL(access), catalogue: undefined }], workspace: "ok" } }));
  await page.route(new RegExp(`/v1/console/projects/${ID}$`), (r) => r.fulfill({ json: DETAIL(access) }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/health$`), (r) => r.fulfill({ json: HEALTH }));
}

async function theme(page: Page, t: "light" | "dark") {
  await page.addInitScript((v) => { try { localStorage.setItem("corvin-theme", v); localStorage.setItem("corvin-dark-default-v2", "1"); } catch { /* ignore */ } }, t);
}

test("read only: the Agentic compute row says its tools do not reach a chat and links to the setting", async ({ page }) => {
  await stub(page, "read");
  await page.route(/\/v1\/console\/chat\/sessions$/, (r) => r.fulfill({ json: { tenant_id: "_default", count: 0, sessions: [] } }));
  await page.goto(`/console/app/projects/${ID}?tab=features`);
  const acs = page.locator('[data-testid="feature-row"][data-feature="acs"]');
  await expect(acs.getByTestId("feature-tools-note")).toHaveText(
    "On, but its tools are not given to a chat. Its tools reach a chat only when the assistant may edit files. Change what the assistant may do");
  await expect(page.locator('[data-testid="feature-row"][data-feature="core"]').getByTestId("feature-tools-note")).toHaveCount(0);
  await acs.getByTestId("feature-tools-link").click();
  await expect(page).toHaveURL(/tab=settings#settings-protection$/);
  await expect(page.locator("#settings-protection")).toBeInViewport();
  await expect(page.getByTestId("attach-tools-note")).toContainText(
    "The tools of Agentic compute are not given to a chat while the assistant reads only. Its tools reach a chat only when the assistant may edit files.");
  await expect(page.getByTestId("attach-explain")).not.toContainText("use its tools");
});

test("can edit: no tools note anywhere, and Use in a chat promises the tools", async ({ page }) => {
  await stub(page, "write");
  await page.route(/\/v1\/console\/chat\/sessions$/, (r) => r.fulfill({ json: { tenant_id: "_default", count: 0, sessions: [] } }));
  await page.goto(`/console/app/projects/${ID}?tab=features`);
  await expect(page.locator('[data-testid="feature-row"][data-feature="acs"]')).toBeVisible();
  await expect(page.getByTestId("feature-tools-note")).toHaveCount(0);
  await page.goto(`/console/app/projects/${ID}?tab=settings`);
  await expect(page.getByTestId("attach-explain")).toContainText("the chat can follow the project's rules and use its tools.");
  await expect(page.getByTestId("attach-tools-note")).toHaveCount(0);
});

async function aSid(page: Page): Promise<string> {
  const r = await page.request.get("/v1/console/chat/sessions");
  expect(r.ok()).toBeTruthy();
  const sessions = (await r.json()).sessions as { sid: string }[];
  test.skip(sessions.length === 0, "needs at least one chat session on the console under test");
  return sessions[0].sid;
}

for (const withheld of [true, false]) {
  test(`the chat's project menu ${withheld ? "says" : "does not say"} that the tools are withheld`, async ({ page }) => {
    const sid = await aSid(page);
    await stub(page, withheld ? "read" : "write");
    await page.route(/\/v1\/console\/projects\/chat-attachment/, (r) => r.fulfill({ json: { session_id: sid, project_id: ID, title: "Alpha research", withheld: null, tools_withheld: withheld } }));
    await page.route(new RegExp(`/v1/console/projects/${ID}/(changes|activity)`), (r) => r.fulfill({ json: { project_id: ID, files: [], commits: [], agents: [], has_repository: true, truncated: false, commit: null } }));
    await page.goto(`/console/app/chat/${sid}`);
    await page.getByTestId("project-menu-button").click();
    if (!withheld) { await expect(page.getByTestId("project-menu-panel")).toBeVisible(); await expect(page.getByTestId("project-menu-tools")).toHaveCount(0); return; }
    await expect(page.getByTestId("project-menu-tools")).toHaveText(
      "This project's tools are not given to this chat. They reach a chat only when the assistant may edit files. Change what the assistant may do");
    await page.getByTestId("project-menu-tools-link").click();
    await expect(page).toHaveURL(new RegExp(`/app/projects/${ID}\\?tab=settings#settings-protection$`));
    await expect(page.locator("#settings-protection")).toBeInViewport();
  });
}

for (const t of ["light", "dark"] as const) {
  for (const w of [1280, 375]) {
    test(`screenshots ${t} ${w}`, async ({ page }) => {
      fs.mkdirSync(OUT, { recursive: true });
      await theme(page, t);
      await page.setViewportSize({ width: w, height: 900 });
      await stub(page, "read");
      await page.route(/\/v1\/console\/chat\/sessions$/, (r) => r.fulfill({ json: { tenant_id: "_default", count: 1, sessions: [{ sid: "s1", title: "Planning" }] } }));
      await page.goto(`/console/app/projects/${ID}?tab=features`);
      await expect(page.getByTestId("feature-tools-note")).toBeVisible();
      await page.screenshot({ path: path.join(OUT, `features-read-${t}-${w}.png`), fullPage: true });
      await page.goto(`/console/app/projects/${ID}?tab=settings#settings-chat`);
      await expect(page.getByTestId("attach-tools-note")).toBeVisible();
      await page.getByTestId("attach-section").scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(OUT, `use-in-chat-read-${t}-${w}.png`) });
      const text = await page.getByTestId("project-inspector").innerText();
      expect(text).not.toMatch(/ADR-\d|CONCEPT-\d|REVIEW-\d|T-0\d{3}/);
    });
  }
}

test("screenshots of the chat menu", async ({ page }) => {
  const sid = await aSid(page);
  fs.mkdirSync(OUT, { recursive: true });
  await stub(page, "read");
  await page.route(/\/v1\/console\/projects\/chat-attachment/, (r) => r.fulfill({ json: { session_id: sid, project_id: ID, title: "Alpha research", withheld: null, tools_withheld: true } }));
  await page.route(new RegExp(`/v1/console/projects/${ID}/(changes|activity)`), (r) => r.fulfill({ json: { project_id: ID, files: [], commits: [], agents: [], has_repository: true, truncated: false, commit: null } }));
  for (const t of ["light", "dark"] as const) {
    for (const w of [1280, 375]) {
      await theme(page, t);                                  // the last init script wins on the next load
      await page.setViewportSize({ width: w, height: 900 });
      await page.goto(`/console/app/chat/${sid}`);
      await page.getByTestId("project-menu-button").click();
      await expect(page.getByTestId("project-menu-tools")).toBeVisible();
      await page.screenshot({ path: path.join(OUT, `chat-menu-read-${t}-${w}.png`) });
    }
  }
});
