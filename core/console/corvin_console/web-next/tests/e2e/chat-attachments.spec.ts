/**
 * E2E test: Chat file attachments
 *
 * Covers:
 *  1. Paperclip button is visible in chat footer
 *  2. Uploading a CSV file → chip appears in preview bar
 *  3. Uploading a PNG image → chip appears with image label
 *  4. Uploading a PDF → chip appears with PDF label
 *  5. Multiple files at once → multiple chips
 *  6. Remove a chip → it disappears
 *  7. Send message with attachments → chat bubble contains the file paths
 *  8. API endpoint directly: POST /chat/sessions/{sid}/attachments returns metadata
 *  9. Disallowed extension is rejected by backend
 * 10. No uncaught JS errors during the flow
 */

import {
  test,
  expect,
  type Page,
  type BrowserContext,
} from "@playwright/test";

const BASE_URL = "http://localhost:5173";
const API_BASE = "http://localhost:8765/v1/console";

const SCREENSHOT_DIR =
  process.env.PLAYWRIGHT_SCREENSHOT_DIR || "/tmp/corvin-e2e-screenshots";

test.describe.configure({ mode: "serial" });

let sharedContext: BrowserContext;
let csrfToken = "";
let testSid = "";

// ── Small in-memory test files ─────────────────────────────────────────────

const CSV_CONTENT = "name,score\nAlice,95\nBob,87\nCarla,92\n";
const PNG_BYTES = Buffer.from(
  "89504e470d0a1a0a0000000d49484452000000010000000108020000009001" +
    "2e000000000c4944415478016360f8cfc00000000200012dd1e800000000" +
    "4945 4e44ae426082",
  "hex",
);
const PDF_CONTENT = "%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF";

// ── Auth + setup ───────────────────────────────────────────────────────────

test.beforeAll(async ({ browser }) => {
  sharedContext = await browser.newContext({
    storageState: "./tests/e2e/auth-state.json",
  });

  // Verify session and grab CSRF token
  const whoami = await sharedContext.request.get(`${API_BASE}/auth/whoami`);
  expect(whoami.status()).toBe(200);
  const whoamiBody = await whoami.json();
  csrfToken = whoamiBody.csrf_token ?? "";
  expect(csrfToken).toBeTruthy();

  // Create a fresh test session
  const create = await sharedContext.request.post(`${API_BASE}/chat/sessions`, {
    headers: { "X-CSRF-Token": csrfToken, "Content-Type": "application/json" },
    data: { title: "Attachment E2E Test" },
  });
  expect(create.status()).toBe(200);
  const createBody = await create.json();
  testSid = createBody.session?.sid ?? "";
  expect(testSid).toBeTruthy();
});

test.afterAll(async () => {
  // Clean up the test session
  if (testSid && csrfToken) {
    await sharedContext.request.delete(`${API_BASE}/chat/sessions/${testSid}`, {
      headers: { "X-CSRF-Token": csrfToken },
    });
  }
  await sharedContext.close();
});

// ── API-level tests (no browser) ───────────────────────────────────────────

test("API: upload single CSV returns attachment metadata", async () => {
  const form = new FormData();
  form.append("files", new Blob([CSV_CONTENT], { type: "text/csv" }), "sales.csv");

  const resp = await sharedContext.request.post(
    `${API_BASE}/chat/sessions/${testSid}/attachments`,
    {
      headers: { "X-CSRF-Token": csrfToken },
      multipart: {
        files: {
          name: "sales.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(CSV_CONTENT),
        },
      },
    },
  );
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body.attachments).toHaveLength(1);
  expect(body.attachments[0].name).toBe("sales.csv");
  expect(body.attachments[0].path).toBe("attachments/sales.csv");
  expect(body.attachments[0].size).toBe(CSV_CONTENT.length);
});

test("API: upload PNG image returns correct MIME", async () => {
  const resp = await sharedContext.request.post(
    `${API_BASE}/chat/sessions/${testSid}/attachments`,
    {
      headers: { "X-CSRF-Token": csrfToken },
      multipart: {
        files: {
          name: "chart.png",
          mimeType: "image/png",
          buffer: PNG_BYTES,
        },
      },
    },
  );
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body.attachments[0].mime).toContain("image");
  expect(body.attachments[0].path).toMatch(/attachments\/chart/);
});

test("API: upload PDF returns correct metadata", async () => {
  const resp = await sharedContext.request.post(
    `${API_BASE}/chat/sessions/${testSid}/attachments`,
    {
      headers: { "X-CSRF-Token": csrfToken },
      multipart: {
        files: {
          name: "contract.pdf",
          mimeType: "application/pdf",
          buffer: Buffer.from(PDF_CONTENT),
        },
      },
    },
  );
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body.attachments[0].name).toBe("contract.pdf");
  expect(body.attachments[0].path).toBe("attachments/contract.pdf");
});

test("API: multiple files via sequential uploads accumulate in workdir", async () => {
  // Upload first file
  const r1 = await sharedContext.request.post(
    `${API_BASE}/chat/sessions/${testSid}/attachments`,
    {
      headers: { "X-CSRF-Token": csrfToken },
      multipart: {
        files: { name: "multi1.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2") },
      },
    },
  );
  expect(r1.status()).toBe(200);
  const b1 = await r1.json();
  expect(b1.attachments).toHaveLength(1);
  expect(b1.attachments[0].name).toBe("multi1.csv");

  // Upload second file
  const r2 = await sharedContext.request.post(
    `${API_BASE}/chat/sessions/${testSid}/attachments`,
    {
      headers: { "X-CSRF-Token": csrfToken },
      multipart: {
        files: { name: "multi2.csv", mimeType: "text/csv", buffer: Buffer.from("x,y\n3,4") },
      },
    },
  );
  expect(r2.status()).toBe(200);
  const b2 = await r2.json();
  expect(b2.attachments).toHaveLength(1);
  expect(b2.attachments[0].name).toBe("multi2.csv");

  // Verify both files are now accessible in workdir
  const f1 = await sharedContext.request.get(
    `${API_BASE}/chat/sessions/${testSid}/workdir/attachments/multi1.csv`,
  );
  const f2 = await sharedContext.request.get(
    `${API_BASE}/chat/sessions/${testSid}/workdir/attachments/multi2.csv`,
  );
  expect(f1.status()).toBe(200);
  expect(f2.status()).toBe(200);
});

test("API: disallowed extension returns 422", async () => {
  const resp = await sharedContext.request.post(
    `${API_BASE}/chat/sessions/${testSid}/attachments`,
    {
      headers: { "X-CSRF-Token": csrfToken },
      multipart: {
        files: {
          name: "evil.exe",
          mimeType: "application/octet-stream",
          buffer: Buffer.from("MZ\x90\x00"),
        },
      },
    },
  );
  expect(resp.status()).toBe(422);
});

test("API: file without CSRF is rejected", async () => {
  const resp = await sharedContext.request.post(
    `${API_BASE}/chat/sessions/${testSid}/attachments`,
    {
      multipart: {
        files: {
          name: "nocsrf.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(CSV_CONTENT),
        },
      },
    },
  );
  expect([401, 403, 422]).toContain(resp.status());
});

// ── UI-level tests ─────────────────────────────────────────────────────────

async function navigateToSession(page: Page, sid: string): Promise<void> {
  await page.goto(`${BASE_URL}/console/app/chat/${sid}`, { waitUntil: "load" });
  await page.waitForTimeout(1500);
}

function collectJsErrors(page: Page): string[] {
  const errs: string[] = [];
  page.on("pageerror", (err) => {
    const msg = err.message;
    if (
      msg.includes("ResizeObserver loop") ||
      msg.includes("Non-Error promise rejection") ||
      msg.includes("ChunkLoadError")
    ) return;
    errs.push(msg);
  });
  return errs;
}

test("UI: paperclip button is visible in chat footer", async () => {
  const page = await sharedContext.newPage();
  const errors = collectJsErrors(page);
  await navigateToSession(page, testSid);

  const btn = page.getByTestId("attach-button");
  await expect(btn).toBeVisible();

  await page.screenshot({
    path: `${SCREENSHOT_DIR}/attach-01-button-visible.png`,
    fullPage: false,
  });
  expect(errors).toHaveLength(0);
  await page.close();
});

test("UI: upload CSV → chip appears in preview bar", async () => {
  const page = await sharedContext.newPage();
  const errors = collectJsErrors(page);
  await navigateToSession(page, testSid);

  // Trigger file input via the paperclip button
  const fileInput = page.getByTestId("file-input");
  await fileInput.setInputFiles({
    name: "report.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(CSV_CONTENT),
  });

  // Wait for upload to complete and chip to appear
  await expect(page.getByTestId("attachment-chip").first()).toBeVisible({ timeout: 8000 });

  // Chip should show the filename
  await expect(page.getByTestId("attachment-chip").first()).toContainText("report.csv");

  await page.screenshot({
    path: `${SCREENSHOT_DIR}/attach-02-csv-chip.png`,
  });
  expect(errors).toHaveLength(0);
  await page.close();
});

test("UI: upload PNG → image chip appears", async () => {
  const page = await sharedContext.newPage();
  const errors = collectJsErrors(page);
  await navigateToSession(page, testSid);

  const fileInput = page.getByTestId("file-input");
  await fileInput.setInputFiles({
    name: "photo.png",
    mimeType: "image/png",
    buffer: PNG_BYTES,
  });

  const chip = page.getByTestId("attachment-chip").first();
  await expect(chip).toBeVisible({ timeout: 8000 });
  await expect(chip).toContainText("photo.png");

  await page.screenshot({
    path: `${SCREENSHOT_DIR}/attach-03-image-chip.png`,
  });
  expect(errors).toHaveLength(0);
  await page.close();
});

test("UI: upload PDF → PDF chip appears", async () => {
  const page = await sharedContext.newPage();
  const errors = collectJsErrors(page);
  await navigateToSession(page, testSid);

  const fileInput = page.getByTestId("file-input");
  await fileInput.setInputFiles({
    name: "document.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from(PDF_CONTENT),
  });

  const chip = page.getByTestId("attachment-chip").first();
  await expect(chip).toBeVisible({ timeout: 8000 });
  await expect(chip).toContainText("document.pdf");

  await page.screenshot({
    path: `${SCREENSHOT_DIR}/attach-04-pdf-chip.png`,
  });
  expect(errors).toHaveLength(0);
  await page.close();
});

test("UI: upload multiple files → multiple chips shown", async () => {
  const page = await sharedContext.newPage();
  const errors = collectJsErrors(page);
  await navigateToSession(page, testSid);

  const fileInput = page.getByTestId("file-input");

  // Upload first batch
  await fileInput.setInputFiles([
    { name: "multi_a.csv", mimeType: "text/csv", buffer: Buffer.from("x,y\n1,2") },
    { name: "multi_b.csv", mimeType: "text/csv", buffer: Buffer.from("p,q\n3,4") },
  ]);
  await expect(page.getByTestId("attachment-chip")).toHaveCount(2, { timeout: 10000 });

  // Upload a second file (adds to existing chips)
  await fileInput.setInputFiles([
    { name: "multi_c.pdf", mimeType: "application/pdf", buffer: Buffer.from(PDF_CONTENT) },
  ]);
  await expect(page.getByTestId("attachment-chip")).toHaveCount(3, { timeout: 10000 });

  await page.screenshot({
    path: `${SCREENSHOT_DIR}/attach-05-multi-chips.png`,
  });
  expect(errors).toHaveLength(0);
  await page.close();
});

test("UI: remove button dismisses individual chip", async () => {
  const page = await sharedContext.newPage();
  const errors = collectJsErrors(page);
  await navigateToSession(page, testSid);

  const fileInput = page.getByTestId("file-input");
  await fileInput.setInputFiles([
    { name: "keep.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2") },
    { name: "remove.csv", mimeType: "text/csv", buffer: Buffer.from("c,d\n3,4") },
  ]);

  await expect(page.getByTestId("attachment-chip")).toHaveCount(2, { timeout: 10000 });

  // Click the remove button on the first chip
  await page.getByTestId("remove-attachment").first().click();

  // Now only 1 chip remains
  await expect(page.getByTestId("attachment-chip")).toHaveCount(1, { timeout: 5000 });

  await page.screenshot({
    path: `${SCREENSHOT_DIR}/attach-06-after-remove.png`,
  });
  expect(errors).toHaveLength(0);
  await page.close();
});

test("UI: send with attachment → chips clear and user message bubble appears", async () => {
  const page = await sharedContext.newPage();
  const errors = collectJsErrors(page);
  await navigateToSession(page, testSid);

  const fileInput = page.getByTestId("file-input");
  await fileInput.setInputFiles({
    name: "analysis.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("product,revenue\nA,100\nB,200"),
  });

  await expect(page.getByTestId("attachment-chip").first()).toBeVisible({ timeout: 8000 });

  // Type a message alongside the attachment
  await page.getByPlaceholder("Message Corvin…").fill("Analysiere bitte diese Daten");

  const sendBtn = page.getByTestId("send-button");
  await expect(sendBtn).toBeEnabled();

  // Capture WS frames sent FROM client — must be set up before click
  const wsFramesSent: string[] = [];
  page.on("websocket", (ws) => {
    ws.on("framesent", (frame) => {
      if (frame.payload) wsFramesSent.push(String(frame.payload));
    });
  });

  await sendBtn.click();

  // After send: chips should be gone, input cleared
  await expect(page.getByTestId("attachment-chip")).toHaveCount(0, { timeout: 5000 });
  await expect(page.getByPlaceholder("Message Corvin…")).toHaveValue("");

  // A user message bubble should be visible in the chat
  // The bubble text (sent via WS) includes the attachment path + the typed message
  // We verify the turn count increased (a new turn was added)
  await page.waitForTimeout(800);

  await page.screenshot({
    path: `${SCREENSHOT_DIR}/attach-07-after-send.png`,
  });

  // The attachment path must appear in a user bubble in the DOM
  // (chat registry adds the message optimistically as a local turn)
  const chatArea = page.locator('[class*="space-y-6"]');
  await expect(chatArea).toContainText("attachments/analysis.csv", { timeout: 5000 });

  expect(errors).toHaveLength(0);
  await page.close();
});

test("UI: send-only-attachment (no text) is enabled when file attached", async () => {
  const page = await sharedContext.newPage();
  const errors = collectJsErrors(page);
  await navigateToSession(page, testSid);

  // Send button should be disabled with no text and no attachments
  const sendBtn = page.getByTestId("send-button");
  await expect(sendBtn).toBeDisabled();

  // Upload a file
  const fileInput = page.getByTestId("file-input");
  await fileInput.setInputFiles({
    name: "onlyfile.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("a,b\n1,2"),
  });

  await expect(page.getByTestId("attachment-chip").first()).toBeVisible({ timeout: 8000 });

  // Now send button should be enabled even without typed text
  await expect(sendBtn).toBeEnabled();

  expect(errors).toHaveLength(0);
  await page.close();
});
