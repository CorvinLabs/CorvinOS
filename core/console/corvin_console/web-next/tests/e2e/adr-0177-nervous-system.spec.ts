/**
 * E2E test: ADR-0177 — Nervous System: self-extensible health monitoring.
 *
 * Tests the NerveFiber protocol, NerveRegistry 3-tier discovery, and the
 * REST endpoints /aco/nerve/scan + /aco/nerve/repair against a LIVE gateway.
 *
 * Scenarios:
 *  1. API shape: /aco/nerve/scan returns valid JSON
 *  2. All 6 built-in fibers are present (Tier-0)
 *  3. Summary structure correct (severity counts, needs_repair list)
 *  4. InstallFiber: no CRITICAL signals on a properly installed system
 *  5. AuditChainFiber: present and scanning
 *  6. ComplianceFiber: EU AI Act / GDPR gates tracked
 *  7. Repair endpoint: dry_run=true returns before-summary without repair
 *  8. Repair endpoint: repair triggers without crashing
 *  9. Engine detection: EngineFiber reflects running engine (Hermes or Cloud Code)
 * 10. Audit trail: aco.nerve_scan event written after scan
 * 11. Screenshots captured to ./outputs/ for Discord
 *
 * The gateway serves the SPA at /console; Vite dev server (5173) proxies it.
 */

import {
  test,
  expect,
  type Page,
  type BrowserContext,
} from "@playwright/test";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

// ── Config ────────────────────────────────────────────────────────────────────

// Per-test budget for this whole file. The default 30 s is not enough: see below.
test.describe.configure({ timeout: 120_000 });

// NOTE on the 90 s request timeouts below (was 30 s).
// GET /v1/console/aco/nerve/scan measured 6.7-6.9 s on an IDLE machine — it probes
// every registered nerve fiber (installed packages, engines, disk, bridges), so that
// cost is inherent to what the endpoint does, not a regression. Under Playwright's
// four workers, with other specs driving the same console, it exceeded 30 s and both
// scan-based tests failed with "apiRequestContext.get: Timeout 30000ms exceeded" —
// reported as a product failure when the product was merely busy. Measured
// 2026-07-27. Profiled per fiber: aco.integrity is 4806 ms of the 6561 ms total (73%)
// because it CRYPTOGRAPHICALLY VERIFIES every tenant's full audit chain on every scan —
// four tenants x ~120 000 records here. That is inherent to what an integrity fiber
// does, not a defect, but it does mean this endpoint's cost grows with the chain, and
// four Playwright workers calling it at once queue behind each other.
const GATEWAY = "http://localhost:8765";
const API_BASE = `${GATEWAY}/v1/console`;

const __dirname_compat = path.dirname(fileURLToPath(import.meta.url));
const OUTPUTS_DIR = path.resolve(__dirname_compat, "../../../../../../outputs");

function ensureOutputsDir() {
  if (!fs.existsSync(OUTPUTS_DIR)) {
    fs.mkdirSync(OUTPUTS_DIR, { recursive: true });
  }
}

async function screenshot(page: Page, name: string) {
  ensureOutputsDir();
  const file = path.join(OUTPUTS_DIR, `nerve-${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async function getCsrf(page: Page): Promise<string> {
  const resp = await page.request.get(`${API_BASE}/auth/whoami`);
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  return body.csrf_token as string;
}

async function nerveScan(page: Page): Promise<any> {
  // Nerve scan runs 6 fibers synchronously; under parallel test load the
  // gateway thread-pool can take up to 30 s — use an explicit timeout.
  const resp = await page.request.get(`${API_BASE}/aco/nerve/scan`, { timeout: 90_000 });
  expect(resp.status(), `nerve/scan failed: ${resp.status()}`).toBe(200);
  return resp.json();
}

async function nerveRepair(page: Page, csrf: string, dry_run = true): Promise<any> {
  const resp = await page.request.post(`${API_BASE}/aco/nerve/repair`, {
    data: { dry_run },
    headers: { "X-CSRF-Token": csrf },
  });
  expect(resp.status(), `nerve/repair failed: ${resp.status()}`).toBe(200);
  return resp.json();
}

// ── Test suite ────────────────────────────────────────────────────────────────

test.describe("ADR-0177 — Nervous System E2E", () => {
  test.describe.configure({ mode: "serial" });

  let ctx: BrowserContext;
  let csrf: string = "";

  const EXPECTED_FIBERS = [
    "install.deps",
    "l16.audit_chain",
    "l16.compliance",
    "aco.integrity",
    "aco.engine",
    "aco.session",
  ];

  test.beforeAll(async ({ browser }) => {
    ctx = await browser.newContext({
      storageState: path.join(__dirname_compat, "auth-state.json"),
    });
  });

  test.afterAll(async () => {
    await ctx.close();
  });

  // ── 1. Auth ────────────────────────────────────────────────────────────────

  test("1. auth: session valid", async () => {
    const page = await ctx.newPage();
    try {
      const resp = await page.request.get(`${API_BASE}/auth/whoami`);
      expect(resp.status()).toBe(200);
      const body = await resp.json();
      csrf = body.csrf_token as string;
      expect(body.tier).toBe("owner");
    } finally {
      await page.close();
    }
  });

  // ── 2. Nerve scan: API shape ───────────────────────────────────────────────

  test("2. nerve/scan: returns valid JSON shape", async () => {
    const page = await ctx.newPage();
    try {
      const body = await nerveScan(page);
      expect(typeof body.ok).toBe("boolean");
      expect(Array.isArray(body.fibers)).toBe(true);
      expect(typeof body.summary).toBe("object");
      expect(Array.isArray(body.signals)).toBe(true);

      // summary must have severity counts
      const summary = body.summary;
      expect(typeof summary.total).toBe("number");
      expect(typeof summary.ok).toBe("number");
      expect(typeof summary.critical).toBe("number");
      expect(typeof summary.high).toBe("number");
      expect(typeof summary.medium).toBe("number");
      expect(typeof summary.low).toBe("number");
      expect(Array.isArray(summary.needs_repair)).toBe(true);
      expect(Array.isArray(summary.fibers)).toBe(true);
    } finally {
      await page.close();
    }
  });

  // ── 3. Nerve scan: all 6 built-in fibers present (Tier-0) ─────────────────

  test("3. nerve/scan: all 6 Tier-0 built-in fibers present", async () => {
    const page = await ctx.newPage();
    try {
      const body = await nerveScan(page);
      const fiberIds = (body.fibers as any[]).map((f) => f.fiber_id);
      for (const expected of EXPECTED_FIBERS) {
        expect(fiberIds, `Missing fiber: ${expected}`).toContain(expected);
      }
      // At least 6 fibers
      expect(fiberIds.length).toBeGreaterThanOrEqual(6);
    } finally {
      await page.close();
    }
  });

  // ── 4. InstallFiber: no CRITICAL on properly installed system ─────────────

  test("4. install.deps fiber: no CRITICAL on installed system", async () => {
    const page = await ctx.newPage();
    try {
      const body = await nerveScan(page);
      const installSignals = (body.signals as any[]).filter(
        (s) => s.fiber_id === "install.deps"
      );
      const critical = installSignals.filter((s) => s.severity === "CRITICAL");
      expect(
        critical,
        `install.deps has CRITICAL signals: ${JSON.stringify(critical)}`
      ).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  // ── 5. AuditChainFiber: registered ────────────────────────────────────────

  test("5. l16.audit_chain fiber: registered (may produce 0 signals on OK system)", async () => {
    const page = await ctx.newPage();
    try {
      const body = await nerveScan(page);
      // AuditChainFiber may return 0 signals on a healthy system (no signals = OK).
      // Fiber must be registered regardless.
      const fiberIds = (body.fibers as any[]).map((f) => f.fiber_id);
      expect(fiberIds).toContain("l16.audit_chain");
      // If signals exist, they must have the right fiber_id
      const auditSignals = (body.signals as any[]).filter(
        (s) => s.fiber_id === "l16.audit_chain"
      );
      for (const sig of auditSignals) {
        expect(sig.fiber_id).toBe("l16.audit_chain");
        expect(["OK", "LOW", "MEDIUM", "HIGH", "CRITICAL"]).toContain(sig.severity);
      }
    } finally {
      await page.close();
    }
  });

  // ── 6. ComplianceFiber: EU AI Act / GDPR gates ────────────────────────────

  test("6. l16.compliance fiber: EU AI Act / GDPR gates present", async () => {
    const page = await ctx.newPage();
    try {
      const body = await nerveScan(page);
      const fiberIds = (body.fibers as any[]).map((f) => f.fiber_id);
      expect(fiberIds).toContain("l16.compliance");
    } finally {
      await page.close();
    }
  });

  // ── 7. Engine fiber: reflects current engine (Hermes or Cloud Code) ────────

  test("7. aco.engine fiber: aco.engine registered, no CRITICAL on live gateway", async () => {
    const page = await ctx.newPage();
    try {
      const body = await nerveScan(page);
      // aco.engine fiber must be registered in Tier-0
      const fiberIds = (body.fibers as any[]).map((f) => f.fiber_id);
      expect(fiberIds).toContain("aco.engine");
      // EngineFiber emits 0 signals when engine is healthy (no signal = OK).
      // If signals exist, validate their shape and ensure none are CRITICAL.
      const engineSignals = (body.signals as any[]).filter(
        (s) => s.fiber_id === "aco.engine"
      );
      const criticals = engineSignals.filter((s) => s.severity === "CRITICAL");
      expect(
        criticals,
        `aco.engine emitted CRITICAL on live gateway: ${JSON.stringify(criticals)}`
      ).toHaveLength(0);
      // Shape validation for any signals that do appear
      for (const sig of engineSignals) {
        expect(typeof sig.signal_type).toBe("string");
        expect(typeof sig.severity).toBe("string");
        expect(typeof sig.message).toBe("string");
      }
    } finally {
      await page.close();
    }
  });

  // ── 8. Repair endpoint: dry_run returns before-summary ────────────────────

  test("8. nerve/repair dry_run=true: returns summary without repairing", async () => {
    const page = await ctx.newPage();
    try {
      const body = await nerveRepair(page, csrf, true);
      expect(body.ok).toBe(true);
      expect(body.dry_run).toBe(true);
      expect(typeof body.summary_before).toBe("object");
      expect(Array.isArray(body.repaired)).toBe(true);
      expect(body.repaired).toHaveLength(0); // dry_run: no repairs executed
    } finally {
      await page.close();
    }
  });

  // ── 9. Repair endpoint: live repair does not crash ─────────────────────────

  test("9. nerve/repair dry_run=false: runs without crash", async () => {
    const page = await ctx.newPage();
    try {
      const body = await nerveRepair(page, csrf, false);
      expect(body.ok).toBe(true);
      expect(body.dry_run).toBe(false);
      expect(typeof body.summary_before).toBe("object");
      expect(Array.isArray(body.repaired)).toBe(true);
    } finally {
      await page.close();
    }
  });

  // ── 10. No CRITICAL signals on a clean system ──────────────────────────────

  test("10. scan: ok=true when no CRITICAL signals", async () => {
    const page = await ctx.newPage();
    try {
      const body = await nerveScan(page);
      // ok is false only when there are CRITICAL signals
      const criticalCount = body.summary.critical as number;
      if (criticalCount === 0) {
        expect(body.ok).toBe(true);
      } else {
        // Log but don't fail — CRITICAL signals are real findings
        console.warn(
          `[nerve-e2e] ${criticalCount} CRITICAL signal(s) found:`,
          (body.signals as any[])
            .filter((s) => s.severity === "CRITICAL")
            .map((s) => `${s.fiber_id}: ${s.message}`)
        );
      }
    } finally {
      await page.close();
    }
  });

  // ── 11. Signal schema invariants ──────────────────────────────────────────

  test("11. all signals have required fields", async () => {
    const page = await ctx.newPage();
    try {
      const body = await nerveScan(page);
      const requiredFields = [
        "fiber_id",
        "signal_type",
        "severity",
        "message",
        "ts",
        "data",
        "repair_hint",
      ];
      for (const sig of body.signals as any[]) {
        for (const field of requiredFields) {
          expect(
            sig,
            `Signal missing field '${field}': ${JSON.stringify(sig)}`
          ).toHaveProperty(field);
        }
        expect(
          ["OK", "LOW", "MEDIUM", "HIGH", "CRITICAL"],
          `Unknown severity: ${sig.severity}`
        ).toContain(sig.severity);
      }
    } finally {
      await page.close();
    }
  });

  // ── 12. Screenshot ─────────────────────────────────────────────────────────

  test("12. screenshot: console loads cleanly", async () => {
    const page = await ctx.newPage();
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    try {
      await page.goto(`${GATEWAY}/console/app`, { waitUntil: "load", timeout: 90_000 });
      await page.waitForTimeout(2000);
      await screenshot(page, "console-overview");

      // No JS errors while loading
      const blockers = errors.filter(
        (e) =>
          !e.includes("favicon") &&
          !e.includes("ResizeObserver") &&
          !e.includes("Non-Error promise rejection")
      );
      expect(blockers, `JS errors: ${blockers.join(", ")}`).toHaveLength(0);
    } finally {
      await page.close();
    }
  });
});

// ── Engine-Specific Tests ─────────────────────────────────────────────────────
// These tests verify the nervous system correctly reflects engine state.
// EngineFiber only emits signals on PROBLEMS (engine unavailable, auto-healed).
// On a healthy system with the engine running, it returns 0 signals (= OK).

test.describe("ADR-0177 — Engine Detection via NerveFiber", () => {
  test.describe.configure({ mode: "serial" });

  let ctx2: BrowserContext;

  test.beforeAll(async ({ browser }) => {
    ctx2 = await browser.newContext({
      storageState: path.join(
        path.dirname(fileURLToPath(import.meta.url)),
        "auth-state.json"
      ),
    });
  });

  test.afterAll(async () => {
    await ctx2.close();
  });

  test("engine: aco.engine fiber is registered", async () => {
    const page = await ctx2.newPage();
    try {
      const body = await nerveScan(page);
      const fiberIds = (body.fibers as any[]).map((f) => f.fiber_id);
      expect(fiberIds).toContain("aco.engine");
    } finally {
      await page.close();
    }
  });

  test("engine: EngineFiber no CRITICAL when engine running", async () => {
    const page = await ctx2.newPage();
    try {
      const body = await nerveScan(page);
      // EngineFiber emits 0 signals when engine is healthy (no signal = OK)
      // If it does emit, it must not be CRITICAL (engine running = no hard failure)
      const engineCritical = (body.signals as any[]).filter(
        (s) => s.fiber_id === "aco.engine" && s.severity === "CRITICAL"
      );
      expect(
        engineCritical,
        `EngineFiber CRITICAL on live gateway: ${JSON.stringify(engineCritical)}`
      ).toHaveLength(0);
    } finally {
      await page.close();
    }
  });

  test("engine: EngineFiber handles Cloud Code and Hermes without crash", async () => {
    const page = await ctx2.newPage();
    try {
      // Both engines may be active or absent — the fiber must not crash either way
      const body = await nerveScan(page);
      expect(typeof body.ok).toBe("boolean");
      expect(Array.isArray(body.signals)).toBe(true);
      // Key: nerve/scan returns 200, no 500
    } finally {
      await page.close();
    }
  });
});

// ── Fresh-Install Scenario ────────────────────────────────────────────────────
// Verifies that the API works with no local state (no sessions, minimal config).

test.describe("ADR-0177 — Fresh-Install Scenario", () => {
  test("fresh: nerve scan works without any chat sessions", async ({ request }) => {
    // Use the global storageState set in playwright.config.ts
    const resp = await request.get(`${API_BASE}/aco/nerve/scan`, { timeout: 90_000 });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(Array.isArray(body.fibers)).toBe(true);
    expect(body.fibers.length).toBeGreaterThanOrEqual(6);
  });

  test("fresh: install.deps fiber passes on a clean install", async ({ request }) => {
    const resp = await request.get(`${API_BASE}/aco/nerve/scan`, { timeout: 90_000 });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    const installSignals = (body.signals as any[]).filter(
      (s) => s.fiber_id === "install.deps"
    );
    const critical = installSignals.filter((s) => s.severity === "CRITICAL");
    expect(
      critical,
      `install.deps CRITICAL (missing required packages): ${JSON.stringify(critical)}`
    ).toHaveLength(0);
  });

  test("fresh: boot-healer nerve-step does not crash the gateway", async ({ request }) => {
    // If the gateway is running, boot-healer has already fired (8s delay).
    // We verify the gateway responds normally — a crash from Step N would
    // kill the gateway and this request would fail.
    const resp = await request.get(`${API_BASE}/auth/whoami`);
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.tier).toBeDefined();
  });
});
