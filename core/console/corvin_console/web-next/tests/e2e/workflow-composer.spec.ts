/**
 * Workflow editor composer (CONCEPT-0115) — real browser against the live console.
 *
 * Run: CONSOLE_BASE_URL=http://127.0.0.1:8765/console npx playwright test workflow-composer
 *
 * Nothing here sends a design message (that would spawn a paid engine turn): the
 * composer is exercised up to Send, and server commands (`/phase`, no spawn) are
 * exercised over the real design WebSocket when the running host serves them.
 */
import { test, expect, type APIRequestContext, type Page } from "@playwright/test";

const ORIGIN = (process.env.CONSOLE_BASE_URL || "http://127.0.0.1:8765/console").replace(/\/console\/?$/, "");
const API = `${ORIGIN}/v1/console`;
const WID = `e2e_composer_${Date.now().toString(36)}`;

async function csrfOf(request: APIRequestContext): Promise<string> {
  const who = await request.get(`${API}/auth/whoami`);
  expect(who.status()).toBe(200);
  return (await who.json()).csrf_token as string;
}

test.beforeAll(async ({ playwright }) => {
  const ctx = await playwright.request.newContext({ storageState: "./tests/e2e/auth-state.json" });
  const csrf = await csrfOf(ctx);
  const r = await ctx.post(`${API}/workflows`, {
    headers: { "X-CSRF-Token": csrf }, data: { id: WID, title: "E2E composer" },
  });
  expect(r.status(), await r.text()).toBeLessThan(300);
  await ctx.dispose();
});

test.afterAll(async ({ playwright }) => {
  const ctx = await playwright.request.newContext({ storageState: "./tests/e2e/auth-state.json" });
  const csrf = await csrfOf(ctx);
  await ctx.delete(`${API}/workflows/${WID}`, { headers: { "X-CSRF-Token": csrf } });
  await ctx.dispose();
});

async function openEditor(page: Page) {
  await page.goto(`/console/app/workflows/${WID}`);
  return page.getByTestId("workflow-composer-textarea");
}

test("the design chat input is the shared composer: pill, mic, send, no legacy textarea", async ({ page }) => {
  const ta = await openEditor(page);
  await expect(ta).toBeVisible();
  await expect(ta).toBeEnabled({ timeout: 15_000 }); // WebSocket connected
  await expect(page.getByTestId("workflow-composer-mic")).toBeVisible();
  await expect(page.locator('[data-composer-field="shared"]').first()).toBeVisible();
  // Send stays disabled with an empty draft and enables with text; Shift+Enter is a newline, not a send.
  const send = page.getByTestId("workflow-composer-send");
  await expect(send).toBeDisabled();
  await ta.fill("first line");
  await expect(send).toBeEnabled();
  await ta.press("Shift+Enter");
  await expect(ta).toHaveValue("first line\n");
  // The legacy two-row Textarea + separate "Send" text button is gone.
  await expect(page.getByPlaceholder(/Describe your workflow… \(Enter to send\)$/)).toHaveCount(0);
});

test("slash palette shows only the design table (server-provided)", async ({ page }) => {
  await page.route(/\/v1\/console\/workflow-design\/commands$/, (route) => route.fulfill({
    json: { commands: [{ cmd: "/phase", args: "", desc: "Show the current design phase" }] },
  }));
  const ta = await openEditor(page);
  await expect(ta).toBeEnabled({ timeout: 15_000 });
  await ta.fill("/");
  const pal = page.getByTestId("slash-palette");
  await expect(pal).toBeVisible();
  await expect(pal.locator("button")).toHaveText([/\/phase/]);
});

test("Space held outside the composer does not start dictation (canvas keeps Space)", async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as { __srStarts: number }).__srStarts = 0;
    class FakeSR {
      continuous = false; interimResults = false; lang = "";
      onresult = null; onerror = null; onend = null;
      start() { (window as unknown as { __srStarts: number }).__srStarts++; }
      stop() {}
    }
    (window as unknown as { SpeechRecognition: unknown }).SpeechRecognition = FakeSR;
  });
  const ta = await openEditor(page);
  await expect(ta).toBeEnabled({ timeout: 15_000 });
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.down("Space");
  await page.waitForTimeout(700);
  await page.keyboard.up("Space");
  expect(await page.evaluate(() => (window as unknown as { __srStarts: number }).__srStarts)).toBe(0);
  // Held INSIDE the composer it does start one recognizer.
  await ta.focus();
  await page.keyboard.down("Space");
  await page.waitForTimeout(700);
  await page.keyboard.up("Space");
  expect(await page.evaluate(() => (window as unknown as { __srStarts: number }).__srStarts)).toBe(1);
});

test("design commands are answered by the server without a design turn (needs the new host)", async ({ page, request }) => {
  const probe = await request.get(`${API}/workflow-design/commands`);
  test.skip(probe.status() === 404, "running host predates /workflow-design/commands — restart corvin-webui");
  const ta = await openEditor(page);
  await expect(ta).toBeEnabled({ timeout: 15_000 });
  await ta.fill("/phase");
  await ta.press("Escape"); // close the palette so Enter sends, not inserts
  await ta.press("Enter");
  await expect(page.getByText(/Current design phase: discovering\./)).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/Designing workflow/)).toHaveCount(0);
});
