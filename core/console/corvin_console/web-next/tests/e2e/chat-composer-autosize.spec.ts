/**
 * E2E: the composer textarea grows to fit a multi-line message instead of
 * clipping it to one line (operator request 2026-10-05).
 *
 * tests/unit/hooks/useAutosizeTextarea.test.tsx proves the resize math in
 * jsdom with a stubbed scrollHeight (jsdom never lays out text, so it can't
 * exercise real browser measurement). This proves the real thing: a real
 * Chromium/Firefox textarea, real text, real layout — the height actually
 * increases on screen.
 */
import { test, expect, type BrowserContext, type Page } from "@playwright/test";

const ORIGIN = (process.env.CONSOLE_BASE_URL || "http://localhost:5173/console").replace(/\/console\/?$/, "");
const BASE_URL = ORIGIN;
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
    data: { title: "Composer Autosize E2E" },
  });
  expect(create.status()).toBe(200);
  sid = (await create.json()).session?.sid ?? "";
  expect(sid).toBeTruthy();
});

test.afterAll(async () => {
  if (sid && csrf) {
    await ctx.request.delete(`${API_BASE}/chat/sessions/${sid}`, {
      headers: { "X-CSRF-Token": csrf },
    });
  }
  await ctx.close();
});

async function navigate(page: Page): Promise<void> {
  await page.goto(`${BASE_URL}/console/app/chat/${sid}`, { waitUntil: "load" });
  await page.waitForTimeout(1000);
}

test("composer grows for a multi-line message and shrinks back when cleared", async () => {
  const page = await ctx.newPage();
  await navigate(page);

  const textarea = page.getByPlaceholder("Message Corvin…");
  await expect(textarea).toBeVisible();

  const oneLineHeight = await textarea.evaluate((el) => el.getBoundingClientRect().height);

  // Shift+Enter inserts a newline instead of sending — the same keystroke an
  // operator uses to write a real multi-line message.
  await textarea.click();
  await textarea.pressSequentially("first line");
  for (let i = 0; i < 5; i++) {
    await page.keyboard.down("Shift");
    await page.keyboard.press("Enter");
    await page.keyboard.up("Shift");
    await textarea.pressSequentially(`line ${i + 2}`);
  }

  const grownHeight = await textarea.evaluate((el) => el.getBoundingClientRect().height);
  expect(grownHeight).toBeGreaterThan(oneLineHeight * 2);

  // The full text must actually be visible (not clipped behind a fixed
  // one-line box with hidden overflow) — scrollHeight no longer exceeds the
  // rendered clientHeight once the element has grown to fit it.
  const { scrollHeight, clientHeight } = await textarea.evaluate((el) => ({
    scrollHeight: el.scrollHeight,
    clientHeight: el.clientHeight,
  }));
  expect(scrollHeight).toBeLessThanOrEqual(clientHeight + 1); // +1: sub-pixel rounding

  await textarea.fill("");
  const shrunkHeight = await textarea.evaluate((el) => el.getBoundingClientRect().height);
  expect(shrunkHeight).toBeLessThan(grownHeight);
  expect(Math.abs(shrunkHeight - oneLineHeight)).toBeLessThanOrEqual(2);

  await page.close();
});

test("composer clamps at a max height and scrolls instead of growing forever", async () => {
  const page = await ctx.newPage();
  await navigate(page);

  const textarea = page.getByPlaceholder("Message Corvin…");
  const longText = Array.from({ length: 40 }, (_, i) => `paragraph line ${i}`).join("\n");
  await textarea.evaluate((el, text) => {
    // 40 lines via real keystrokes would make this test slow for no extra
    // signal over the previous test's keystroke-level proof; set the value
    // directly and fire the same event React listens to, then let the hook
    // react exactly as it would to typed input.
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    setter.call(el, text);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }, longText);

  const height = await textarea.evaluate((el) => el.getBoundingClientRect().height);
  // DEFAULT_MAX_PX in use-autosize-textarea.ts is 240 — asserting a looser
  // bound here so this test doesn't become the place that pins that exact
  // constant.
  expect(height).toBeLessThan(300);

  const overflowY = await textarea.evaluate((el) => getComputedStyle(el).overflowY);
  expect(overflowY).toBe("auto");

  await page.close();
});
