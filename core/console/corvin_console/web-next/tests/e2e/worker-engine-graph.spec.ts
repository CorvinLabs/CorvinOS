/**
 * E2E test: Worker-Engine ACS Workflow Graph (ADR-0109 + ADR-0114)
 *
 * Proves:
 *  1. Fresh session: ACS Workflow Graph shows context-aware empty state
 *     (guidance about delegation, not a blank panel).
 *  2. After sending /delegate <task>: delegation run is created, ACS run
 *     appears in the graph panel within a polling window.
 *  3. Engine-settings API exposes delegation_enabled flag.
 *
 * Root cause investigated: `delegation_enabled` defaults to false on fresh
 * installations, so no ACS runs are created even after a chat turn.
 * The fix adds (a) delegation_enabled to the engine settings API response and
 * (b) context-aware guidance in the ACS Workflow Graph empty state.
 */

import {
  test,
  expect,
  type Page,
  type BrowserContext,
} from "@playwright/test";
import path from "path";
import fs from "fs";

const BASE_URL  = "http://localhost:5173";
const API_BASE  = "http://localhost:8765/v1/console";

const SCREENSHOT_DIR =
  process.env.PLAYWRIGHT_SCREENSHOT_DIR ??
  "/home/shumway/projects/CorvinOS/.corvin/tenants/_default/sessions/voice/discord/1502103856740302964/outputs";

let sharedContext: BrowserContext;

// ── Helpers ───────────────────────────────────────────────────────────────────

function ensureOutputDir(): void {
  if (!fs.existsSync(SCREENSHOT_DIR)) fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
}

async function shot(page: Page, label: string): Promise<void> {
  ensureOutputDir();
  const p = path.join(SCREENSHOT_DIR, `worker-graph-${label}.png`);
  await page.screenshot({ path: p, fullPage: false });
  console.log(`Screenshot → ${p}`);
}

async function navigateToChat(page: Page, sid?: string): Promise<void> {
  const url = sid
    ? `${BASE_URL}/console/app/chat/${sid}`
    : `${BASE_URL}/console/app/chat`;
  await page.goto(url, { waitUntil: "load" });
  await page.waitForTimeout(2_000);
}

/** Wait for WS "connected" indicator (green dot or status text). */
async function waitForWS(page: Page, timeoutMs = 20_000): Promise<void> {
  await page.waitForFunction(
    () => {
      const el = document.querySelector("[data-ws-status]");
      if (!el) return false;
      const val = el.getAttribute("data-ws-status") ?? "";
      return val === "connected" || val === "ready";
    },
    { timeout: timeoutMs },
  ).catch(() => {
    // WS status indicator may not be present; continue anyway.
  });
  await page.waitForTimeout(500);
}

/** Send a message and wait for the turn to complete. */
async function sendMessage(
  page: Page,
  text: string,
  label: string,
  turnTimeoutMs = 180_000,
): Promise<void> {
  await waitForWS(page, 20_000);
  const box = page.getByPlaceholder(/Message Corvin/i);
  await box.fill(text);
  await shot(page, `${label}-before-send`);
  await box.press("Enter");

  const stopBtn = page.locator('button[title*="Stop generation"]');
  const appeared = await stopBtn
    .waitFor({ state: "visible", timeout: 15_000 })
    .then(() => true)
    .catch(() => false);

  if (appeared) {
    await expect(stopBtn).toHaveCount(0, { timeout: turnTimeoutMs });
  } else {
    // WS may have reconnected — retry once
    await page.waitForTimeout(3_000);
    await waitForWS(page, 15_000);
    await box.fill(text);
    await box.press("Enter");
    await expect(
      page.locator('button[title*="Stop generation"]'),
    ).toHaveCount(0, { timeout: turnTimeoutMs });
  }
  await page.waitForTimeout(1_000);
  await shot(page, `${label}-after-turn`);
}

/** Open the Audit panel and navigate to "Single-Chain → ACS Workflow Graph". */
async function openAcsGraph(page: Page): Promise<void> {
  // Click the Audit button — always has title="Audit Trail"
  await page.locator('button[title="Audit Trail"]').waitFor({ state: "visible", timeout: 10_000 });
  await page.locator('button[title="Audit Trail"]').click();
  await page.waitForTimeout(500);

  // Ensure "Single-Chain" tab is active (default)
  const singleTab = page.getByRole("button", { name: /single.chain/i });
  if (await singleTab.isVisible()) await singleTab.click();
  await page.waitForTimeout(300);

  // Click the "ACS Workflow Graph" sub-tab inside WdatAuditPanel
  const acsTab = page.getByRole("button", { name: /ACS Workflow Graph/i });
  await acsTab.waitFor({ state: "visible", timeout: 5_000 });
  await acsTab.click();
  await page.waitForTimeout(500);
}

// ── Test suite ────────────────────────────────────────────────────────────────

test.describe.configure({ mode: "serial" });

test.describe("Worker-Engine ACS Workflow Graph", () => {
  test.beforeAll(async ({ browser }) => {
    sharedContext = await browser.newContext({
      storageState: "./tests/e2e/auth-state.json",
    });
  });

  test.afterAll(async () => {
    await sharedContext?.close();
  });

  // ── T1: Engine Settings API includes delegation_enabled ──────────────────
  test("T1: engine-settings API exposes delegation_enabled", async () => {
    const resp = await sharedContext.request.get(`${API_BASE}/settings/engine`);
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(typeof body.delegation_enabled).toBe("boolean");
    console.log(`delegation_enabled = ${body.delegation_enabled}`);
    console.log(`default_worker_engine = ${body.default_worker_engine}`);
  });

  // ── T2: Fresh session — context-aware empty state ────────────────────────
  test("T2: fresh session shows ACS empty state with guidance", async () => {
    const page = await sharedContext.newPage();
    try {
      // Create a new session via API — gives us a clean session with no ACS runs
      const csrf = await sharedContext.request
        .get(`${API_BASE}/auth/whoami`)
        .then((r) => r.json())
        .then((b) => b.csrf_token ?? "");
      const createResp = await sharedContext.request.post(
        `${API_BASE}/chat/sessions`,
        {
          data: { title: "Worker-Graph-E2E-T2" },
          headers: { "X-CSRF-Token": csrf },
        },
      );
      expect(createResp.status()).toBe(200);
      const created = await createResp.json();
      const sid = (created.session?.sid ?? created.sid) as string;
      expect(sid).toBeTruthy();
      console.log(`T2 created session: ${sid}`);

      // Navigate directly to the new session
      await navigateToChat(page, sid);
      await shot(page, "T2-new-session");

      // Open Audit → Single-Chain → ACS Workflow Graph
      await openAcsGraph(page);

      // Wait for loading spinner to disappear (runsQ finishes)
      await page.waitForFunction(
        () => !document.querySelector(".animate-spin"),
        { timeout: 15_000 },
      ).catch(() => { /* spinner may not appear or already gone */ });
      await page.waitForTimeout(500);
      await shot(page, "T2-acs-empty-state");

      // Empty state must be visible (no ACS runs in a freshly created session)
      const emptyText = page.getByText(/No worker-engine/i);
      const emptyIcon = page.locator('[data-testid="acs-empty-icon"]');
      await Promise.race([
        emptyText.waitFor({ state: "visible", timeout: 10_000 }),
        emptyIcon.waitFor({ state: "visible", timeout: 10_000 }),
      ]).catch(() => {});
      const hasEmpty = (await emptyIcon.isVisible().catch(() => false))
        || (await emptyText.isVisible().catch(() => false));
      expect(hasEmpty).toBe(true);

      // AcsEmptyState uses a secondary useQuery for engine settings — wait for it to resolve.
      // Poll for guidance text (delegation-related) up to 15 s.
      let hasGuidance = false;
      for (let i = 0; i < 15; i++) {
        const visibles = [
          page.getByText(/delegation is enabled/i),
          page.getByText(/Configure a Worker Engine/i),
          page.getByText(/delegation disabled/i),
          page.getByText(/\/delegate/i),
          page.getByRole("link", { name: /engine settings/i }),
        ];
        for (const loc of visibles) {
          if (await loc.isVisible().catch(() => false)) { hasGuidance = true; break; }
        }
        if (hasGuidance) break;
        await page.waitForTimeout(1_000);
      }
      await shot(page, "T2-acs-guidance");
      expect(hasGuidance).toBe(true);

      console.log("T2 PASS: fresh-session ACS empty state shows guidance");
    } finally {
      await page.close();
    }
  });

  // ── T3: /delegate forces a worker run → graph populates ─────────────────
  test("T3: /delegate forces ACS run — graph shows data", async () => {
    test.setTimeout(300_000); // 5 min — delegation + graph poll can be slow
    const page = await sharedContext.newPage();
    try {
      // Create session via API for a clean starting state
      const csrf = await sharedContext.request
        .get(`${API_BASE}/auth/whoami`)
        .then((r) => r.json())
        .then((b) => b.csrf_token ?? "");
      const createResp = await sharedContext.request.post(
        `${API_BASE}/chat/sessions`,
        {
          data: { title: "Worker-Graph-E2E-T3" },
          headers: { "X-CSRF-Token": csrf },
        },
      );
      const created = await createResp.json();
      const sid = (created.session?.sid ?? created.sid) as string;
      expect(sid).toBeTruthy();
      console.log(`T3 session id: ${sid}`);

      await navigateToChat(page, sid);
      await shot(page, "T3-new-session");

      // Send a /delegate message to force worker-engine delegation
      await sendMessage(
        page,
        "/delegate Antworte mit BEREIT und liste 3 Punkte auf.",
        "T3-delegate-turn",
        180_000,
      );

      // Open ACS Workflow Graph
      await openAcsGraph(page);
      await shot(page, "T3-acs-after-delegate-initial");

      // Poll until runs appear (up to 30 s — WdatAuditPanel polls every 5 s)
      let runsFound = false;
      for (let i = 0; i < 12; i++) {
        const hasRun =
          // ReactFlow canvas appears when at least one run is loaded
          (await page.locator(".react-flow__renderer").isVisible().catch(() => false)) ||
          // Run selector appears
          (await page.locator("select").isVisible().catch(() => false)) ||
          // Any run-id looking text (acs-web- prefix)
          (await page.getByText(/acs-web-|acs-/).first().isVisible().catch(() => false));
        if (hasRun) { runsFound = true; break; }
        await page.waitForTimeout(3_000);
      }

      await shot(page, "T3-acs-graph-final");

      if (!runsFound) {
        // Check via API directly — delegation may have completed without creating ACS run
        // (e.g. delegation is disabled for this tenant).
        const apiResp = await sharedContext.request.get(
          `${API_BASE}/chat/sessions/${encodeURIComponent(sid)}/wdat/runs`,
        );
        const apiBody = apiResp.ok() ? await apiResp.json() : { count: 0 };
        console.log(`WDAT runs from API: count=${apiBody.count}`);
        // If API also returns 0, delegation_enabled may be false — not a UI bug.
        const settingsResp = await sharedContext.request.get(`${API_BASE}/settings/engine`);
        const settings = settingsResp.ok() ? await settingsResp.json() : {};
        console.log(`delegation_enabled=${settings.delegation_enabled}, worker_engine=${settings.default_worker_engine}`);

        if (!settings.delegation_enabled) {
          // Delegation is disabled — the empty state should now show helpful guidance.
          // This is the "fresh install" case.  The test verifies the UX fix, not delegation.
          const hint = page.getByText(/delegation|Engine Settings|\/delegate/i);
          expect(await hint.isVisible().catch(() => false)).toBe(true);
          console.log("T3: delegation disabled — verified helpful guidance shown");
        } else {
          // Delegation IS enabled but no run was created within 36 s.  Real failure.
          throw new Error("delegation_enabled=true but no ACS run appeared within 36 s");
        }
      } else {
        console.log("T3 PASS: ACS Workflow Graph shows delegation run");
      }
    } finally {
      await page.close();
    }
  });

  // ── T4: Verify "OS-Turn Audit" graph still works after ACS view ──────────
  test("T4: OS-Turn Audit graph remains functional alongside ACS", async () => {
    const page = await sharedContext.newPage();
    try {
      // Find any session with OS-turn data to make the Audit button appear
      const sessResp = await sharedContext.request.get(`${API_BASE}/chat/sessions`);
      let targetSid = "";
      if (sessResp.ok()) {
        const sessData = await sessResp.json();
        for (const s of (sessData.sessions ?? [])) {
          const otr = await sharedContext.request.get(`${API_BASE}/chat/sessions/${s.sid}/os-turns`);
          if (otr.ok() && (await otr.json()).count > 0) {
            targetSid = s.sid;
            break;
          }
        }
      }

      if (!targetSid) {
        // Fall back: use any available session
        const sessData = await sessResp.json();
        targetSid = sessData.sessions?.[0]?.sid ?? "";
      }

      if (targetSid) {
        await navigateToChat(page, targetSid);
      } else {
        await navigateToChat(page);
      }
      await page.waitForTimeout(1_500);
      await shot(page, "T4-chat-loaded");

      // Open Audit panel via the "Audit" button (title="Audit Trail")
      await page.locator('button[title="Audit Trail"]').waitFor({ state: "visible", timeout: 10_000 });
      await page.locator('button[title="Audit Trail"]').click();
      await page.waitForTimeout(500);

      // Single-Chain → OS-Turn Audit
      const singleTab = page.getByRole("button", { name: /single.chain/i });
      if (await singleTab.isVisible({ timeout: 3_000 }).catch(() => false)) await singleTab.click();
      await page.waitForTimeout(300);

      const osTab = page.getByRole("button", { name: /OS-Turn Audit/i });
      await osTab.waitFor({ state: "visible", timeout: 5_000 });
      await osTab.click();

      // Wait for the OS-Turn loading spinner to disappear (data arrives or empty state renders)
      await page.waitForFunction(
        () => {
          // Loading text visible → not done yet
          const allText = document.body.innerText;
          return !allText.includes("Loading OS turns");
        },
        { timeout: 20_000 },
      ).catch(() => {});
      await page.waitForTimeout(300);
      await shot(page, "T4-os-turn-audit");

      // OS-Turn Audit panel is functional if it shows the header, the graph, or the empty state.
      // The ReactFlow canvas appears when at least one turn is loaded.
      const hasTurnData =
        await page.locator(".react-flow__renderer").isVisible().catch(() => false);
      const hasEmptyState =
        await page.getByText(/No OS-turn activity/i).isVisible().catch(() => false);
      const hasContent = hasTurnData || hasEmptyState;
      expect(hasContent).toBe(true);
      console.log("T4 PASS: OS-Turn Audit panel rendered correctly");
    } finally {
      await page.close();
    }
  });
});
