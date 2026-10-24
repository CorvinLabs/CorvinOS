/**
 * E2E (live, real `claude` turns) — CONCEPT-0107 /btw v2.
 *
 * Mid-stream, the operator types a note WITHOUT `/btw` and presses Enter, or
 * holds Space and speaks: the note appears in the history as a BTW bubble
 * before the running answer, its chip walks to "Taken into account", the
 * model's reply reflects it, and a reload shows the same bubble. Voice goes
 * through a 1.5 s undo window; Esc puts the transcript in the composer.
 *
 * Costs real API turns — run deliberately:
 *   CORVIN_E2E_ALLOW_LIVE=1 CONSOLE_BASE_URL=http://127.0.0.1:8765/console npx playwright test \
 *     tests/e2e/chat-btw-steer.spec.ts --project=chromium
 */
import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { resolveUrlVar } from "./sandbox-origin";

const ORIGIN = resolveUrlVar("CONSOLE_BASE_URL", "http://localhost:5173/console").replace(/\/console\/?$/, "");
const API_BASE = `${ORIGIN}/v1/console`;
test.setTimeout(300_000);

let ctx: BrowserContext;
let csrf = "";
const sids: string[] = [];

test.beforeAll(async ({ browser }) => {
  ctx = await browser.newContext({ storageState: "./tests/e2e/auth-state.json" });
  const who = await ctx.request.get(`${API_BASE}/auth/whoami`);
  expect(who.status()).toBe(200);
  csrf = (await who.json()).csrf_token ?? "";
});

test.afterAll(async () => {
  for (const sid of sids) {
    await ctx.request.delete(`${API_BASE}/chat/sessions/${sid}`, { headers: { "X-CSRF-Token": csrf } });
  }
  await ctx.close();
});

async function newChat(title: string): Promise<string> {
  const r = await ctx.request.post(`${API_BASE}/chat/sessions`, {
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" }, data: { title },
  });
  const sid = (await r.json()).session?.sid ?? "";
  sids.push(sid);
  return sid;
}

const FAKE_SPEECH = `
(() => {
  let active = null;
  class FakeRecognition {
    constructor() { this.running = false; }
    start() { active = this; this.running = true; }
    stop() { if (active === this) active = null; this.running = false; setTimeout(() => this.onend && this.onend(), 30); }
  }
  window.SpeechRecognition = FakeRecognition;
  window.webkitSpeechRecognition = FakeRecognition;
  window.__sttSay = (text) => {
    if (!active) return false;
    active.onresult && active.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: text }], { isFinal: true })] });
    return true;
  };
})();
`;

const LONG_PROMPT =
  "Schreibe einen ausführlichen Aufsatz mit mindestens 700 Wörtern über die Geschichte der Eisenbahn in Europa.";

async function startLongTurn(page: Page, sid: string) {
  await page.goto(`${ORIGIN}/console/app/chat/${sid}`, { waitUntil: "load" });
  const composer = page.getByTestId("chat-composer-textarea");
  await expect(composer).toBeVisible({ timeout: 20_000 });
  await composer.fill(LONG_PROMPT);
  // Right after a console restart the socket may still be reconnecting; the
  // composer keeps the text then, so pressing Enter again is the user's retry.
  for (let i = 0; i < 12; i++) {
    await composer.press("Enter");
    if (await page.getByTitle("Stop generation").isVisible({ timeout: 5_000 }).catch(() => false)) break;
    await page.getByTitle("Stop generation").waitFor({ timeout: 5_000 }).catch(() => {});
    if (await page.getByTitle("Stop generation").isVisible()) break;
  }
  await expect(page.getByTitle("Stop generation")).toBeVisible({ timeout: 10_000 });
  return composer;
}

async function waitTurnDone(page: Page) {
  await expect(page.getByTitle("Stop generation")).toHaveCount(0, { timeout: 240_000 });
}

test("typed note mid-stream (no /btw) is shown, delivered, answered and survives reload", async () => {
  const sid = await newChat("BTW typed E2E");
  const page = await ctx.newPage();
  const composer = await startLongTurn(page, sid);

  await composer.fill("Beende deine Antwort mit dem einzelnen Wort ANANAS.");
  await composer.press("Enter");
  const bubble = page.getByTestId("btw-bubble");
  await expect(bubble).toHaveCount(1);
  await expect(bubble).toContainText("ANANAS");
  await expect(composer).toHaveValue("");                       // the text moved into the bubble
  const seen = new Set<string>();
  const t0 = Date.now();
  while (Date.now() - t0 < 240_000) {
    const st = await bubble.getAttribute("data-btw-state");
    if (st) seen.add(st);
    if (st === "answered" || st === "refused" || st === "undelivered" || st === "failed") break;
    await page.waitForTimeout(250);
  }
  console.log("BTW states seen:", [...seen].join(" → "));
  await waitTurnDone(page);
  await expect(bubble).toHaveAttribute("data-btw-state", "answered");
  // The bubble sits before the answer in the history.
  const order = await page.locator('[data-testid="btw-bubble"], .rounded-tl-md').evaluateAll(
    (els) => els.map((e) => (e.getAttribute("data-testid") === "btw-bubble" ? "btw" : "answer")));
  expect(order.indexOf("btw")).toBeLessThan(order.lastIndexOf("answer"));
  await expect(page.locator(".rounded-tl-md").last()).toContainText(/ANANAS/i, { timeout: 30_000 });

  await page.reload({ waitUntil: "load" });
  const again = page.getByTestId("btw-bubble");
  await expect(again).toHaveCount(1, { timeout: 20_000 });
  await expect(again).toContainText("ANANAS");
  await expect(again).toHaveAttribute("data-btw-state", "answered");
  await expect(again).not.toContainText("/btw");
});

test("voice mid-stream: undo window, Esc keeps it in the composer, release sends a BTW note", async () => {
  const sid = await newChat("BTW voice E2E");
  const page = await ctx.newPage();
  await page.addInitScript(FAKE_SPEECH);
  const composer = await startLongTurn(page, sid);

  const speak = async (text: string) => {
    await composer.click();
    await page.keyboard.down("Space");
    await page.waitForTimeout(700);
    expect(await page.evaluate((t) => (window as any).__sttSay(t), text)).toBe(true);
    await page.keyboard.up("Space");
  };

  // 1) Esc inside the undo window: nothing is sent, the words land in the field.
  await speak("das hier nicht senden");
  await expect(page.getByTestId("voice-steer-undo")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("voice-steer-undo")).toHaveCount(0);
  await expect(composer).toHaveValue(/das hier nicht senden/);
  await expect(page.getByTestId("btw-bubble")).toHaveCount(0);
  await composer.fill("");

  // 2) Release and let the window run out: a BTW note, given by voice.
  await speak("Beende deine Antwort mit dem einzelnen Wort MANGO.");
  await expect(page.getByTestId("voice-steer-undo")).toBeVisible();
  await expect(composer).toHaveValue("");                       // spoken text never stays in the field
  const bubble = page.getByTestId("btw-bubble");
  await expect(bubble).toHaveCount(1, { timeout: 5_000 });
  await expect(bubble).toHaveAttribute("data-btw-via", "voice");
  await waitTurnDone(page);
  await expect(bubble).toHaveAttribute("data-btw-state", /answered|undelivered/);
  console.log("voice BTW final state:", await bubble.getAttribute("data-btw-state"));
});
