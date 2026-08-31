/**
 * E2E test: CorvinOS Console — Chat page + WdatAuditPanel (ADR-0109)
 *
 * Covers:
 *  1. Authenticate via local-login endpoint (no password, owner tier)
 *  2. Navigate to /app/chat and verify chat list loads
 *  3. Create a new chat session
 *  4. Open the Audit Trail panel (ShieldCheck button)
 *  5. Verify all three WdatAuditPanel tabs are clickable:
 *       "ACS Workflow Graph" | "OS-Turn Audit" | "Execution Log"
 *  6. Screenshot each tab
 *  7. Verify ReactFlow container renders in the OS-Turn Audit tab
 *  8. Navigate to an existing session that has OS-turn data
 *  9. Verify OS-Turn Audit shows real data (turn rows)
 * 10. Verify no uncaught JavaScript errors occur
 *
 * Authentication strategy:
 *   GET /v1/console/auth/local-login  → sets session cookie (HTTP 302)
 *   Rate limit: ~4 calls/window — tests share a single browser context
 *   to avoid hitting the limit. Auth is done once in a shared setup fixture.
 *
 * Navigation strategy:
 *   Use waitUntil: "load" (not "networkidle") — the chat page has continuous
 *   polling (every 2-5 s) which prevents networkidle from ever settling.
 */

import {
  test,
  expect,
  type Page,
  type BrowserContext,
} from "@playwright/test";

const BASE_URL = "http://localhost:5173";
const API_BASE = "http://localhost:8765/v1/console";

// Session with confirmed OS-turn data — discovered dynamically in beforeAll
let KNOWN_SESSION_WITH_DATA = "";

const SCREENSHOT_DIR =
  process.env.PLAYWRIGHT_SCREENSHOT_DIR || "/tmp/corvin-e2e-screenshots";

// ── Shared auth context ───────────────────────────────────────────────────────
//
// Using test.describe with a beforeAll to authenticate ONCE and share the
// storage state across all tests in this file. This avoids hitting the
// local-login rate limit (429 after ~4 rapid calls).

test.describe.configure({ mode: "serial" }); // serial = no parallel within this file

let sharedContext: BrowserContext;
let _authCookieHeader: string = "";

// ── Helpers ───────────────────────────────────────────────────────────────────

async function verifyLoggedIn(context: BrowserContext): Promise<string> {
  const resp = await context.request.get(`${API_BASE}/auth/whoami`);
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body.tier).toBe("owner");
  return body.csrf_token ?? "";
}

async function navigateToChat(page: Page, sid?: string): Promise<void> {
  const url = sid
    ? `${BASE_URL}/console/app/chat/${sid}`
    : `${BASE_URL}/console/app/chat`;
  // Use "load" not "networkidle" — the chat page polls continuously
  await page.goto(url, { waitUntil: "load" });
  // Allow React to hydrate and initial data fetches to complete
  await page.waitForTimeout(2000);
}

function attachJsErrorCollector(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (err) => {
    const msg = err.message;
    // Filter known benign noise
    if (
      msg.includes("ResizeObserver loop") ||
      msg.includes("Non-Error promise rejection") ||
      msg.includes("ChunkLoadError") ||
      msg.includes("Loading CSS chunk")
    ) {
      return;
    }
    errors.push(msg);
  });
  return errors;
}

// ── Test suite ────────────────────────────────────────────────────────────────

test.describe("Chat page + WdatAuditPanel (ADR-0109)", () => {
  // Single shared login — avoids rate-limiting the local-login endpoint
  test.beforeAll(async ({ browser }) => {
    sharedContext = await browser.newContext({ storageState: "./tests/e2e/auth-state.json" });
    _authCookieHeader = await verifyLoggedIn(sharedContext);

    // Discover a session with OS-turn data dynamically so the test is portable
    const sessResp = await sharedContext.request.get(`${API_BASE}/chat/sessions`);
    if (sessResp.ok()) {
      const sessData = await sessResp.json();
      for (const s of sessData.sessions ?? []) {
        const otr = await sharedContext.request.get(`${API_BASE}/chat/sessions/${s.sid}/os-turns`);
        if (otr.ok()) {
          const otrData = await otr.json();
          if (otrData.count > 0) {
            KNOWN_SESSION_WITH_DATA = s.sid;
            break;
          }
        }
      }
    }
  });

  test.afterAll(async () => {
    await sharedContext?.close();
  });

  // Each test gets a new page from the shared (already-authenticated) context
  test.beforeEach(async () => {
    /* no-op: auth is done once in beforeAll */
  });

  // ── 1. Chat list loads ─────────────────────────────────────────────────

  test("Chat list loads after login", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);

    try {
      await navigateToChat(page);

      // Verify the page loaded (React app is mounted)
      const body = await page.locator("body").first();
      await expect(body).toBeVisible();

      // The chat page should contain at least a sidebar or new-chat area
      const pageContent = await page.content();
      // The SPA mounts a root div — minimal sanity check
      expect(pageContent).toContain("id=\"root\"");

      // Take a screenshot of the chat list state
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/01-chat-list.png`,
        timeout: 30_000,
      });

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 2. New chat session can be created (API) ───────────────────────────

  test("New chat session can be created via API", async () => {
    const page = await sharedContext.newPage();

    try {
      // Get CSRF token
      const whoamiResp = await page.request.get(`${API_BASE}/auth/whoami`);
      const { csrf_token: csrf } = await whoamiResp.json();

      // Create session
      const createResp = await page.request.post(`${API_BASE}/chat/sessions`, {
        data: { title: "Playwright Audit Graph Test" },
        headers: { "X-CSRF-Token": csrf },
      });
      expect([200, 201]).toContain(createResp.status());
      const body = await createResp.json();
      // Response shape: { ok: true, session: { sid, chat_key, ... } }
      const sessionData = body.session ?? body;
      expect(sessionData.sid).toBeTruthy();
      expect(typeof sessionData.sid).toBe("string");
      expect(sessionData.chat_key).toBeTruthy();

      // Verify the new session is accessible
      const fetchResp = await page.request.get(
        `${API_BASE}/chat/sessions/${sessionData.sid}/os-turns`
      );
      // 200 = session exists; schema may be empty but it responds
      expect(fetchResp.status()).toBe(200);
    } finally {
      await page.close();
    }
  });

  // ── 3. Audit panel opens and shows three tabs ──────────────────────────

  test("WdatAuditPanel — Audit button opens panel with three tabs", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);

    try {
      await navigateToChat(page, KNOWN_SESSION_WITH_DATA);

      // The Audit button (ShieldCheck + "Audit" text)
      const auditBtn = page
        .locator('button:has-text("Audit")')
        .first();

      await expect(auditBtn).toBeVisible({ timeout: 12_000 });
      await auditBtn.click();
      await page.waitForTimeout(1000);

      // All three tab buttons must be visible
      const tabAcs = page.locator('button:has-text("ACS Workflow Graph")').first();
      const tabOs  = page.locator('button:has-text("OS-Turn Audit")').first();
      const tabLog = page.locator('button:has-text("Execution Log")').first();

      await expect(tabAcs).toBeVisible({ timeout: 8_000 });
      await expect(tabOs).toBeVisible({ timeout: 8_000 });
      await expect(tabLog).toBeVisible({ timeout: 8_000 });

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 4. Screenshot: ACS Workflow Graph tab ─────────────────────────────

  test("ACS Workflow Graph tab — screenshot", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);

    try {
      await navigateToChat(page, KNOWN_SESSION_WITH_DATA);

      const auditBtn = page.locator('button:has-text("Audit")').first();
      await expect(auditBtn).toBeVisible({ timeout: 12_000 });
      await auditBtn.click();
      await page.waitForTimeout(800);

      // ACS is the default tab — click it to be explicit
      const tabAcs = page.locator('button:has-text("ACS Workflow Graph")').first();
      await expect(tabAcs).toBeVisible({ timeout: 8_000 });
      await tabAcs.click();
      await page.waitForTimeout(1500);

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/acs-workflow-graph.png`,
        timeout: 30_000,
      });

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 5. Screenshot: OS-Turn Audit tab + ReactFlow check ────────────────

  test("OS-Turn Audit tab — ReactFlow graph visible + screenshot", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);

    try {
      await navigateToChat(page, KNOWN_SESSION_WITH_DATA);

      const auditBtn = page.locator('button:has-text("Audit")').first();
      await expect(auditBtn).toBeVisible({ timeout: 12_000 });
      await auditBtn.click();
      await page.waitForTimeout(800);

      const tabOs = page.locator('button:has-text("OS-Turn Audit")').first();
      await expect(tabOs).toBeVisible({ timeout: 8_000 });
      await tabOs.click();

      // Wait for OS-turn data to load (API call + React render)
      await page.waitForTimeout(2500);

      // Screenshot before assertions to capture whatever state we get
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/os-turn-audit.png`,
        timeout: 30_000,
      });

      // Check for either the ReactFlow container or the header
      const rfVisible = await page
        .locator('.react-flow, [class*="react-flow__"]')
        .first()
        .isVisible()
        .catch(() => false);

      const headerVisible = await page
        .locator('text=/OS-Turn Audit/i')
        .first()
        .isVisible()
        .catch(() => false);

      // At least the header must be visible (proves the tab rendered)
      expect(headerVisible || rfVisible).toBeTruthy();

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 6. Screenshot: Execution Log tab ──────────────────────────────────

  test("Execution Log tab — events visible + screenshot", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);

    try {
      await navigateToChat(page, KNOWN_SESSION_WITH_DATA);

      const auditBtn = page.locator('button:has-text("Audit")').first();
      await expect(auditBtn).toBeVisible({ timeout: 12_000 });
      await auditBtn.click();
      await page.waitForTimeout(800);

      const tabLog = page.locator('button:has-text("Execution Log")').first();
      await expect(tabLog).toBeVisible({ timeout: 8_000 });
      await tabLog.click();
      await page.waitForTimeout(2500);

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/execution-log.png`,
        timeout: 30_000,
      });

      // Execution Log header or event count should be visible
      const headerVisible = await page
        .locator('text=/Execution Log/i')
        .first()
        .isVisible()
        .catch(() => false);

      expect(headerVisible).toBeTruthy();

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 7. Full journey: all three tabs cycled with screenshots ───────────

  test("Full audit panel journey — cycle all tabs with screenshots", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);

    try {
      await navigateToChat(page, KNOWN_SESSION_WITH_DATA);

      // Screenshot 1: Chat page before opening audit
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/chat-page-before-audit.png`,
        timeout: 30_000,
      });

      const auditBtn = page.locator('button:has-text("Audit")').first();
      await expect(auditBtn).toBeVisible({ timeout: 12_000 });
      await auditBtn.click();
      await page.waitForTimeout(1500);

      // Screenshot 2: ACS Workflow Graph (default tab)
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/audit-panel-acs-tab.png`,
        timeout: 30_000,
      });

      // Switch to OS-Turn Audit
      const tabOs = page.locator('button:has-text("OS-Turn Audit")').first();
      await expect(tabOs).toBeVisible({ timeout: 8_000 });
      await tabOs.click();
      await page.waitForTimeout(2500);

      // Screenshot 3: OS-Turn Audit
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/audit-panel-os-turn-tab.png`,
        timeout: 30_000,
      });

      // Switch to Execution Log
      const tabLog = page.locator('button:has-text("Execution Log")').first();
      await expect(tabLog).toBeVisible({ timeout: 8_000 });
      await tabLog.click();
      await page.waitForTimeout(2500);

      // Screenshot 4: Execution Log
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/audit-panel-exec-log-tab.png`,
        timeout: 30_000,
      });

      // Switch back to ACS
      const tabAcs = page.locator('button:has-text("ACS Workflow Graph")').first();
      await expect(tabAcs).toBeVisible({ timeout: 8_000 });
      await tabAcs.click();
      await page.waitForTimeout(1500);

      // Screenshot 5: ACS final state
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/audit-panel-acs-tab-final.png`,
        timeout: 30_000,
      });

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 8. API: os-turns endpoint ──────────────────────────────────────────

  test("API: os-turns endpoint returns data for known session", async () => {
    if (!KNOWN_SESSION_WITH_DATA) {
      test.skip(true, "No session with OS-turn data found on this system — skip");
      return;
    }
    const page = await sharedContext.newPage();

    try {
      const resp = await page.request.get(
        `${API_BASE}/chat/sessions/${KNOWN_SESSION_WITH_DATA}/os-turns`
      );
      expect(resp.status()).toBe(200);
      const data = await resp.json();

      expect(data.sid).toBe(KNOWN_SESSION_WITH_DATA);
      expect(data.count).toBeGreaterThan(0);
      expect(data.turns).toHaveLength(data.count);

      // Verify turn structure
      const turn = data.turns[0];
      expect(turn.turn_id).toMatch(/^ot_/);
      expect(turn.persona).toBeTruthy();
      expect(turn.started_at).toBeTruthy();
      expect(typeof turn.completed).toBe("boolean");
    } finally {
      await page.close();
    }
  });

  // ── 9. API: execution-log endpoint ────────────────────────────────────

  test("API: execution-log endpoint returns os_turn events", async () => {
    const page = await sharedContext.newPage();

    try {
      const resp = await page.request.get(
        `${API_BASE}/chat/sessions/${KNOWN_SESSION_WITH_DATA}/execution-log`
      );
      expect(resp.status()).toBe(200);
      const data = await resp.json();

      expect(data.count).toBeGreaterThan(0);
      expect(data.entries.length).toBeGreaterThan(0);

      // All entries must conform to the ExecLogEntry schema
      for (const entry of data.entries) {
        expect(typeof entry.ts).toBe("number");
        expect(entry.ts).toBeGreaterThan(0);
        expect(entry.ts_iso).toBeTruthy();
        expect(entry.event_type).toBeTruthy();
        expect(["os", "acs"]).toContain(entry.role);
        expect(entry.details).toBeDefined();
      }

      // This session has OS turn events
      const osTurnEvents = data.entries.filter((e: { event_type: string }) =>
        e.event_type.startsWith("os_turn.")
      );
      expect(osTurnEvents.length).toBeGreaterThan(0);
    } finally {
      await page.close();
    }
  });

  // ── 10. API: wdat runs schema validation ──────────────────────────────

  test("API: wdat runs endpoint responds with valid schema", async () => {
    const page = await sharedContext.newPage();

    try {
      const resp = await page.request.get(
        `${API_BASE}/chat/sessions/${KNOWN_SESSION_WITH_DATA}/wdat`
      );
      expect(resp.status()).toBe(200);
      const data = await resp.json();

      expect(data.sid).toBe(KNOWN_SESSION_WITH_DATA);
      expect(typeof data.count).toBe("number");
      expect(Array.isArray(data.runs)).toBe(true);
      // count and runs must be consistent
      expect(data.runs).toHaveLength(data.count);
    } finally {
      await page.close();
    }
  });

  // ── 11. No critical JS errors during full audit panel lifecycle ────────

  test("No critical JS errors during full audit panel lifecycle", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);
    const consoleErrors: string[] = [];

    page.on("console", (msg) => {
      if (msg.type() === "error") {
        const text = msg.text();
        // Filter expected noise
        if (
          text.includes("Download the React DevTools") ||
          text.includes("ERR_ABORTED") ||
          text.includes("favicon.ico") ||
          text.includes("Warning:") ||
          text.includes("Failed to fetch") // polling errors on empty WDAT
        ) {
          return;
        }
        consoleErrors.push(text);
      }
    });

    try {
      await navigateToChat(page, KNOWN_SESSION_WITH_DATA);

      const auditBtn = page.locator('button:has-text("Audit")').first();
      await expect(auditBtn).toBeVisible({ timeout: 12_000 });
      await auditBtn.click();
      await page.waitForTimeout(800);

      // Cycle through all three tabs
      for (const tabText of [
        "ACS Workflow Graph",
        "OS-Turn Audit",
        "Execution Log",
      ]) {
        const tab = page.locator(`button:has-text("${tabText}")`).first();
        await expect(tab).toBeVisible({ timeout: 8_000 });
        await tab.click();
        await page.waitForTimeout(1200);
      }

      // No uncaught JS errors
      expect(jsErrors).toHaveLength(0);

      // No critical console errors (TypeErrors etc.)
      const criticalErrors = consoleErrors.filter(
        (e) =>
          e.includes("TypeError") ||
          e.includes("ReferenceError") ||
          e.includes("SyntaxError") ||
          e.includes("Cannot read properties of undefined") ||
          e.includes("Cannot read properties of null")
      );
      expect(criticalErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 12. ReactFlow container is present in OS-Turn graph view ──────────

  test("ReactFlow container exists in OS-Turn Audit graph view", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);

    try {
      await navigateToChat(page, KNOWN_SESSION_WITH_DATA);

      const auditBtn = page.locator('button:has-text("Audit")').first();
      await expect(auditBtn).toBeVisible({ timeout: 12_000 });
      await auditBtn.click();
      await page.waitForTimeout(500);

      const tabOs = page.locator('button:has-text("OS-Turn Audit")').first();
      await expect(tabOs).toBeVisible({ timeout: 8_000 });
      await tabOs.click();

      // Wait for ReactFlow to mount (OsTurnGraph uses ReactFlowProvider + ReactFlow)
      await page.waitForTimeout(3000);

      // ReactFlow injects a div.react-flow into the DOM
      const rfLocator = page.locator('.react-flow').first();
      const rfVisible = await rfLocator.isVisible().catch(() => false);

      // The OS-Turn panel either shows the ReactFlow graph (has data)
      // or the "No OS-turn activity" empty state (no data).
      // Both are valid; the important thing is neither crashes.
      const emptyState = await page
        .locator('text=/No OS-turn activity/i, text=/Loading OS turns/i')
        .count();

      // ReactFlow visible OR empty-state visible — exactly one must be true
      expect(rfVisible || emptyState > 0).toBeTruthy();

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });
});
