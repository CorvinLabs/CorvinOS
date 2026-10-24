/**
 * E2E repro: hold-Space speech-to-text must land in the chat composer.
 *
 * The chat page mounts TWO push-to-talk implementations at once — ChatPane's
 * own (pages/chat.tsx) and the `useVoiceInput` hook inside ComposerBase — and
 * both listen for Space on `window`. Chrome allows ONE live SpeechRecognition
 * per page: starting a second aborts the first (error "aborted" + end). Each
 * `onend` restarts its recognizer, so the two evict each other forever and no
 * `result` is ever delivered. This fake mirrors that Chrome behaviour.
 */
import { test, expect, type BrowserContext } from "@playwright/test";
import { resolveUrlVar } from "./sandbox-origin";

const ORIGIN = resolveUrlVar("CONSOLE_BASE_URL", "http://localhost:5173/console").replace(/\/console\/?$/, "");
const API_BASE = `${ORIGIN}/v1/console`;

let ctx: BrowserContext;
let csrf = "";
let sid = "";

test.beforeAll(async ({ browser }) => {
  ctx = await browser.newContext({ storageState: "./tests/e2e/auth-state.json" });
  const who = await ctx.request.get(`${API_BASE}/auth/whoami`);
  expect(who.status()).toBe(200);
  csrf = (await who.json()).csrf_token ?? "";
  const create = await ctx.request.post(`${API_BASE}/chat/sessions`, {
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    data: { title: "STT composer E2E" },
  });
  expect(create.status()).toBe(200);
  sid = (await create.json()).session?.sid ?? "";
});

test.afterAll(async () => {
  if (sid && csrf) {
    await ctx.request.delete(`${API_BASE}/chat/sessions/${sid}`, { headers: { "X-CSRF-Token": csrf } });
  }
  await ctx.close();
});

const FAKE_SPEECH = `
(() => {
  let active = null;
  const log = (window.__sttLog = []);
  class FakeRecognition {
    constructor() { this.continuous = false; this.interimResults = false; this.lang = ""; this.running = false; }
    start() {
      if (this.running) throw new DOMException("already started", "InvalidStateError");
      if (active && active !== this) {           // Chrome: newest start() evicts the older one
        const old = active; old.running = false;
        setTimeout(() => { old.onerror && old.onerror({ error: "aborted" }); old.onend && old.onend(); }, 30);
      }
      active = this; this.running = true; log.push("start");
    }
    stop() { if (active === this) active = null; this.running = false; setTimeout(() => this.onend && this.onend(), 30); }
  }
  window.SpeechRecognition = FakeRecognition;
  window.webkitSpeechRecognition = FakeRecognition;
  // test hook: deliver a final result to whichever recognizer is live
  window.__sttSay = (text) => {
    if (!active) return false;
    active.onresult && active.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: text }], { isFinal: true })] });
    return true;
  };
})();
`;

test("hold Space dictates into the composer (one live recognizer, text shown)", async () => {
  const page = await ctx.newPage();
  await page.addInitScript(FAKE_SPEECH);
  await page.goto(`${ORIGIN}/console/app/chat/${sid}`, { waitUntil: "load" });

  const composer = page.getByTestId("chat-composer-textarea");
  await expect(composer).toBeVisible({ timeout: 20_000 });
  await composer.click();

  await page.keyboard.down("Space");
  await page.waitForTimeout(700);                       // past the 400 ms hold threshold
  const delivered = await page.evaluate(() => (window as any).__sttSay("hallo welt"));
  await page.keyboard.up("Space");
  await page.waitForTimeout(300);

  const starts = await page.evaluate(() => (window as any).__sttLog.length);
  // Exactly one start per hold: two listeners = two starts = the eviction loop.
  expect(starts, "recognizer starts for ONE Space hold").toBe(1);
  expect(delivered, "a live recognizer existed to receive speech").toBe(true);
  await expect(composer).toHaveValue("hallo welt");    // no stray leading space from the hold
});
