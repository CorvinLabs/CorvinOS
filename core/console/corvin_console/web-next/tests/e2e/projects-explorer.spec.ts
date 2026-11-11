/**
 * The explorer panel on the served bundle (CONCEPT-0128). A REAL chat session of the console under test; the project endpoints are stubbed;
 * EventSource is replaced by a controllable fake so every state is deterministic. The real stream, a real turn and the real restart
 * are proven in tests/e2e/projects-explorer-live.spec.ts and tests/e2e/test_projects_activity_e2e.py.
 */
import { test, expect, type Page } from "@playwright/test";

const ALPHA = { id: "alpha", path: "/home/u/projects/alpha", template: "x@1", state: "ok", title: "ALPHA", role: "software", classification: "internal", assistant_access: "write", active: ["core", "kb"], features: { core: true } };
const SNAP = (over: Record<string, unknown> = {}) => ({ epoch: "9", seq: 0, oldest_seq: 1, reset: false, gap: false, events: [], agents: [], watcher: "running", note: "", ...over });

async function aSid(page: Page): Promise<string> {
  const r = await page.request.get("/v1/console/chat/sessions");
  const sessions = (await r.json()).sessions as { sid: string }[];
  test.skip(sessions.length === 0, "needs at least one chat session on the console under test");
  return sessions[0].sid;
}

async function setup(page: Page, snap: Record<string, unknown> = SNAP(), opts: { snapStatus?: number } = {}) {
  const sid = await aSid(page);
  await page.addInitScript(() => {
    const w = window as unknown as Record<string, unknown>;
    w.__esUrls = [];
    class FakeES {
      static CLOSED = 2; static CONNECTING = 0; static OPEN = 1;
      readyState = 0; url: string; l: Record<string, ((e: MessageEvent) => void)[]> = {};
      constructor(url: string) {
        this.url = url;
        if (url.includes("/activity/stream")) { (w.__esUrls as string[]).push(url); w.__es = this; }          // the app's own settings stream is not ours
        setTimeout(() => this.emit("open", null), 20);
      }
      addEventListener(t: string, fn: (e: MessageEvent) => void) { (this.l[t] ||= []).push(fn); }
      emit(t: string, data: unknown) { if (t === "open") this.readyState = 1; (this.l[t] || []).forEach((f) => f({ data: data === null ? "" : JSON.stringify(data) } as MessageEvent)); }
      close() { this.readyState = 2; }
    }
    w.EventSource = FakeES;
    w.__push = (t: string, d: unknown) => (w.__es as FakeES).emit(t, d);
    w.__setState = (n: number) => { (w.__es as FakeES).readyState = n; };
  });
  await page.route(/\/v1\/console\/projects$/, (r) => r.fulfill({ json: { projects_root: "/p", projects: [ALPHA] } }));
  await page.route(/\/v1\/console\/projects\/chat-attachment/, (r) => r.fulfill({ json: { session_id: sid, project_id: "alpha", title: "ALPHA" } }));
  await page.route(/\/v1\/console\/projects\/alpha\/changes(\?|$)/, (r) => r.fulfill({ json: { project_id: "alpha", commit: null, files: [], commits: [], has_repository: true, truncated: false } }));
  await page.route(/\/v1\/console\/projects\/alpha\/activity(\?|$)/, (r) => opts.snapStatus ? r.fulfill({ status: opts.snapStatus, json: { detail: "no such project" } }) : r.fulfill({ json: snap }));
  await page.route(/\/v1\/console\/task-tracking\/items/, (r) => r.fulfill({ json: { items: [
    { id: "kb:1", title: "T-0012 Fix VAT rounding", status: "in_progress", labels: [], project: "alpha" }, { id: "kb:2", title: "T-0013 Later", status: "open", labels: [] }],
    server_time: new Date().toISOString(), summary: {}, import_available: false } }));
  await page.goto(`/console/app/chat/${sid}`);
}

const open = async (page: Page) => { await page.getByTestId("project-menu-button").click(); await page.getByTestId("project-menu-explorer").click(); await expect(page.getByTestId("explorer-panel")).toBeVisible(); };
const push = (page: Page, t: string, d: unknown) => page.evaluate(([a, b]) => (window as unknown as { __push: (t: string, d: unknown) => void }).__push(a as string, b), [t, d]);
const ev = (seq: number, kind: string, more: Record<string, unknown> = {}) => ({ seq, id: `9:${seq}`, ts: Date.now() / 1000, kind, session_id: "c1", ...more });

test("the menu gets one new entry and the explorer opens on the snapshot, then streams from its cursor", async ({ page }) => {
  await setup(page, SNAP({ seq: 4, events: [ev(4, "turn_finished", { reason: "done" })], agents: [{ session_id: "c1", title: "Plan", state: "idle", since: 1, tool: null, detail: "", tools: 2, finished: Date.now() / 1000 - 90, ok: true }] }));
  await page.getByTestId("project-menu-button").click();
  for (const id of ["project-menu-explorer", "project-menu-changes", "project-menu-storage", "project-menu-settings", "project-menu-all"]) await expect(page.getByTestId(id)).toBeVisible();
  await expect(page.getByTestId("project-option")).toHaveCount(2);                               // the multiple choice is untouched
  await page.getByTestId("project-menu-explorer").click();
  await expect(page.getByTestId("explorer-status")).toHaveAttribute("data-conn", "live");
  await expect(page.getByTestId("explorer-status")).toContainText("Live");
  const urls = await page.evaluate(() => (window as unknown as { __esUrls: string[] }).__esUrls);
  expect(urls).toHaveLength(1);
  expect(decodeURIComponent(urls[0])).toContain("after=9:4");                                  // the stream resumes where the snapshot ended
  await expect(page.locator('[data-testid="explorer-agent"][data-state="idle"]')).toContainText("finished");
  await expect(page.getByTestId("explorer-now-empty")).toBeVisible();
  await expect(page.getByTestId("explorer-tasks-empty")).toHaveCount(0);
  await expect(page.getByTestId("explorer-task")).toHaveText("T-0012 Fix VAT rounding");         // only the task that is in progress
});

test("a turn is followed live: working card, tool rows collapse, file rows link, finish, filters", async ({ page }) => {
  await setup(page);
  await open(page);
  await expect(page.getByTestId("explorer-empty")).toContainText("No agent has worked");
  await push(page, "activity", ev(1, "turn_started", { title: "Fix VAT" }));              // no snapshot: the event alone makes the card
  await expect(page.locator('[data-testid="explorer-agent"][data-state="working"]')).toContainText("Fix VAT");
  await expect(page.getByTestId("agent-tool")).toContainText("Thinking");
  await expect(page.getByTestId("agent-elapsed")).toContainText("Working for");
  await expect(page.getByTestId("explorer-announce")).toHaveText("Corvin started working in Fix VAT");
  for (let i = 2; i <= 4; i++) await push(page, "activity", ev(i, "tool_use", { name: "Read", detail: "invoice.py" }));
  await push(page, "activity", ev(5, "tool_use", { name: "Bash", detail: "pytest" }));
  await expect(page.getByTestId("agent-tool")).toContainText("Now: Bash pytest");
  await expect(page.getByTestId("agent-tool")).toContainText("4 tool calls");
  await push(page, "activity", ev(6, "file_changed", { path: "docs/kb/tasks/T-0012-vat.md", status: "modified", additions: 4, deletions: 1, area: "knowledge", links: [{ id: "T-0012", kind: "task" }] }));
  const texts = await page.getByTestId("explorer-row").evaluateAll((els) => els.map((e) => (e.textContent || "").replace(/\s+/g, " ").trim()));
  expect(texts[0]).toContain("Changed docs/kb/tasks/T-0012-vat.md +4 -1");
  expect(texts[1]).toContain("Ran pytest");
  expect(texts[2]).toContain("Read invoice.py");
  expect(texts[2]).toContain("×3");                                                            // three Reads, one row
  await expect(page.getByTestId("explorer-link")).toHaveAttribute("href", /\/app\/initiatives\?scope=project:alpha&q=T-0012$/);
  await page.locator('[data-testid="explorer-filter"][data-filter="files"]').click();
  await expect(page.getByTestId("explorer-row")).toHaveCount(1);
  await page.locator('[data-testid="explorer-filter"][data-filter="tools"]').click();
  await expect(page.getByTestId("explorer-row")).toHaveCount(3);                               // started + Read x3 (one row) + Bash
  await push(page, "activity", ev(7, "turn_finished", { reason: "done" }));
  await expect(page.locator('[data-testid="explorer-agent"][data-state="idle"]')).toContainText("finished");
  await expect(page.getByTestId("explorer-announce")).toHaveText("Corvin finished in Fix VAT");
});

test("a console restart clears the feed and says so; a gap is named", async ({ page }) => {
  await setup(page);
  await open(page);
  await push(page, "snapshot", SNAP({ seq: 2, events: [ev(1, "turn_started"), ev(2, "tool_use", { name: "Read", detail: "a.py" })] }));
  await expect(page.getByTestId("explorer-row")).toHaveCount(2);
  await push(page, "snapshot", SNAP({ epoch: "10", seq: 1, events: [{ ...ev(1, "turn_started"), id: "10:1" }] }));
  await expect(page.getByTestId("explorer-restarted")).toContainText("The console restarted");
  await expect(page.getByTestId("explorer-row")).toHaveCount(1);
  await push(page, "snapshot", SNAP({ epoch: "10", seq: 1, gap: true }));
  await expect(page.getByTestId("explorer-gap")).toContainText("Some events were missed");
});

test("failures are said in words: a refused stream, a removed project, a paused watcher, a slow reconnect - but a normal rollover is silent", async ({ page }) => {
  await setup(page, SNAP({ watcher: "paused" }));
  await open(page);
  await expect(page.getByTestId("explorer-status")).toContainText("file watching paused");
  // the server ends a stream every 10 s and the browser reopens it within 2 s: nothing to see
  await page.evaluate(() => { (window as unknown as { __setState: (n: number) => void }).__setState(0); });
  await push(page, "error", null);
  await page.waitForTimeout(1200);
  await push(page, "open", null);
  await page.waitForTimeout(300);
  await expect(page.getByTestId("explorer-status")).toHaveAttribute("data-conn", "live");
  // a reconnect that does not come back is named after a grace period
  await push(page, "error", null);
  await expect(page.getByTestId("explorer-status")).toHaveAttribute("data-conn", "reconnecting", { timeout: 9000 });
  // a refusal closes the EventSource for good (no automatic retry)
  await page.evaluate(() => { (window as unknown as { __setState: (n: number) => void }).__setState(2); });
  await push(page, "error", null);
  await expect(page.getByTestId("explorer-status")).toContainText("Could not connect");
  await expect(page.getByTestId("explorer-empty")).toContainText("connection is down");
});

test("a project that is gone says so instead of an empty feed", async ({ page }) => {
  await setup(page, SNAP(), { snapStatus: 404 });
  await open(page);
  await expect(page.getByTestId("explorer-status")).toContainText("removed from the list");
  expect(await page.evaluate(() => (window as unknown as { __esUrls: string[] }).__esUrls.length)).toBe(0);   // no stream is opened for a missing project
});

test("the live dot on the menu button follows the snapshot only: it shows an agent in any chat and never opens a stream", async ({ page }) => {
  await setup(page, SNAP({ agents: [{ session_id: "other-chat", title: "Elsewhere", state: "working", since: Date.now() / 1000, tool: "Edit", detail: "a.py", tools: 1 }] }));
  await expect(page.getByTestId("project-live-dot")).toBeVisible({ timeout: 12000 });
  await expect(page.getByTestId("project-live-dot")).toHaveAttribute("aria-label", "An agent is working on this project");
  expect(await page.evaluate(() => (window as unknown as { __esUrls: string[] }).__esUrls.length)).toBe(0);
});

test("Escape closes the explorer and the Changes button swaps to the Changes panel", async ({ page }) => {
  await setup(page);
  await open(page);
  await page.getByTestId("explorer-open-changes").click();
  await expect(page.getByTestId("explorer-panel")).toHaveCount(0);
  await expect(page.getByTestId("changes-panel")).toBeVisible();
  await page.keyboard.press("Escape");
  await open(page);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("explorer-panel")).toHaveCount(0);
});

test("a project without a knowledge base says it has no task board instead of a read error", async ({ page }) => {
  await setup(page);
  await page.route(/\/v1\/console\/task-tracking\/items/, (r) => r.fulfill({ status: 404, json: { detail: "no such project" } }));
  await open(page);
  await expect(page.getByTestId("explorer-tasks-none")).toContainText("no task board yet");
  // the switch is in Features, and the sentence leads there (REVIEW-0067 M2)
  await expect(page.getByTestId("explorer-tasks-features")).toHaveAttribute("href", /\/app\/projects\/alpha\?tab=features$/);
  await expect(page.getByTestId("explorer-tasks-error")).toHaveCount(0);
  await expect(page.getByTestId("explorer-tasks-empty")).toHaveCount(0);
});

test("a board that fails for another reason says so plainly", async ({ page }) => {
  await setup(page);
  await page.route(/\/v1\/console\/task-tracking\/items/, (r) => r.fulfill({ status: 500, json: { detail: "boom" } }));
  await open(page);
  await expect(page.getByTestId("explorer-tasks-error")).toContainText("could not be read just now");
  await expect(page.getByTestId("explorer-tasks-none")).toHaveCount(0);
});

test("Tab stays inside the explorer: it is a modal panel (REVIEW-0067 #15)", async ({ page }) => {
  await setup(page);
  await open(page);
  await expect(page.getByTestId("explorer-panel")).toHaveAttribute("aria-modal", "true");
  let outside = 0;
  for (let i = 0; i < 30; i++) {
    await page.keyboard.press(i % 7 === 6 ? "Shift+Tab" : "Tab");
    const inPanel = await page.evaluate(() => !!document.activeElement?.closest('[data-testid="explorer-panel"]'));
    if (!inPanel) outside += 1;
  }
  expect(outside).toBe(0);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("explorer-panel")).toHaveCount(0);
});

test("Tab stays inside the Changes panel too", async ({ page }) => {
  await setup(page);
  await page.getByTestId("project-menu-button").click();
  await page.getByTestId("project-menu-changes").click();
  await expect(page.getByTestId("changes-panel")).toHaveAttribute("aria-modal", "true");
  let outside = 0;
  for (let i = 0; i < 20; i++) {
    await page.keyboard.press("Tab");
    if (!(await page.evaluate(() => !!document.activeElement?.closest('[data-testid="changes-panel"]')))) outside += 1;
  }
  expect(outside).toBe(0);
});
