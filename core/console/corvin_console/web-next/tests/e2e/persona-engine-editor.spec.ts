/**
 * E2E tests for ADR-0123 PersonaEngineEditor
 *
 * Tests the "Engine & Model" section on the Personas detail page.
 * Auth pattern mirrors compliance-critical-pages.spec.ts:
 *   - context.request.get() for local-login (sets cookie on the 8765 domain)
 *   - shared BrowserContext to avoid hitting the rate-limit
 *   - page navigation via direct 8765 URLs (production build, not dev server)
 */
import { test, expect, Page, BrowserContext, Browser } from "@playwright/test";

const API_BASE = "http://localhost:8765/v1/console";
const NAV_BASE = "http://localhost:5173/console";
const OUT_DIR = process.env.PLAYWRIGHT_SCREENSHOT_DIR || "/tmp/corvin-e2e-screenshots";

test.describe.configure({ mode: "serial" });

let sharedCtx: BrowserContext;
let csrfToken = "";

async function getCSRF(ctx: BrowserContext): Promise<string> {
  const resp = await ctx.request.get(`${API_BASE}/auth/whoami`);
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  return body.csrf_token ?? "";
}

test.describe("ADR-0123 — PersonaEngineEditor", () => {
  test.beforeAll(async ({ browser }: { browser: Browser }) => {
    sharedCtx = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      storageState: "./tests/e2e/auth-state.json",
    });
    csrfToken = await getCSRF(sharedCtx);
  });

  test.afterAll(async () => {
    await sharedCtx?.close();
  });

  // ── Helper to get a fresh page from shared context ──────────────────────────

  async function newPage(): Promise<Page> {
    const page = await sharedCtx.newPage();
    page.on("console", (msg) => {
      if (msg.type() === "error") {
         
        console.log("[page-err]", msg.text().slice(0, 120));
      }
    });
    return page;
  }

  // ── Test 1: API endpoint returns expected config ─────────────────────────────

  test("GET /personas/coder/engine returns claude_code + opus config", async () => {
    const resp = await sharedCtx.request.get(`${API_BASE}/personas/coder/engine`);
    expect(resp.status()).toBe(200);
    const body = await resp.json();

    expect(body.engine).toBe("claude_code");
    expect(body.os_model).toBe("claude-opus-4-8");
    expect(body.worker_model).toBe("claude-opus-4-8");
    expect(body.engine_lock).toBe(false);
    expect(Array.isArray(body.available_engines)).toBe(true);
    expect(body.available_engines.length).toBeGreaterThan(0);
  });

  test("GET /personas/assistant/engine returns haiku config", async () => {
    const resp = await sharedCtx.request.get(`${API_BASE}/personas/assistant/engine`);
    expect(resp.status()).toBe(200);
    const body = await resp.json();

    expect(body.engine).toBe("claude_code");
    expect(body.os_model).toBe("claude-haiku-4-5");
    expect(body.worker_model).toBe("claude-haiku-4-5");
  });

  test("PUT /personas/coder/engine copies bundle to user scope and saves", async () => {
    // Write the same config back (idempotent round-trip)
    const resp = await sharedCtx.request.put(`${API_BASE}/personas/coder/engine`, {
      data: {
        engine: "claude_code",
        os_model: "claude-opus-4-8",
        worker_model: "claude-opus-4-8",
        engine_lock: false,
      },
      headers: { "X-CSRF-Token": csrfToken },
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.ok).toBe(true);
    expect(body.engine).toBe("claude_code");
  });

  test("PUT /personas/coder/engine rejects copilot as OS engine", async () => {
    const resp = await sharedCtx.request.put(`${API_BASE}/personas/coder/engine`, {
      data: { engine: "copilot", os_model: null, worker_model: null, engine_lock: false },
      headers: { "X-CSRF-Token": csrfToken },
    });
    expect(resp.status()).toBe(400);
    const body = await resp.json();
    expect(body.detail).toContain("worker-only");
  });

  // ── Test 2: UI renders Engine & Model card ───────────────────────────────────

  test("personas detail page renders Engine & Model card for coder", async () => {
    const page = await newPage();
    try {
      await page.goto(`${NAV_BASE}/app/personas/coder`, { waitUntil: "load" });
      await page.waitForTimeout(3000);

      // The card heading must be visible
      await expect(page.locator("text=Engine & Model")).toBeVisible({ timeout: 10000 });

      // Engine dropdown should exist and show claude_code
      await page.waitForFunction(
        () =>
          Array.from(document.querySelectorAll("select")).some((s) =>
            Array.from(s.options).some((o) => o.value === "claude_code"),
          ),
        { timeout: 10000 },
      );
    } finally {
      await page.close();
    }
  });

  test("cost-tier badge shows '$$$ premium' for coder (opus model)", async () => {
    const page = await newPage();
    try {
      await page.goto(`${NAV_BASE}/app/personas/coder`, { waitUntil: "load" });
      await page.waitForFunction(
        () => document.body.textContent?.includes("$$$ premium"),
        { timeout: 10000 },
      );
      await expect(page.locator("text=$$$ premium").first()).toBeVisible();
    } finally {
      await page.close();
    }
  });

  test("assistant persona shows '$ budget' for haiku model", async () => {
    const page = await newPage();
    try {
      await page.goto(`${NAV_BASE}/app/personas/assistant`, { waitUntil: "load" });
      await page.waitForFunction(
        () => document.body.textContent?.includes("$ budget"),
        { timeout: 10000 },
      );
      await expect(page.locator("text=$ budget").first()).toBeVisible();
    } finally {
      await page.close();
    }
  });

  test("engine-lock switch is toggleable on coder (user-scope)", async () => {
    const page = await newPage();
    try {
      await page.goto(`${NAV_BASE}/app/personas/coder`, { waitUntil: "load" });
      await page.waitForTimeout(2500);

      const lockSwitch = page.locator('[role="switch"]');
      await expect(lockSwitch).toBeVisible({ timeout: 8000 });

      const initialChecked = await lockSwitch.getAttribute("aria-checked");
      await lockSwitch.click();
      await page.waitForTimeout(400);
      const afterChecked = await lockSwitch.getAttribute("aria-checked");
      expect(initialChecked).not.toBe(afterChecked);

      // Toggle back to initial state
      await lockSwitch.click();
      await page.waitForTimeout(200);
    } finally {
      await page.close();
    }
  });

  test("engines page description references Personas page (ADR-0123)", async () => {
    const page = await newPage();
    try {
      await page.goto(`${NAV_BASE}/app/engines`, { waitUntil: "load" });
      await page.waitForTimeout(2000);
      const text = await page.textContent("body");
      expect(text).toContain("Personas page");
    } finally {
      await page.close();
    }
  });

  // ── Screenshot ───────────────────────────────────────────────────────────────

  test("screenshot: PersonaEngineEditor on coder persona", async () => {
    const { mkdirSync } = await import("fs");
    try { mkdirSync(OUT_DIR, { recursive: true }); } catch { /* intentional */ }

    const page = await newPage();
    try {
      await page.goto(`${NAV_BASE}/app/personas/coder`, { waitUntil: "load" });
      await page.waitForFunction(
        () => document.body.textContent?.includes("Engine & Model"),
        { timeout: 12000 },
      );
      await page.waitForTimeout(1000);

      await page.screenshot({
        path: `${OUT_DIR}/persona-engine-editor-coder.png`,
        fullPage: true,
      });
    } finally {
      await page.close();
    }
  });

  test("screenshot: assistant persona with haiku budget badge", async () => {
    const { mkdirSync } = await import("fs");
    try { mkdirSync(OUT_DIR, { recursive: true }); } catch { /* intentional */ }

    const page = await newPage();
    try {
      await page.goto(`${NAV_BASE}/app/personas/assistant`, { waitUntil: "load" });
      await page.waitForFunction(
        () => document.body.textContent?.includes("$ budget"),
        { timeout: 12000 },
      );
      await page.waitForTimeout(1000);

      await page.screenshot({
        path: `${OUT_DIR}/persona-engine-editor-assistant.png`,
        fullPage: true,
      });
    } finally {
      await page.close();
    }
  });
});
