/**
 * E2E: Agent conversations as a Marketplace plugin panel (agent_conversations plugin, CONCEPT-0001).
 *
 * REAL against the live console: the plugin is installed + enabled through the real marketplace
 * route, its entry appears in the sidebar's Marketplace group (a manifest entry — the static panel
 * is gone), the page opens, and the New-conversation dialog loads its settings form.
 *
 * MOCKED (stated, not hidden): the live install has no registered agent and no paired peer, so a
 * running conversation cannot be produced here. The thread/composer behaviour is therefore driven
 * against a stubbed /federation/conversations API in the REAL SPA, and asserts the exact requests
 * the browser sends. The server side of the same requests (single writer, prompt contents, audit,
 * refusals) is proven against real signed peer turns in tests/federation/
 * test_agent_conversation_operator_e2e.py.
 */
import { test, expect, type BrowserContext, type Page, type Route } from "@playwright/test";

const ORIGIN = (process.env.CONSOLE_BASE_URL || "http://127.0.0.1:8765/console").replace(/\/console\/?$/, "");
const BASE_URL = `${ORIGIN}/console`;
const PLUGIN_ID = "plugin:contributor-integration-agent_conversations";
const CID = "a".repeat(32);

test.describe.configure({ mode: "serial" });

let ctx: BrowserContext;
let page: Page;
const errors: string[] = [];

test.beforeAll(async ({ browser }) => {
  ctx = await browser.newContext({ storageState: "./tests/e2e/auth-state.json", viewport: { width: 1500, height: 950 } });
  page = await ctx.newPage();
  page.on("pageerror", (e) => { if (!/ResizeObserver loop|ChunkLoadError/.test(e.message)) errors.push(e.message); });
  const who = await (await ctx.request.get(`${ORIGIN}/v1/console/auth/whoami`)).json();
  // Idempotent: installing an installed plugin is fine; enabling records the operator's consent.
  const r = await ctx.request.post(`${ORIGIN}/v1/console/api/v1/marketplace/plugins/${encodeURIComponent(PLUGIN_ID)}/install`, {
    headers: { "X-CSRF-Token": who.csrf_token },
    data: { wait: true, enable_after_install: true, consent_granted: true },
  });
  expect(r.status(), await r.text()).toBeLessThan(300);
});
test.afterAll(async () => { await ctx.close(); });

test("the panel is a manifest entry in the sidebar's Marketplace group", async () => {
  await page.goto(`${BASE_URL}/app/dashboard`);
  const link = page.locator('nav a[href$="/app/agent-conversations"]:visible');
  await expect(link).toHaveCount(1, { timeout: 20_000 });
  await expect(link).toHaveText(/Agent conversations/);
  const group = link.locator("xpath=ancestor::div[.//button[starts-with(normalize-space(.), 'Marketplace')]][1]");
  await expect(group.getByRole("button", { name: /^Marketplace/ })).toBeVisible();
});

test("opens, and the New dialog carries the settings form", async () => {
  await page.locator('nav a[href$="/app/agent-conversations"]:visible').click();
  await expect(page).toHaveURL(/\/app\/agent-conversations/);
  await expect(page.getByRole("heading", { name: "Agent conversations" })).toBeVisible();
  await page.getByTestId("new-conversation").click();
  const settings = page.getByTestId("conversation-settings");
  await expect(settings).toBeVisible();
  await expect(page.getByTestId("setting-max-words-value")).toHaveText("250 words");
  await page.getByTestId("setting-max-words").fill("120");
  await expect(page.getByTestId("setting-max-words-value")).toHaveText("120 words");
  await page.getByTestId("setting-note-local").fill("Be terse.");
  await expect(page.getByTestId("start-conversation")).toBeDisabled();   // nothing chosen yet
  await page.keyboard.press("Escape");
});

// ── stubbed running conversation in the real SPA ────────────────────────────────────────────────
const sent: { method: string; url: string; body: unknown }[] = [];
let paused = false;

function conversation() {
  return {
    conversation_id: CID, status: "running", reason: null, paused,
    local: { agent_id: "opus", model: "m", address: "agent://a/opus" },
    peer: { agent_id: "haiku", model: "m", address: "agent://b/haiku", endpoint_id: "peer-B" },
    max_turns: 6, started_at: 1, ended_at: null, turns: 2, topic: "Pick a name", ask: false, pending: 0,
    settings: { max_words: 250, pace_s: 5, role_notes: { local: "", peer: "" } }, last_seq: 5,
    messages: [
      { seq: 1, ts: 1, speaker: "operator", agent_id: "operator", address: null, task_id: null, status: "ok", text: "Pick a name", duration_ms: 0 },
      { seq: 2, ts: 2, speaker: "local", agent_id: "opus", address: "agent://a/opus", task_id: "t", status: "ok", text: "I propose **Atlas**.", duration_ms: 1200 },
      { seq: 3, ts: 3, speaker: "peer", agent_id: "haiku", address: "agent://b/haiku", task_id: "t2", status: "ok", text: "Atlas works.", duration_ms: 900 },
      { seq: 5, ts: 5, speaker: "operator", agent_id: "operator", address: null, task_id: null, status: "ok", text: "Keep it short.", duration_ms: 0, target: "peer" },
    ],
    events: [{ seq: 4, ts: 4, event: "paused" }],
  };
}

async function stub(route: Route) {
  const req = route.request();
  const url = new URL(req.url());
  const path = url.pathname.replace(/^.*\/federation/, "");
  const json = (data: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
  if (req.method() === "GET" && path === "/conversations") return json({ conversations: [conversation()] });
  if (req.method() === "GET" && path === "/conversations-commands")
    return json({ commands: [{ cmd: "/pause", args: "", desc: "Pause" }, { cmd: "/words", args: "<50-600>", desc: "Length" }] });
  if (req.method() === "GET" && path === `/conversations/${CID}`) return json(conversation());
  sent.push({ method: req.method(), url: path, body: req.postData() ? JSON.parse(req.postData()!) : null });
  if (path.endsWith("/pause")) { paused = true; return json({ paused: true }); }
  if (path.endsWith("/resume")) { paused = false; return json({ paused: false }); }
  if (path.endsWith("/command")) return json({ notice: "Paused after the current turn." });
  if (path.endsWith("/messages")) return json({ queued: 1 }, 202);
  if (path.endsWith("/settings")) return json({ queued: 1 }, 202);
  return json({}, 404);
}

test("thread: bubbles by author, system row, private-note caption, URL-held selection", async () => {
  await page.route("**/v1/console/federation/**", stub);
  await page.goto(`${BASE_URL}/app/agent-conversations`);
  await page.getByTestId("conversation-item").first().click();
  await expect(page).toHaveURL(new RegExp(`c=${CID}`));
  const rows = page.getByTestId("conversation-message");
  await expect(rows).toHaveCount(4);
  await expect(rows.nth(1)).toHaveAttribute("data-speaker", "local");
  await expect(rows.nth(1).locator("strong")).toHaveText("Atlas");               // Markdown, not raw
  await expect(rows.nth(2)).toHaveAttribute("data-speaker", "peer");
  await expect(rows.nth(3)).toContainText("to haiku only");
  await expect(page.getByTestId("conversation-event")).toHaveText("Paused");
  // operator + own agent sit right of the peer
  const x = async (i: number) => (await rows.nth(i).boundingBox())!.x;
  expect(await x(1)).toBeGreaterThan(await x(2) - 1);
  await page.reload();                                                           // selection survives a reload
  await expect(page.getByTestId("conversation-thread")).toBeVisible();
});

test("composer: interjection goes to the chosen agent, / lists server commands, commands are not sent as text", async () => {
  sent.length = 0;
  const input = page.getByTestId("conversation-input");
  await page.getByTestId("target-peer").click();
  await input.fill("Prefer snake_case.");
  await page.getByTestId("conversation-send").click();
  await expect.poll(() => sent.find((s) => s.url.endsWith("/messages"))?.body).toEqual({ text: "Prefer snake_case.", target: "peer" });

  await page.getByTestId("target-both").click();
  await input.fill("/p");
  await expect(page.getByTestId("command-hints")).toContainText("/pause");
  await page.getByTestId("command-hints").getByRole("button").first().click();
  await input.press("Enter");
  await expect.poll(() => sent.find((s) => s.url.endsWith("/command"))?.body).toEqual({ line: "/pause" });
  await expect(page.getByTestId("conversation-notice")).toContainText("Paused after the current turn.");
  expect(sent.filter((s) => s.url.endsWith("/messages"))).toHaveLength(1);       // the command was NOT a message
});

test("pause toggle and live settings send exactly the changed fields", async () => {
  sent.length = 0;
  await page.getByTestId("pause-toggle").click();
  await expect.poll(() => sent.some((s) => s.url.endsWith("/pause"))).toBe(true);
  // The settings column is open by default — no click needed to reach it.
  await expect(page.getByTestId("settings-drawer")).toBeVisible();
  await expect(page.getByTestId("sidebar-participants")).toContainText("opus");
  await expect(page.getByTestId("sidebar-status")).toContainText("2 / 6");
  await expect(page.getByTestId("apply-settings")).toBeDisabled();               // nothing changed yet
  await page.getByTestId("setting-note-peer").fill("Answer in one line.");
  await page.getByTestId("apply-settings").click();
  await expect.poll(() => sent.find((s) => s.url.endsWith("/settings"))?.body)
    .toEqual({ settings: { role_notes: { local: "", peer: "Answer in one line." } } });
  await page.screenshot({ path: "test-results/agent-conversations-thread.png" });
  // Hiding is remembered across a reload; reopen so later runs start from the default.
  await page.getByTestId("settings-toggle").click();
  await expect(page.getByTestId("settings-drawer")).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId("conversation-thread")).toBeVisible();
  await expect(page.getByTestId("settings-drawer")).toHaveCount(0);
  await page.getByTestId("settings-toggle").click();
  await expect(page.getByTestId("settings-drawer")).toBeVisible();
});

test("no uncaught page errors", async () => { expect(errors).toEqual([]); });
