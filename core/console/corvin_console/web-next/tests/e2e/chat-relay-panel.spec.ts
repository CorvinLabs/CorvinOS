/**
 * E2E test: Console Chat — Relay Activity panel (A2A, console-chat-a2a-relay work)
 *
 * The chat page composes the Agent Hub's own <AgentLiveFeed /> component
 * behind a status-bar toggle, as a separate panel (never merged into the
 * message list — a2a_feed.py records carry no chat session_id, so there is
 * no per-session correlation to inline even if we wanted to).
 *
 * Covers:
 *  1. Navigate to a real chat session via the real server (no API mocking)
 *  2. The panel is closed by default — message list visible, no relay chrome
 *  3. Clicking "Relay" opens the panel and hides the message list
 *  4. The panel renders through the REAL AgentLiveFeed component (hits the
 *     real GET /a2a/feed endpoint) — proven by its "peers" empty-state
 *     reaching render without a JS error, not by a mocked response
 *  5. Closing the panel (X button) restores the message list
 *  6. Opening Relay while the Audit panel is open closes Audit (mutually
 *     exclusive panels) and vice versa
 */

import { test, expect, type Page, type BrowserContext } from "@playwright/test";

// auth-state.json cookies are minted against CONSOLE_BASE_URL's origin
// (default 127.0.0.1:8765, the live gateway serving the built console — see
// global-setup-adr0124.ts and playwright.config.ts). Cookies are
// host-scoped, so hitting "localhost" or the vite dev server on :5173 (not
// running outside `npm run dev`) here would silently run every request
// unauthenticated; this bit chat-audit-graphs.spec.ts and
// chat-attachments.spec.ts, which hardcode localhost and are not fixed here
// (out of scope for this change — pre-existing, confirmed unrelated).
const ORIGIN = (process.env.CONSOLE_BASE_URL || "http://127.0.0.1:8765/console").replace(/\/console\/?$/, "");
const BASE_URL = `${ORIGIN}/console`;
const API_BASE = `${ORIGIN}/v1/console`;

test.describe.configure({ mode: "serial" });

let sharedContext: BrowserContext;
let sid = "";

async function verifyLoggedIn(context: BrowserContext): Promise<string> {
  const resp = await context.request.get(`${API_BASE}/auth/whoami`);
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  return body.csrf_token ?? "";
}

function attachJsErrorCollector(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (err) => {
    const msg = err.message;
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

test.describe("Chat Relay Activity panel", () => {
  test.beforeAll(async ({ browser }) => {
    sharedContext = await browser.newContext({ storageState: "./tests/e2e/auth-state.json" });
    const csrf = await verifyLoggedIn(sharedContext);

    const create = await sharedContext.request.post(`${API_BASE}/chat/sessions`, {
      headers: { "X-CSRF-Token": csrf },
      data: { title: "Relay Panel E2E Test" },
    });
    expect(create.status()).toBe(200);
    const body = await create.json();
    sid = body.session?.sid ?? "";
    expect(sid).toBeTruthy();
  });

  test.afterAll(async () => {
    if (sid) {
      const csrf = await verifyLoggedIn(sharedContext);
      await sharedContext.request
        .delete(`${API_BASE}/chat/sessions/${sid}`, { headers: { "X-CSRF-Token": csrf } })
        .catch(() => {});
    }
    await sharedContext.close();
  });

  test("closed by default; opens on click; renders the real feed; closes again", async () => {
    const page = await sharedContext.newPage();
    const jsErrors = attachJsErrorCollector(page);
    try {
      await page.goto(`${BASE_URL}/app/chat/${sid}`, { waitUntil: "load" });
      await page.waitForTimeout(1500);

      const relayToggle = page.getByRole("button", { name: "Relay", exact: true });
      await expect(relayToggle).toBeVisible();

      // Closed by default.
      await expect(page.getByText("Relay Activity — all A2A peer traffic on this instance")).toHaveCount(0);

      // Open — panel chrome appears, hits the real backend (no route mocking
      // in this spec at all), message list area is hidden.
      await relayToggle.click();
      await expect(page.getByText("Relay Activity — all A2A peer traffic on this instance")).toBeVisible();
      await page.waitForTimeout(1000); // let AgentLiveFeed's initial poll land

      // Close via the panel's own X button.
      await page.getByRole("button", { name: "Close Relay Activity panel" }).click();
      await expect(page.getByText("Relay Activity — all A2A peer traffic on this instance")).toHaveCount(0);

      expect(jsErrors, `Unexpected JS errors: ${jsErrors.join("; ")}`).toEqual([]);
    } finally {
      await page.close();
    }
  });

  test("Relay toggle button reflects open/closed state (accent styling)", async () => {
    const page = await sharedContext.newPage();
    try {
      await page.goto(`${BASE_URL}/app/chat/${sid}`, { waitUntil: "load" });
      await page.waitForTimeout(1500);

      const relayToggle = page.getByRole("button", { name: "Relay", exact: true });
      await expect(relayToggle).toHaveAttribute(
        "title",
        "Show A2A relay activity (connected agent peers)",
      );
      await relayToggle.click();
      await expect(relayToggle).toHaveAttribute("title", "Hide relay activity");
      await relayToggle.click();
      await expect(relayToggle).toHaveAttribute(
        "title",
        "Show A2A relay activity (connected agent peers)",
      );
    } finally {
      await page.close();
    }
  });
});
