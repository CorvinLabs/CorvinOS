/**
 * gap/chat release-readiness proof, in a real browser against a SANDBOX console
 * (never :8765 — see sandbox-origin.ts).
 *
 *  1. The chat header has an Audit opener again, with a close control.
 *  2. Auto-Repair works (the POST carries the CSRF token) on a session with a
 *     real HIGH anomaly.
 *  3. The slash palette lists only commands that act; `/new` really opens a new
 *     chat; `/audit` shows real events; `/create workflow` answers honestly.
 *
 * Needs: GC_BASE (console origin), GC_ROOT (sandbox CORVIN_HOME root),
 * GC_COOKIE (session cookie value). Run with playwright.gap-chat.config.ts.
 */
import { test, expect, type Page, type BrowserContext } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { assertNotLiveConsole } from "./sandbox-origin";

const BASE = process.env.GC_BASE ?? "";
const ROOT = process.env.GC_ROOT ?? "";
const COOKIE = process.env.GC_COOKIE ?? "";
const SHOTS = process.env.GC_SHOTS ?? "/tmp/gc-shots";

test.skip(!BASE || !ROOT || !COOKIE, "needs GC_BASE, GC_ROOT, GC_COOKIE (sandbox console)");

let ctx: BrowserContext;
let csrf = "";

async function api(pathname: string, init?: { method?: string; data?: unknown }) {
  const r = await ctx.request.fetch(`${BASE}/v1/console${pathname}`, {
    method: init?.method ?? "GET",
    data: init?.data,
    headers: init?.method && init.method !== "GET" ? { "X-CSRF-Token": csrf } : undefined,
  });
  return r;
}

async function newChat(title: string): Promise<{ sid: string; chat_key: string }> {
  const r = await api("/chat/sessions", { method: "POST", data: { title } });
  expect([200, 201]).toContain(r.status());
  const body = await r.json();
  return body.session ?? body;
}

async function openChat(page: Page, sid: string) {
  await page.goto(`${BASE}/console/app/chat/${sid}`, { waitUntil: "load" });
  await expect(page.locator("textarea").first()).toBeVisible({ timeout: 20_000 });
}

test.beforeAll(async ({ browser }) => {
  assertNotLiveConsole(BASE, "GC_BASE");
  ctx = await browser.newContext();
  await ctx.addCookies([{ name: "corvin_console_sid", value: COOKIE, url: BASE }]);
  const who = await ctx.request.get(`${BASE}/v1/console/auth/whoami`);
  expect(who.status()).toBe(200);
  csrf = (await who.json()).csrf_token;
  fs.mkdirSync(SHOTS, { recursive: true });
});
test.afterAll(async () => { await ctx?.close(); });

test("audit opener is in the chat header, opens the panel and closes it again", async () => {
  const chat = await newChat("gap audit opener");
  const page = await ctx.newPage();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await openChat(page, chat.sid);

  const opener = page.getByTestId("chat-audit-toggle");
  await expect(opener).toBeVisible();
  await expect(page.getByText("Single-Chain", { exact: true })).toHaveCount(0);

  await opener.click();
  await expect(page.getByText("Single-Chain", { exact: true })).toBeVisible();
  await expect(page.getByText("Dual-Track", { exact: true })).toBeVisible();
  await expect(page.getByText("TDE Graph", { exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "audit-open.png") });

  await page.getByTestId("chat-audit-close").click();
  await expect(page.getByText("Single-Chain", { exact: true })).toHaveCount(0);
  await expect(page.locator("textarea").first()).toBeVisible();

  // the header button toggles as well
  await opener.click();
  await expect(page.getByText("Single-Chain", { exact: true })).toBeVisible();
  await opener.click();
  await expect(page.getByText("Single-Chain", { exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
  await page.close();
});

test("Auto-Repair succeeds (CSRF sent) on a session with a HIGH anomaly", async () => {
  const chat = await newChat("gap auto-repair");
  // A real anomaly in the session's own debug log: a delegation decision that
  // was never followed by acs.run.start (anomaly_detector._check_delegation_orphans, HIGH).
  const dir = path.join(ROOT, "corvin_home", "tenants", "_default", "sessions", chat.chat_key);
  expect(fs.existsSync(dir)).toBe(true);
  fs.appendFileSync(path.join(dir, "chat_debug.jsonl"),
    JSON.stringify({ ts: new Date().toISOString(), event: "delegation.decision", will_delegate: true }) + "\n");

  const page = await ctx.newPage();
  const repairCalls: { status: number; csrf: string | undefined }[] = [];
  page.on("response", async (res) => {
    if (res.url().includes("/aco/repair")) {
      repairCalls.push({ status: res.status(), csrf: res.request().headers()["x-csrf-token"] });
    }
  });
  await openChat(page, chat.sid);
  await page.getByTestId("chat-audit-toggle").click();
  await page.getByRole("button", { name: "Debug Log", exact: true }).click(); // the anomaly scan lives in this view
  const repair = page.getByRole("button", { name: /Auto-Repair/ });
  await expect(repair).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: path.join(SHOTS, "repair-before.png") });
  await repair.click();
  await expect(page.getByText(/Converged|Repair applied/)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Repair failed")).toHaveCount(0);
  await page.screenshot({ path: path.join(SHOTS, "repair-after.png") });
  expect(repairCalls.length).toBe(1);
  expect(repairCalls[0].status).toBe(200);
  expect(repairCalls[0].csrf).toBeTruthy();
  await page.close();
});

test("slash palette lists only commands that act", async () => {
  const chat = await newChat("gap palette");
  const page = await ctx.newPage();
  await openChat(page, chat.sid);
  const box = page.locator("textarea").first();
  await box.fill("/");
  const palette = page.getByTestId("slash-palette");
  await expect(palette).toBeVisible();
  const names = (await palette.locator("button").allInnerTexts()).map((t) => t.split(/\s/)[0]);
  await page.screenshot({ path: path.join(SHOTS, "palette.png") });
  for (const dead of ["/clear", "/reset", "/forget", "/skills", "/memory", "/dialectic-on",
                      "/dialectic-off", "/share", "/go", "/propose", "/persona"]) {
    expect(names, `${dead} must not be offered`).not.toContain(dead);
  }
  for (const live of ["/help", "/new", "/audit", "/federation", "/resume", "/erase", "/quota"]) {
    expect(names, `${live} must be offered`).toContain(live);
  }
  expect(names.filter((n) => n === "/create").length).toBe(1); // only "/create task"
  await expect(palette.getByText("/create workflow")).toHaveCount(0);
  await page.close();
});

test("/new opens a new chat; /audit shows events; /create workflow is honest", async () => {
  const chat = await newChat("gap slash behaviour");
  const page = await ctx.newPage();
  await openChat(page, chat.sid);
  const box = page.locator("textarea").first();

  // /create workflow: honest answer, no queued id
  await box.fill('/create workflow name="x"');
  await box.press("Escape");
  await box.press("Enter");
  await expect(page.getByText(/not available on this build/i).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/queued/i)).toHaveCount(0);

  // /audit: real event types from the chain
  await box.fill("/audit last 5");
  await box.press("Escape");
  await box.press("Enter");
  await expect(page.getByText(/audit events/i).first()).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: path.join(SHOTS, "slash-audit-create.png") });

  // /new: a different chat id in the URL, nothing sent to the model
  const before = page.url();
  await box.fill("/new");
  await box.press("Escape");
  await box.press("Enter");
  await expect.poll(() => page.url(), { timeout: 20_000 }).not.toBe(before);
  expect(page.url()).toMatch(/\/app\/chat\/[A-Za-z0-9_-]+$/);
  await page.screenshot({ path: path.join(SHOTS, "slash-new.png") });
  await page.close();
});

// Real-engine acceptance: a chat turn made through the console WebSocket with the
// real claude engine (GC_REAL_SID = that chat, GC_REAL_MODEL = the model the turn
// reported) must appear in the Audit panel opened from the chat header.
test("a real engine turn appears in the Audit panel (OS-Turn Audit list)", async () => {
  const sid = process.env.GC_REAL_SID ?? "";
  const model = process.env.GC_REAL_MODEL ?? "";
  test.skip(!sid || !model, "needs GC_REAL_SID and GC_REAL_MODEL from a real engine turn");
  const page = await ctx.newPage();
  await openChat(page, sid);
  await page.getByTestId("chat-audit-toggle").click();
  await page.getByRole("button", { name: "OS-Turn Audit", exact: true }).click();
  await expect(page.getByText(/OS-Turn Audit \(EU AI Act Art\. 12\) — 1 turns/)).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "List", exact: true }).click();
  await expect(page.getByText(model, { exact: true }).first()).toBeVisible();
  await expect(page.getByText("ok", { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "real-turn-os-audit.png") });
  await page.close();
});
