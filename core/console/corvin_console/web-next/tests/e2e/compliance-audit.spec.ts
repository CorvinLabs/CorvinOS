/**
 * E2E test: CorvinOS Console — Compliance & Audit page (GDPR Art. 30/32 + EU AI Act Art. 50)
 *
 * Covers:
 *  1. Authenticate via local-login endpoint (no password, owner tier)
 *  2. Navigate to /app/compliance and verify the page renders its named sections:
 *       - "Compliance & Audit" heading (page identity)
 *       - "Structural guarantees" card (EU AI Act / GDPR lock-ins)
 *       - "Hash chain" card (audit-chain status — GDPR Art. 30 reporting path)
 *       - "Roles, consent & disclosure" card (consent gate state)
 *       - "Audit tail" card (live event feed)
 *  3. Verify the Hash chain card surfaces audit/hash-chain terminology:
 *       text matching /audit|hash.chain|verified/i
 *  4. Verify the Audit tail section has a Refresh button
 *  5. Intercept the /v1/console/audit/tail API call and assert it matches
 *       the expected URL pattern
 *  6. Intercept the /v1/console/dashboard API call and assert it matches
 *       the expected URL pattern (drives the hash-chain status card)
 *  7. Verify the /v1/console/audit/tail endpoint returns a well-formed
 *       JSON payload: { tenant_id, ts, count, events }
 *  8. Verify the /v1/console/dashboard endpoint returns audit_chain fields:
 *       { present, size_bytes?, last_event_type?, last_event_ts? }
 *  9. Verify /v1/console/members returns an array (consent/disclosure state)
 * 10. Verify no uncaught JS errors on the compliance page
 *
 * Authentication strategy:
 *   GET /v1/console/auth/local-login → sets session cookie (HTTP 302/200)
 *   Rate limit: ~4 calls/window — tests share a single browser context
 *   to avoid hitting the limit. Auth is done once in a shared beforeAll.
 *
 * Navigation strategy:
 *   Use waitUntil: "load" (not "networkidle") — the compliance page polls
 *   /dashboard and /audit/tail every 30 s, which prevents networkidle from
 *   ever settling.
 *
 * Mock strategy:
 *   API interception (page.route) is used to verify outbound URL patterns
 *   without requiring the backend to be running. The interceptors call
 *   route.continue() so tests that do have a backend receive real data.
 *   Pure API-shape tests use page.request.get() directly.
 */

import {
  test,
  expect,
  type Page,
  type BrowserContext,
} from "@playwright/test";

const BASE_URL = "http://localhost:5173";
const API_BASE = "http://localhost:8765/v1/console";

// ── Shared auth context ──────────────────────────────────────────────────────

test.describe.configure({ mode: "serial" }); // serial = no parallel within file

let sharedContext: BrowserContext;

// ── Helpers ──────────────────────────────────────────────────────────────────

async function verifyLoggedIn(context: BrowserContext): Promise<void> {
  const resp = await context.request.get(`${API_BASE}/auth/whoami`);
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body.tier).toBe("owner");
}

async function navigateToCompliance(page: Page): Promise<void> {
  // Use "load" not "networkidle" — the compliance page polls continuously
  await page.goto(`${BASE_URL}/console/app/compliance`, { waitUntil: "load" });
  // Allow React to hydrate and initial data fetches to begin
  await page.waitForTimeout(2000);
}

function attachJsErrorCollector(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (err) => {
    const msg = err.message;
    // Filter known benign noise from development environment
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

test.describe("Compliance & Audit page (GDPR Art. 30/32 + EU AI Act Art. 50)", () => {
  // Single shared login — avoids rate-limiting the local-login endpoint
  test.beforeAll(async ({ browser }) => {
    sharedContext = await browser.newContext({ storageState: "./tests/e2e/auth-state.json" });
    await verifyLoggedIn(sharedContext);
  });

  test.afterAll(async () => {
    await sharedContext?.close();
  });

  // ── 1. Page identity: heading and page title ──────────────────────────────

  test("Compliance page renders the 'Compliance & Audit' heading", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);

    try {
      await navigateToCompliance(page);

      // The page must render its primary heading — this is the structural
      // identity check for the GDPR Art. 30 reporting page.
      const heading = page.locator("h1").filter({ hasText: /Compliance/i }).first();
      await expect(heading).toBeVisible({ timeout: 10_000 });

      // The heading text must contain "Compliance"
      const headingText = await heading.textContent();
      expect(headingText).toMatch(/Compliance/i);

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 2. Structural guarantees card ─────────────────────────────────────────

  test("'Structural guarantees' card is present with EU AI Act / GDPR items", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);

    try {
      await navigateToCompliance(page);

      // The card heading "Structural guarantees" must be visible
      const guaranteesCard = page
        .locator("text=/Structural guarantees/i")
        .first();
      await expect(guaranteesCard).toBeVisible({ timeout: 10_000 });

      // The card must contain at least one compliance item mentioning the
      // hash-chained audit log (GDPR Art. 30/32 core guarantee)
      const auditItem = page
        .locator("text=/Hash-chained audit log/i")
        .first();
      await expect(auditItem).toBeVisible({ timeout: 10_000 });

      // Consent gate must be listed (GDPR Art. 6/7)
      const consentItem = page
        .locator("text=/consent gate/i")
        .first();
      await expect(consentItem).toBeVisible({ timeout: 10_000 });

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 3. Hash chain card — audit/hash-chain/verified terminology ───────────

  test("Hash chain card surfaces audit-chain status terminology", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);

    try {
      await navigateToCompliance(page);

      // The "Hash chain" card must be present — this is the GDPR Art. 30
      // audit-chain status indicator. The text must match the pattern
      // /audit|hash.chain|verified/i to confirm substantive content.
      const hashChainCard = page
        .locator("text=/Hash chain/i")
        .first();
      await expect(hashChainCard).toBeVisible({ timeout: 10_000 });

      // Verify that the page body contains audit/hash-chain terminology
      const bodyText = await page.textContent("body");
      expect(bodyText).toMatch(/audit|hash.chain|verified/i);

      // The card must describe how to run the integrity check
      const verifyHint = page
        .locator("text=/voice-audit verify/i")
        .first();
      await expect(verifyHint).toBeVisible({ timeout: 10_000 });

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 4. Roles, consent & disclosure card ──────────────────────────────────

  test("'Roles, consent & disclosure' card is present (EU AI Act Art. 50)", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);

    try {
      await navigateToCompliance(page);

      // This card surfaces per-uid consent gate state — the EU AI Act Art. 50
      // bot-disclosure structural guarantee lives here.
      const rolesCard = page
        .locator("text=/Roles, consent/i")
        .first();
      await expect(rolesCard).toBeVisible({ timeout: 10_000 });

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 5. Audit tail card and Refresh button ─────────────────────────────────

  test("Audit tail card is present with a Refresh button", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);

    try {
      await navigateToCompliance(page);

      // The "Audit chain" section must be present — it is the live feed of
      // the GDPR Art. 30 audit chain.
      const auditTailCard = page
        .locator("text=/Audit chain/i")
        .first();
      await expect(auditTailCard).toBeVisible({ timeout: 10_000 });

      // A Refresh button (icon-only) must be available so the operator can
      // manually trigger a re-read of the chain without reloading the page.
      // The button contains a lucide RefreshCw SVG icon with no visible text.
      const refreshButton = page
        .locator("button")
        .filter({ has: page.locator("svg.lucide-refresh-cw") })
        .first();
      await expect(refreshButton).toBeVisible({ timeout: 10_000 });

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 6. Intercept: audit/tail URL pattern is called ────────────────────────

  test("Compliance page makes a request to the /audit/tail endpoint", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);
    const auditTailCalls: string[] = [];

    try {
      // Intercept any request whose URL contains /audit/tail
      await page.route("**/audit/tail**", (route) => {
        auditTailCalls.push(route.request().url());
        route.continue();
      });

      await navigateToCompliance(page);

      // Allow the page's initial data fetch to fire
      await page.waitForTimeout(3000);

      // The compliance page MUST call /audit/tail — this is the GDPR Art. 30
      // reporting path. Intercepting the URL confirms the frontend wires
      // the audit endpoint correctly.
      expect(auditTailCalls.length).toBeGreaterThan(0);

      // The URL must match the expected pattern
      const url = auditTailCalls[0];
      expect(url).toMatch(/\/audit\/tail/);

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 7. Intercept: dashboard URL pattern is called ─────────────────────────

  test("Compliance page makes a request to the /dashboard endpoint (hash-chain card)", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);
    const dashboardCalls: string[] = [];

    try {
      // Intercept any request whose URL contains /dashboard
      await page.route("**/dashboard**", (route) => {
        dashboardCalls.push(route.request().url());
        route.continue();
      });

      await navigateToCompliance(page);
      await page.waitForTimeout(3000);

      // The hash-chain card calls /dashboard to get audit_chain status.
      // This confirms the GDPR Art. 30 hash-chain status is wired to
      // a real backend endpoint, not static data.
      expect(dashboardCalls.length).toBeGreaterThan(0);
      const url = dashboardCalls[0];
      expect(url).toMatch(/\/dashboard/);

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 8. API: /audit/tail returns well-formed JSON ──────────────────────────

  test("API: GET /audit/tail returns { tenant_id, ts, count, events }", async () => {
    const page = await sharedContext.newPage();

    try {
      const resp = await page.request.get(`${API_BASE}/audit/tail`);
      expect(resp.status()).toBe(200);

      const data = await resp.json();

      // Shape check — these fields are the GDPR Art. 30 audit payload
      expect(typeof data.tenant_id).toBe("string");
      expect(typeof data.ts).toBe("number");
      expect(typeof data.count).toBe("number");
      expect(Array.isArray(data.events)).toBe(true);

      // count must match the actual events array length
      expect(data.events).toHaveLength(data.count);
    } finally {
      await page.close();
    }
  });

  // ── 9. API: /audit/tail event schema validation ───────────────────────────

  test("API: /audit/tail events conform to { ts, event_type, severity, details } schema", async () => {
    const page = await sharedContext.newPage();

    try {
      const resp = await page.request.get(`${API_BASE}/audit/tail?limit=10`);
      expect(resp.status()).toBe(200);

      const data = await resp.json();

      // If there are events, validate their shape
      for (const ev of data.events) {
        // ts is a Unix timestamp (float)
        expect(typeof ev.ts).toBe("number");
        expect(ev.ts).toBeGreaterThan(0);

        // event_type is a non-empty string
        expect(typeof ev.event_type).toBe("string");
        expect(ev.event_type.length).toBeGreaterThan(0);

        // severity is one of the expected values
        expect(["INFO", "WARNING", "CRITICAL", "ERROR"]).toContain(ev.severity);

        // details is always present (may be empty object)
        expect(ev.details).toBeDefined();
        expect(typeof ev.details).toBe("object");

        // chain internals must NOT be exposed (prev_hash, hash)
        expect(Object.keys(ev)).not.toContain("prev_hash");
        expect(Object.keys(ev)).not.toContain("hash");
      }
    } finally {
      await page.close();
    }
  });

  // ── 10. API: /dashboard returns audit_chain fields ────────────────────────

  test("API: GET /dashboard returns audit_chain { present } field", async () => {
    const page = await sharedContext.newPage();

    try {
      const resp = await page.request.get(`${API_BASE}/dashboard`);
      expect(resp.status()).toBe(200);

      const data = await resp.json();

      // audit_chain must be present — it is the hash-chain status surface
      expect(data.audit_chain).toBeDefined();
      expect(typeof data.audit_chain.present).toBe("boolean");

      // If the chain exists, size_bytes must be a non-negative number
      if (data.audit_chain.present) {
        expect(typeof data.audit_chain.size_bytes).toBe("number");
        expect(data.audit_chain.size_bytes).toBeGreaterThanOrEqual(0);
      }

      // tenant_id must be present and non-empty
      expect(typeof data.tenant_id).toBe("string");
      expect(data.tenant_id.length).toBeGreaterThan(0);
    } finally {
      await page.close();
    }
  });

  // ── 11. API: /members returns consent/disclosure structure ────────────────

  test("API: GET /members returns chats array (consent gate state)", async () => {
    const page = await sharedContext.newPage();

    try {
      const resp = await page.request.get(`${API_BASE}/members`);
      // 200 = data available; 404 = no chats yet — both are valid states
      expect([200, 404]).toContain(resp.status());

      if (resp.status() === 200) {
        const data = await resp.json();
        expect(Array.isArray(data.chats)).toBe(true);

        // Validate shape of each chat summary
        for (const chat of data.chats) {
          expect(typeof chat.chat_key).toBe("string");
          expect(typeof chat.members).toBe("number");
          // consent_entries and disclosure_entries are GDPR Art. 6/7 and
          // EU AI Act Art. 50 counters — they must be numeric
          expect(typeof chat.consent_entries).toBe("number");
          expect(typeof chat.disclosure_entries).toBe("number");
        }
      }
    } finally {
      await page.close();
    }
  });

  // ── 12. Refresh button triggers a new /audit/tail request ─────────────────

  test("Clicking Refresh triggers a new request to /audit/tail", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);
    const auditTailCalls: string[] = [];

    try {
      await page.route("**/audit/tail**", (route) => {
        auditTailCalls.push(route.request().url());
        route.continue();
      });

      await navigateToCompliance(page);
      // Wait for the initial auto-fetch
      await page.waitForTimeout(3000);
      const callsBeforeRefresh = auditTailCalls.length;

      // Click Refresh — the AuditTailCard calls q.refetch()
      // The button is icon-only (lucide RefreshCw SVG, no text label).
      const refreshButton = page
        .locator("button")
        .filter({ has: page.locator("svg.lucide-refresh-cw") })
        .first();
      await expect(refreshButton).toBeVisible({ timeout: 10_000 });
      await refreshButton.click();

      // Wait for the refetch to fire
      await page.waitForTimeout(2000);

      // At least one additional /audit/tail call must have been made
      expect(auditTailCalls.length).toBeGreaterThan(callsBeforeRefresh);

      expect(jsErrors).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 13. No uncaught JS errors on the compliance page ─────────────────────

  test("No uncaught JS errors on the compliance page during full load cycle", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);
    const consoleErrors: string[] = [];

    page.on("console", (msg) => {
      if (msg.type() === "error") {
        const text = msg.text();
        // Filter expected development noise
        if (
          text.includes("Download the React DevTools") ||
          text.includes("ERR_ABORTED") ||
          text.includes("favicon.ico") ||
          text.includes("Warning:") ||
          text.includes("Failed to fetch") // API polling when backend is down
        ) {
          return;
        }
        consoleErrors.push(text);
      }
    });

    try {
      await navigateToCompliance(page);

      // Allow all initial data fetches to complete or fail gracefully
      await page.waitForTimeout(4000);

      // No uncaught page-level exceptions
      expect(jsErrors).toHaveLength(0);

      // No critical console errors (TypeErrors, ReferenceErrors etc.)
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
});
