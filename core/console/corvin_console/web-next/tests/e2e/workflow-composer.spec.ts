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

// ── Attachments (CONCEPT-0115 P3) — need a host that serves the attachment routes ──

async function attachmentRoutesLive(request: APIRequestContext): Promise<boolean> {
  const r = await request.get(`${API}/workflows/${WID}/attachments/0000000000000000`);
  if (r.status() !== 404) return false;
  return /attachment not found/.test(await r.text()); // a missing route answers "Not Found"
}

test("paperclip uploads, shows a chip, removing it deletes the stored file", async ({ page, request }) => {
  test.skip(!(await attachmentRoutesLive(request)), "running host predates the attachment routes — restart corvin-webui");
  const ta = await openEditor(page);
  await expect(ta).toBeEnabled({ timeout: 15_000 });
  let uploadedId = "";
  page.on("response", async (res) => {
    if (res.url().endsWith(`/workflows/${WID}/attachments`) && res.request().method() === "POST") {
      uploadedId = (await res.json()).attachments[0].id;
    }
  });
  await page.getByTestId("workflow-file-input").setInputFiles({
    name: "brief.md", mimeType: "text/markdown", buffer: Buffer.from("# brief\nrevenue 7\n"),
  });
  const chip = page.getByTestId("attachment-chip");
  await expect(chip).toContainText("brief.md");
  await expect.poll(() => uploadedId).not.toBe("");
  expect((await request.get(`${API}/workflows/${WID}/attachments/${uploadedId}`)).status()).toBe(200);
  await page.getByTestId("remove-attachment").click();
  await expect(chip).toHaveCount(0);
  await expect.poll(async () => (await request.get(`${API}/workflows/${WID}/attachments/${uploadedId}`)).status()).toBe(404);
});

test("dropping files on the design pane stages them", async ({ page, request }) => {
  test.skip(!(await attachmentRoutesLive(request)), "running host predates the attachment routes — restart corvin-webui");
  const ta = await openEditor(page);
  await expect(ta).toBeEnabled({ timeout: 15_000 });
  const dt = await page.evaluateHandle(() => {
    const d = new DataTransfer();
    d.items.add(new File(["a,b\n1,2\n"], "dropped.csv", { type: "text/csv" }));
    return d;
  });
  const zone = page.getByTestId("workflow-design-dropzone");
  await zone.dispatchEvent("dragenter", { dataTransfer: dt });
  await zone.dispatchEvent("dragover", { dataTransfer: dt });
  await zone.dispatchEvent("drop", { dataTransfer: dt });
  await expect(page.getByTestId("attachment-chip")).toContainText("dropped.csv");
  await page.getByTestId("remove-attachment").click(); // leave nothing behind
});

test("a non-text attachment is refused over the real WebSocket before any engine runs", async ({ page, request }) => {
  test.skip(!(await attachmentRoutesLive(request)), "running host predates the attachment routes — restart corvin-webui");
  const ta = await openEditor(page);
  await expect(ta).toBeEnabled({ timeout: 15_000 });
  await page.getByTestId("workflow-file-input").setInputFiles({
    name: "scan.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 not text"),
  });
  await expect(page.getByTestId("attachment-chip")).toContainText("scan.pdf");
  await ta.fill("read it");
  await page.getByTestId("workflow-composer-send").click();
  await expect(page.getByTestId("workflow-send-error")).toContainText(/text-based/);
  await expect(page.getByText("Not sent")).toBeVisible();
  await expect(page.getByText(/Designing workflow/)).toHaveCount(0);
});
